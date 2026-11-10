"""Checks of the cycle state machine (``hoshitori.cycle``), runnable against a mutant."""

from __future__ import annotations

import copy
import types
from collections.abc import Callable
from typing import Any

from hoshitori import canonical
from hoshitori.ids import natural_id

T = 1_793_311_000_000
PH = "a" * 64
MH, SH, BR = "b" * 64, "c" * 64, "d" * 64
SRC = "urn:hoshitori:scheduler:test"
_GONE = object()

ALLOWED_FROM: dict[str, set[str]] = {
    "PLANNED": {"DISCOVERING", "ABANDONED"},
    "DISCOVERING": {"SEALED", "ABANDONED"},
    "SEALED": {"COLLECTING", "ABANDONED"},
    "COLLECTING": {"SNAPSHOT_FROZEN", "ABANDONED"},
    "SNAPSHOT_FROZEN": {"RUNNING", "ABANDONED"},
    "RUNNING": {"COMMITTING", "ABANDONED"},
    "COMMITTING": {"COMMITTED", "ABANDONED"},
}
FULL = ["DISCOVERING", "SEALED", "COLLECTING", "SNAPSHOT_FROZEN", "RUNNING", "COMMITTING", "COMMITTED"]


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str | None = None, kind: type[Exception] | None = None) -> None:
        expected = kind or self.mod.CycleError
        try:
            call()
        except expected as exc:
            if kind is None and type(exc) is not self.mod.CycleError:
                self.bad.append(f"{label}: raised {type(exc).__name__}, expected a plain CycleError")
            elif says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def steps(mod: types.ModuleType) -> dict[str, Any]:
    S, Step = mod.State, mod.Step
    return {
        "DISCOVERING": Step(S.DISCOVERING, T + 1_000, discovery_at_ms=T + 500),
        "SEALED": Step(S.SEALED, T + 3_100, sealed_at_ms=T + 3_000, manifest_hash=MH),
        "COLLECTING": Step(S.COLLECTING, T + 3_200),
        "SNAPSHOT_FROZEN": Step(S.SNAPSHOT_FROZEN, T + 60_100, snapshot_at_ms=T + 60_000, snapshot_hash=SH),
        "RUNNING": Step(S.RUNNING, T + 60_200),
        "COMMITTING": Step(S.COMMITTING, T + 90_000, batch_root=BR),
        "COMMITTED": Step(S.COMMITTED, T + 95_100, commitment_id="cm-1", committed_at_ms=T + 95_000),
        "ABANDONED": Step(S.ABANDONED, T + 200_000, reason="PROCESS_LOST"),
    }


def build(mod: types.ModuleType, path: list[str]) -> Any:
    table = steps(mod)
    history = mod.begin(PH, T, T - 1_000)
    for name in path:
        history = mod.advance(history, table[name])
    return history


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("record type", (mod.TYPE, mod.TYPE_VERSION, mod.MAX_INSTANT_MS), ("cycle_state", 1, 253_402_300_799_999))
    c.eq(
        "states",
        [s.value for s in mod.State],
        ["PLANNED", "DISCOVERING", "SEALED", "COLLECTING", "SNAPSHOT_FROZEN", "RUNNING", "COMMITTING", "COMMITTED", "ABANDONED"],
    )
    c.eq(
        "order",
        [s.value for s in mod.ORDER],
        ["PLANNED", "DISCOVERING", "SEALED", "COLLECTING", "SNAPSHOT_FROZEN", "RUNNING", "COMMITTING", "COMMITTED"],
    )
    c.eq("terminal", sorted(s.value for s in mod.TERMINAL), ["ABANDONED", "COMMITTED"])
    c.eq("reasons", sorted(mod.ABANDON_REASONS), ["CLOCK_OFFSET", "DISCOVERY_SNAPSHOT_GAP", "PROCESS_LOST", "SNAPSHOT_TOO_OLD"])
    c.eq("what each state carries", {s.value: sorted(r) for s, r in mod.REQUIRED.items()}, {
        "PLANNED": ["planned_for_ms", "protocol_hash"], "DISCOVERING": ["discovery_at_ms"], "SEALED": ["manifest_hash", "sealed_at_ms"], "COLLECTING": [],
        "SNAPSHOT_FROZEN": ["snapshot_at_ms", "snapshot_hash"], "RUNNING": [], "COMMITTING": ["batch_root"], "COMMITTED": ["commitment_id", "committed_at_ms"], "ABANDONED": ["reason"]})  # fmt: skip
    for cls in (mod.Step, mod.Transition, mod.Beat):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("a conflict is a kind of cycle error", issubclass(mod.Conflict, mod.CycleError), True)
    c.eq("a step's id", build(mod, [])[0].transition_id, natural_id("cs", {"cycle": mod.cycle_id(PH, T), "seq": 1}))
    c.eq("a cycle's id", mod.cycle_id(PH, T), natural_id("cy", {"protocol": PH, "planned_for_ms": T}))
    c.eq(
        "another slot, another cycle",
        (mod.cycle_id(PH, T) != mod.cycle_id(PH, T + 1), mod.cycle_id(PH, T) != mod.cycle_id("e" * 64, T)),
        (True, True),
    )
    return c.bad


