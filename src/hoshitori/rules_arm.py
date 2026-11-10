"""The rules arm ``L0``: a fixed rule on observations and a frozen mapping from what the rule says to a historical base rate.

``RULES_ARM_SPEC`` is the rule; this module is the mapping, its calibration and the decision. The arm exists to answer
whether a model adds anything over something fixed, cheap and hard to accuse of tuning, so everything it uses is written down
and hashed before a scored period:

* **The state.** For a horizon of ``h`` hours, ``z = ret_h / (atr14_share * sqrt(h))`` is the move so far in units of the
  move expected over ``h``; it falls in one of five states by fixed edges. The square root is the correctly rounded decimal
  square root at 28 digits.
* **The mapping.** For each horizon and state: how many non-overlapping windows were seen, how many ended higher and how many
  exactly level. The probability is ``(ups + 1) / (n + 2)``, rounded to six places and held to [0.01, 0.99]; a cell with
  fewer than ``MIN_CELL`` windows gives none. The mapping is a canonical record with its calibration facts and its SHA-256,
  and that hash is part of the arm's version, so a different mapping is a different agent.
* **Calibration** reads hourly bars only and uses the same two functions as the live features (``return_between``,
  ``atr_share_over``), so the rule that was counted is the rule that is applied. An origin with a bar missing in what it
  needs, or a range of zero, is counted as skipped and never filled in.
* **The decision** reads the cycle's snapshot through an independent reader: the features item of the candidate and nothing
  else. A feature that is unknown is a ``FAILED_EVIDENCE``, a state with too few windows is a voluntary ``NO_CALL``, and
  anything else is a call with a probability and an action (``LONG`` when the probability clears one half by the edge,
  otherwise ``FLAT``; there is no short).
"""

from __future__ import annotations

import hashlib
import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, InvalidOperation, localcontext
from enum import StrEnum
from typing import Final

from hoshitori import canonical
from hoshitori.evidence import EvidenceItem, Kind
from hoshitori.features import ATR_BARS, SOURCE, atr_share_over, return_between
from hoshitori.snapshot import Reader
from hoshitori.timeline import TimelineError, from_ms, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339
from hoshitori.venue_data import Candle

RULE_VERSION: Final = "1"
HORIZONS: Final = (1, 4, 24)  # hours
STATES: Final = ("S1", "S2", "S3", "S4", "S5")
EDGES: Final = (Decimal("-1"), Decimal("-0.25"), Decimal("0.25"), Decimal("1"))
MIN_CELL: Final = 100
EDGE: Final = Decimal("0.02")
P_FLOOR: Final = Decimal("0.01")
P_CEIL: Final = Decimal("0.99")
INVALIDATION: Final = ("close below entry minus 1.5 ATR14",)
MAX_INSTANT_MS: Final = 253_402_300_799_999
_CTX: Final = Context(prec=28, rounding=ROUND_HALF_EVEN)
_SIX: Final = Decimal("1E-6")
_HOUR: Final = timedelta(hours=1)


class RulesError(ValueError):
    """A mapping or a history that is not valid, or a decision asked of something that is not a snapshot reader."""


class Verdict(StrEnum):
    CALL = "CALL"
    NO_CALL = "NO_CALL"
    FAILED_EVIDENCE = "FAILED_EVIDENCE"


class Reason(StrEnum):
    UNMAPPED = "UNMAPPED"  # the state has too few windows behind it
    NO_RANGE = "NO_RANGE"  # the hourly range is zero, so the move cannot be put in units of it
    NO_FEATURES = "NO_FEATURES"  # the candidate has no features item (or more than one)
    FEATURE_UNKNOWN = "FEATURE_UNKNOWN"  # a feature the rule needs is unknown


class Action(StrEnum):
    LONG = "LONG"
    FLAT = "FLAT"


