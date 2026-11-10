"""Arm R's plan: whether RYO's own stop or its first target was touched first in the 24 hours its verdict is scored over.

RYO's preview plan gives absolute levels (``ryo_read``: entry, stop, first target). The window is the one the read is already scored on:
the same cycle's 24-hour calls on the token, which share one origin, one oracle pair and one start price ``P0`` (contract section 25).
So no new settlement rule is written: the levels become distances from ``P0`` and the window is settled by ``settlement.settle_barrier``,
the rule the agents' barrier calls are settled by and that the second implementation agrees with.

* **A long plan.** RYO's plan has its stop below its entry and its target above (``ryo_read`` refuses any other), so it is a long plan:
  ``TARGET`` if the target was touched first, ``STOP`` if the stop was, ``TIMEOUT`` if neither in 24 hours, ``AMBIGUOUS`` if one minute
  touched both and no finer data tells them apart.
* **Already past.** If ``P0`` is at or above the target, or at or below the stop, the plan was decided before the window opened; it is
  recorded as ``ALREADY_PAST`` and never scored. The plan is RYO's at the time of its answer; the window starts later.
* **One record per read,** ``ryo_plan_settlement``, whose id is the read's; it names the 24-hour call whose window it used. A read with no
  plan has no record: its plan is absent, as RYO said.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final, cast

from hoshitori import canonical, envelope, ryo_read
from hoshitori.ids import natural_id
from hoshitori.settlement import Barrier, Settlement, Side
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPE: Final = "ryo_plan_settlement"
TYPE_VERSION: Final = 1
ALREADY_PAST: Final = "ALREADY_PAST"
HORIZON_MS: Final = ryo_read.HORIZON_HOURS * 3_600_000
STATES: Final = ("SETTLED", "AMBIGUOUS", "VOID", "UNSETTLEABLE", ALREADY_PAST)
OUTCOMES: Final = ("TARGET", "STOP", "TIMEOUT", "AMBIGUOUS")
MAX_INSTANT_MS: Final = 253_402_300_799_999
_MEMBERS: Final = frozenset(
    {"read_id", "window_forecast_id", "pair_id", "state", "outcome", "reason", "p0", "stop", "target", "touch_open_ms"}
)
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,99}")
_PLAIN = re.compile(r"[0-9]{1,30}(\.[0-9]{1,30})?")
_REASON = re.compile(r"[A-Z][A-Z_]{0,63}")


class RyoPlanError(ValueError):
    """A plan settlement that cannot be recorded, or a record that is not one."""


@dataclass(frozen=True)
class Window:
    """The 24-hour window a read is scored over: the call whose window it is, where it starts, its oracle pair and its start price."""

    forecast_id: str
    origin_ms: int
    quote_class: str
    pair_id: str
    p0: str | None  # none when the window could not be settled: then the plan cannot be either


def barrier_of(read_id: str, plan: ryo_read.Plan, window: Window) -> Barrier | None:
    """The plan as a long barrier from the window's start price, or None when the start price is already at or past a level."""
    if window.p0 is None:
        raise RyoPlanError("a window with no start price settles no plan")
    p0, stop, target = Decimal(window.p0), Decimal(plan.stop), Decimal(plan.target)
    if not stop < p0 < target:
        return None
    return Barrier(read_id, window.origin_ms, HORIZON_MS, window.quote_class, Side.LONG, target - p0, p0 - stop)


def record_id(read_id: str) -> str:
    return natural_id("rp", {"read": read_id})


