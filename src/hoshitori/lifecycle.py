"""The life of a forecast slot: one state machine, every step a record, no way back, and the denominator that follows from it.

Contract sections 5, 8, 17 and 27 are the rules; ``DOMAIN_MODEL`` section 4 is the shape. A *slot* is one scheduled candidate of one
arm at one horizon of one contract in one cycle (the forecast id of ``forecast``). Its history is the list of transitions it has
made; the history alone says where the slot is, and replaying the records of the ledger rebuilds it exactly. Nothing here reads a
clock: every instant is given.

* **One way forward.** SCHEDULED, CREATED, EVIDENCE_FROZEN, DECIDED, then either COMMITTED or a terminal; after COMMITTED only
  AWAITING_ORIGIN, OPEN, SETTLING and a terminal outcome (SETTLED, AMBIGUOUS, VOID, UNSETTLEABLE). A failure is possible only
  *before* the commitment: a committed forecast is never abandoned after the price is seen, and an attempt to do so is an error.
* **Failures are kept apart from abstentions.** NO_CALL is a voluntary abstention with a reason from a closed list. FAILED,
  FAILED_EVIDENCE, FAILED_LATE, FAILED_STALE and FAILED_LOST are operational and lower availability, never coverage.
* **Idempotent.** Delivering a step twice changes nothing; delivering a different step into a state already entered is a conflict
  and is refused.
* **Times are consistent.** Steps never go back in time; the origin of a slot is the one the clocks give for its eligible time;
  a slot opens no earlier than its origin and settles no earlier than its horizon after it.
* **An end gives a reason that fits it.** A tie and a disagreement of the two oracles are results of the settlement (contract
  sections 2 and 7), so only a settling slot ends with ``VOID_TIE`` or ``UNSETTLEABLE_DISAGREE``, and a barrier forecast, which has no
  tie (section 3), never ends with ``VOID_TIE``. A void for a gap is decided before the origin is set (section 25: the gap between the
  snapshot and the origin is held to the horizon's bound), so a slot that has opened or is settling never ends with ``VOID_GAP``. The
  contract does not say where the other reasons may come from, so each is taken from every state the table lets its end follow.
* **Levels** (C0 to C4) are set when a forecast is committed (or an abstention recorded) and then only change by a record that says
  so: lowering one needs a reason; a committed forecast that has not reached C3 by 90 seconds after its commitment, the latest its
  attestation phase can close, never does, because a token that arrives after the close changes no level (section 2); and a headline
  counts a forecast only if it reached C3 by its origin and still holds it.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.forecast import Level
from hoshitori.ids import natural_id
from hoshitori.settlement import VOID_REASONS
from hoshitori.timeline import TOKEN_BEFORE_MS, TimelineError, attestation_close, eligible_after, from_ms, horizon_ok, origin, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339

TYPE: Final = "forecast_state"
TYPE_VERSION: Final = 1
NO_CALL_REASONS: Final = frozenset({"EVIDENCE_CONFLICT_UNRESOLVED", "ESSENTIAL_PREMISE_UNPROVEN", "BELOW_MINIMUM_EDGE", "OUT_OF_SCOPE"})
UNSETTLEABLE_REASONS: Final = frozenset({"UNSETTLEABLE_MISSING", "UNSETTLEABLE_DISAGREE"})
MAX_REASON: Final = 200


class LifecycleError(ValueError):
    """A step that is not allowed, or a record that is not a step."""


class Conflict(LifecycleError):
    """A different step into a state the slot has already entered."""


class State(StrEnum):
    SCHEDULED = "SCHEDULED"
    CREATED = "CREATED"
    EVIDENCE_FROZEN = "EVIDENCE_FROZEN"
    DECIDED = "DECIDED"
    COMMITTED = "COMMITTED"
    AWAITING_ORIGIN = "AWAITING_ORIGIN"
    OPEN = "OPEN"
    SETTLING = "SETTLING"
    SETTLED = "SETTLED"
    AMBIGUOUS = "AMBIGUOUS"
    VOID = "VOID"
    UNSETTLEABLE = "UNSETTLEABLE"
    NO_CALL = "NO_CALL"
    FAILED = "FAILED"
    FAILED_EVIDENCE = "FAILED_EVIDENCE"
    FAILED_LATE = "FAILED_LATE"
    FAILED_STALE = "FAILED_STALE"
    FAILED_LOST = "FAILED_LOST"
    LEVEL = "LEVEL"  # not a state: a change of commitment level, recorded beside the states


FAILURES: Final = (State.FAILED, State.FAILED_EVIDENCE, State.FAILED_LATE, State.FAILED_STALE, State.FAILED_LOST)
_BEFORE_DECISION = (State.SCHEDULED, State.CREATED, State.EVIDENCE_FROZEN)
_OUTCOMES = (State.SETTLED, State.AMBIGUOUS, State.VOID, State.UNSETTLEABLE)
TERMINAL: Final = frozenset({*_OUTCOMES, State.NO_CALL, *FAILURES})

# where each state can be entered from
ENTRY: Final = {
    State.CREATED: (State.SCHEDULED,),
    State.EVIDENCE_FROZEN: (State.CREATED,),
    State.DECIDED: (State.EVIDENCE_FROZEN,),
    State.COMMITTED: (State.DECIDED,),
    State.NO_CALL: (State.DECIDED,),
    State.FAILED: (*_BEFORE_DECISION, State.DECIDED),
    State.FAILED_EVIDENCE: (*_BEFORE_DECISION, State.DECIDED),
    State.FAILED_LATE: (State.CREATED, State.EVIDENCE_FROZEN),
    State.FAILED_STALE: (State.EVIDENCE_FROZEN, State.DECIDED),
    State.FAILED_LOST: (*_BEFORE_DECISION, State.DECIDED),
    State.AWAITING_ORIGIN: (State.COMMITTED,),
    State.OPEN: (State.AWAITING_ORIGIN,),
    State.SETTLING: (State.OPEN,),
    State.SETTLED: (State.SETTLING,),
    State.AMBIGUOUS: (State.SETTLING,),
    State.VOID: (State.COMMITTED, State.AWAITING_ORIGIN, State.OPEN, State.SETTLING),
    State.UNSETTLEABLE: (State.AWAITING_ORIGIN, State.OPEN, State.SETTLING),
}
# the ends whose reason only a settlement can give, so that they follow SETTLING alone: a tie of the outcome close with the reference
# close (contract section 2) and a disagreement of the two oracles (section 7)
SETTLEMENT_ONLY: Final = frozenset({(State.VOID, "VOID_TIE"), (State.UNSETTLEABLE, "UNSETTLEABLE_DISAGREE")})

# the members a step must and may carry, by state
REQUIRED: Final = {
    State.SCHEDULED: frozenset({"contract", "horizon_ms"}),
    State.COMMITTED: frozenset({"level", "committed_at_ms"}),
    State.NO_CALL: frozenset({"reason", "level"}),
    State.AWAITING_ORIGIN: frozenset({"eligible_after_ms"}),
    State.OPEN: frozenset({"origin_ms"}),
    State.SETTLED: frozenset({"settlement_id"}),
    State.AMBIGUOUS: frozenset({"settlement_id"}),
    State.VOID: frozenset({"reason"}),
    State.UNSETTLEABLE: frozenset({"reason"}),
    State.LEVEL: frozenset({"level"}),
    **{failure: frozenset({"reason"}) for failure in FAILURES},
}
OPTIONAL: Final = {
    # the time-stamps' own numbers, for a C3 slot: the earliest origin follows from them,
    # and a step on disk can be checked against them
    State.AWAITING_ORIGIN: frozenset({"attested_at_ms", "uncertainty_ms", "spread_ms"}),
    State.VOID: frozenset({"settlement_id"}),
    State.UNSETTLEABLE: frozenset({"settlement_id"}),
    State.LEVEL: frozenset({"reason"}),
    State.FAILED_EVIDENCE: frozenset({"ref"}),
}


@dataclass(frozen=True)
class Step:
    """What is asked of a slot: go to ``state`` at ``at_ms`` with these members. Members that do not belong to the state are refused."""

    state: State
    at_ms: int
    contract: str | None = None
    horizon_ms: int | None = None
    reason: str | None = None
    level: Level | None = None
    committed_at_ms: int | None = None
    eligible_after_ms: int | None = None
    attested_at_ms: int | None = None
    uncertainty_ms: int | None = None
    spread_ms: int | None = None
    origin_ms: int | None = None
    settlement_id: str | None = None
    ref: str | None = None

    def members(self) -> dict[str, canonical.JsonValue]:
        raw: dict[str, canonical.JsonValue | None] = {
            "contract": self.contract,
            "horizon_ms": self.horizon_ms,
            "reason": self.reason,
            "level": None if self.level is None else self.level.value,
            "committed_at_ms": self.committed_at_ms,
            "eligible_after_ms": self.eligible_after_ms,
            "attested_at_ms": self.attested_at_ms,
            "uncertainty_ms": self.uncertainty_ms,
            "spread_ms": self.spread_ms,
            "origin_ms": self.origin_ms,
            "settlement_id": self.settlement_id,
            "ref": self.ref,
        }
        return {name: value for name, value in raw.items() if value is not None}


@dataclass(frozen=True)
class Transition:
    """One recorded step of a slot: its place in the slot's history and what it carried."""

    forecast_id: str
    seq: int
    step: Step

    @property
    def transition_id(self) -> str:
        return natural_id("ft", {"forecast": self.forecast_id, "seq": self.seq})