def state_of(z: Decimal) -> str:
    """The state of a normalised move: ``S1`` at or below -1, ``S2`` up to -0.25 including it, ``S3`` between, ``S4`` up to 1, ``S5``."""
    if z <= EDGES[0]:
        return STATES[0]
    if z <= EDGES[1]:
        return STATES[1]
    if z < EDGES[2]:
        return STATES[2]
    if z < EDGES[3]:
        return STATES[3]
    return STATES[4]


def normalised(ret: Decimal, atr_share: Decimal, hours: int) -> Decimal | None:
    """The move so far in units of the move expected over ``hours``, or ``None`` if the hourly range is zero."""
    with localcontext(_CTX):
        scale = atr_share * Decimal(hours).sqrt()
        return None if not scale else ret / scale


# --- the mapping ------------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Cell:
    n: int  # independent windows
    ups: int  # of them, ended above the origin
    ties: int  # ended exactly level (counted apart, not in n)


@dataclass(frozen=True)
class BaseRates:
    cells: tuple[tuple[tuple[int, str], Cell], ...]  # ((horizon, state), cell) for every horizon and state
    venue: str
    quote_class: str
    instruments: tuple[str, ...]
    period_start_ms: int
    period_end_ms: int
    skipped: int  # origins left out for a missing bar or a zero range

    def __post_init__(self) -> None:
        keys = [key for key, _ in self.cells]
        if keys != [(h, s) for h in HORIZONS for s in STATES]:
            raise RulesError("a mapping has a cell for every horizon and state, in order")
        for _, cell in self.cells:
            if min(cell.n, cell.ups, cell.ties, self.skipped) < 0 or cell.ups > cell.n:
                raise RulesError("a cell counts windows: ups are some of n, and nothing is negative")
        for what, value in (("the period start", self.period_start_ms), ("the period end", self.period_end_ms)):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
                raise RulesError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
        if self.period_end_ms < self.period_start_ms:
            raise RulesError("the period ends no earlier than it starts")
        if not self.venue or not self.quote_class or list(self.instruments) != sorted(set(self.instruments)):
            raise RulesError("a mapping names its venue, its quote class and its instruments, sorted and once each")

    def cell(self, hours: int, state: str) -> Cell:
        return dict(self.cells)[(hours, state)]

    def p(self, hours: int, state: str) -> Decimal | None:
        """The mapped probability of a rise, or ``None`` if the state has fewer than ``MIN_CELL`` windows."""
        cell = self.cell(hours, state)
        if cell.n < MIN_CELL:
            return None
        with localcontext(_CTX):
            value = (Decimal(cell.ups + 1) / Decimal(cell.n + 2)).quantize(_SIX, rounding=ROUND_HALF_EVEN)
        return min(max(value, P_FLOOR), P_CEIL)

    def body(self) -> dict[str, canonical.JsonValue]:
        return {
            "rule_version": RULE_VERSION,
            "min_cell": MIN_CELL,
            "edge": format(EDGE, "f"),
            "venue": self.venue,
            "quote_class": self.quote_class,
            "instruments": list(self.instruments),
            "interval_s": 3600,
            "period_start": format_rfc3339_ms(from_ms(self.period_start_ms)),
            "period_end": format_rfc3339_ms(from_ms(self.period_end_ms)),
            "skipped": self.skipped,
            "cells": {str(h): {s: {"n": c.n, "ups": c.ups, "ties": c.ties} for (hh, s), c in self.cells if hh == h} for h in HORIZONS},
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical.dumps(self.body())).hexdigest()


def dump(rates: BaseRates) -> bytes:
    """The file a mapping is kept in: its body and its hash, as canonical bytes."""
    return canonical.dumps({**rates.body(), "sha256": rates.sha256})