def check_every_path(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    table = steps(mod)
    seen: set[tuple[str, ...]] = set()
    stack: list[tuple[str, ...]] = [()]
    terminals = 0
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        history = build(mod, list(path))
        now = mod.current_state(history).value
        if not ALLOWED_FROM.get(now):
            terminals += 1
        for name, step in table.items():
            allowed = name in ALLOWED_FROM.get(now, set())
            try:
                after = mod.advance(history, step)
            except mod.CycleError as exc:
                if name in path:
                    c.bad.append(f"{'>'.join(path)} then {name}: a step already taken was refused instead of ignored ({exc})")
                elif allowed:
                    c.bad.append(f"{'>'.join(path)} then {name}: refused ({exc}) but allowed")
                continue
            if name in path:
                c.eq(f"{'>'.join(path)} then {name} again", after, history)
            elif not allowed:
                c.bad.append(f"{'>'.join(path)} then {name}: accepted but not allowed")
            else:
                c.eq(
                    f"{'>'.join(path)} then {name}: one step longer, the rest kept",
                    (len(after), after[: len(history)]),
                    (len(history) + 1, history),
                )
                stack.append((*path, name))
    c.eq("terminal histories: abandoned after each of the seven states, and the committed one", terminals, 8)
    c.eq("histories: the eight along the way, and an abandoned one after each of the seven before the end", len(seen), 15)
    return c.bad


def check_members_and_times(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step = mod.State, mod.Step
    h = build(mod, ["DISCOVERING", "SEALED", "COLLECTING"])
    frozen = build(mod, FULL[:4])
    cases: list[tuple[str, Callable[[], Any]]] = [
        ("a start with a missing hash", lambda: mod.begin("", T, T)),
        ("a hash with one character off the alphabet", lambda: mod.begin("a" * 63 + "g", T, T)),
        ("a hash with a tilde", lambda: mod.begin("a" * 63 + "~", T, T)),
        ("a start with an upper-case hash", lambda: mod.begin("A" * 64, T, T)),
        ("a start at a negative slot", lambda: mod.begin(PH, -1, T)),
        ("a start at a negative time", lambda: mod.begin(PH, T, -1)),
        ("a start with a boolean time", lambda: mod.begin(PH, T, True)),
        ("a start past the year 9999", lambda: mod.begin(PH, T, 253_402_300_800_000)),
        ("discovery without its time", lambda: mod.advance(build(mod, []), Step(S.DISCOVERING, T + 1_000))),
        ("discovery that carries more", lambda: mod.advance(build(mod, []), Step(S.DISCOVERING, T + 1_000, discovery_at_ms=T, reason="x"))),
        (
            "discovery recorded before it started",
            lambda: mod.advance(build(mod, []), Step(S.DISCOVERING, T + 1_000, discovery_at_ms=T + 1_001)),
        ),
        ("discovery before its slot", lambda: mod.advance(build(mod, []), Step(S.DISCOVERING, T - 1, discovery_at_ms=T - 1))),
        ("sealing without a hash", lambda: mod.advance(build(mod, ["DISCOVERING"]), Step(S.SEALED, T + 3_100, sealed_at_ms=T + 3_000))),
        (
            "sealing with a short hash",
            lambda: mod.advance(build(mod, ["DISCOVERING"]), Step(S.SEALED, T + 3_100, sealed_at_ms=T + 3_000, manifest_hash="ab")),
        ),
        (
            "sealing recorded before it happened",
            lambda: mod.advance(build(mod, ["DISCOVERING"]), Step(S.SEALED, T + 3_100, sealed_at_ms=T + 3_101, manifest_hash=MH)),
        ),
        (
            "sealing before the discovery",
            lambda: mod.advance(build(mod, ["DISCOVERING"]), Step(S.SEALED, T + 3_100, sealed_at_ms=T + 499, manifest_hash=MH)),
        ),
        (
            "a snapshot recorded before it was taken",
            lambda: mod.advance(h, Step(S.SNAPSHOT_FROZEN, T + 60_100, snapshot_at_ms=T + 60_101, snapshot_hash=SH)),
        ),
        (
            "a snapshot before the sealing",
            lambda: mod.advance(h, Step(S.SNAPSHOT_FROZEN, T + 4_000, snapshot_at_ms=T + 2_999, snapshot_hash=SH)),
        ),
        (
            "a snapshot ten minutes and a millisecond after the discovery",
            lambda: mod.advance(h, Step(S.SNAPSHOT_FROZEN, T + 700_000, snapshot_at_ms=T + 500 + 600_001, snapshot_hash=SH)),
        ),
        ("a snapshot without a hash", lambda: mod.advance(h, Step(S.SNAPSHOT_FROZEN, T + 60_100, snapshot_at_ms=T + 60_000))),
        ("a batch without a root", lambda: mod.advance(build(mod, FULL[:5]), Step(S.COMMITTING, T + 90_000))),
        ("a batch with a root that is a number", lambda: mod.advance(build(mod, FULL[:5]), Step(S.COMMITTING, T + 90_000, batch_root=5))),
        (
            "a commitment before the snapshot",
            lambda: mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="cm-1", committed_at_ms=T + 59_999)),
        ),
        (
            "a commitment recorded before it happened",
            lambda: mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="cm-1", committed_at_ms=T + 95_101)),
        ),
        (
            "a commitment without an id",
            lambda: mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, committed_at_ms=T + 95_000)),
        ),
        (
            "a commitment with an empty id",
            lambda: mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="", committed_at_ms=T + 95_000)),
        ),
        (
            "a commitment with an id of 201 characters",
            lambda: mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="c" * 201, committed_at_ms=T + 95_000)),
        ),
        (
            "a commitment with an id that is not canonical text",
            lambda: mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="é", committed_at_ms=T + 95_000)),
        ),
        ("an abandonment without a reason", lambda: mod.advance(frozen, Step(S.ABANDONED, T + 70_000))),
        ("an abandonment for another reason", lambda: mod.advance(frozen, Step(S.ABANDONED, T + 70_000, reason="BORED"))),
        ("a step earlier than the one before", lambda: mod.advance(frozen, Step(S.RUNNING, T + 60_099))),
        ("a state that cannot follow", lambda: mod.advance(frozen, steps(mod)["COMMITTING"])),
        ("a state that skips ahead", lambda: mod.advance(build(mod, []), steps(mod)["SEALED"])),
        ("planning twice", lambda: mod.advance(frozen, Step(S.PLANNED, T, planned_for_ms=T, protocol_hash=PH))),
        ("a step after the end", lambda: mod.advance(build(mod, FULL), steps(mod)["ABANDONED"])),
        ("a step after an abandonment", lambda: mod.advance(build(mod, ["DISCOVERING", "ABANDONED"]), steps(mod)["SEALED"])),
        ("advancing nothing", lambda: mod.advance((), steps(mod)["DISCOVERING"])),
        ("a negative step time", lambda: mod.advance(frozen, Step(S.RUNNING, -1))),
    ]
    for label, call in cases:
        c.refuses(label, call, SAYS.get(label))
    c.eq("the epoch is a time", len(mod.begin(PH, 0, 0)), 1)
    c.eq("no history has no state", mod.current_state(build(mod, [])), mod.State.PLANNED)
    c.refuses("no history at all", lambda: mod.current_state(()), "a cycle has no history yet")
    c.eq(
        "a snapshot exactly ten minutes after the discovery",
        mod.current_state(mod.advance(h, Step(S.SNAPSHOT_FROZEN, T + 700_000, snapshot_at_ms=T + 500 + 600_000, snapshot_hash=SH))),
        S.SNAPSHOT_FROZEN,
    )
    c.eq(
        "a snapshot at the instant of the sealing",
        mod.current_state(mod.advance(h, Step(S.SNAPSHOT_FROZEN, T + 4_000, snapshot_at_ms=T + 3_000, snapshot_hash=SH))),
        S.SNAPSHOT_FROZEN,
    )
    c.eq(
        "a discovery at the slot", mod.current_state(mod.advance(build(mod, []), Step(S.DISCOVERING, T, discovery_at_ms=T))), S.DISCOVERING
    )
    c.eq(
        "a sealing at the discovery",
        mod.current_state(mod.advance(build(mod, ["DISCOVERING"]), Step(S.SEALED, T + 1_000, sealed_at_ms=T + 500, manifest_hash=MH))),
        S.SEALED,
    )
    c.eq(
        "a commitment at the snapshot",
        mod.current_state(mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="cm", committed_at_ms=T + 60_000))),
        S.COMMITTED,
    )
    c.eq(
        "a commitment at the instant it is recorded",
        mod.current_state(mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_000, commitment_id="cm", committed_at_ms=T + 95_000))),
        S.COMMITTED,
    )
    for reason in sorted(mod.ABANDON_REASONS):
        c.eq(f"abandoned for {reason}", mod.current_state(mod.advance(frozen, mod.abandon_step(reason, T + 70_000))), mod.State.ABANDONED)
    c.eq(
        "a commitment id of 200 characters",
        mod.current_state(
            mod.advance(build(mod, FULL[:6]), Step(S.COMMITTED, T + 95_100, commitment_id="c" * 200, committed_at_ms=T + 95_000))
        ),
        S.COMMITTED,
    )
    return c.bad


