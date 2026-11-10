"""Arm R: what RYO itself concluded about a candidate, recorded beside the agents and settled by the same settlement (``ARM_R_SPEC``).

RYO's ``deep_analysis`` ends in a verdict word (``verdict.call``) and a preview trade plan. Neither is a probability, so arm R is never
given a probability or a Brier score (contract section 6). What it does get:

* **A reading of the word**, by a table fixed in advance and versioned (``TABLE_VERSION``): ``UP``, ``DOWN``, ``NOT_LONG`` (advises against
  buying), ``NONE`` (no direction) or ``UNREAD`` (a word the table does not know, kept verbatim). ``cautious`` is read as advice against a
  long, not as a forecast of a fall.
* **A hit or a miss** against the direction the same cycle's 24-hour directional settlement found on the same token: the same origin, the
  same oracle pair, the same rule. A tie is excluded for every reading, as the directional contract excludes it. ``NONE`` and ``UNREAD``
  are never scored.
* **The plan's levels** (the entry, the stop and the first target), read from the captured answer, because the evidence item does not hold
  the plan (it is an opaque interpretation field). A plan that is missing, not a number, or not ordered stop below entry below target is
  recorded as absent with RYO's own availability word; it is never repaired.

A read is a ``ryo_read`` record committed in the cycle's batch, one per candidate RYO read; its id is the cycle and the candidate, so a
read written twice is one read. Nothing here is shown to an agent: the agents' snapshot reader gives them observations only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final, cast

from hoshitori import canonical, envelope, jsonnum
from hoshitori.connector import ConnectorError
from hoshitori.ids import natural_id
from hoshitori.ryo_envelope import read_rest_success
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPE: Final = "ryo_read"
TYPE_VERSION: Final = 1
TABLE_VERSION: Final = "ryo-reading-1"
TOOL: Final = "deep_analysis"
HORIZON_HOURS: Final = 24
MAX_INSTANT_MS: Final = 253_402_300_799_999
MAX_WORD: Final = 64

READINGS: Final[dict[str, str]] = {
    "accumulate": "UP",
    "bullish": "UP",
    "constructive": "UP",
    "positive": "UP",
    "bearish": "DOWN",
    "negative": "DOWN",
    "avoid": "NOT_LONG",
    "cautious": "NOT_LONG",
    "neutral": "NONE",
}
KINDS: Final = ("UP", "DOWN", "NOT_LONG", "NONE", "UNREAD")
SCORED: Final = ("UP", "DOWN", "NOT_LONG")
DIRECTIONS: Final = ("ROSE", "FELL", "TIE")
READING_WORDS: Final[dict[str, str]] = {
    "UP": "read as up",
    "DOWN": "read as down",
    "NOT_LONG": "read as advice against buying",
    "NONE": "no direction",
    "UNREAD": "a word our table does not read",
}
_MEMBERS: Final = frozenset(
    {
        "cycle_id", "candidate_id", "symbol", "horizon_hours", "tool", "call", "reading", "table_version", "plan", "plan_availability",
        "evidence_id", "capture_id",
    }
)  # fmt: skip
_PLAN_MEMBERS: Final = frozenset({"entry", "stop", "target"})
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_WORD = re.compile(r"[a-z][a-z_ -]{0,63}")
_PLAIN = re.compile(r"[0-9]{1,15}(\.[0-9]{1,12})?")


class RyoReadError(ValueError):
    """A read that cannot be recorded, or a record that is not one."""


@dataclass(frozen=True)
class Plan:
    entry: str
    stop: str
    target: str


@dataclass(frozen=True)
class Read:
    """What one accepted ``deep_analysis`` answer said, before the cycle it belongs to is known."""

    candidate_id: str
    symbol: str
    call: str
    plan: Plan | None
    plan_availability: str
    evidence_id: str  # the interpretation item the call was read from
    capture_id: str

    def body(self, cycle_id: str) -> dict[str, canonical.JsonValue]:
        return body_of(
            cycle_id=cycle_id,
            candidate_id=self.candidate_id,
            symbol=self.symbol,
            call=self.call,
            plan=self.plan,
            plan_availability=self.plan_availability,
            evidence_id=self.evidence_id,
            capture_id=self.capture_id,
        )


def read_of(candidate_id: str, symbol: str, interpretation_id: str, call: object, capture_id: str, body: bytes) -> Read | None:
    """The read of an accepted answer, or None when it has no verdict word (then RYO said nothing to score, and nothing is made up)."""
    if not isinstance(call, str) or not call.strip():
        return None
    plan, availability = plan_from(body)
    return Read(candidate_id, symbol, call.strip(), plan, availability, interpretation_id, capture_id)


def reading_of(call: str) -> str:
    """The reading of RYO's verdict word by the table: one of ``KINDS``. Case and the spaces around the word do not matter."""
    return READINGS.get(call.strip().lower(), "UNREAD")


def hit(reading: str, direction: str) -> bool | None:
    """Whether a read was right about a settled direction, or None when it is not scored (no direction, an unread word, or a tie)."""
    if reading not in KINDS:
        raise RyoReadError(f"the reading is one of {KINDS}")
    if direction not in DIRECTIONS:
        raise RyoReadError(f"the direction is one of {DIRECTIONS}")
    if reading not in SCORED or direction == "TIE":
        return None
    if reading == "UP":
        return direction == "ROSE"
    return direction == "FELL"  # DOWN, and NOT_LONG (right when the price did not rise): the same once a tie is excluded above


