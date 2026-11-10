"""The life of a cycle: eight states and a ninth that ends it early, every step a record, and what a restart may do.

Contract sections 6 and 25 and ``DISCOVERY_MANIFEST_SPEC`` section 1 and 7 are the rules.

    PLANNED -> DISCOVERING -> SEALED -> COLLECTING -> SNAPSHOT_FROZEN -> RUNNING -> COMMITTING -> COMMITTED
    any state before COMMITTED -> ABANDONED(reason)

* **One way forward, and an end.** A cycle that is abandoned stays abandoned: nothing of its snapshot or its partial outputs
  is committed later, and a new cycle takes a new snapshot. A committed cycle is final.
* **The order of its times is part of the rules.** The manifest is sealed no earlier than the discovery, the snapshot is
  taken no earlier than the sealing and no more than ten minutes after the discovery; a snapshot later than that is not
  frozen but the cycle abandoned.
* **A restart reads the record.** A cycle that is not over when its process is lost is closed as abandoned, whatever state it
  reached: nothing of its snapshot or its partial outputs is committed later (contract section 25, item 9), so no cycle is resumed.
  ``recovery.close_lost`` does the closing.
* **The heartbeat is computed, not trusted.** From the schedule and the cycle records alone, the cycles that never started,
  never finished, finished late or were abandoned are listed.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.ids import natural_id
from hoshitori.timeline import DISCOVERY_TO_SNAPSHOT_MS, TimelineError, cycle_order_ok, from_ms, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339

TYPE: Final = "cycle_state"
TYPE_VERSION: Final = 1
MAX_INSTANT_MS: Final = 253_402_300_799_999
ABANDON_REASONS: Final = frozenset({"SNAPSHOT_TOO_OLD", "DISCOVERY_SNAPSHOT_GAP", "PROCESS_LOST", "CLOCK_OFFSET"})


class CycleError(ValueError):
    """A step that is not allowed, or a record that is not a step."""


class Conflict(CycleError):
    """A different step into a state the cycle has already entered."""


class State(StrEnum):
    PLANNED = "PLANNED"
    DISCOVERING = "DISCOVERING"
    SEALED = "SEALED"
    COLLECTING = "COLLECTING"
    SNAPSHOT_FROZEN = "SNAPSHOT_FROZEN"
    RUNNING = "RUNNING"
    COMMITTING = "COMMITTING"
    COMMITTED = "COMMITTED"
    ABANDONED = "ABANDONED"


ORDER: Final = (
    State.PLANNED,
    State.DISCOVERING,
    State.SEALED,
    State.COLLECTING,
    State.SNAPSHOT_FROZEN,
    State.RUNNING,
    State.COMMITTING,
    State.COMMITTED,
)
TERMINAL: Final = frozenset({State.COMMITTED, State.ABANDONED})
TERMINAL_WORDS: Final = frozenset(state.value for state in TERMINAL)
REQUIRED: Final = {
    State.PLANNED: frozenset({"planned_for_ms", "protocol_hash"}),
    State.DISCOVERING: frozenset({"discovery_at_ms"}),
    State.SEALED: frozenset({"sealed_at_ms", "manifest_hash"}),
    State.COLLECTING: frozenset(),
    State.SNAPSHOT_FROZEN: frozenset({"snapshot_at_ms", "snapshot_hash"}),
    State.RUNNING: frozenset(),
    State.COMMITTING: frozenset({"batch_root"}),
    State.COMMITTED: frozenset({"commitment_id", "committed_at_ms"}),
    State.ABANDONED: frozenset({"reason"}),
}
OPTIONAL: Final = {State.COMMITTING: frozenset({"batch_size"})}  # a step of an older cycle has none
MAX_BATCH: Final = 5000  # the most records of a batch (``commit.MAX_RECORDS``)
_TIMES: Final = {
    "planned_for_ms": "planned_for",
    "discovery_at_ms": "discovery_at",
    "sealed_at_ms": "sealed_at",
    "snapshot_at_ms": "snapshot_at",
    "committed_at_ms": "committed_at",
}
_HASHES: Final = ("protocol_hash", "manifest_hash", "snapshot_hash", "batch_root")


@dataclass(frozen=True)
class Step:
    state: State
    at_ms: int
    planned_for_ms: int | None = None
    protocol_hash: str | None = None
    discovery_at_ms: int | None = None
    sealed_at_ms: int | None = None
    manifest_hash: str | None = None
    snapshot_at_ms: int | None = None
    snapshot_hash: str | None = None
    batch_root: str | None = None
    batch_size: int | None = None
    commitment_id: str | None = None
    committed_at_ms: int | None = None
    reason: str | None = None

    def members(self) -> dict[str, canonical.JsonValue]:
        raw: dict[str, canonical.JsonValue | None] = {
            "planned_for_ms": self.planned_for_ms,
            "protocol_hash": self.protocol_hash,
            "discovery_at_ms": self.discovery_at_ms,
            "sealed_at_ms": self.sealed_at_ms,
            "manifest_hash": self.manifest_hash,
            "snapshot_at_ms": self.snapshot_at_ms,
            "snapshot_hash": self.snapshot_hash,
            "batch_root": self.batch_root,
            "batch_size": self.batch_size,
            "commitment_id": self.commitment_id,
            "committed_at_ms": self.committed_at_ms,
            "reason": self.reason,
        }
        return {name: value for name, value in raw.items() if value is not None}


@dataclass(frozen=True)
class Transition:
    cycle_id: str
    seq: int
    step: Step

    @property
    def transition_id(self) -> str:
        return natural_id("cs", {"cycle": self.cycle_id, "seq": self.seq})


History = tuple[Transition, ...]


def cycle_id(protocol_hash: str, planned_for_ms: int) -> str:
    """The id of a cycle: the protocol it runs under and the schedule slot it was planned for."""
    return natural_id("cy", {"protocol": protocol_hash, "planned_for_ms": planned_for_ms})


def _instant(value: object, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
        raise CycleError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
    return value


def _check_members(step: Step) -> None:
    present, required = set(step.members()), REQUIRED[step.state]
    if not required <= present <= required | OPTIONAL.get(step.state, frozenset()):
        raise CycleError(f"{step.state.value} carries {sorted(required)}, not {sorted(present)}")
    if step.batch_size is not None and (
        isinstance(step.batch_size, bool) or not isinstance(step.batch_size, int) or not 1 <= step.batch_size <= MAX_BATCH
    ):
        raise CycleError(f"batch_size is a whole number from 1 to {MAX_BATCH}")
    _instant(step.at_ms, "the time of a step")
    for name in _TIMES:
        value = getattr(step, name)
        if value is not None:
            _instant(value, name)
    for name in _HASHES:
        value = getattr(step, name)
        if value is not None and (not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value)):
            raise CycleError(f"{name} is 64 lower-case hexadecimal characters")
    for name in ("commitment_id", "reason"):
        value = getattr(step, name)
        if value is not None:
            try:
                canonical.dumps({"v": value})
            except canonical.CanonicalError as exc:
                raise CycleError(f"{name} is not valid canonical text: {exc}") from exc
            if not isinstance(value, str) or not value or len(value) > 200:
                raise CycleError(f"{name} is a non-empty string of at most 200 characters")


def current_state(history: History) -> State:
    if not history:
        raise CycleError("a cycle has no history yet")
    return history[-1].step.state


def begin(protocol_hash: str, planned_for_ms: int, at_ms: int) -> History:
    """A cycle is planned for a slot of the schedule: the first step of every history."""
    step = Step(State.PLANNED, at_ms, planned_for_ms=planned_for_ms, protocol_hash=protocol_hash)
    _check_members(step)
    return (Transition(cycle_id(protocol_hash, planned_for_ms), 1, step),)


def _entered(history: History, state: State) -> Step | None:
    return next((t.step for t in history if t.step.state is state), None)


def _need[T](value: T | None) -> T:
    """A member that ``_check_members`` has already shown to be there for this state."""
    return cast(T, value)


def advance(history: History, step: Step) -> History:
    """The history after ``step``, or the same history if the step was already taken, or an error."""
    if not history:
        raise CycleError("a cycle begins with begin()")
    _check_members(step)
    if step.state is State.PLANNED:
        raise CycleError("a cycle is planned once")
    if any(t.step == step for t in history):
        return history
    taken = _entered(history, step.state)
    if taken is not None:
        raise Conflict(f"{step.state.value} was entered with other members")
    current = current_state(history)
    if current in TERMINAL:
        raise CycleError(f"{step.state.value} cannot follow {current.value}: the cycle is over")
    if step.state is not State.ABANDONED and ORDER.index(step.state) != ORDER.index(current) + 1:
        raise CycleError(f"{step.state.value} cannot follow {current.value}")
    if step.at_ms < history[-1].step.at_ms:
        raise CycleError("a step is not earlier than the one before it")
    _check_rules(history, step)
    return (*history, Transition(history[0].cycle_id, len(history) + 1, step))


def _check_rules(history: History, step: Step) -> None:
    state = step.state
    planned = _need(history[0].step.planned_for_ms)
    if state is State.DISCOVERING:
        if _need(step.discovery_at_ms) > step.at_ms or step.at_ms < planned:
            raise CycleError("discovery starts no earlier than the slot it was planned for, and is recorded no earlier than it started")
    elif state is State.SEALED:
        sealed = _need(step.sealed_at_ms)
        if sealed > step.at_ms or sealed < _need(_need(_entered(history, State.DISCOVERING)).discovery_at_ms):
            raise CycleError("the manifest is sealed no earlier than the discovery, and recorded no earlier than it was sealed")
    elif state is State.SNAPSHOT_FROZEN:
        discovery = _need(_need(_entered(history, State.DISCOVERING)).discovery_at_ms)
        sealed = _need(_need(_entered(history, State.SEALED)).sealed_at_ms)
        snapshot = _need(step.snapshot_at_ms)
        if snapshot > step.at_ms:
            raise CycleError("a snapshot is recorded no earlier than it was taken")
        if not cycle_order_ok(discovery, sealed, snapshot):
            minutes = DISCOVERY_TO_SNAPSHOT_MS // 60_000
            raise CycleError(f"the snapshot is taken no earlier than the sealing and within {minutes} minutes of the discovery")
    elif state is State.COMMITTED:
        committed = _need(step.committed_at_ms)
        if committed > step.at_ms or committed < _need(_need(_entered(history, State.SNAPSHOT_FROZEN)).snapshot_at_ms):
            raise CycleError("a cycle is committed no earlier than its snapshot, and recorded no earlier than it was committed")
    elif state is State.ABANDONED and step.reason not in ABANDON_REASONS:
        raise CycleError(f"a cycle is abandoned for a reason from {sorted(ABANDON_REASONS)}")


def abandon_step(reason: str, at_ms: int) -> Step:
    return Step(State.ABANDONED, at_ms, reason=reason)


def snapshot_expired(history: History, now_ms: int, limit_ms: int) -> bool:
    """Whether the frozen snapshot is older than the limit at ``now_ms``: if so the cycle must be abandoned and never committed."""
    taken = _entered(history, State.SNAPSHOT_FROZEN)
    if taken is None or current_state(history) in TERMINAL:
        return False
    return _instant(now_ms, "now") - _need(taken.snapshot_at_ms) > _instant(limit_ms, "the limit")


# --- records ----------------------------------------------------------------------------------------------------------------------------
def _stamp(ms: int) -> str:
    return format_rfc3339_ms(from_ms(ms))


def to_record(transition: Transition, source_urn: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    _instant(created_at_ms, "the creation time")
    body: dict[str, canonical.JsonValue] = {
        "cycle_id": transition.cycle_id,
        "seq": transition.seq,
        "state": transition.step.state.value,
        "at": _stamp(transition.step.at_ms),
    }
    for name, value in transition.step.members().items():
        body[_TIMES.get(name, name)] = _stamp(cast(int, value)) if name in _TIMES else value
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": transition.transition_id,
        "source": source_urn,
        "created_at": _stamp(created_at_ms),
        "body": body,
        "refs": [],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise CycleError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


def _read_instant(value: canonical.JsonValue, what: str) -> int:
    if not isinstance(value, str):
        raise CycleError(f"{what} is an RFC 3339 string")
    try:
        ms = to_ms(parse_rfc3339(value))
    except (TimeError, TimelineError) as exc:
        raise CycleError(f"{what}: {exc}") from exc
    if _stamp(ms) != value:
        raise CycleError(f"{what} is written as UTC with milliseconds")
    return ms


def _optional_instant(body: dict[str, canonical.JsonValue], name: str) -> int | None:
    return _read_instant(body[name], name) if name in body else None


def _optional_text(body: dict[str, canonical.JsonValue], name: str) -> str | None:
    value = body.get(name)
    if value is not None and not isinstance(value, str):
        raise CycleError(f"{name} is text")
    return value


def from_record(record: dict[str, canonical.JsonValue]) -> Transition:
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise CycleError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise CycleError("not a cycle-state record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])
    cycle, seq, name = body.get("cycle_id"), body.get("seq"), body.get("state")
    if not isinstance(cycle, str) or isinstance(seq, bool) or not isinstance(seq, int) or seq < 1:
        raise CycleError("a step has a cycle id and a position of one or more")
    if not isinstance(name, str) or name not in State.__members__:
        raise CycleError("a step has a known state")
    state = State(name)
    allowed = {"cycle_id", "seq", "state", "at"} | {_TIMES.get(member, member) for member in REQUIRED[state]}
    optional = {_TIMES.get(member, member) for member in OPTIONAL.get(state, frozenset())}
    if not allowed <= set(body) <= allowed | optional:
        raise CycleError(f"{state.value} has the members {sorted((set(body) ^ allowed) - optional)} wrong")
    step = Step(
        state,
        _read_instant(body["at"], "at"),
        planned_for_ms=_optional_instant(body, "planned_for"),
        protocol_hash=_optional_text(body, "protocol_hash"),
        discovery_at_ms=_optional_instant(body, "discovery_at"),
        sealed_at_ms=_optional_instant(body, "sealed_at"),
        manifest_hash=_optional_text(body, "manifest_hash"),
        snapshot_at_ms=_optional_instant(body, "snapshot_at"),
        snapshot_hash=_optional_text(body, "snapshot_hash"),
        batch_root=_optional_text(body, "batch_root"),
        batch_size=cast(int | None, body.get("batch_size")),
        commitment_id=_optional_text(body, "commitment_id"),
        committed_at_ms=_optional_instant(body, "committed_at"),
        reason=_optional_text(body, "reason"),
    )
    _check_members(step)
    transition = Transition(cycle, seq, step)
    if record["id"] != transition.transition_id:
        raise CycleError("the id is not the id of this step")
    return transition


def rebuild(records: Iterable[dict[str, canonical.JsonValue]]) -> dict[str, History]:
    """The histories of every cycle from its records in ledger order; a gap, a reorder or a step the rules would refuse is an error."""
    histories: dict[str, History] = {}
    for record in records:
        transition = from_record(record)
        history = histories.get(transition.cycle_id, ())
        if any(t == transition for t in history):
            continue
        if transition.seq != len(history) + 1:
            raise CycleError(f"step {transition.seq} of {transition.cycle_id} arrived when {len(history)} were known")
        if not history:
            step = transition.step
            if step.state is not State.PLANNED:
                raise CycleError("a cycle's first step is PLANNED")
            began = begin(_need(step.protocol_hash), _need(step.planned_for_ms), step.at_ms)
            if began[0].cycle_id != transition.cycle_id:
                raise CycleError("the cycle id is not the id of this protocol and slot")
            histories[transition.cycle_id] = began
            continue
        advanced = advance(history, transition.step)
        if advanced == history:
            raise CycleError("a step repeated under another position")
        histories[transition.cycle_id] = advanced
    return histories


# --- the heartbeat ----------------------------------------------------------------------------------------------------------------------
class Finding(StrEnum):
    MISSING = "MISSING"  # the slot passed and no cycle began
    UNFINISHED = "UNFINISHED"  # begun, not over, and past the time it should have finished by
    LATE = "LATE"  # committed, but after the time it should have finished by
    ABANDONED = "ABANDONED"


@dataclass(frozen=True)
class Beat:
    planned_for_ms: int
    finding: Finding
    cycle_id: str | None


def _cycle_of(history: History) -> str:
    return history[0].cycle_id


def heartbeat(
    schedule: Sequence[int], histories: Iterable[History], *, now_ms: int, start_grace_ms: int, finish_within_ms: int
) -> tuple[Beat, ...]:
    """The slots of the schedule whose cycle did not start, did not finish, finished late or was abandoned, from the records alone."""
    by_slot = {_need(h[0].step.planned_for_ms): h for h in histories}
    _instant(now_ms, "now")
    _instant(start_grace_ms, "the start grace")
    _instant(finish_within_ms, "the finish limit")
    found: list[Beat] = []
    for slot in sorted(set(schedule)):
        _instant(slot, "a scheduled time")
        history = by_slot.get(slot)
        if history is None:
            if now_ms > slot + start_grace_ms:
                found.append(Beat(slot, Finding.MISSING, None))
            continue
        state = current_state(history)
        if state is State.ABANDONED:
            found.append(Beat(slot, Finding.ABANDONED, _cycle_of(history)))
        elif state is State.COMMITTED:
            if _need(history[-1].step.committed_at_ms) > slot + finish_within_ms:
                found.append(Beat(slot, Finding.LATE, _cycle_of(history)))
        elif now_ms > slot + finish_within_ms:
            found.append(Beat(slot, Finding.UNFINISHED, _cycle_of(history)))
    return tuple(found)
