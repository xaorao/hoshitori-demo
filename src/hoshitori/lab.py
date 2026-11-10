"""The Hypothesis Laboratory core (product description 4.20): from a plain-language proposal to a frozen, typed, monitored
hypothesis.

A model may propose a typed document; deterministic code decides what it is. The condition that opens a forecast and the
condition that invalidates it are written in the one condition grammar of the indicator skill (one grammar and one evaluator for
every feature that has a condition), so a condition outside it is refused with the grammar's own words and no text is ever run.
Every missing, ambiguous or out-of-limit part of a proposal is a choice for the person and is listed; nothing is completed,
corrected or capped silently. A confirmed mandate is frozen as a version whose hash covers its parent, and an edit is a new
version. A preview on past bars says what it is. The monitor of the invalidation condition keeps three values apart, so a
condition that cannot be decided is UNKNOWN and never HOLDS.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from types import ModuleType
from typing import Any, Final, cast

from hoshitori.command_log import CommandLog

HORIZONS: Final = (1, 4, 24)
DIRECTIONS: Final = ("UP", "DOWN")
MAX_WEIGHT: Final = Decimal("0.05")  # the per-token limit of the risk kernel
ASSUMPTION_KINDS: Final = ("OBSERVATION", "INTERPRETATION", "CAUSAL", "FORECAST")
MAX_STATEMENT: Final = 400
MAX_ASSUMPTIONS: Final = 8
MAX_ASSUMPTION_TEXT: Final = 200
PREVIEW_LABEL: Final = "demonstration, not evidence: made on past data, which a model may have seen; no forecast was committed"
GRAMMAR_MISSING: Final = (
    "the indicator condition grammar is not installed, so a condition cannot be validated; "
    "install the skill beside this package: pip install ./skills/indicator-condition-check"
)
_SYMBOL = re.compile(r"[A-Z0-9]{1,20}")
_FIELDS: Final = {
    "statement": "the statement of the hypothesis",
    "symbol": "the token the hypothesis is about",
    "direction": "the direction (UP or DOWN)",
    "horizon_h": "the horizon in hours (1, 4 or 24)",
    "condition": "the condition that opens a forecast",
    "invalidation": "the condition that invalidates it",
    "max_weight": "the largest weight the strategy may hold",
    "assumptions": "the assumptions the hypothesis rests on (at least one)",
}


class LabError(ValueError):
    """A version that cannot be frozen, or a request about one that does not exist."""


def _grammar() -> ModuleType:
    try:
        from indicator_condition_check import condition  # type: ignore[import-untyped]
    except ImportError:
        raise LabError(GRAMMAR_MISSING) from None
    return cast(ModuleType, condition)


def grammar() -> ModuleType:
    """The condition grammar, or the laboratory's own refusal when it is not installed."""
    return _grammar()


def normalize_condition(raw: Any) -> dict[str, Any]:
    """A condition in its canonical form, with every default explicit; the grammar's own error if it is outside the grammar."""
    grammar = _grammar()
    return dict(grammar.normalize(grammar.parse_condition(raw)))


@dataclass(frozen=True)
class Assumption:
    id: str
    kind: str
    text: str


@dataclass(frozen=True)
class Mandate:
    statement: str
    symbol: str
    direction: str
    horizon_h: int
    condition: dict[str, Any]
    invalidation: dict[str, Any]
    max_weight: Decimal
    assumptions: tuple[Assumption, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "symbol": self.symbol,
            "direction": self.direction,
            "horizon_h": self.horizon_h,
            "condition": self.condition,
            "invalidation": self.invalidation,
            "max_weight": str(self.max_weight),
            "assumptions": [{"id": a.id, "kind": a.kind, "text": a.text} for a in self.assumptions],
        }

    @staticmethod
    def from_record(record: Mapping[str, Any]) -> Mandate:
        return Mandate(
            record["statement"],
            record["symbol"],
            record["direction"],
            record["horizon_h"],
            dict(record["condition"]),
            dict(record["invalidation"]),
            Decimal(record["max_weight"]),
            tuple(Assumption(a["id"], a["kind"], a["text"]) for a in record["assumptions"]),
        )