History = tuple[Transition, ...]


def _check_instant(value: int, what: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LifecycleError(f"{what} is a whole number of milliseconds")


def _text(value: object, what: str) -> None:
    """Text that can be written into a record: a non-empty string of at most 200 characters in the canonical profile."""
    if not isinstance(value, str) or not value or len(value) > MAX_REASON:
        raise LifecycleError(f"{what} is a non-empty string")
    try:
        canonical.dumps({"v": value})
    except canonical.CanonicalError as exc:
        raise LifecycleError(f"{what} is not valid canonical text: {exc}") from exc


def _check_members(step: Step) -> None:
    if step.level is not None and not isinstance(step.level, Level):
        raise LifecycleError("a level is C0 to C4")
    present = set(step.members())
    required = REQUIRED.get(step.state, frozenset())
    allowed = required | OPTIONAL.get(step.state, frozenset())
    if present - allowed or required - present:
        raise LifecycleError(
            f"{step.state.value} carries {sorted(required)} and may carry {sorted(allowed - required)}, not {sorted(present)}"
        )
    _check_instant(step.at_ms, "the time of a step")
    if step.reason is not None:
        _text(step.reason, "a reason")
    for name in ("settlement_id", "ref"):
        value = getattr(step, name)
        if value is not None:
            _text(value, name)
    for name in ("committed_at_ms", "eligible_after_ms", "attested_at_ms", "origin_ms"):
        value = getattr(step, name)
        if value is not None:
            _check_instant(value, name)
    for name in ("uncertainty_ms", "spread_ms"):
        value = getattr(step, name)
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise LifecycleError(f"{name} is a whole number of milliseconds, zero or more")


NAMES_ATTESTED: Final = "a C3 slot names its attested time, uncertainty and spread when it waits for the origin"


def _check_attested_origin(history: History, step: Step, committed: int, *, legacy: bool) -> None:
    """At C3 the earliest origin is the attested time plus the uncertainty and the spread, and the step says them, so that what is
    written can be held to them; below C3 no token counts and the slot waits from its commit time. A step written before C3 slots named
    them is read as it is (``legacy``)."""
    named = (step.attested_at_ms, step.uncertainty_ms, step.spread_ms)
    c3 = level_at(history, step.at_ms) is Level.C3
    if not c3:
        if any(x is not None for x in named):
            raise LifecycleError("only a C3 slot carries an attested time")
        return
    if all(x is None for x in named) and legacy:
        return
    if any(x is None for x in named):
        raise LifecycleError(NAMES_ATTESTED)
    attested, uncertainty, spread = cast(tuple[int, int, int], named)
    if attested < committed - TOKEN_BEFORE_MS or attested > attestation_close(committed):
        raise LifecycleError(
            "an attestation lies within the phase of its commitment, "
            "from the token window before it to the close of the attestation after it"
        )
    if _need(step.eligible_after_ms) < eligible_after(attested, uncertainty, spread):
        raise LifecycleError("a slot cannot become eligible before its attestation plus the uncertainty and the spread")


def _state(history: History) -> State:
    return next(t.step.state for t in reversed(history) if t.step.state is not State.LEVEL)


def _entered(history: History, state: State) -> Transition | None:
    return next((t for t in history if t.step.state is state), None)


def current_state(history: History) -> State:
    if not history:
        raise LifecycleError("a slot has no history yet")
    return _state(history)


def begin(forecast_id: str, contract: str, horizon_ms: int, at_ms: int) -> History:
    """A slot enters the cycle's population: the first step of every history, and the slot's contract and horizon for good."""
    if contract not in ("D", "B"):
        raise LifecycleError("a slot is contract D or B")
    try:
        horizon_ok(horizon_ms)
    except TimelineError as exc:
        raise LifecycleError(str(exc)) from exc
    _text(forecast_id, "a slot id")
    step = Step(State.SCHEDULED, at_ms, contract=contract, horizon_ms=horizon_ms)
    _check_members(step)
    return (Transition(forecast_id, 1, step),)


def _horizon(history: History) -> int:
    return _need(history[0].step.horizon_ms)


def _level_now(history: History) -> Level | None:
    return next((t.step.level for t in reversed(history) if t.step.level is not None), None)


def advance(history: History, step: Step, *, legacy: bool = False) -> History:
    """The history after ``step``, or the same history if the step was already taken, or an error.

    ``legacy`` is for reading what was written before a C3 slot named its attested time: such a step is read, never written
    (``rebuild``)."""
    if not history:
        raise LifecycleError("a slot begins with begin()")
    _check_members(step)
    if step.state is State.SCHEDULED:
        raise LifecycleError("a slot is scheduled once")
    previous = history[-1].step
    existing = next((t for t in history if t.step == step), None)
    if existing is not None:
        return history  # delivered before: nothing changes
    if step.state is State.LEVEL:
        return _change_level(history, step)
    taken = _entered(history, step.state)
    if taken is not None:
        raise Conflict(f"{step.state.value} was entered with other members at step {taken.seq}")
    current = _state(history)
    if current not in ENTRY.get(step.state, ()):
        raise LifecycleError(f"{step.state.value} cannot follow {current.value}")
    if step.at_ms < previous.at_ms:
        raise LifecycleError("a step is not earlier than the one before it")
    _check_state_rules(history, step, legacy=legacy)
    return (*history, Transition(history[0].forecast_id, len(history) + 1, step))


def _check_state_rules(history: History, step: Step, *, legacy: bool) -> None:
    state = step.state
    if state is State.COMMITTED and _need(step.committed_at_ms) > step.at_ms:
        raise LifecycleError("a forecast is committed no later than the step that records it")
    if state is State.COMMITTED:
        decided = _need(_entered(history, State.DECIDED))  # a commitment follows a decision, and its time is no earlier than the decision's
        if _need(step.committed_at_ms) < decided.step.at_ms:
            raise LifecycleError("a forecast is committed no earlier than it was decided")
    if state is State.NO_CALL and step.reason not in NO_CALL_REASONS:
        raise LifecycleError(f"a voluntary no-call has a reason from {sorted(NO_CALL_REASONS)}")
    if state is State.VOID and step.reason not in VOID_REASONS:
        raise LifecycleError(f"a void has a reason from {sorted(VOID_REASONS)}")
    if state is State.UNSETTLEABLE and step.reason not in UNSETTLEABLE_REASONS:
        raise LifecycleError(f"an unsettleable forecast has a reason from {sorted(UNSETTLEABLE_REASONS)}")
    if (state, step.reason) == (State.VOID, "VOID_TIE") and history[0].step.contract != "D":
        raise LifecycleError("only a directional forecast can end in a tie")
    if (state, step.reason) in SETTLEMENT_ONLY and _state(history) is not State.SETTLING:
        raise LifecycleError(f"{step.reason} is a result of the settlement, so only a settling slot can end with it")
    if (state, step.reason) == (State.VOID, "VOID_GAP") and _state(history) in (State.OPEN, State.SETTLING):
        raise LifecycleError("VOID_GAP is decided before the origin is set, so a slot that has opened cannot end with it")
    if state is State.AMBIGUOUS and history[0].step.contract != "B":
        raise LifecycleError("only a barrier forecast can end ambiguous")
    if state is State.AWAITING_ORIGIN:
        committed = _need(_need(_entered(history, State.COMMITTED)).step.committed_at_ms)
        if _need(step.eligible_after_ms) < committed - TOKEN_BEFORE_MS:
            raise LifecycleError("a slot cannot become eligible before its commitment less the token window")
        _check_attested_origin(history, step, committed, legacy=legacy)
    if state is State.OPEN:
        eligible = _need(_need(_entered(history, State.AWAITING_ORIGIN)).step.eligible_after_ms)
        origin_ms = _need(step.origin_ms)
        if origin_ms != origin(eligible):
            raise LifecycleError("the origin is the close of the first candle that closes after the eligible time")
        if step.at_ms < origin_ms:
            raise LifecycleError("a slot opens no earlier than its origin")
    if state is State.SETTLING:
        opened = _need(_need(_entered(history, State.OPEN)).step.origin_ms)
        if step.at_ms < opened + _horizon(history):
            raise LifecycleError("a slot settles no earlier than its horizon after its origin")


def _need[T](value: T | None) -> T:
    if value is None:
        raise LifecycleError("a member the history needs is missing")
    return value


# --- levels -----------------------------------------------------------------------------------------------------------------------------
def _change_level(history: History, step: Step) -> History:
    now = _level_now(history)
    if now is None:
        raise LifecycleError("a level changes only after the slot has been committed or recorded as a no-call")
    level = _need(step.level)
    if step.at_ms < history[-1].step.at_ms:
        raise LifecycleError("a step is not earlier than the one before it")
    if level == now:
        raise LifecycleError("the level is already that level")
    if level.rank < now.rank and step.reason is None:
        raise LifecycleError("lowering a level needs a reason: a downgrade is a record that says why")
    committed = _entered(history, State.COMMITTED)
    if (
        committed is not None
        and level.independent
        and step.at_ms > attestation_close(_need(committed.step.committed_at_ms))
        and not any(t.step.level is not None and t.step.level.independent for t in history)
    ):
        raise LifecycleError("a forecast reaches C3 by the close of its attestation phase or not at all: a later token changes no level")
    return (*history, Transition(history[0].forecast_id, len(history) + 1, step))


def current_level(history: History) -> Level | None:
    return _level_now(history)


def level_at(history: History, at_ms: int) -> Level | None:
    """The level the slot had reached by ``at_ms`` (inclusive), or ``None`` if it had none yet."""
    return next((t.step.level for t in reversed(history) if t.step.level is not None and t.step.at_ms <= at_ms), None)


def headline_eligible(history: History) -> bool:
    """A forecast counts towards a headline if it reached C3 by its origin and still holds a level of C3 or more."""
    opened = _entered(history, State.OPEN)
    if opened is None or _entered(history, State.COMMITTED) is None:
        return False
    at_origin, now = level_at(history, _need(opened.step.origin_ms)), _level_now(history)
    return at_origin is not None and at_origin.independent and now is not None and now.independent


# --- records ----------------------------------------------------------------------------------------------------------------------------
_TIMES: Final = {
    "committed_at_ms": "committed_at",
    "eligible_after_ms": "eligible_after",
    "attested_at_ms": "attested_at",
    "origin_ms": "origin",
}


def _stamp(ms: int) -> str:
    return format_rfc3339_ms(from_ms(ms))


def to_record(transition: Transition, source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger for one step."""
    _check_instant(created_at_ms, "the creation time")
    body: dict[str, canonical.JsonValue] = {
        "forecast_id": transition.forecast_id,
        "seq": transition.seq,
        "state": transition.step.state.value,
        "at": _stamp(transition.step.at_ms),
    }
    for name, value in transition.step.members().items():
        body[_TIMES.get(name, name)] = _stamp(value) if name in _TIMES and isinstance(value, int) else value
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": transition.transition_id,
        "source": source,
        "created_at": _stamp(created_at_ms),
        "body": body,
        "refs": [],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise LifecycleError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def _read_instant(text: canonical.JsonValue, what: str) -> int:
    if not isinstance(text, str):
        raise LifecycleError(f"{what} is an RFC 3339 string")
    try:
        milliseconds = to_ms(parse_rfc3339(text))
    except (TimeError, TimelineError) as exc:
        raise LifecycleError(f"{what}: {exc}") from exc
    if _stamp(milliseconds) != text:
        raise LifecycleError(f"{what} is written as UTC with milliseconds")
    return milliseconds


def from_record(record: dict[str, canonical.JsonValue]) -> Transition:
    """Read one step back, as strictly as it was written."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise LifecycleError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise LifecycleError("not a forecast-state record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])  # the envelope check above guarantees an object
    forecast_id, seq, name = body.get("forecast_id"), body.get("seq"), body.get("state")
    if not isinstance(forecast_id, str) or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise LifecycleError("a step has a slot id and a position of one or more")
    if not isinstance(name, str) or name not in State.__members__:
        raise LifecycleError("a step has a known state")
    state = State(name)
    allowed = REQUIRED.get(state, frozenset()) | OPTIONAL.get(state, frozenset())
    extra = set(body) - {"forecast_id", "seq", "state", "at"} - {_TIMES.get(member, member) for member in allowed}
    if extra:
        raise LifecycleError(f"{state.value} does not carry {sorted(extra)}")
    level = body.get("level")
    if level is not None and (not isinstance(level, str) or level not in Level.__members__):
        raise LifecycleError("a level is C0 to C4")
    horizon = body.get("horizon_ms")
    if horizon is not None and (isinstance(horizon, bool) or not isinstance(horizon, int)):
        raise LifecycleError("horizon_ms is a whole number")
    step = Step(
        state,
        _read_instant(body.get("at"), "at"),
        contract=_optional_text(body, "contract"),
        horizon_ms=horizon,
        reason=_optional_text(body, "reason"),
        level=None if level is None else Level(level),
        committed_at_ms=_optional_instant(body, "committed_at"),
        eligible_after_ms=_optional_instant(body, "eligible_after"),
        attested_at_ms=_optional_instant(body, "attested_at"),
        uncertainty_ms=_optional_count(body, "uncertainty_ms"),
        spread_ms=_optional_count(body, "spread_ms"),
        origin_ms=_optional_instant(body, "origin"),
        settlement_id=_optional_text(body, "settlement_id"),
        ref=_optional_text(body, "ref"),
    )
    _check_members(step)
    transition = Transition(forecast_id, seq, step)
    if record["id"] != transition.transition_id:
        raise LifecycleError("the id is not the id of this step")
    return transition