SAYS: dict[str, str] = {
    "a start with a missing hash": "protocol_hash is 64 lower-case hexadecimal characters",
    "a start with an upper-case hash": "protocol_hash is 64 lower-case hexadecimal characters",
    "a hash with one character off the alphabet": "protocol_hash is 64 lower-case hexadecimal characters",
    "a hash with a tilde": "protocol_hash is 64 lower-case hexadecimal characters",
    "a start at a negative slot": "planned_for_ms is a whole number of milliseconds up to the end of the year 9999",
    "a start at a negative time": "the time of a step is a whole number of milliseconds up to the end of the year 9999",
    "a start with a boolean time": "the time of a step is a whole number of milliseconds up to the end of the year 9999",
    "a start past the year 9999": "the time of a step is a whole number of milliseconds up to the end of the year 9999",
    "discovery without its time": "DISCOVERING carries ['discovery_at_ms'], not []",
    "discovery that carries more": "DISCOVERING carries ['discovery_at_ms'], not ['discovery_at_ms', 'reason']",
    "discovery recorded before it started": "discovery starts no earlier than the slot it was planned for, and is recorded no earlier than it started",
    "discovery before its slot": "discovery starts no earlier than the slot it was planned for, and is recorded no earlier than it started",
    "sealing without a hash": "SEALED carries ['manifest_hash', 'sealed_at_ms'], not ['sealed_at_ms']",
    "sealing with a short hash": "manifest_hash is 64 lower-case hexadecimal characters",
    "sealing recorded before it happened": "the manifest is sealed no earlier than the discovery, and recorded no earlier than it was sealed",
    "sealing before the discovery": "the manifest is sealed no earlier than the discovery, and recorded no earlier than it was sealed",
    "a snapshot recorded before it was taken": "a snapshot is recorded no earlier than it was taken",
    "a snapshot before the sealing": "the snapshot is taken no earlier than the sealing and within 10 minutes of the discovery",
    "a snapshot ten minutes and a millisecond after the discovery": "the snapshot is taken no earlier than the sealing and within 10 minutes of the discovery",
    "a snapshot without a hash": "SNAPSHOT_FROZEN carries ['snapshot_at_ms', 'snapshot_hash'], not ['snapshot_at_ms']",
    "a batch without a root": "COMMITTING carries ['batch_root'], not []",
    "a batch with a root that is a number": "batch_root is 64 lower-case hexadecimal characters",
    "a commitment before the snapshot": "a cycle is committed no earlier than its snapshot, and recorded no earlier than it was committed",
    "a commitment recorded before it happened": "a cycle is committed no earlier than its snapshot, and recorded no earlier than it was committed",
    "a commitment without an id": "COMMITTED carries ['commitment_id', 'committed_at_ms'], not ['committed_at_ms']",
    "a commitment with an empty id": "commitment_id is a non-empty string of at most 200 characters",
    "a commitment with an id of 201 characters": "commitment_id is a non-empty string of at most 200 characters",
    "a commitment with an id that is not canonical text": "commitment_id is not valid canonical text: string is not NFC",
    "an abandonment without a reason": "ABANDONED carries ['reason'], not []",
    "an abandonment for another reason": "a cycle is abandoned for a reason from ['CLOCK_OFFSET', 'DISCOVERY_SNAPSHOT_GAP', 'PROCESS_LOST', 'SNAPSHOT_TOO_OLD']",
    "a step earlier than the one before": "a step is not earlier than the one before it",
    "a state that cannot follow": "COMMITTING cannot follow SNAPSHOT_FROZEN",
    "a state that skips ahead": "SEALED cannot follow PLANNED",
    "planning twice": "a cycle is planned once",
    "a step after the end": "ABANDONED cannot follow COMMITTED: the cycle is over",
    "a step after an abandonment": "SEALED cannot follow ABANDONED: the cycle is over",
    "advancing nothing": "a cycle begins with begin()",
    "a negative step time": "the time of a step is a whole number of milliseconds up to the end of the year 9999",
}