def _plain(value: object) -> str | None:
    text = value.text if isinstance(value, jsonnum.Num) else None
    if text is None or _PLAIN.fullmatch(text) is None:
        return None
    try:
        return text if Decimal(text) > 0 else None
    except InvalidOperation:
        return None


def plan_from(body: bytes) -> tuple[Plan | None, str]:
    """The plan of a captured ``deep_analysis`` answer and RYO's availability word for it; no plan when it is missing or not well formed."""
    try:
        answer = read_rest_success(body, TOOL)
    except ConnectorError:
        return None, "unreadable"
    availability = answer.availability.get("trade_plan", "absent")
    plan = answer.data.get("trade_plan")
    if not isinstance(plan, dict):
        return None, availability
    targets = plan.get("targets")
    entry, stop = _plain(plan.get("entry")), _plain(plan.get("stop"))
    target = _plain(targets[0]) if isinstance(targets, list) and targets else None
    if entry is None or stop is None or target is None or not Decimal(stop) < Decimal(entry) < Decimal(target):
        return None, availability if availability != "available" else "malformed"
    return Plan(entry, stop, target), availability


def record_id(cycle_id: str, candidate_id: str) -> str:
    return natural_id("rr", {"cycle": cycle_id, "candidate": candidate_id})


def body_of(
    *,
    cycle_id: str,
    candidate_id: str,
    symbol: str,
    call: str,
    plan: Plan | None,
    plan_availability: str,
    evidence_id: str,
    capture_id: str,
) -> dict[str, canonical.JsonValue]:
    """The body of a read, checked."""
    body: dict[str, canonical.JsonValue] = {
        "cycle_id": cycle_id,
        "candidate_id": candidate_id,
        "symbol": symbol,
        "horizon_hours": HORIZON_HOURS,
        "tool": TOOL,
        "call": call,
        "reading": reading_of(call),
        "table_version": TABLE_VERSION,
        "plan": None if plan is None else {"entry": plan.entry, "stop": plan.stop, "target": plan.target},
        "plan_availability": plan_availability,
        "evidence_id": evidence_id,
        "capture_id": capture_id,
    }
    check_body(body)
    return body


def _id(value: object, what: str) -> None:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise RyoReadError(f"{what} is an identifier")


def check_body(body: object) -> None:
    """The body is a read this module writes, or an error."""
    if not isinstance(body, dict) or set(body) != _MEMBERS:
        raise RyoReadError(f"a read has exactly the members {sorted(_MEMBERS)}")
    for name in ("cycle_id", "candidate_id", "evidence_id", "capture_id"):
        _id(body[name], name)
    symbol, call = body["symbol"], body["call"]
    if not isinstance(symbol, str) or not 1 <= len(symbol) <= 20 or not symbol.isalnum():
        raise RyoReadError("the symbol is letters and digits, up to 20 characters")
    if not isinstance(call, str) or not 1 <= len(call) <= MAX_WORD or not call.isprintable():
        raise RyoReadError(f"the call is RYO's word as given, 1 to {MAX_WORD} printable characters")
    if body["horizon_hours"] != HORIZON_HOURS or isinstance(body["horizon_hours"], bool):
        raise RyoReadError(f"a read is for the {HORIZON_HOURS}-hour horizon")
    if body["tool"] != TOOL:
        raise RyoReadError(f"a read comes from {TOOL}")
    if body["table_version"] != TABLE_VERSION:
        raise RyoReadError(f"the reading table is {TABLE_VERSION}")
    if body["reading"] != reading_of(call):
        raise RyoReadError("the reading is the table's reading of the call")
    availability = body["plan_availability"]
    if not isinstance(availability, str) or _WORD.fullmatch(availability) is None:
        raise RyoReadError("the plan's availability is a lower-case word")
    plan = body["plan"]
    if plan is None:
        return
    if not isinstance(plan, dict) or set(plan) != _PLAN_MEMBERS:
        raise RyoReadError(f"a plan has exactly the members {sorted(_PLAN_MEMBERS)}")
    values = []
    for name in ("stop", "entry", "target"):
        text = plan[name]
        if not isinstance(text, str) or _PLAIN.fullmatch(text) is None or Decimal(text) <= 0:
            raise RyoReadError(f"the plan's {name} is a positive plain decimal")
        values.append(Decimal(text))
    if not values[0] < values[1] < values[2]:
        raise RyoReadError("a plan has its stop below its entry and its entry below its target")


def to_record(body: dict[str, canonical.JsonValue], source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger."""
    check_body(body)
    if isinstance(created_at_ms, bool) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise RyoReadError("the creation time is a whole number of milliseconds")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": record_id(cast(str, body["cycle_id"]), cast(str, body["candidate_id"])),
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": body,
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise RyoReadError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def from_record(record: dict[str, canonical.JsonValue]) -> dict[str, canonical.JsonValue]:
    """The body a record holds, read as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise RyoReadError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise RyoReadError("not a read record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    check_body(body)
    if record["id"] != record_id(cast(str, body["cycle_id"]), cast(str, body["candidate_id"])):
        raise RyoReadError("the id is not the id of what the record says")
    return body