@dataclass(frozen=True)
class Draft:
    """What a proposal came to: a mandate when there is nothing left to choose, otherwise the list of choices."""

    mandate: Mandate | None
    choices: tuple[str, ...]


def _assumptions(value: Any, choices: list[str]) -> tuple[Assumption, ...]:
    if not isinstance(value, list):
        choices.append("the assumptions are a list")
        return ()
    if len(value) > MAX_ASSUMPTIONS:
        choices.append(f"a mandate has at most {MAX_ASSUMPTIONS} assumptions")
        return ()
    found: list[Assumption] = []
    for item in value:
        if not isinstance(item, dict) or not {"id", "kind", "text"} <= set(item):
            choices.append("an assumption has an id, a kind and a text")
        elif item["kind"] not in ASSUMPTION_KINDS:
            choices.append(f"an assumption's kind is one of {list(ASSUMPTION_KINDS)}, not {item['kind']}")
        elif not isinstance(item["text"], str) or not 1 <= len(item["text"]) <= MAX_ASSUMPTION_TEXT:
            choices.append(f"an assumption has 1 to {MAX_ASSUMPTION_TEXT} characters of text")
        elif not isinstance(item["id"], str) or not item["id"]:
            choices.append("an assumption has an id, a kind and a text")
        else:
            found.append(Assumption(item["id"], item["kind"], item["text"]))
    seen: set[str] = set()
    for a in found:
        if a.id in seen:
            choices.append(f"an assumption id is used once: {a.id}")
            break
        seen.add(a.id)
    return tuple(found)


def _condition(name: str, value: Any, choices: list[str]) -> dict[str, Any]:
    try:
        return normalize_condition(value)
    except LabError:
        raise
    except Exception as exc:  # the grammar's own error, in its own words
        choices.append(f"the {name} is not in the grammar: {exc}")
        return {}


def draft_from(proposal: Mapping[str, Any]) -> Draft:
    """Check a proposal; never complete, correct or cap any part of it."""
    choices: list[str] = []
    for field, phrase in _FIELDS.items():
        if field not in proposal or (field == "assumptions" and proposal[field] == []):
            choices.append(f"choose {phrase}")
    statement = proposal.get("statement")
    if "statement" in proposal and (not isinstance(statement, str) or not 1 <= len(statement) <= MAX_STATEMENT):
        choices.append(f"the statement is 1 to {MAX_STATEMENT} characters")
    symbol = proposal.get("symbol")
    if "symbol" in proposal and (not isinstance(symbol, str) or not _SYMBOL.fullmatch(symbol)):
        choices.append(f"the token is 1 to 20 capital letters or digits, not {symbol!r}")
    direction = proposal.get("direction")
    if "direction" in proposal and direction not in DIRECTIONS:
        choices.append(f"the direction is UP or DOWN, not {direction}")
    horizon = proposal.get("horizon_h")
    if "horizon_h" in proposal and horizon not in HORIZONS:
        choices.append(f"the horizon is 1, 4 or 24 hours, not {horizon}")
    weight = Decimal(0)
    if "max_weight" in proposal:
        try:
            weight = Decimal(str(proposal["max_weight"]))
            if not weight.is_finite():
                raise InvalidOperation
        except InvalidOperation:
            choices.append(f"the weight is a decimal number, not {proposal['max_weight']!r}")
        else:
            if weight <= 0:
                choices.append(f"the weight is above zero, not {proposal['max_weight']}")
            elif weight > MAX_WEIGHT:
                choices.append(
                    f"the weight {proposal['max_weight']} is above the limit of {MAX_WEIGHT}; the limit is never applied silently"
                )
    condition = _condition("condition", proposal["condition"], choices) if "condition" in proposal else {}
    invalidation = _condition("invalidation", proposal["invalidation"], choices) if "invalidation" in proposal else {}
    assumptions = _assumptions(proposal["assumptions"], choices) if "assumptions" in proposal else ()
    choices.extend(f"unknown member {name}" for name in proposal if name not in _FIELDS)
    if choices:
        return Draft(None, tuple(choices))
    mandate = Mandate(str(statement), str(symbol), str(direction), int(horizon), condition, invalidation, weight, assumptions)  # type: ignore[arg-type]
    return Draft(mandate, ())