def body_of(read_id: str, plan: ryo_read.Plan, window: Window, settled: Settlement | None) -> dict[str, canonical.JsonValue]:
    """The body of a plan settlement: the barrier's settlement, or (with ``settled`` None) a plan that was already past at the start."""
    if settled is None:
        state, outcome, reason, touch = ALREADY_PAST, None, None, None
    else:
        if settled.forecast_id != read_id or settled.contract != "B":
            raise RyoPlanError("the settlement is the barrier settlement of this read's plan")
        state, outcome, reason, touch = settled.state.value, settled.outcome, settled.reason, settled.touch_open_ms
        if settled.p0 is not None and (window.p0 is None or Decimal(settled.p0) != Decimal(window.p0)):
            raise RyoPlanError("the barrier's start price is the window's start price")
    body: dict[str, canonical.JsonValue] = {
        "read_id": read_id,
        "window_forecast_id": window.forecast_id,
        "pair_id": window.pair_id,
        "state": state,
        "outcome": outcome,
        "reason": reason,
        "p0": window.p0,
        "stop": plan.stop,
        "target": plan.target,
        "touch_open_ms": touch,
    }
    check_body(body)
    return body


def _id(value: object, what: str) -> None:
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise RyoPlanError(f"{what} is an identifier")


def _price(value: object, what: str) -> Decimal:
    if not isinstance(value, str) or _PLAIN.fullmatch(value) is None or Decimal(value) <= 0:
        raise RyoPlanError(f"{what} is a positive plain decimal")
    return Decimal(value)


def check_body(body: object) -> None:
    """The body is a plan settlement this module writes, or an error."""
    if not isinstance(body, dict) or set(body) != _MEMBERS:
        raise RyoPlanError(f"a plan settlement has exactly the members {sorted(_MEMBERS)}")
    for name in ("read_id", "window_forecast_id", "pair_id"):
        _id(body[name], name)
    stop, target = _price(body["stop"], "the stop"), _price(body["target"], "the target")
    if not stop < target:
        raise RyoPlanError("the stop is below the target")
    state, outcome, reason, touch = body["state"], body["outcome"], body["reason"], body["touch_open_ms"]
    if state not in STATES:
        raise RyoPlanError(f"the state is one of {STATES}")
    if body["p0"] is None:
        if state not in ("VOID", "UNSETTLEABLE"):
            raise RyoPlanError("only a void or unsettleable plan has no start price")
        if outcome is not None or touch is not None or reason is None:
            raise RyoPlanError("a void or unsettleable plan has a reason and no outcome")
        return
    p0 = _price(body["p0"], "the start price")
    if reason is not None and (not isinstance(reason, str) or _REASON.fullmatch(reason) is None):
        raise RyoPlanError("a reason is an upper-case code")
    if touch is not None and (isinstance(touch, bool) or not isinstance(touch, int) or not 0 <= touch <= MAX_INSTANT_MS):
        raise RyoPlanError("the touch time is a whole number of milliseconds")
    past = not stop < p0 < target
    if state == ALREADY_PAST:
        if not past or outcome is not None or reason is not None or touch is not None:
            raise RyoPlanError("a plan already past at the start has a start price at or beyond a level, and nothing else")
        return
    if past:
        raise RyoPlanError("a plan whose start price is at or beyond a level is already past")
    if state == "SETTLED":
        if outcome not in ("TARGET", "STOP", "TIMEOUT") or reason is not None or (touch is None) != (outcome == "TIMEOUT"):
            raise RyoPlanError("a settled plan says which level came first, and when, or that neither did")
    elif state == "AMBIGUOUS":
        if outcome != "AMBIGUOUS" or reason != "SAME_CANDLE" or touch is None:
            raise RyoPlanError("an ambiguous plan is one whose minute touched both levels")
    elif outcome is not None or touch is not None or reason is None:
        raise RyoPlanError("a void or unsettleable plan has a reason and no outcome")


def to_record(body: dict[str, canonical.JsonValue], source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger."""
    check_body(body)
    if isinstance(created_at_ms, bool) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise RyoPlanError("the creation time is a whole number of milliseconds")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": record_id(cast(str, body["read_id"])),
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": body,
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise RyoPlanError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def from_record(record: dict[str, canonical.JsonValue]) -> dict[str, canonical.JsonValue]:
    """The body a record holds, read as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise RyoPlanError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise RyoPlanError("not a plan settlement of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    check_body(body)
    if record["id"] != record_id(cast(str, body["read_id"])):
        raise RyoPlanError("the id is not the id of what the record says")
    return body