def _optional_text(body: dict[str, canonical.JsonValue], name: str) -> str | None:
    value = body.get(name)
    if value is not None and not isinstance(value, str):
        raise LifecycleError(f"{name} is text")
    return value


def _optional_count(body: dict[str, canonical.JsonValue], name: str) -> int | None:
    value = body.get(name)
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise LifecycleError(f"{name} is a whole number")
    return value


def _optional_instant(body: dict[str, canonical.JsonValue], name: str) -> int | None:
    return None if name not in body else _read_instant(body[name], name)


def rebuild(records: Iterable[dict[str, canonical.JsonValue]]) -> dict[str, History]:
    """The histories of every slot from its records in ledger order. A step delivered twice counts once; a gap, a reorder or a step
    the rules would have refused is an error, so a history on disk cannot say what the rules would not."""
    histories: dict[str, History] = {}
    for record in records:
        transition = from_record(record)
        history = histories.get(transition.forecast_id, ())
        if any(t == transition for t in history):
            continue
        if transition.seq != len(history) + 1:
            raise LifecycleError(f"step {transition.seq} of {transition.forecast_id} arrived when {len(history)} were known")
        if not history:
            step = transition.step
            if step.state is not State.SCHEDULED:
                raise LifecycleError("a slot's first step is SCHEDULED")
            histories[transition.forecast_id] = begin(transition.forecast_id, _need(step.contract), _need(step.horizon_ms), step.at_ms)
            continue
        advanced = advance(history, transition.step, legacy=True)
        if advanced == history:
            raise LifecycleError("a step repeated under another position")
        histories[transition.forecast_id] = advanced
    return histories


