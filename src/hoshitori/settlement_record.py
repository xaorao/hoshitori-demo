"""The settlement as a record: what a producer submits to the ledger when a forecast's horizon has been settled, and the strict reader.

``CYCLE_RUNNER_SPEC`` section 3 is the rule. A settlement is a new record with a natural id (the forecast, the oracle pair and the rule
version, as in ``settlement``); it never edits the forecast, and it names the forecast record it settles by its id and digest. Only the
states that end a slot are recorded: a ``PENDING`` answer writes nothing. The reader is as strict as the writer: closed lists, plain
decimal text that reads back to itself, a state that agrees with its outcome and reason (a tie only for a directional forecast: a barrier
forecast has no tie, contract section 3), and an id that is the id of what the body says.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.forecast import ORACLE_PAIRS
from hoshitori.ids import natural_id
from hoshitori.lifecycle import UNSETTLEABLE_REASONS
from hoshitori.settlement import RULE_VERSION, VOID_REASONS, Settlement, State
from hoshitori.timeline import from_ms
from hoshitori.timeutil import format_rfc3339_ms

TYPE: Final = "settlement"
TYPE_VERSION: Final = 1
MAX_INSTANT_MS: Final = 253_402_300_799_999
RECORDED: Final = (State.SETTLED, State.AMBIGUOUS, State.VOID, State.UNSETTLEABLE)
BARRIER_OUTCOMES: Final = ("TARGET", "STOP", "TIMEOUT")
FLAGS: Final = frozenset({"BASIS_STRESS"})
_MEMBERS: Final = frozenset(
    {"forecast_id", "contract", "pair_id", "rule_version", "state", "outcome", "reason", "p0", "outcome_price", "touch_open_ms", "flags"}
)


class SettlementRecordError(ValueError):
    """A settlement that cannot be recorded, or a record that is not one."""


def _decimal(text: object, what: str) -> None:
    try:
        ok = isinstance(text, str) and Decimal(text).is_finite() and format(Decimal(text), "f") == text
    except InvalidOperation:
        ok = False
    if not ok:
        raise SettlementRecordError(f"{what} is a plain decimal number written the way the settlement writes it")


def _text(value: object, what: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise SettlementRecordError(f"{what} is a non-empty string of at most 200 characters")


def check(s: Settlement) -> None:
    """The settlement is one that ends a slot and agrees with itself, or an error."""
    _text(s.forecast_id, "the forecast id")
    if s.contract not in ("D", "B"):
        raise SettlementRecordError("a settlement is of contract D or B")
    if s.pair_id not in ORACLE_PAIRS:
        raise SettlementRecordError(f"the oracle pair is one of {ORACLE_PAIRS}")
    if s.rule_version != RULE_VERSION:
        raise SettlementRecordError(f"the rule version is {RULE_VERSION}")
    if s.state not in RECORDED:
        raise SettlementRecordError("only a settlement that ends a slot is recorded: settled, ambiguous, void or unsettleable")
    if s.settlement_id != natural_id("st", {"forecast": s.forecast_id, "pair": s.pair_id, "rule": s.rule_version}):
        raise SettlementRecordError("the settlement id is not the id of this forecast, pair and rule version")
    for value, what in ((s.p0, "p0"), (s.outcome_price, "the outcome price")):
        if value is not None:
            _decimal(value, what)
    if s.touch_open_ms is not None and (isinstance(s.touch_open_ms, bool) or not 0 <= s.touch_open_ms <= MAX_INSTANT_MS):
        raise SettlementRecordError("the touch time is a whole number of milliseconds")
    if list(s.flags) != sorted(set(s.flags)) or not set(s.flags) <= FLAGS:
        raise SettlementRecordError(f"flags are some of {sorted(FLAGS)}, sorted and once each")
    if s.state is State.SETTLED:
        _settled(s)
    elif s.state is State.AMBIGUOUS:
        if s.contract != "B" or s.outcome != "AMBIGUOUS" or s.reason != "SAME_CANDLE" or s.p0 is None or s.touch_open_ms is None:
            raise SettlementRecordError("an ambiguous settlement is a barrier forecast whose minute touched both levels")
        if s.outcome_price is not None:
            raise SettlementRecordError("a barrier settlement has no outcome price")
    else:
        if s.outcome is not None or s.touch_open_ms is not None:
            raise SettlementRecordError("a void or unsettleable settlement has no outcome and no touch")
        if s.state is State.VOID and s.reason not in VOID_REASONS:
            raise SettlementRecordError(f"a void settlement has a reason from {sorted(VOID_REASONS)}")
        if s.state is State.VOID and s.reason == "VOID_TIE" and s.contract != "D":  # a barrier forecast has no tie (contract section 3)
            raise SettlementRecordError("only a directional forecast can end in a tie")
        if s.state is State.UNSETTLEABLE and s.reason not in UNSETTLEABLE_REASONS:
            raise SettlementRecordError(f"an unsettleable settlement has a reason from {sorted(UNSETTLEABLE_REASONS)}")
        if s.contract == "B" and s.outcome_price is not None:
            raise SettlementRecordError("a barrier settlement has no outcome price")


def _settled(s: Settlement) -> None:
    if s.reason is not None or s.p0 is None:
        raise SettlementRecordError("a settled forecast has a reference price and no reason")
    if s.contract == "D":
        if s.outcome not in ("0", "1") or s.outcome_price is None or s.touch_open_ms is not None:
            raise SettlementRecordError("a directional outcome is 1 or 0 with the outcome price, and has no touch")
    elif s.outcome not in BARRIER_OUTCOMES or s.outcome_price is not None:
        raise SettlementRecordError(f"a barrier outcome is one of {BARRIER_OUTCOMES}, with no outcome price")
    elif (s.outcome == "TIMEOUT") != (s.touch_open_ms is None):
        raise SettlementRecordError("a barrier forecast that timed out has no touch, and one that reached a level has one")


def to_record(s: Settlement, forecast_digest: str, source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger; it names the forecast record it settles by id and digest."""
    check(s)
    if isinstance(created_at_ms, bool) or not 0 <= created_at_ms <= MAX_INSTANT_MS:
        raise SettlementRecordError("the creation time is a whole number of milliseconds")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": s.settlement_id,
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": {
            "forecast_id": s.forecast_id,
            "contract": s.contract,
            "pair_id": s.pair_id,
            "rule_version": s.rule_version,
            "state": s.state.value,
            "outcome": s.outcome,
            "reason": s.reason,
            "p0": s.p0,
            "outcome_price": s.outcome_price,
            "touch_open_ms": s.touch_open_ms,
            "flags": list(s.flags),
        },
        "refs": [{"id": s.forecast_id, "sha256": forecast_digest}],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise SettlementRecordError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def from_record(record: dict[str, canonical.JsonValue]) -> tuple[Settlement, str]:
    """The settlement and the digest of the forecast record it names, read as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise SettlementRecordError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise SettlementRecordError("not a settlement record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    if set(body) != _MEMBERS:
        raise SettlementRecordError(f"the body has the members {sorted(set(body) ^ _MEMBERS)} wrong")
    forecast_id, contract, pair, version = (
        _string(body, "forecast_id"),
        _string(body, "contract"),
        _string(body, "pair_id"),
        _string(body, "rule_version"),
    )
    refs = cast(list[dict[str, str]], record.get("refs", []))
    if len(refs) != 1 or refs[0]["id"] != forecast_id:
        raise SettlementRecordError("a settlement names exactly the forecast it settles")
    state = body["state"]
    if not isinstance(state, str) or state not in {member.value for member in State}:
        raise SettlementRecordError("the state is not a settlement state")
    flags = body["flags"]
    if not isinstance(flags, list) or not all(isinstance(flag, str) for flag in flags):
        raise SettlementRecordError("flags are a list of strings")
    touch = body["touch_open_ms"]
    if touch is not None and (isinstance(touch, bool) or not isinstance(touch, int)):
        raise SettlementRecordError("the touch time is a whole number or null")
    settlement = Settlement(
        str(record["id"]),
        forecast_id,
        contract,
        pair,
        version,
        State(state),
        _optional(body, "outcome"),
        _optional(body, "reason"),
        _optional(body, "p0"),
        _optional(body, "outcome_price"),
        touch,
        tuple(cast(list[str], flags)),
    )
    check(settlement)
    return settlement, refs[0]["sha256"]


def _string(body: dict[str, canonical.JsonValue], name: str) -> str:
    value = body[name]
    if not isinstance(value, str):
        raise SettlementRecordError(f"{name} is a string")
    return value


def _optional(body: dict[str, canonical.JsonValue], name: str) -> str | None:
    value = body[name]
    if value is not None and not isinstance(value, str):
        raise SettlementRecordError(f"{name} is a string or null")
    return value