def check_idempotence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step = mod.State, mod.Step
    h = build(mod, ["DISCOVERING", "SEALED"])
    c.refuses(
        "a different sealing",
        lambda: mod.advance(h, Step(S.SEALED, T + 3_100, sealed_at_ms=T + 3_001, manifest_hash=MH)),
        "SEALED was entered with other members",
        kind=mod.Conflict,
    )
    c.refuses(
        "a sealing with another manifest",
        lambda: mod.advance(h, Step(S.SEALED, T + 3_100, sealed_at_ms=T + 3_000, manifest_hash="e" * 64)),
        "SEALED was entered with other members",
        kind=mod.Conflict,
    )
    c.eq("the same sealing again", mod.advance(h, steps(mod)["SEALED"]), h)
    done = build(mod, FULL)
    c.eq("the same end again", mod.advance(done, steps(mod)["COMMITTED"]), done)
    c.eq("all earlier steps again", all(mod.advance(done, steps(mod)[name]) == done for name in FULL), True)
    c.refuses(
        "another end",
        lambda: mod.advance(done, Step(S.COMMITTED, T + 96_000, commitment_id="cm-2", committed_at_ms=T + 95_500)),
        "COMMITTED was entered with other members",
        kind=mod.Conflict,
    )
    return c.bad