def _whole(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RulesError(f"{what} is a whole number")
    return value


def _instant(text: object, what: str) -> int:
    if not isinstance(text, str):
        raise RulesError(f"{what} is an RFC 3339 time")
    try:
        ms = to_ms(parse_rfc3339(text))
    except (TimeError, TimelineError) as exc:
        raise RulesError(f"{what}: {exc}") from exc
    if format_rfc3339_ms(from_ms(ms)) != text:
        raise RulesError(f"{what} is written as UTC with milliseconds")
    return ms


def load(raw: bytes) -> BaseRates:
    """A mapping from its file, or an error: the bytes are canonical, every member is there once, the constants are this rule's and the
    hash is the hash of what the file says."""
    try:
        record = canonical.parse_record(raw)
    except canonical.CanonicalError as exc:
        raise RulesError(f"the mapping file is not a canonical record: {exc}") from exc
    if canonical.dumps(record) != raw:
        raise RulesError("the mapping file is not in canonical form")
    claimed = record.pop("sha256", None)
    names = {
        "rule_version",
        "min_cell",
        "edge",
        "venue",
        "quote_class",
        "instruments",
        "interval_s",
        "period_start",
        "period_end",
        "skipped",
        "cells",
    }
    if set(record) != names:
        raise RulesError(f"the mapping file has the members {sorted(names)}")
    if (
        record["rule_version"] != RULE_VERSION
        or record["min_cell"] != MIN_CELL
        or record["edge"] != format(EDGE, "f")
        or record["interval_s"] != 3600
    ):
        raise RulesError("the mapping was made for another version of the rule or other constants")
    venue, quote, instruments, cells = record["venue"], record["quote_class"], record["instruments"], record["cells"]
    if (
        not isinstance(venue, str)
        or not isinstance(quote, str)
        or not isinstance(instruments, list)
        or not all(isinstance(i, str) for i in instruments)
    ):
        raise RulesError("the venue, the quote class and the instruments are text")
    if not isinstance(cells, dict) or set(cells) != {str(h) for h in HORIZONS}:
        raise RulesError("the cells are given for every horizon")
    built: list[tuple[tuple[int, str], Cell]] = []
    for h in HORIZONS:
        row = cells[str(h)]
        if not isinstance(row, dict) or set(row) != set(STATES):
            raise RulesError("each horizon has every state")
        for state in STATES:
            cell = row[state]
            if not isinstance(cell, dict) or set(cell) != {"n", "ups", "ties"}:
                raise RulesError("a cell has n, ups and ties")
            built.append(((h, state), Cell(_whole(cell["n"], "n"), _whole(cell["ups"], "ups"), _whole(cell["ties"], "ties"))))
    rates = BaseRates(
        tuple(built),
        venue,
        quote,
        tuple(str(i) for i in instruments),
        _instant(record["period_start"], "the period start"),
        _instant(record["period_end"], "the period end"),
        _whole(record["skipped"], "skipped"),
    )
    if claimed != rates.sha256:
        raise RulesError("the mapping file's hash is not the hash of its contents")
    return rates


def agent_version_hash(mapping: BaseRates) -> str:
    """The hash that stands for this arm in a forecast: the rule, its constants and the mapping."""
    return hashlib.sha256(
        canonical.dumps(
            {"arm": "L0", "rule_version": RULE_VERSION, "mapping": mapping.sha256, "min_cell": MIN_CELL, "edge": format(EDGE, "f")}
        )
    ).hexdigest()


# --- calibration ------------------------------------------------------------------------------------------------------------------------
def _origin(by_open: Mapping[datetime, Candle], bar: Candle, hours: int) -> tuple[Decimal, Decimal] | None:
    """The return over ``hours`` and the hourly range share at an origin, the way the live features take them: the bar ``hours`` earlier
    must exist, and the fifteen bars the range stands on must follow one another. ``None`` if one is missing or the price is too large."""
    before = by_open.get(bar.open_time - _HOUR * hours)
    run = [by_open.get(bar.open_time - _HOUR * back) for back in reversed(range(ATR_BARS + 1))]
    if before is None or any(found is None for found in run):
        return None
    try:
        return return_between(bar, before), atr_share_over([found for found in run if found is not None])
    except InvalidOperation:
        return None


def calibrate(
    histories: Mapping[str, Sequence[Candle]], *, venue: str, quote_class: str, period_start_ms: int, period_end_ms: int
) -> BaseRates:
    """Count, per horizon and state, the non-overlapping windows of the given hourly histories (instrument -> bars, oldest first).

    Walking each history from the oldest bar, a bar is an origin if the features can be taken at it, the bar ``h`` hours later exists and
    the hourly range is not zero; the next origin is at least ``h`` hours later, so windows never overlap. A bar that cannot be an origin is
    counted in ``skipped`` (the first bars of a history, which have no past, are among them).
    """
    counts = {(h, s): [0, 0, 0] for h in HORIZONS for s in STATES}
    skipped = 0
    for name in sorted(histories):
        bars = list(histories[name])
        if any(a.open_time >= b.open_time for a, b in itertools.pairwise(bars)):
            raise RulesError(f"the bars of {name} are in strictly increasing time")
        by_open = {bar.open_time: bar for bar in bars}
        for hours in HORIZONS:
            free_from = None
            for bar in bars:
                if free_from is not None and bar.open_time < free_from:
                    continue
                got, later = _origin(by_open, bar, hours), by_open.get(bar.open_time + _HOUR * hours)
                z = None if got is None else normalised(got[0], got[1], hours)
                if later is None or z is None:
                    skipped += 1
                    continue
                cell = counts[(hours, state_of(z))]
                end, start = Decimal(later.close), Decimal(bar.close)
                outcome = (end > start) - (end < start)  # 1 higher, -1 lower, 0 level
                if outcome:
                    cell[0] += 1
                    cell[1] += max(outcome, 0)
                else:
                    cell[2] += 1
                free_from = bar.open_time + _HOUR * hours
    return BaseRates(
        tuple(((h, s), Cell(*counts[(h, s)])) for h in HORIZONS for s in STATES),
        venue,
        quote_class,
        tuple(sorted(histories)),
        period_start_ms,
        period_end_ms,
        skipped,
    )


# --- the decision -----------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Decision:
    candidate_id: str
    horizon_hours: int
    verdict: Verdict
    reason: Reason | None
    action: Action | None
    p: Decimal | None
    state: str | None
    evidence_ids: tuple[str, ...]
    observation_set_hash: str
    mapping_sha256: str
    invalidation: tuple[str, ...]


def _number(item: EvidenceItem, name: str) -> Decimal | None:
    value = item.field(name)
    if not isinstance(value, str):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


def decide(reader: Reader, candidate_id: str, mapping: BaseRates, hours: int) -> Decision:
    """The arm's decision for one candidate and one horizon, from what the reader gives it."""
    if hours not in HORIZONS:
        raise RulesError(f"the horizon is one of {HORIZONS} hours")
    items = [item for item in reader.items(candidate_id) if item.source == SOURCE and item.kind is Kind.DERIVED]
    ids = reader.ids(candidate_id)

    def result(
        verdict: Verdict, reason: Reason | None, action: Action | None, p: Decimal | None, state: str | None, used: tuple[str, ...]
    ) -> Decision:
        return Decision(
            candidate_id,
            hours,
            verdict,
            reason,
            action,
            p,
            state,
            used,
            reader.set_hash(candidate_id, used),
            mapping.sha256,
            INVALIDATION if verdict is Verdict.CALL else (),
        )

    if len(items) != 1:
        return result(Verdict.FAILED_EVIDENCE, Reason.NO_FEATURES, None, None, None, ())
    item = items[0]
    used = (item.evidence_id, *(p for p, _ in item.derived_from if p in ids))
    ret, atr = _number(item, f"ret_{hours}h"), _number(item, "atr14_share")
    if ret is None or atr is None:
        return result(Verdict.FAILED_EVIDENCE, Reason.FEATURE_UNKNOWN, None, None, None, used)
    z = normalised(ret, atr, hours)
    if z is None:
        return result(Verdict.NO_CALL, Reason.NO_RANGE, None, None, None, used)
    state = state_of(z)
    p = mapping.p(hours, state)
    if p is None:
        return result(Verdict.NO_CALL, Reason.UNMAPPED, None, None, state, used)
    return result(Verdict.CALL, None, Action.LONG if p >= Decimal("0.5") + EDGE else Action.FLAT, p, state, used)