@dataclass(frozen=True)
class Version:
    version_hash: str
    mandate: Mandate
    parent: str | None
    by: str
    frozen_at_ms: int | None = None  # when it was frozen; a version from before this was recorded has none and cannot be run forward


@dataclass(frozen=True)
class Preview:
    label: str
    bars: int
    true_bars: int
    false_bars: int
    null_bars: int
    last_true_index: int | None
    first_ts: int
    last_ts: int


@dataclass(frozen=True)
class Monitor:
    state: str  # INVALIDATED, HOLDS or UNKNOWN
    reason: str | None


def _version_hash(record: Mapping[str, Any], parent: str | None) -> str:
    text = json.dumps({"mandate": record, "parent": parent}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


class Lab:
    """The frozen versions, kept in a command log and rebuilt from it."""

    def __init__(self, path: Path) -> None:
        self._log = CommandLog(path)
        self._versions: dict[str, Version] = {}
        for command in self._log.read():
            self._add(Mandate.from_record(command["mandate"]), command["parent"], command["by"], command.get("at_ms"))

    def _add(self, mandate: Mandate, parent: str | None, by: str, at_ms: int | None) -> str:
        key = _version_hash(mandate.to_record(), parent)
        self._versions[key] = Version(key, mandate, parent, by, at_ms)
        return key

    def versions(self) -> dict[str, Version]:
        return dict(self._versions)

    def freeze(self, draft: Draft, by: str, parent: str | None = None, at_ms: int | None = None) -> str:
        """Freeze a confirmed draft. ``at_ms`` is the moment, which a forward run needs: only what happens after it can count."""
        if not by:
            raise LabError("a version is frozen by someone")
        if at_ms is not None and (isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0):
            raise LabError("the moment of freezing is whole milliseconds, zero or more")
        if draft.mandate is None:
            raise LabError("the draft still has choices to confirm: " + "; ".join(draft.choices))
        if parent is not None and parent not in self._versions:
            raise LabError("the parent version does not exist")
        record = draft.mandate.to_record()
        if _version_hash(record, parent) in self._versions:
            raise LabError("this version is already frozen")
        self._log.append({"op": "freeze", "mandate": record, "parent": parent, "by": by, "at_ms": at_ms})
        return self._add(draft.mandate, parent, by, at_ms)

    def _version(self, version: str) -> Version:
        if version not in self._versions:
            raise LabError("no such version")
        return self._versions[version]

    def preview(self, version: str, bars: Any) -> Preview:
        """The entry condition on past bars, labelled for what it is: a demonstration."""
        grammar = _grammar()
        found = self._version(version)
        if len(bars) == 0:
            raise LabError("no bars to preview")
        result = grammar.evaluate(grammar.parse_condition(found.mandate.condition), bars)
        series = result.series
        return Preview(
            PREVIEW_LABEL,
            len(series),
            sum(v is True for v in series),
            sum(v is False for v in series),
            sum(v is None for v in series),
            result.last_true_index,
            bars.ts[0],
            bars.ts[-1],
        )

    def monitor(self, version: str, bars: Any) -> Monitor:
        """Whether the invalidation condition holds on the last bar: true, false or, when it cannot be told, unknown."""
        grammar = _grammar()
        found = self._version(version)
        result = grammar.evaluate(grammar.parse_condition(found.mandate.invalidation), bars)
        last = result.series[-1] if result.series else None
        if last is True:
            return Monitor("INVALIDATED", None)
        if last is False:
            return Monitor("HOLDS", None)
        reasons = [c.null_reason for c in result.clauses if c.null_reason]
        return Monitor("UNKNOWN", reasons[0] if reasons else "the condition cannot be decided on the last bar")

    def forward_spec(self, version: str) -> dict[str, Any]:
        """The frozen version as data for a rule arm in the practice league; nothing in it is code."""
        m = self._version(version).mandate
        return {
            "kind": "RULE_ARM",
            "version": version,
            "symbol": m.symbol,
            "direction": m.direction,
            "horizon_h": m.horizon_h,
            "entry": m.condition,
            "invalidation": m.invalidation,
            "max_weight": str(m.max_weight),
            "frozen_at_ms": self._version(version).frozen_at_ms,
        }