def all_records(mod: types.ModuleType, history: Any) -> list[dict[str, canonical.JsonValue]]:
    return [mod.to_record(t, SRC, t.step.at_ms + 1) for t in history]


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    h = build(mod, FULL)
    records = all_records(mod, h)
    for transition, record in zip(h, records, strict=True):
        again = mod.from_record(canonical.parse_record(canonical.dumps(record)))
        c.eq(f"step {transition.seq} reads back", again, transition)
        c.eq(f"step {transition.seq} has its id", record["id"], transition.transition_id)
    ab = build(mod, ["DISCOVERING", "ABANDONED"])
    for transition in ab:
        rec = mod.to_record(transition, SRC, transition.step.at_ms)
        c.eq(
            f"abandoned history step {transition.seq} reads back", mod.from_record(canonical.parse_record(canonical.dumps(rec))), transition
        )
    c.eq(
        "a planned body",
        records[0]["body"],
        {
            "cycle_id": mod.cycle_id(PH, T),
            "seq": 1,
            "state": "PLANNED",
            "at": "2026-10-29T21:56:39.000Z",
            "planned_for": "2026-10-29T21:56:40.000Z",
            "protocol_hash": PH,
        },
    )
    c.eq(
        "a sealed body",
        records[2]["body"],
        {
            "cycle_id": mod.cycle_id(PH, T),
            "seq": 3,
            "state": "SEALED",
            "at": "2026-10-29T21:56:43.100Z",
            "sealed_at": "2026-10-29T21:56:43.000Z",
            "manifest_hash": MH,
        },
    )
    c.eq(
        "a committed body",
        records[7]["body"],
        {
            "cycle_id": mod.cycle_id(PH, T),
            "seq": 8,
            "state": "COMMITTED",
            "at": "2026-10-29T21:58:15.100Z",
            "commitment_id": "cm-1",
            "committed_at": "2026-10-29T21:58:15.000Z",
        },
    )
    c.eq("an abandoned body", mod.to_record(ab[-1], SRC, T + 200_000)["body"]["reason"], "PROCESS_LOST")
    c.eq("the envelope", {k: records[0][k] for k in ("spec", "type", "type_version", "source", "created_at", "refs", "privacy")}, {"spec": "hoshitori.record/1", "type": "cycle_state", "type_version": 1, "source": SRC, "created_at": "2026-10-29T21:56:39.001Z", "refs": [], "privacy": "public"})  # fmt: skip
    c.eq("the ledger's copy is read", mod.from_record({**records[1], "committed_at": "2026-10-29T21:57:00.000Z"}), h[1])
    c.refuses(
        "a negative creation time",
        lambda: mod.to_record(h[0], SRC, -1),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(h[0], "scheduler", T),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )

    def changed(rec: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
        out = copy.deepcopy(rec)
        node = out
        for key in path[:-1]:
            node = node[key]
        if value is _GONE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return out

    planned, sealed, done_rec = records[0], records[2], records[7]
    cases: list[tuple[str, dict[str, Any]]] = [
        ("an unknown envelope member", changed(planned, ("extra",), 1)),
        ("another type", changed(planned, ("type",), "forecast_state")),
        ("another version", changed(planned, ("type_version",), 2)),
        ("a cycle id that is a number", changed(planned, ("body", "cycle_id"), 5)),
        ("a position of zero", changed(planned, ("body", "seq"), 0)),
        ("a position that is a boolean", changed(planned, ("body", "seq"), True)),
        ("a state that is unknown", changed(planned, ("body", "state"), "RETIRED")),
        ("a state that is a number", changed(planned, ("body", "state"), 1)),
        ("a time without milliseconds", changed(planned, ("body", "at"), "2026-10-29T21:56:39Z")),
        ("a time that is a number", changed(planned, ("body", "at"), 5)),
        ("a time that is nonsense", changed(planned, ("body", "at"), "yesterday")),
        ("a time that is missing", changed(planned, ("body", "at"), _GONE)),
        ("a member the state does not carry", changed(planned, ("body", "reason"), "x")),
        ("a member that is missing", changed(sealed, ("body", "manifest_hash"), _GONE)),
        ("a slot time in milliseconds", changed(planned, ("body", "planned_for"), 1793311000000)),
        ("a hash that is a number", changed(sealed, ("body", "manifest_hash"), 5)),
        ("a hash in capitals", changed(sealed, ("body", "manifest_hash"), "B" * 64)),
        ("a commitment id that is a number", changed(done_rec, ("body", "commitment_id"), 5)),
        ("an id of another step", changed(planned, ("id",), "csAAAAAAAAAAAAAAAAAAAAAAAAAA")),
        ("a position that disagrees with the id", changed(planned, ("body", "seq"), 2)),
        ("a body that is not an object", changed(planned, ("body",), [])),
    ]
    for label, rec in cases:
        c.refuses(label, lambda r=rec: mod.from_record(r), READING.get(label))
    return c.bad


READING: dict[str, str] = {
    "an unknown envelope member": "the envelope: unknown envelope members: ['extra']",
    "another type": "not a cycle-state record of a version this reader knows",
    "another version": "not a cycle-state record of a version this reader knows",
    "a cycle id that is a number": "a step has a cycle id and a position of one or more",
    "a position of zero": "a step has a cycle id and a position of one or more",
    "a position that is a boolean": "a step has a cycle id and a position of one or more",
    "a state that is unknown": "a step has a known state",
    "a state that is a number": "a step has a known state",
    "a time without milliseconds": "at is written as UTC with milliseconds",
    "a time that is a number": "at is an RFC 3339 string",
    "a time that is nonsense": "at: not an RFC 3339 timestamp with an explicit offset: 'yesterday'",
    "a time that is missing": "PLANNED has the members ['at'] wrong",
    "a member the state does not carry": "PLANNED has the members ['reason'] wrong",
    "a member that is missing": "SEALED has the members ['manifest_hash'] wrong",
    "a slot time in milliseconds": "planned_for is an RFC 3339 string",
    "a hash that is a number": "manifest_hash is text",
    "a hash in capitals": "manifest_hash is 64 lower-case hexadecimal characters",
    "a commitment id that is a number": "commitment_id is text",
    "an id of another step": "the id is not the id of this step",
    "a position that disagrees with the id": "the id is not the id of this step",
    "a body that is not an object": "the envelope: body must be an object",
}


def check_rebuild(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = build(mod, FULL)
    other_hash = "e" * 64
    other = mod.begin(other_hash, T + 3_600_000, T)
    recs_a = all_records(mod, a)
    recs_b = all_records(mod, other)
    mixed = [recs_a[0], recs_b[0], recs_a[1], *recs_a[2:]]
    built = mod.rebuild(mixed)
    c.eq("two interleaved cycles", built, {mod.cycle_id(PH, T): a, mod.cycle_id(other_hash, T + 3_600_000): other})
    c.eq("a step delivered twice counts once", mod.rebuild([*mixed, recs_a[3], recs_a[0], recs_b[0]]), built)
    c.eq("an empty ledger", mod.rebuild([]), {})
    c.refuses("a gap", lambda: mod.rebuild([recs_a[0], recs_a[2]]), f"step 3 of {mod.cycle_id(PH, T)} arrived when 1 were known")
    c.refuses(
        "a reorder", lambda: mod.rebuild([recs_a[0], recs_a[2], recs_a[1]]), f"step 3 of {mod.cycle_id(PH, T)} arrived when 1 were known"
    )
    c.refuses(
        "a start that is not the plan", lambda: mod.rebuild([recs_a[1]]), f"step 2 of {mod.cycle_id(PH, T)} arrived when 0 were known"
    )
    first = mod.to_record(mod.Transition(mod.cycle_id(PH, T), 1, steps(mod)["DISCOVERING"]), SRC, T)
    c.refuses("a first step that is not the plan", lambda: mod.rebuild([first]), "a cycle's first step is PLANNED")
    illegal = mod.to_record(mod.Transition(mod.cycle_id(PH, T), 2, steps(mod)["RUNNING"]), SRC, T)
    c.refuses("a step the rules would refuse", lambda: mod.rebuild([recs_a[0], illegal]), "RUNNING cannot follow PLANNED")
    wrong_id = mod.to_record(mod.Transition("cyAAAAAAAAAAAAAAAAAAAAAAAAAA", 1, mod.begin(PH, T, T - 1_000)[0].step), SRC, T)
    c.refuses("a plan under another cycle id", lambda: mod.rebuild([wrong_id]), "the cycle id is not the id of this protocol and slot")
    again = mod.to_record(mod.Transition(mod.cycle_id(PH, T), 3, steps(mod)["DISCOVERING"]), SRC, T)
    c.refuses(
        "the same step again under a later position", lambda: mod.rebuild([*recs_a[:2], again]), "a step repeated under another position"
    )
    return c.bad


def check_expiry(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("no cycle is resumed: the module offers no recovery verdict, only the expiry of a snapshot", hasattr(mod, "recover"), False)
    frozen = build(mod, FULL[:4])
    c.eq("a snapshot just inside the limit", mod.snapshot_expired(frozen, T + 60_000 + 900_000, 900_000), False)
    c.eq("a snapshot just outside it", mod.snapshot_expired(frozen, T + 60_000 + 900_001, 900_000), True)
    c.eq("a snapshot not yet taken", mod.snapshot_expired(build(mod, FULL[:2]), T + 10**9, 1), False)
    c.eq(
        "a cycle that is over cannot expire",
        (mod.snapshot_expired(build(mod, FULL), T + 10**9, 1), mod.snapshot_expired(build(mod, [*FULL[:4], "ABANDONED"]), T + 10**9, 1)),
        (False, False),
    )
    c.eq(
        "it expires while running and committing too",
        (mod.snapshot_expired(build(mod, FULL[:5]), T + 10**9, 1), mod.snapshot_expired(build(mod, FULL[:6]), T + 10**9, 1)),
        (True, True),
    )
    c.refuses(
        "a time that is negative",
        lambda: mod.snapshot_expired(frozen, -1, 5),
        "now is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a limit that is negative",
        lambda: mod.snapshot_expired(frozen, T, -1),
        "the limit is a whole number of milliseconds up to the end of the year 9999",
    )
    c.eq("abandon step", mod.abandon_step("CLOCK_OFFSET", T + 5), mod.Step(mod.State.ABANDONED, T + 5, reason="CLOCK_OFFSET"))
    return c.bad


def check_heartbeat(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    F = mod.Finding
    c.eq("findings", [f.value for f in F], ["MISSING", "UNFINISHED", "LATE", "ABANDONED"])
    H = 3_600_000
    slots = [T, T + H, T + 2 * H, T + 3 * H, T + 4 * H, T + 5 * H]
    done = build(mod, FULL)  # committed at T + 95_000
    late = mod.begin(PH, T + H, T + H - 1_000)
    for _name, step in (
        ("DISCOVERING", mod.Step(mod.State.DISCOVERING, T + H + 1_000, discovery_at_ms=T + H + 500)),
        ("SEALED", mod.Step(mod.State.SEALED, T + H + 2_000, sealed_at_ms=T + H + 2_000, manifest_hash=MH)),
        ("COLLECTING", mod.Step(mod.State.COLLECTING, T + H + 3_000)),
        ("SNAPSHOT_FROZEN", mod.Step(mod.State.SNAPSHOT_FROZEN, T + H + 4_000, snapshot_at_ms=T + H + 4_000, snapshot_hash=SH)),
        ("RUNNING", mod.Step(mod.State.RUNNING, T + H + 5_000)),
        ("COMMITTING", mod.Step(mod.State.COMMITTING, T + H + 6_000, batch_root=BR)),
        ("COMMITTED", mod.Step(mod.State.COMMITTED, T + H + 400_000, commitment_id="cm-2", committed_at_ms=T + H + 400_000)),
    ):
        late = mod.advance(late, step)
    abandoned = mod.advance(mod.begin(PH, T + 2 * H, T + 2 * H), mod.abandon_step("PROCESS_LOST", T + 2 * H + 10))
    running = mod.advance(
        mod.begin(PH, T + 3 * H, T + 3 * H - 1), mod.Step(mod.State.DISCOVERING, T + 3 * H + 1, discovery_at_ms=T + 3 * H)
    )
    now = T + 5 * H + 10_000
    beats = mod.heartbeat(slots, [done, late, abandoned, running], now_ms=now, start_grace_ms=60_000, finish_within_ms=300_000)
    c.eq(
        "what the schedule and the records show",
        [(b.planned_for_ms - T, b.finding.value) for b in beats],
        [(H, "LATE"), (2 * H, "ABANDONED"), (3 * H, "UNFINISHED"), (4 * H, "MISSING")],
    )
    c.eq(
        "a cycle is named when there is one",
        [b.cycle_id for b in beats],
        [mod.cycle_id(PH, T + H), mod.cycle_id(PH, T + 2 * H), mod.cycle_id(PH, T + 3 * H), None],
    )
    c.eq("a committed cycle in time is not a finding", [b for b in beats if b.planned_for_ms == T], [])
    early = mod.heartbeat(slots, [done], now_ms=T + H + 60_000, start_grace_ms=60_000, finish_within_ms=300_000)
    c.eq("a slot within its grace is not yet missing", early, ())
    just = mod.heartbeat(slots, [done], now_ms=T + H + 60_001, start_grace_ms=60_000, finish_within_ms=300_000)
    c.eq("one millisecond past the grace it is", [(b.planned_for_ms - T, b.finding.value) for b in just], [(H, "MISSING")])
    on_time = mod.heartbeat([T + 3 * H], [running], now_ms=T + 3 * H + 300_000, start_grace_ms=1, finish_within_ms=300_000)
    c.eq("a cycle still within its time to finish", on_time, ())
    over = mod.heartbeat([T + 3 * H], [running], now_ms=T + 3 * H + 300_001, start_grace_ms=1, finish_within_ms=300_000)
    c.eq("one millisecond past it", [b.finding.value for b in over], ["UNFINISHED"])
    exactly = mod.heartbeat([T], [done], now_ms=T + 10**8, start_grace_ms=1, finish_within_ms=95_000)
    c.eq("committed exactly at the limit is in time", exactly, ())
    past = mod.heartbeat([T], [done], now_ms=T + 10**8, start_grace_ms=1, finish_within_ms=94_999)
    c.eq("one millisecond past the limit is late", [b.finding.value for b in past], ["LATE"])
    c.eq(
        "a slot listed twice is looked at once",
        len(mod.heartbeat([T + H, T + H], [], now_ms=T + 5 * H, start_grace_ms=1, finish_within_ms=1)),
        1,
    )
    c.eq(
        "slots are looked at in time order",
        [b.planned_for_ms for b in mod.heartbeat([T + 2 * H, T + H, T], [], now_ms=T + 5 * H, start_grace_ms=1, finish_within_ms=1)],
        [T, T + H, T + 2 * H],
    )
    c.eq("no schedule", mod.heartbeat([], [done], now_ms=T, start_grace_ms=1, finish_within_ms=1), ())
    c.refuses(
        "a time that is negative",
        lambda: mod.heartbeat([T], [], now_ms=-1, start_grace_ms=1, finish_within_ms=1),
        "now is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a grace that is negative",
        lambda: mod.heartbeat([T], [], now_ms=T, start_grace_ms=-1, finish_within_ms=1),
        "the start grace is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a finish limit that is negative",
        lambda: mod.heartbeat([T], [], now_ms=T, start_grace_ms=1, finish_within_ms=-1),
        "the finish limit is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a slot that is negative",
        lambda: mod.heartbeat([-1], [], now_ms=T, start_grace_ms=1, finish_within_ms=1),
        "a scheduled time is a whole number of milliseconds up to the end of the year 9999",
    )
    return c.bad


def check_batch_size(mod: types.ModuleType) -> list[str]:
    """The step that commits a batch names how many records it has, beside their root: a verifier then knows which
    records under the stamped checkpoint are the batch (the last ``batch_size``) and can recompute the root from them. A step written before
    this member existed has none, and is still read."""
    c = Case(mod)
    S, Step = mod.State, mod.Step
    running = build(mod, FULL[:5])
    with_size = mod.advance(running, Step(S.COMMITTING, T + 90_000, batch_root=BR, batch_size=6))
    c.eq("a batch that names its size", with_size[-1].step.batch_size, 6)
    c.eq(
        "and one that names none, as before", mod.advance(running, Step(S.COMMITTING, T + 90_000, batch_root=BR))[-1].step.batch_size, None
    )
    c.eq(
        "the smallest batch is one record",
        mod.advance(running, Step(S.COMMITTING, T + 90_000, batch_root=BR, batch_size=1))[-1].step.batch_size,
        1,
    )
    c.eq(
        "the largest is five thousand",
        mod.advance(running, Step(S.COMMITTING, T + 90_000, batch_root=BR, batch_size=5000))[-1].step.batch_size,
        5000,
    )
    says = "batch_size is a whole number from 1 to 5000"
    for label, value in (("zero", 0), ("below zero", -1), ("past the largest", 5001), ("a boolean", True), ("text", "6")):
        c.refuses(
            label.join(["a batch size of ", ""]),
            lambda v=value: mod.advance(running, Step(S.COMMITTING, T + 90_000, batch_root=BR, batch_size=v)),
            says,
        )
    c.refuses(
        "a size on a step that has no batch",
        lambda: mod.advance(build(mod, FULL[:4]), Step(S.RUNNING, T + 80_000, batch_size=6)),
        "RUNNING carries [], not ['batch_size']",
    )
    record = mod.to_record(with_size[-1], "urn:hoshitori:test", T + 90_000)
    c.eq("the record names it", record["body"]["batch_size"], 6)  # type: ignore[index]
    c.eq("and reads back as the step", mod.from_record(record).step, with_size[-1].step)
    old = mod.to_record(mod.advance(running, Step(S.COMMITTING, T + 90_000, batch_root=BR))[-1], "urn:hoshitori:test", T + 90_000)
    c.eq("a record without it is a record of an older batch, and reads", mod.from_record(old).step.batch_size, None)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_every_path,
        check_members_and_times,
        check_batch_size,
        check_idempotence,
        check_records,
        check_rebuild,
        check_expiry,
        check_heartbeat,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