# --- the denominator --------------------------------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Denominator:
    """Every scheduled slot, counted once, by what became of it. The sum of the parts is the whole."""

    scheduled: int
    in_flight: int  # not yet decided, nothing recorded
    pending: int  # committed, not yet an outcome
    settled: int
    ambiguous: int
    void: int
    unsettleable: int
    no_calls: int
    failed: int
    failed_evidence: int
    failed_late: int
    failed_stale: int
    failed_lost: int

    def __post_init__(self) -> None:
        parts = (self.in_flight, self.forecasts, self.no_calls, self.failures)
        if self.scheduled != sum(parts):
            raise LifecycleError("the parts of the denominator do not add up to the slots scheduled")

    @property
    def forecasts(self) -> int:
        """Calls made: every slot that reached its commitment, whatever came after."""
        return self.pending + self.settled + self.ambiguous + self.void + self.unsettleable

    @property
    def failures(self) -> int:
        return self.failed + self.failed_evidence + self.failed_late + self.failed_stale + self.failed_lost

    def coverage_inputs(self) -> tuple[int, int, int]:
        """``(calls, voluntary no-calls, failures)`` for ``scoring.coverage``."""
        return (self.forecasts, self.no_calls, self.failures)


def denominator(histories: Iterable[History]) -> Denominator:
    counted: Counter[str] = Counter()
    seen: set[str] = set()
    total = 0
    for history in histories:
        slot = history[0].forecast_id
        if slot in seen:
            raise LifecycleError("a slot is counted once")
        seen.add(slot)
        total += 1
        state = current_state(history)
        if state in _BEFORE_DECISION or state is State.DECIDED:
            counted["in_flight"] += 1
        elif state is State.NO_CALL:
            counted["no_calls"] += 1
        elif state in FAILURES or state in _OUTCOMES:
            counted[state.value.lower()] += 1
        else:
            counted["pending"] += 1
    return Denominator(
        scheduled=total,
        in_flight=counted["in_flight"],
        pending=counted["pending"],
        settled=counted["settled"],
        ambiguous=counted["ambiguous"],
        void=counted["void"],
        unsettleable=counted["unsettleable"],
        no_calls=counted["no_calls"],
        failed=counted["failed"],
        failed_evidence=counted["failed_evidence"],
        failed_late=counted["failed_late"],
        failed_stale=counted["failed_stale"],
        failed_lost=counted["failed_lost"],
    )
