"""Checks of the slot lifecycle (``hoshitori.lifecycle``), runnable against a mutant."""

from __future__ import annotations

import copy
import types
from collections.abc import Callable
from typing import Any

from hoshitori import canonical, timeline
from hoshitori.ids import natural_id

H = 3_600_000
T = 1_793_311_000_000  # the slot is scheduled
COMMITTED_AT = T + 4_000
ELIGIBLE = COMMITTED_AT + 6_000
ORIGIN = timeline.origin(ELIGIBLE)
HORIZON = 4 * H
END = ORIGIN + HORIZON
FID = "fc-test-slot"
SRC = "urn:hoshitori:scheduler:test"

# The rules written the other way round: from each state, where a slot may go next.
ALLOWED_FROM: dict[str, set[str]] = {
    "SCHEDULED": {"CREATED", "FAILED", "FAILED_EVIDENCE", "FAILED_LOST"},
    "CREATED": {"EVIDENCE_FROZEN", "FAILED", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED_LOST"},
    "EVIDENCE_FROZEN": {"DECIDED", "FAILED", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED_STALE", "FAILED_LOST"},
    "DECIDED": {"COMMITTED", "NO_CALL", "FAILED", "FAILED_EVIDENCE", "FAILED_STALE", "FAILED_LOST"},
    "COMMITTED": {"AWAITING_ORIGIN", "VOID"},
    "AWAITING_ORIGIN": {"OPEN", "VOID", "UNSETTLEABLE"},
    "OPEN": {"SETTLING", "VOID", "UNSETTLEABLE"},
    "SETTLING": {"SETTLED", "AMBIGUOUS", "VOID", "UNSETTLEABLE"},
}


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str | None = None, kind: type[Exception] | None = None) -> None:
        expected = kind or self.mod.LifecycleError
        try:
            call()
        except expected as exc:
            if kind is None and type(exc) is not self.mod.LifecycleError:
                self.bad.append(f"{label}: raised {type(exc).__name__}, expected a plain LifecycleError")
            elif says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def steps(mod: types.ModuleType) -> dict[str, Any]:
    S, Step, Level = mod.State, mod.Step, mod.Level
    after = END + 1_000
    out: dict[str, Any] = {
        "CREATED": Step(S.CREATED, T + 1_000),
        "EVIDENCE_FROZEN": Step(S.EVIDENCE_FROZEN, T + 2_000),
        "DECIDED": Step(S.DECIDED, T + 3_000),
        "COMMITTED": Step(S.COMMITTED, COMMITTED_AT, level=Level.C1, committed_at_ms=COMMITTED_AT),
        "NO_CALL": Step(S.NO_CALL, T + 3_500, reason="BELOW_MINIMUM_EDGE", level=Level.C1),
        "AWAITING_ORIGIN": Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1_000, eligible_after_ms=ELIGIBLE),
        "OPEN": Step(S.OPEN, ORIGIN, origin_ms=ORIGIN),
        "SETTLING": Step(S.SETTLING, END),
        "SETTLED": Step(S.SETTLED, after + 4_000, settlement_id="st-1"),
        "AMBIGUOUS": Step(S.AMBIGUOUS, after + 4_000, settlement_id="st-1"),
        "VOID": Step(
            S.VOID, after, reason="VOID_OUTAGE"
        ),  # a reason that fits every state it may follow (a gap fits only before the origin)
        "UNSETTLEABLE": Step(S.UNSETTLEABLE, after, reason="UNSETTLEABLE_MISSING"),
    }
    for name in ("FAILED", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED_STALE", "FAILED_LOST"):
        out[name] = Step(S[name], T + 3_600, reason="tool timed out")
    return out


def build(mod: types.ModuleType, path: list[str], contract: str = "D") -> Any:
    table = steps(mod)
    history = mod.begin(FID, contract, HORIZON, T)
    for name in path:
        history = mod.advance(history, table[name])
    return history


FULL = ["CREATED", "EVIDENCE_FROZEN", "DECIDED", "COMMITTED", "AWAITING_ORIGIN", "OPEN", "SETTLING"]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("record type", (mod.TYPE, mod.TYPE_VERSION), ("forecast_state", 1))
    c.eq("reasons", (sorted(mod.NO_CALL_REASONS), sorted(mod.UNSETTLEABLE_REASONS), mod.MAX_REASON),
         (["BELOW_MINIMUM_EDGE", "ESSENTIAL_PREMISE_UNPROVEN", "EVIDENCE_CONFLICT_UNRESOLVED", "OUT_OF_SCOPE"], ["UNSETTLEABLE_DISAGREE", "UNSETTLEABLE_MISSING"], 200))  # fmt: skip
    c.eq("states", [s.value for s in mod.State],
         ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "DECIDED", "COMMITTED", "AWAITING_ORIGIN", "OPEN", "SETTLING", "SETTLED", "AMBIGUOUS", "VOID",
          "UNSETTLEABLE", "NO_CALL", "FAILED", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED_STALE", "FAILED_LOST", "LEVEL"])  # fmt: skip
    c.eq("failures", [s.value for s in mod.FAILURES], ["FAILED", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED_STALE", "FAILED_LOST"])
    c.eq(
        "the ends only a settlement gives",
        sorted((s.value, r) for s, r in mod.SETTLEMENT_ONLY),
        [("UNSETTLEABLE", "UNSETTLEABLE_DISAGREE"), ("VOID", "VOID_TIE")],
    )
    c.eq("terminal states", sorted(s.value for s in mod.TERMINAL),
         sorted(["SETTLED", "AMBIGUOUS", "VOID", "UNSETTLEABLE", "NO_CALL", "FAILED", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED_STALE", "FAILED_LOST"]))  # fmt: skip
    c.eq("the table written the other way round", {state.value: {t.value for t in targets_from(mod, state)} for state in mod.State if state in {s for v in mod.ENTRY.values() for s in v}}, ALLOWED_FROM)  # fmt: skip
    for cls in (mod.Step, mod.Transition, mod.Denominator):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.refuses("a conflict is a lifecycle error", lambda: (_ for _ in ()).throw(mod.Conflict("x")), kind=mod.Conflict)
    c.eq("a conflict is a kind of lifecycle error", issubclass(mod.Conflict, mod.LifecycleError), True)
    return c.bad


def targets_from(mod: types.ModuleType, state: Any) -> list[Any]:
    return [target for target, sources in mod.ENTRY.items() if state in sources]


def check_every_path(mod: types.ModuleType) -> list[str]:
    """Walk every reachable history and try every state: it is accepted exactly when the rules, written the other way round, allow it."""
    c = Case(mod)
    table = steps(mod)
    for contract in ("D", "B"):
        seen: set[tuple[str, ...]] = set()
        stack: list[tuple[str, ...]] = [()]
        terminal_paths = 0
        while stack:
            path = stack.pop()
            if path in seen:
                continue
            seen.add(path)
            history = build(mod, list(path), contract)
            now = mod.current_state(history).value
            if not ALLOWED_FROM.get(now):
                terminal_paths += 1
            for name in table:
                step = table[name]
                try:
                    after = mod.advance(history, step)
                except mod.LifecycleError as exc:
                    allowed = name in ALLOWED_FROM.get(now, set()) and not (name == "AMBIGUOUS" and contract == "D")
                    entered = name in path
                    if allowed and not entered:
                        c.bad.append(f"{contract} {'>'.join(path)} then {name}: refused ({exc}) but allowed")
                    elif entered:
                        c.bad.append(
                            f"{contract} {'>'.join(path)} then {name}: a step already taken was refused instead of ignored ({exc})"
                        )
                    continue
                if name in path:
                    c.eq(f"{contract} {'>'.join(path)} then {name} again", after, history)
                    continue
                allowed = name in ALLOWED_FROM.get(now, set()) and not (name == "AMBIGUOUS" and contract == "D")
                if not allowed:
                    c.bad.append(f"{contract} {'>'.join(path)} then {name}: accepted but not allowed")
                    continue
                c.eq(f"{contract} {'>'.join(path)} then {name}: one step longer", len(after), len(history) + 1)
                c.eq(f"{contract} {'>'.join(path)} then {name}: the earlier steps are kept", after[: len(history)], history)
                stack.append((*path, name))
        c.eq(
            f"{contract}: every terminal state is reached",
            len({p[-1] for p in seen if p and p[-1] in {s.value for s in mod.TERMINAL}}),
            10 if contract == "B" else 9,
        )
        c.eq(f"{contract}: number of terminal paths", terminal_paths, 26 if contract == "B" else 25)
    return c.bad


def check_begin(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    h = mod.begin(FID, "D", HORIZON, T)
    c.eq(
        "one scheduled step",
        (len(h), h[0].seq, h[0].forecast_id, h[0].step.state, h[0].step.contract, h[0].step.horizon_ms, h[0].step.at_ms),
        (1, 1, FID, mod.State.SCHEDULED, "D", HORIZON, T),
    )
    c.eq("the state of a new slot", mod.current_state(h), mod.State.SCHEDULED)
    c.refuses("no history has no state", lambda: mod.current_state(()), "a slot has no history yet")
    c.refuses("a contract that is neither", lambda: mod.begin(FID, "P", HORIZON, T), "a slot is contract D or B")
    c.refuses(
        "a horizon below an hour", lambda: mod.begin(FID, "D", 59 * 60_000, T), "a horizon is a whole number of minutes, at least one hour"
    )
    c.refuses("an empty id", lambda: mod.begin("", "D", HORIZON, T), "a slot id is a non-empty string")
    c.refuses("an id that is a number", lambda: mod.begin(1, "D", HORIZON, T), "a slot id is a non-empty string")
    c.eq("a slot scheduled at the epoch", mod.begin(FID, "D", HORIZON, 0)[0].step.at_ms, 0)
    c.refuses(
        "an id that is not canonical text",
        lambda: mod.begin("e\u0301", "D", HORIZON, T),
        "a slot id is not valid canonical text: string is not NFC",
    )
    c.eq(
        "the id of a step is the natural id of its slot and position",
        mod.Transition(FID, 3, h[0].step).transition_id,
        natural_id("ft", {"forecast": FID, "seq": 3}),
    )
    c.refuses("a negative time", lambda: mod.begin(FID, "D", HORIZON, -1), "the time of a step is a whole number of milliseconds")
    c.refuses("a boolean time", lambda: mod.begin(FID, "D", HORIZON, True), "the time of a step is a whole number of milliseconds")
    c.refuses(
        "scheduling twice",
        lambda: mod.advance(h, mod.Step(mod.State.SCHEDULED, T, contract="D", horizon_ms=HORIZON)),
        "a slot is scheduled once",
    )
    c.refuses("advancing nothing", lambda: mod.advance((), steps(mod)["CREATED"]), "a slot begins with begin()")
    id1 = mod.Transition(FID, 1, h[0].step).transition_id
    c.eq("transition ids", (id1 == mod.Transition(FID, 1, h[0].step).transition_id, id1 == mod.Transition(FID, 2, h[0].step).transition_id, id1 == mod.Transition("other", 1, h[0].step).transition_id, id1[:2]), (True, False, False, "ft"))  # fmt: skip
    return c.bad


def check_attested_origin(mod: types.ModuleType) -> list[str]:
    """A C3 slot's origin cannot be before its time-stamps allow : the step that waits for the origin
    names the attested time, the uncertainty and the spread, and is eligible no earlier than their sum. A slot that was written before the
    step named them is still read, and a new step of one is refused."""
    c = Case(mod)
    S, Step, Level = mod.State, mod.Step, mod.Level
    decided = build(mod, ["CREATED", "EVIDENCE_FROZEN", "DECIDED"])
    c3 = mod.advance(decided, Step(S.COMMITTED, COMMITTED_AT, level=Level.C3, committed_at_ms=COMMITTED_AT))
    attested, uncertainty, spread = COMMITTED_AT + 59_000, 5_000, 2_000
    due = attested + uncertainty + spread

    def waits(eligible: int, **over: Any) -> Any:
        members = {"attested_at_ms": attested, "uncertainty_ms": uncertainty, "spread_ms": spread, **over}
        return Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=eligible, **members)

    c.eq(
        "the attested time plus the uncertainty and the spread is the earliest",
        mod.current_state(mod.advance(c3, waits(due))),
        S.AWAITING_ORIGIN,
    )
    c.eq("and a later one is allowed", mod.current_state(mod.advance(c3, waits(due + 1))), S.AWAITING_ORIGIN)
    early = "a slot cannot become eligible before its attestation plus the uncertainty and the spread"
    c.refuses("a millisecond before it", lambda: mod.advance(c3, waits(due - 1)), early)
    c.refuses(
        "the commit time less the token window, as before (the product accepted it)",
        lambda: mod.advance(c3, waits(COMMITTED_AT - 1_000)),
        early,
    )
    c.refuses(
        "a C3 slot that names none of them",
        lambda: mod.advance(c3, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due)),
        "a C3 slot names its attested time, uncertainty and spread when it waits for the origin",
    )
    for name in ("attested_at_ms", "uncertainty_ms", "spread_ms"):
        c.refuses(
            f"a C3 slot that names all but {name}",
            lambda n=name: mod.advance(c3, waits(due, **{n: None})),
            "a C3 slot names its attested time, uncertainty and spread when it waits for the origin",
        )
    phase = (
        "an attestation lies within the phase of its commitment, from the token window before it to the close of the attestation after it"
    )
    c.refuses("an attestation before the token window", lambda: mod.advance(c3, waits(due, attested_at_ms=COMMITTED_AT - 1_001)), phase)
    c.eq(
        "at the edge of the token window",
        mod.current_state(mod.advance(c3, waits(COMMITTED_AT - 1_000 + uncertainty + spread, attested_at_ms=COMMITTED_AT - 1_000))),
        S.AWAITING_ORIGIN,
    )
    close = timeline.attestation_close(COMMITTED_AT)
    c.refuses(
        "an attestation after the close of the phase",
        lambda: mod.advance(c3, waits(close + 1 + uncertainty + spread, attested_at_ms=close + 1)),
        phase,
    )
    c.eq(
        "at the close of the phase",
        mod.current_state(mod.advance(c3, waits(close + uncertainty + spread, attested_at_ms=close))),
        S.AWAITING_ORIGIN,
    )
    c1 = mod.advance(decided, Step(S.COMMITTED, COMMITTED_AT, level=Level.C1, committed_at_ms=COMMITTED_AT))
    c.refuses("a slot below C3 carries no attested time", lambda: mod.advance(c1, waits(due)), "only a C3 slot carries an attested time")
    c.eq(
        "and waits from its commit time as before",
        mod.current_state(mod.advance(c1, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1, eligible_after_ms=COMMITTED_AT))),
        S.AWAITING_ORIGIN,
    )
    raised = mod.advance(
        mod.advance(decided, Step(S.COMMITTED, COMMITTED_AT, level=Level.C2, committed_at_ms=COMMITTED_AT)),
        Step(S.LEVEL, COMMITTED_AT + 30_000, level=Level.C3),
    )
    c.eq("a slot raised to C3 before it waits is a C3 slot", mod.current_state(mod.advance(raised, waits(due))), S.AWAITING_ORIGIN)
    c.refuses(
        "and needs the same",
        lambda: mod.advance(raised, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due)),
        "a C3 slot names its attested time, uncertainty and spread when it waits for the origin",
    )
    # all three or none: a part of them is refused whatever the level, and a legacy step is a step with none
    named = "a C3 slot names its attested time, uncertainty and spread when it waits for the origin"
    c.refuses(
        "a C3 slot that names only the attested time",
        lambda: mod.advance(c3, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due, attested_at_ms=attested)),
        named,
    )
    c.refuses(
        "a slot below C3 that names only the spread",
        lambda: mod.advance(c1, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due, spread_ms=1)),
        "only a C3 slot carries an attested time",
    )
    c.refuses(
        "a slot below C3 that names only the attested time",
        lambda: mod.advance(c1, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due, attested_at_ms=attested)),
        "only a C3 slot carries an attested time",
    )
    partial = [
        *c3,
        mod.Transition(FID, len(c3) + 1, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due, attested_at_ms=attested)),
    ]
    c.refuses(
        "a step on disk that names only some of them is refused, not read as an older step",
        lambda: mod.rebuild([mod.to_record(x, "urn:hoshitori:test", x.step.at_ms) for x in partial]),
        named,
    )
    # the numbers are whole milliseconds, zero or more
    whole = "{} is a whole number of milliseconds, zero or more"
    for name in ("uncertainty_ms", "spread_ms"):
        c.refuses(f"a negative {name}", lambda n=name: mod.advance(c3, waits(due + 10, **{n: -1})), whole.format(name))
        c.refuses(f"a boolean {name}", lambda n=name: mod.advance(c3, waits(due + 10, **{n: True})), whole.format(name))
        c.refuses(f"a {name} that is not a number", lambda n=name: mod.advance(c3, waits(due + 10, **{n: "5"})), whole.format(name))
    c.eq(
        "zero uncertainty and zero spread are allowed",
        mod.current_state(mod.advance(c3, waits(attested, uncertainty_ms=0, spread_ms=0))),
        S.AWAITING_ORIGIN,
    )
    # the record carries them, and reads back
    good = mod.advance(c3, waits(due))
    bad_member = mod.to_record(good[-1], "urn:hoshitori:test", COMMITTED_AT + 60_000)
    for name in ("uncertainty_ms", "spread_ms"):
        for value in (True, "5", 1.5):
            broken = {**bad_member, "body": {**bad_member["body"], name: value}}  # type: ignore[dict-item]
            c.refuses(f"a record whose {name} is {value!r}", lambda b=broken: mod.from_record(b), f"{name} is a whole number")
    record = mod.to_record(good[-1], "urn:hoshitori:test", COMMITTED_AT + 60_000)
    body = record["body"]
    c.eq(
        "the record names them",
        {k: body[k] for k in ("attested_at", "uncertainty_ms", "spread_ms", "eligible_after")},  # type: ignore[index]
        {
            "attested_at": mod.to_record(good[-1], "urn:hoshitori:test", 0)["body"]["attested_at"],  # type: ignore[index]
            "uncertainty_ms": uncertainty,
            "spread_ms": spread,
            "eligible_after": body["eligible_after"],  # type: ignore[index]
        },
    )
    c.eq("and reads back as the step", mod.from_record(record).step, good[-1].step)
    c.eq("a history of records rebuilds", mod.rebuild([mod.to_record(x, "urn:hoshitori:test", x.step.at_ms) for x in good])[FID], good)
    # a slot written before the step named them is still read, and is not told apart from a new one that is refused
    old_step = Step(S.AWAITING_ORIGIN, COMMITTED_AT + 60_000, eligible_after_ms=due)
    old = [*c3, mod.Transition(FID, len(c3) + 1, old_step)]
    legacy = mod.rebuild([mod.to_record(x, "urn:hoshitori:test", x.step.at_ms) for x in old])
    c.eq("an older C3 slot with no attested time is still read", mod.current_state(legacy[FID]), S.AWAITING_ORIGIN)
    forged = [*c3, mod.Transition(FID, len(c3) + 1, waits(due - 1))]
    c.refuses(
        "a step on disk that names an attestation and an eligible time before it is refused as new ones are",
        lambda: mod.rebuild([mod.to_record(x, "urn:hoshitori:test", x.step.at_ms) for x in forged]),
        early,
    )
    c.refuses(
        "and so is one on a slot below C3 that names an attested time",
        lambda: mod.rebuild(
            [mod.to_record(x, "urn:hoshitori:test", x.step.at_ms) for x in [*c1, mod.Transition(FID, len(c1) + 1, waits(due))]]
        ),
        "only a C3 slot carries an attested time",
    )
    return c.bad


def check_member_rules(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step, Level = mod.State, mod.Step, mod.Level
    h = build(mod, ["CREATED"])
    c.refuses(
        "a reason on a state that carries none",
        lambda: mod.advance(h, Step(S.EVIDENCE_FROZEN, T + 2_000, reason="x")),
        "EVIDENCE_FROZEN carries [] and may carry [], not ['reason']",
    )
    c.refuses(
        "a level on a state that carries none",
        lambda: mod.advance(h, Step(S.EVIDENCE_FROZEN, T + 2_000, level=Level.C1)),
        "EVIDENCE_FROZEN carries [] and may carry [], not ['level']",
    )
    d = build(mod, ["CREATED", "EVIDENCE_FROZEN", "DECIDED"])
    c.refuses(
        "a commitment without a level",
        lambda: mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, committed_at_ms=COMMITTED_AT)),
        "COMMITTED carries ['committed_at_ms', 'level'] and may carry [], not ['committed_at_ms']",
    )
    c.refuses(
        "a commitment without a time",
        lambda: mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level=Level.C1)),
        "COMMITTED carries ['committed_at_ms', 'level'] and may carry [], not ['level']",
    )
    c.refuses(
        "a commitment that is later than its record",
        lambda: mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level=Level.C1, committed_at_ms=COMMITTED_AT + 1)),
        "a forecast is committed no later than the step that records it",
    )
    c.refuses(
        "a commitment that is earlier than the decision",
        lambda: mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level=Level.C1, committed_at_ms=T + 3_000 - 1)),
        "a forecast is committed no earlier than it was decided",
    )
    c.eq(
        "a commitment at the very instant of the decision",
        len(mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level=Level.C1, committed_at_ms=T + 3_000))),
        len(d) + 1,
    )
    c.eq(
        "a commitment at the same instant",
        len(mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level=Level.C2, committed_at_ms=COMMITTED_AT))),
        5,
    )
    c.refuses(
        "a level that is text",
        lambda: mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level="C1", committed_at_ms=COMMITTED_AT)),
        "a level is C0 to C4",
    )
    for reason in ("OUT_OF_SCOPE", "ESSENTIAL_PREMISE_UNPROVEN", "EVIDENCE_CONFLICT_UNRESOLVED", "BELOW_MINIMUM_EDGE"):
        c.eq(
            f"a no-call for {reason}",
            mod.current_state(mod.advance(d, Step(S.NO_CALL, T + 3_500, reason=reason, level=Level.C1))),
            S.NO_CALL,
        )
    c.refuses(
        "a no-call for another reason",
        lambda: mod.advance(d, Step(S.NO_CALL, T + 3_500, reason="BORED", level=Level.C1)),
        "a voluntary no-call has a reason from ['BELOW_MINIMUM_EDGE', 'ESSENTIAL_PREMISE_UNPROVEN', 'EVIDENCE_CONFLICT_UNRESOLVED', 'OUT_OF_SCOPE']",
    )
    c.refuses(
        "a no-call without a reason",
        lambda: mod.advance(d, Step(S.NO_CALL, T + 3_500, level=Level.C1)),
        "NO_CALL carries ['level', 'reason'] and may carry [], not ['level']",
    )
    c.refuses(
        "a no-call without a level",
        lambda: mod.advance(d, Step(S.NO_CALL, T + 3_500, reason="OUT_OF_SCOPE")),
        "NO_CALL carries ['level', 'reason'] and may carry [], not ['reason']",
    )
    c.refuses(
        "a failure without a reason",
        lambda: mod.advance(d, Step(S.FAILED, T + 3_600)),
        "FAILED carries ['reason'] and may carry [], not []",
    )
    c.refuses(
        "a reference on a plain failure",
        lambda: mod.advance(d, Step(S.FAILED, T + 3_600, reason="x", ref="r")),
        "FAILED carries ['reason'] and may carry [], not ['reason', 'ref']",
    )
    c.eq(
        "a reference on an evidence failure",
        mod.current_state(mod.advance(d, Step(S.FAILED_EVIDENCE, T + 3_600, reason="RYO down", ref="cap-1"))),
        S.FAILED_EVIDENCE,
    )
    c.refuses("an empty reason", lambda: mod.advance(d, Step(S.FAILED, T + 3_600, reason="")), "a reason is a non-empty string")
    c.refuses("a reason that is a number", lambda: mod.advance(d, Step(S.FAILED, T + 3_600, reason=5)), "a reason is a non-empty string")
    c.eq("a reason of 200 characters", mod.current_state(mod.advance(d, Step(S.FAILED, T + 3_600, reason="x" * 200))), S.FAILED)
    c.refuses(
        "a reason that is not canonical text",
        lambda: mod.advance(d, Step(S.FAILED, T + 3_600, reason="e\u0301")),
        "a reason is not valid canonical text: string is not NFC",
    )
    c.refuses(
        "a reference that is not canonical text",
        lambda: mod.advance(d, Step(S.FAILED_EVIDENCE, T + 3_600, reason="x", ref="e\u0301")),
        "ref is not valid canonical text: string is not NFC",
    )
    c.eq(
        "a reference of 200 characters",
        mod.current_state(mod.advance(d, Step(S.FAILED_EVIDENCE, T + 3_600, reason="x", ref="r" * 200))),
        S.FAILED_EVIDENCE,
    )
    c.refuses(
        "a reason of 201 characters", lambda: mod.advance(d, Step(S.FAILED, T + 3_600, reason="x" * 201)), "a reason is a non-empty string"
    )
    c.refuses(
        "an empty reference", lambda: mod.advance(d, Step(S.FAILED_EVIDENCE, T + 3_600, reason="x", ref="")), "ref is a non-empty string"
    )
    c.refuses(
        "a negative step time",
        lambda: mod.advance(d, Step(S.FAILED, -1, reason="x")),
        "the time of a step is a whole number of milliseconds",
    )
    c.refuses(
        "a boolean step time",
        lambda: mod.advance(d, Step(S.FAILED, True, reason="x")),
        "the time of a step is a whole number of milliseconds",
    )
    c.refuses(
        "a step earlier than the one before",
        lambda: mod.advance(d, Step(S.FAILED, T + 2_999, reason="x")),
        "a step is not earlier than the one before it",
    )
    c.eq("a step at the same instant", mod.current_state(mod.advance(d, Step(S.FAILED, T + 3_000, reason="x"))), S.FAILED)
    c.refuses("a state that cannot follow", lambda: mod.advance(d, steps(mod)["OPEN"]), "OPEN cannot follow DECIDED")
    c.refuses("a settlement that jumps ahead", lambda: mod.advance(d, steps(mod)["SETTLED"]), "SETTLED cannot follow DECIDED")
    # the later states
    committed = build(mod, [*FULL[:4]])
    awaiting = build(mod, [*FULL[:5]])
    opened = build(mod, [*FULL[:6]])
    settling = build(mod, FULL)
    c.refuses(
        "awaiting without an eligible time",
        lambda: mod.advance(committed, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1)),
        "AWAITING_ORIGIN carries ['eligible_after_ms'] and may carry ['attested_at_ms', 'spread_ms', 'uncertainty_ms'], not []",
    )
    c.refuses(
        "eligible before the commitment less the token window",
        lambda: mod.advance(committed, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1, eligible_after_ms=COMMITTED_AT - 1_001)),
        "a slot cannot become eligible before its commitment less the token window",
    )
    c.eq(
        "eligible exactly at the edge of the token window",
        mod.current_state(mod.advance(committed, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1, eligible_after_ms=COMMITTED_AT - 1_000))),
        S.AWAITING_ORIGIN,
    )
    c.refuses(
        "opening without an origin",
        lambda: mod.advance(awaiting, Step(S.OPEN, ORIGIN)),
        "OPEN carries ['origin_ms'] and may carry [], not []",
    )
    c.refuses(
        "opening at the wrong origin",
        lambda: mod.advance(awaiting, Step(S.OPEN, ORIGIN + 60_000, origin_ms=ORIGIN + 60_000)),
        "the origin is the close of the first candle that closes after the eligible time",
    )
    c.refuses(
        "opening at an origin one minute early",
        lambda: mod.advance(awaiting, Step(S.OPEN, ORIGIN, origin_ms=ORIGIN - 60_000)),
        "the origin is the close of the first candle that closes after the eligible time",
    )
    c.refuses(
        "opening before the origin",
        lambda: mod.advance(awaiting, Step(S.OPEN, ORIGIN - 1, origin_ms=ORIGIN)),
        "a slot opens no earlier than its origin",
    )
    c.eq("opening at the origin", mod.current_state(mod.advance(awaiting, steps(mod)["OPEN"])), S.OPEN)
    c.eq("opening later than the origin", mod.current_state(mod.advance(awaiting, Step(S.OPEN, ORIGIN + 90_000, origin_ms=ORIGIN))), S.OPEN)
    on_boundary = mod.advance(committed, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1, eligible_after_ms=ORIGIN))
    c.refuses(
        "an eligible time on a candle close opens at the next close",
        lambda: mod.advance(on_boundary, Step(S.OPEN, ORIGIN, origin_ms=ORIGIN)),
        "the origin is the close of the first candle that closes after the eligible time",
    )
    c.eq(
        "and does open at the next",
        mod.current_state(mod.advance(on_boundary, Step(S.OPEN, ORIGIN + 60_000, origin_ms=ORIGIN + 60_000))),
        S.OPEN,
    )
    c.refuses(
        "settling one millisecond early",
        lambda: mod.advance(opened, Step(S.SETTLING, END - 1)),
        "a slot settles no earlier than its horizon after its origin",
    )
    c.eq("settling exactly at the horizon", mod.current_state(mod.advance(opened, Step(S.SETTLING, END))), S.SETTLING)
    c.refuses(
        "a settlement without an id",
        lambda: mod.advance(settling, Step(S.SETTLED, END + 1)),
        "SETTLED carries ['settlement_id'] and may carry [], not []",
    )
    c.refuses(
        "an ambiguous end for a directional slot",
        lambda: mod.advance(settling, steps(mod)["AMBIGUOUS"]),
        "only a barrier forecast can end ambiguous",
    )
    barrier_settling = build(mod, FULL, "B")
    c.eq("an ambiguous end for a barrier slot", mod.current_state(mod.advance(barrier_settling, steps(mod)["AMBIGUOUS"])), S.AMBIGUOUS)
    for reason in sorted(mod.VOID_REASONS - {"VOID_GAP"}):
        c.eq(f"a void for {reason}", mod.current_state(mod.advance(settling, Step(S.VOID, END + 1, reason=reason))), S.VOID)
    c.refuses(
        "a void for another reason",
        lambda: mod.advance(settling, Step(S.VOID, END + 1, reason="VOID_BORED")),
        "a void has a reason from ['VOID_DELISTED', 'VOID_GAP', 'VOID_OUTAGE', 'VOID_REDENOMINATION', 'VOID_TIE', 'VOID_TOKEN_EVENT']",
    )
    c.eq(
        "a void may name its settlement",
        mod.current_state(mod.advance(settling, Step(S.VOID, END + 1, reason="VOID_TIE", settlement_id="st-9"))),
        S.VOID,
    )
    for reason in sorted(mod.UNSETTLEABLE_REASONS):
        c.eq(
            f"unsettleable for {reason}",
            mod.current_state(mod.advance(settling, Step(S.UNSETTLEABLE, END + 1, reason=reason, settlement_id="st-9"))),
            S.UNSETTLEABLE,
        )
    c.refuses(
        "unsettleable for another reason",
        lambda: mod.advance(settling, Step(S.UNSETTLEABLE, END + 1, reason="UNSETTLEABLE_TIRED")),
        "an unsettleable forecast has a reason from ['UNSETTLEABLE_DISAGREE', 'UNSETTLEABLE_MISSING']",
    )
    c.eq(
        "a settlement id of 200 characters",
        mod.current_state(mod.advance(settling, Step(S.SETTLED, END + 1, settlement_id="s" * 200))),
        S.SETTLED,
    )
    c.refuses(
        "a settlement id that is not canonical text",
        lambda: mod.advance(settling, Step(S.SETTLED, END + 1, settlement_id="e\u0301")),
        "settlement_id is not valid canonical text: string is not NFC",
    )
    c.refuses(
        "a settlement id of 201 characters",
        lambda: mod.advance(settling, Step(S.SETTLED, END + 1, settlement_id="s" * 201)),
        "settlement_id is a non-empty string",
    )
    c.refuses(
        "a settlement id that is a number",
        lambda: mod.advance(settling, Step(S.SETTLED, END + 1, settlement_id=7)),
        "settlement_id is a non-empty string",
    )
    c.refuses(
        "an origin that is a boolean",
        lambda: mod.advance(awaiting, Step(S.OPEN, ORIGIN, origin_ms=True)),
        "origin_ms is a whole number of milliseconds",
    )
    c.refuses(
        "an eligible time that is negative",
        lambda: mod.advance(committed, Step(S.AWAITING_ORIGIN, COMMITTED_AT + 1, eligible_after_ms=-1)),
        "eligible_after_ms is a whole number of milliseconds",
    )
    c.refuses(
        "a commitment time that is a float",
        lambda: mod.advance(d, Step(S.COMMITTED, COMMITTED_AT, level=Level.C1, committed_at_ms=1.5)),
        "committed_at_ms is a whole number of milliseconds",
    )
    return c.bad


def check_idempotence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step = mod.State, mod.Step
    table = steps(mod)
    h = build(mod, FULL)
    c.eq("delivering any earlier step again changes nothing", all(mod.advance(h, table[name]) == h for name in FULL), True)
    d = build(mod, ["CREATED", "EVIDENCE_FROZEN"])
    c.refuses(
        "a different step into a state already entered",
        lambda: mod.advance(d, Step(S.EVIDENCE_FROZEN, T + 2_500)),
        "EVIDENCE_FROZEN was entered with other members at step 3",
        kind=mod.Conflict,
    )
    c.refuses(
        "a different commitment",
        lambda: mod.advance(build(mod, FULL), Step(S.COMMITTED, COMMITTED_AT, level=mod.Level.C2, committed_at_ms=COMMITTED_AT)),
        "COMMITTED was entered with other members at step 5",
        kind=mod.Conflict,
    )
    final = mod.advance(h, table["SETTLED"])
    c.eq("a repeat of the final step", mod.advance(final, table["SETTLED"]), final)
    c.refuses(
        "a second, different end",
        lambda: mod.advance(final, Step(S.SETTLED, END + 9_000, settlement_id="st-2")),
        "SETTLED was entered with other members at step 9",
        kind=mod.Conflict,
    )
    c.refuses("another end after the first", lambda: mod.advance(final, table["VOID"]), "VOID cannot follow SETTLED")
    c.refuses(
        "a failure after a commitment",
        lambda: mod.advance(build(mod, FULL[:4]), Step(S.FAILED, COMMITTED_AT + 5, reason="late excuse")),
        "FAILED cannot follow COMMITTED",
    )
    c.refuses(
        "a late failure after the price is known",
        lambda: mod.advance(build(mod, FULL), Step(S.FAILED_LOST, END + 5, reason="lost")),
        "FAILED_LOST cannot follow SETTLING",
    )
    c.refuses(
        "a failure after a failure",
        lambda: mod.advance(mod.advance(build(mod, ["CREATED"]), table["FAILED"]), table["FAILED_LOST"]),
        "FAILED_LOST cannot follow FAILED",
    )
    c.refuses(
        "a commitment after a no-call",
        lambda: mod.advance(build(mod, ["CREATED", "EVIDENCE_FROZEN", "DECIDED", "NO_CALL"]), table["COMMITTED"]),
        "COMMITTED cannot follow NO_CALL",
    )
    return c.bad


def check_levels(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step, L = mod.State, mod.Step, mod.Level
    table = steps(mod)
    before = build(mod, ["CREATED", "EVIDENCE_FROZEN", "DECIDED"])
    c.refuses(
        "a level before any commitment",
        lambda: mod.advance(before, Step(S.LEVEL, T + 3_100, level=L.C2)),
        "a level changes only after the slot has been committed or recorded as a no-call",
    )
    c.eq("no level yet", (mod.current_level(before), mod.level_at(before, T + 10**9)), (None, None))
    h = build(mod, FULL[:4])
    c.eq(
        "the level at commitment",
        (mod.current_level(h), mod.level_at(h, COMMITTED_AT), mod.level_at(h, COMMITTED_AT - 1)),
        (L.C1, L.C1, None),
    )
    up = mod.advance(h, Step(S.LEVEL, COMMITTED_AT + 5_000, level=L.C3))
    c.eq("raising", (mod.current_level(up), len(up), mod.current_state(up)), (L.C3, 6, S.COMMITTED))
    c.eq(
        "the level as of each instant",
        (mod.level_at(up, COMMITTED_AT + 4_999), mod.level_at(up, COMMITTED_AT + 5_000), mod.level_at(up, COMMITTED_AT + 10**9)),
        (L.C1, L.C3, L.C3),
    )
    c.eq("delivering the raise again", mod.advance(up, Step(S.LEVEL, COMMITTED_AT + 5_000, level=L.C3)), up)
    c.refuses(
        "the same level again", lambda: mod.advance(up, Step(S.LEVEL, COMMITTED_AT + 6_000, level=L.C3)), "the level is already that level"
    )
    c.refuses(
        "lowering without a reason",
        lambda: mod.advance(up, Step(S.LEVEL, COMMITTED_AT + 6_000, level=L.C2)),
        "lowering a level needs a reason: a downgrade is a record that says why",
    )
    down = mod.advance(up, Step(S.LEVEL, COMMITTED_AT + 6_000, level=L.C2, reason="signer certificate no longer trusted"))
    c.eq(
        "lowering with a reason",
        (mod.current_level(down), mod.level_at(down, COMMITTED_AT + 5_500), mod.level_at(down, COMMITTED_AT + 6_000)),
        (L.C2, L.C3, L.C2),
    )
    c.eq(
        "a level change at the instant of the last step",
        mod.current_level(mod.advance(down, Step(S.LEVEL, COMMITTED_AT + 6_000, level=L.C4))),
        L.C4,
    )
    c.refuses(
        "a level change earlier than the last step",
        lambda: mod.advance(down, Step(S.LEVEL, COMMITTED_AT + 5_999, level=L.C4)),
        "a step is not earlier than the one before it",
    )
    c.eq(
        "a raise may carry a reason",
        mod.current_level(mod.advance(h, Step(S.LEVEL, COMMITTED_AT + 5_000, level=L.C2, reason="public reference found"))),
        L.C2,
    )
    c.refuses(
        "a level change without a level",
        lambda: mod.advance(h, Step(S.LEVEL, COMMITTED_AT + 5_000)),
        "LEVEL carries ['level'] and may carry ['reason'], not []",
    )
    c.refuses(
        "a level change that carries more",
        lambda: mod.advance(h, Step(S.LEVEL, COMMITTED_AT + 5_000, level=L.C3, origin_ms=1)),
        "LEVEL carries ['level'] and may carry ['reason'], not ['level', 'origin_ms']",
    )
    nc = build(mod, ["CREATED", "EVIDENCE_FROZEN", "DECIDED", "NO_CALL"])
    c.eq("a no-call has a level and can change it", mod.current_level(mod.advance(nc, Step(S.LEVEL, T + 4_000, level=L.C3))), L.C3)
    c.eq("a level change leaves the state alone", mod.current_state(mod.advance(nc, Step(S.LEVEL, T + 4_000, level=L.C3))), S.NO_CALL)
    # the headline
    base = build(mod, FULL[:6])  # committed, awaiting, open; level C1
    c.eq("not eligible at C1", mod.headline_eligible(base), False)
    c3_in_time = mod.advance(build(mod, FULL[:5]), Step(S.LEVEL, ORIGIN - 1, level=L.C3))
    c3_in_time = mod.advance(c3_in_time, table["OPEN"])
    c.eq("eligible: C3 reached before the origin", mod.headline_eligible(c3_in_time), True)
    c3_at_origin = mod.advance(build(mod, FULL[:5]), Step(S.LEVEL, ORIGIN, level=L.C3))
    c.eq("eligible: C3 reached at the origin", mod.headline_eligible(mod.advance(c3_at_origin, table["OPEN"])), True)
    late = mod.advance(base, Step(S.LEVEL, ORIGIN + 1, level=L.C3))
    c.eq("not eligible: C3 reached after the origin", mod.headline_eligible(late), False)
    c4 = mod.advance(build(mod, FULL[:5]), Step(S.LEVEL, ORIGIN - 1, level=L.C4))
    c.eq("eligible at C4", mod.headline_eligible(mod.advance(c4, table["OPEN"])), True)
    c2 = mod.advance(build(mod, FULL[:5]), Step(S.LEVEL, ORIGIN - 1, level=L.C2))
    c.eq("not eligible at C2", mod.headline_eligible(mod.advance(c2, table["OPEN"])), False)
    dropped = mod.advance(c3_in_time, Step(S.LEVEL, ORIGIN + 10, level=L.C2, reason="token revoked"))
    c.eq("not eligible after a downgrade", mod.headline_eligible(dropped), False)
    c.eq("eligible again after a restoration", mod.headline_eligible(mod.advance(dropped, Step(S.LEVEL, ORIGIN + 20, level=L.C3))), True)
    c.eq(
        "not eligible before it opens",
        mod.headline_eligible(mod.advance(build(mod, FULL[:5]), Step(S.LEVEL, ORIGIN - 1, level=L.C3))),
        False,
    )
    c.eq("not eligible before it is committed", mod.headline_eligible(before), False)
    c.eq("a no-call is not a forecast", mod.headline_eligible(mod.advance(nc, Step(S.LEVEL, T + 4_000, level=L.C4))), False)
    c.eq(
        "a settled forecast keeps its eligibility",
        mod.headline_eligible(mod.advance(build(mod, [*FULL[:5]]), Step(S.LEVEL, ORIGIN - 1, level=L.C3)) and build(mod, FULL)),
        False,
    )
    return c.bad


def all_records(mod: types.ModuleType, history: Any) -> list[dict[str, canonical.JsonValue]]:
    return [mod.to_record(t, SRC, t.step.at_ms + 1) for t in history]


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step, L = mod.State, mod.Step, mod.Level
    h = build(mod, [*FULL, "SETTLED"], "B")
    h = mod.advance(h, Step(S.LEVEL, END + 6_000, level=L.C2))  # a raise that stays below C3, which may come after the attestation closes
    records = all_records(mod, h)
    for transition, record in zip(h, records, strict=True):
        again = mod.from_record(canonical.parse_record(canonical.dumps(record)))
        c.eq(f"step {transition.seq} reads back", again, transition)
        c.eq(f"step {transition.seq} has its id", record["id"], transition.transition_id)
    extra = build(mod, ["CREATED"])
    extra = mod.advance(extra, Step(S.FAILED_EVIDENCE, T + 2_000, reason="RYO unreachable", ref="cap-77"))
    voided = mod.advance(build(mod, FULL), Step(S.VOID, END + 1, reason="VOID_TIE", settlement_id="st-9"))
    for transition in (extra[-1], voided[-1]):
        rec = mod.to_record(transition, SRC, transition.step.at_ms)
        c.eq(f"{transition.step.state.value} reads back", mod.from_record(canonical.parse_record(canonical.dumps(rec))), transition)
    first = records[0]["body"]
    c.eq(
        "a scheduled body",
        first,
        {"forecast_id": FID, "seq": 1, "state": "SCHEDULED", "at": "2026-10-29T21:56:40.000Z", "contract": "B", "horizon_ms": HORIZON},
    )
    committed = records[4]["body"]
    c.eq(
        "a committed body",
        committed,
        {
            "forecast_id": FID,
            "seq": 5,
            "state": "COMMITTED",
            "at": "2026-10-29T21:56:44.000Z",
            "level": "C1",
            "committed_at": "2026-10-29T21:56:44.000Z",
        },
    )
    c.eq("an origin body", records[6]["body"]["origin"], "2026-10-29T21:57:00.000Z")
    c.eq("an eligible body", records[5]["body"]["eligible_after"], "2026-10-29T21:56:50.000Z")
    c.eq(
        "a settled body",
        records[8]["body"],
        {"forecast_id": FID, "seq": 9, "state": "SETTLED", "at": "2026-10-30T01:57:05.000Z", "settlement_id": "st-1"},
    )
    c.eq(
        "a level body",
        records[9]["body"],
        {"forecast_id": FID, "seq": 10, "state": "LEVEL", "at": "2026-10-30T01:57:06.000Z", "level": "C2"},
    )
    c.eq("the envelope", {k: records[0][k] for k in ("spec", "type", "type_version", "source", "created_at", "refs", "privacy")},
         {"spec": "hoshitori.record/1", "type": "forecast_state", "type_version": 1, "source": SRC, "created_at": "2026-10-29T21:56:40.001Z", "refs": [], "privacy": "public"})  # fmt: skip
    ledger_copy = {**records[1], "committed_at": "2026-10-29T21:56:50.000Z"}
    c.eq("the ledger's copy is read", mod.from_record(ledger_copy), h[1])
    c.refuses("a negative creation time", lambda: mod.to_record(h[0], SRC, -1), "the creation time is a whole number of milliseconds")
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(h[0], "scheduler", T),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )

    def changed(record: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
        out = copy.deepcopy(record)
        node = out
        for key in path[:-1]:
            node = node[key]
        if value is _GONE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return out

    sched, comm, level = records[0], records[4], records[9]
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("an unknown envelope member", changed(sched, ("extra",), 1), "the envelope: unknown envelope members: ['extra']"),
        ("another type", changed(sched, ("type",), "forecast"), "not a forecast-state record of a version this reader knows"),
        ("another version", changed(sched, ("type_version",), 2), "not a forecast-state record of a version this reader knows"),
        ("a body that is not an object", changed(sched, ("body",), []), "the envelope: body must be an object"),
        ("a slot id that is a number", changed(sched, ("body", "forecast_id"), 5), "a step has a slot id and a position of one or more"),
        ("a position of zero", changed(sched, ("body", "seq"), 0), "a step has a slot id and a position of one or more"),
        ("a position that is a boolean", changed(sched, ("body", "seq"), True), "a step has a slot id and a position of one or more"),
        ("a position that is text", changed(sched, ("body", "seq"), "1"), "a step has a slot id and a position of one or more"),
        ("a state that is a number", changed(sched, ("body", "state"), 1), "a step has a known state"),
        ("a state that is unknown", changed(sched, ("body", "state"), "RETIRED"), "a step has a known state"),
        ("a state in lower case", changed(sched, ("body", "state"), "scheduled"), "a step has a known state"),
        ("a time without milliseconds", changed(sched, ("body", "at"), "2026-10-29T21:56:40Z"), "at is written as UTC with milliseconds"),
        (
            "a time that is nonsense",
            changed(sched, ("body", "at"), "yesterday"),
            "at: not an RFC 3339 timestamp with an explicit offset: 'yesterday'",
        ),
        ("a time that is a number", changed(sched, ("body", "at"), 5), "at is an RFC 3339 string"),
        ("a time that is missing", changed(sched, ("body", "at"), _GONE), "at is an RFC 3339 string"),
        ("a member the state does not carry", changed(sched, ("body", "reason"), "x"), "SCHEDULED does not carry ['reason']"),
        (
            "a member that is missing",
            changed(comm, ("body", "level"), _GONE),
            "COMMITTED carries ['committed_at_ms', 'level'] and may carry [], not ['committed_at_ms']",
        ),
        ("a time member in milliseconds", changed(comm, ("body", "committed_at"), 1793311004000), "committed_at is an RFC 3339 string"),
        ("a level that is unknown", changed(comm, ("body", "level"), "C9"), "a level is C0 to C4"),
        ("a level that is a number", changed(comm, ("body", "level"), 3), "a level is C0 to C4"),
        ("a horizon that is text", changed(sched, ("body", "horizon_ms"), "4"), "horizon_ms is a whole number"),
        ("a horizon that is a boolean", changed(sched, ("body", "horizon_ms"), True), "horizon_ms is a whole number"),
        ("a text member that is a number", changed(level, ("body", "reason"), 5), "reason is text"),
        ("an id of another step", changed(sched, ("id",), "ftAAAAAAAAAAAAAAAAAAAAAAAAAA"), "the id is not the id of this step"),
        ("an id of another position", changed(sched, ("body", "seq"), 2), "the id is not the id of this step"),
    ]
    for label, record, says in cases:
        c.refuses(label, lambda r=record: mod.from_record(r), says)
    return c.bad


_GONE = object()


def check_rebuild(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    S, Step = mod.State, mod.Step
    a = build(mod, [*FULL, "SETTLED"])
    other = mod.begin("fc-other", "D", HORIZON, T)
    other = mod.advance(other, Step(S.CREATED, T + 1))
    recs_a = all_records(mod, a)
    recs_b = all_records(mod, other)
    mixed = [recs_a[0], recs_b[0], recs_a[1], recs_b[1], *recs_a[2:]]
    built = mod.rebuild(mixed)
    c.eq("two interleaved slots", built, {FID: a, "fc-other": other})
    c.eq("a step delivered twice counts once", mod.rebuild([*mixed, recs_a[3], recs_a[0], recs_b[1]]), built)
    c.eq("an empty ledger", mod.rebuild([]), {})
    c.eq("a long history survives a rebuild", mod.rebuild(recs_a)[FID], a)
    again = mod.to_record(mod.Transition(FID, 3, h_second(mod)), SRC, T)
    c.refuses(
        "the same step again under a later position", lambda: mod.rebuild([*recs_a[:2], again]), "a step repeated under another position"
    )
    c.refuses("a gap", lambda: mod.rebuild([recs_a[0], recs_a[2]]), "step 3 of fc-test-slot arrived when 1 were known")
    c.refuses("a reorder", lambda: mod.rebuild([recs_a[0], recs_a[2], recs_a[1]]), "step 3 of fc-test-slot arrived when 1 were known")
    c.refuses("a start that is not the schedule", lambda: mod.rebuild([recs_a[1]]), "step 2 of fc-test-slot arrived when 0 were known")
    first = mod.to_record(mod.Transition(FID, 1, Step(S.CREATED, T)), SRC, T)
    c.refuses("a first step that is not scheduled", lambda: mod.rebuild([first]), "a slot's first step is SCHEDULED")
    illegal = mod.to_record(mod.Transition(FID, 2, Step(S.OPEN, ORIGIN, origin_ms=ORIGIN)), SRC, T)
    c.refuses("a step the rules would have refused", lambda: mod.rebuild([recs_a[0], illegal]), "OPEN cannot follow SCHEDULED")
    clash = mod.to_record(mod.Transition(FID, 2, Step(S.CREATED, T + 999)), SRC, T)
    c.refuses(
        "another step in a position that is taken",
        lambda: mod.rebuild([*recs_a[:2], clash]),
        "step 2 of fc-test-slot arrived when 2 were known",
    )
    c.refuses(
        "a damaged record", lambda: mod.rebuild([{**recs_a[0], "type": "x"}]), "not a forecast-state record of a version this reader knows"
    )
    return c.bad


def h_second(mod: types.ModuleType) -> Any:
    """The CREATED step, to be delivered again under a later position."""
    return steps(mod)["CREATED"]


def on_disk(mod: types.ModuleType, history: Any, step: Any) -> list[dict[str, canonical.JsonValue]]:
    """The records of a history and of one more step after it, as a ledger would hold them had the step been written."""
    extra = mod.Transition(history[0].forecast_id, len(history) + 1, step)
    return [*all_records(mod, history), mod.to_record(extra, SRC, step.at_ms + 1)]


def refused_both_ways(c: Case, label: str, history: Any, step: Any, says: str) -> None:
    """One rule: a step that advance refuses is refused, with the same words, when rebuild reads records that say it was taken."""
    c.refuses(label, lambda: c.mod.advance(history, step), says)
    c.refuses(f"{label}, read back from the ledger", lambda: c.mod.rebuild(on_disk(c.mod, history, step)), says)


def check_settlement_reasons(mod: types.ModuleType) -> list[str]:
    """A tie and a disagreement of the two oracles are results of the settlement (contract sections 2 and 7; section 17: SETTLING reads
    the outcome candle and the second oracle), so only a settling slot ends with them, and a barrier forecast never ends in a tie
    (section 3). Where the other reasons may come from the contract does not say, so they are taken from every state the table allows,
    as before. The product writes VOID_GAP from AWAITING_ORIGIN (league) and every other end from SETTLING (settle_job)."""
    c = Case(mod)
    S, Step = mod.State, mod.Step
    early = {  # a slot in each state before the settlement, and a time at which an end might be asked of it
        "at the commitment": (FULL[:4], COMMITTED_AT + 500),
        "before the origin": (FULL[:5], COMMITTED_AT + 2_000),
        "five minutes into the window": (FULL[:6], ORIGIN + 5 * 60_000),
    }
    tie, disagree, gap = "VOID_TIE", "UNSETTLEABLE_DISAGREE", "VOID_GAP"
    gap_says = f"{gap} is decided before the origin is set, so a slot that has opened cannot end with it"
    for contract in ("D", "B"):
        tie_says = "only a directional forecast can end in a tie"
        if contract == "D":
            tie_says = f"{tie} is a result of the settlement, so only a settling slot can end with it"
        for label, (path, at) in early.items():
            history = build(mod, path, contract)
            refused_both_ways(c, f"{contract}: a tie {label}", history, Step(S.VOID, at, reason=tie), tie_says)
            if path != FULL[:4]:  # an unsettleable end cannot follow the commitment at all
                refused_both_ways(
                    c,
                    f"{contract}: a disagreement {label}",
                    history,
                    Step(S.UNSETTLEABLE, at, reason=disagree, settlement_id="st-9"),
                    f"{disagree} is a result of the settlement, so only a settling slot can end with it",
                )
            for reason in sorted(mod.VOID_REASONS - {tie}):
                if reason == gap and label == "five minutes into the window":
                    refused_both_ways(c, f"{contract}: a gap {label}", history, Step(S.VOID, at, reason=gap), gap_says)
                    continue
                c.eq(
                    f"{contract}: {reason} {label}, as before",
                    mod.current_state(mod.advance(history, Step(S.VOID, at, reason=reason))),
                    S.VOID,
                )
            if path != FULL[:4]:
                c.eq(
                    f"{contract}: UNSETTLEABLE_MISSING {label}, as before",
                    mod.current_state(mod.advance(history, Step(S.UNSETTLEABLE, at, reason="UNSETTLEABLE_MISSING"))),
                    S.UNSETTLEABLE,
                )
        settling = build(mod, FULL, contract)
        if contract == "D":
            for members in ({}, {"settlement_id": "st-9"}):
                ended = mod.advance(settling, Step(S.VOID, END + 1, reason=tie, **members))
                c.eq(f"D: a tie at the settlement, with {sorted(members)}", mod.current_state(ended), S.VOID)
        else:
            refused_both_ways(c, "B: a tie at the settlement", settling, Step(S.VOID, END + 1, reason=tie, settlement_id="st-9"), tie_says)
        for reason in sorted(mod.UNSETTLEABLE_REASONS):
            c.eq(
                f"{contract}: {reason} at the settlement",
                mod.current_state(mod.advance(settling, Step(S.UNSETTLEABLE, END + 1, reason=reason, settlement_id="st-9"))),
                S.UNSETTLEABLE,
            )
        refused_both_ways(c, f"{contract}: a gap at the settlement", settling, Step(S.VOID, END + 1, reason=gap), gap_says)
        for reason in sorted(mod.VOID_REASONS - {tie, gap}):
            c.eq(
                f"{contract}: {reason} at the settlement",
                mod.current_state(mod.advance(settling, Step(S.VOID, END + 1, reason=reason))),
                S.VOID,
            )
    return c.bad


def check_late_levels(mod: types.ModuleType) -> list[str]:
    """The attestation phase closes 90 seconds after the commitment at the latest, and a token that arrives after the close changes
    neither the origin nor the level (contract section 2), so a forecast that has not reached C3 by then never does. What the contract
    does not settle is taken as before: a raise that stays below C3, a raise of a forecast that had reached C3 (C4, or C3 again after a
    downgrade), and the level of a no-call, whose history has no commitment time."""
    c = Case(mod)
    S, Step, L = mod.State, mod.Step, mod.Level
    close = COMMITTED_AT + 90_000
    says = "a forecast reaches C3 by the close of its attestation phase or not at all: a later token changes no level"
    committed = build(mod, FULL[:4])  # committed at C1
    c.eq("a raise to C3 at the close", mod.current_level(mod.advance(committed, Step(S.LEVEL, close, level=L.C3))), L.C3)
    refused_both_ways(c, "a raise to C3 a millisecond after the close", committed, Step(S.LEVEL, close + 1, level=L.C3), says)
    refused_both_ways(c, "a raise to C4 after the close", committed, Step(S.LEVEL, close + 1, level=L.C4), says)
    refused_both_ways(c, "a raise to C3 once the slot is open", build(mod, FULL[:6]), Step(S.LEVEL, close + 1, level=L.C3), says)
    at_c2 = mod.advance(build(mod, FULL[:3]), Step(S.COMMITTED, COMMITTED_AT, level=L.C2, committed_at_ms=COMMITTED_AT))
    ten = COMMITTED_AT + 600_000
    refused_both_ways(
        c, "a raise from C2 to C3 ten minutes after the commitment, before the origin", at_c2, Step(S.LEVEL, ten, level=L.C3), says
    )
    origin = timeline.origin(ten)
    opened = mod.advance(mod.advance(at_c2, Step(S.AWAITING_ORIGIN, ten, eligible_after_ms=ten)), Step(S.OPEN, origin, origin_ms=origin))
    refused_both_ways(c, "and at its origin", opened, Step(S.LEVEL, origin, level=L.C3), says)
    c.eq("so it never counts towards a headline", mod.headline_eligible(opened), False)
    # as before
    c.eq("a raise to C2 after the close", mod.current_level(mod.advance(committed, Step(S.LEVEL, close + 1, level=L.C2))), L.C2)
    reached = mod.advance(committed, Step(S.LEVEL, close, level=L.C3))
    c.eq(
        "a raise to C4 after the close, having reached C3",
        mod.current_level(mod.advance(reached, Step(S.LEVEL, close + 1, level=L.C4))),
        L.C4,
    )
    lowered = mod.advance(reached, Step(S.LEVEL, close + 1, level=L.C2, reason="a signer certificate is no longer trusted"))
    c.eq("a lowering after the close", mod.current_level(lowered), L.C2)
    c.eq("C3 again after the close, having reached it", mod.current_level(mod.advance(lowered, Step(S.LEVEL, close + 2, level=L.C3))), L.C3)
    recorded_late = mod.advance(build(mod, FULL[:3]), Step(S.COMMITTED, close + 5_000, level=L.C3, committed_at_ms=COMMITTED_AT))
    c.eq("a commitment recorded after the close keeps the level it reached", mod.current_level(recorded_late), L.C3)
    c.eq("and may rise to C4", mod.current_level(mod.advance(recorded_late, Step(S.LEVEL, close + 6_000, level=L.C4))), L.C4)
    no_call = build(mod, ["CREATED", "EVIDENCE_FROZEN", "DECIDED", "NO_CALL"])
    c.eq("a no-call's level after a day", mod.current_level(mod.advance(no_call, Step(S.LEVEL, T + 86_400_000, level=L.C3))), L.C3)
    return c.bad


def mix(mod: types.ModuleType) -> list[Any]:
    """A population with one of every end, for the denominator."""
    S, Step = mod.State, mod.Step
    table = steps(mod)
    out = []

    def slot(name: str, contract: str, path: list[str], last: Any = None) -> Any:
        h = mod.begin(name, contract, HORIZON, T)
        for item in path:
            h = mod.advance(h, table[item])
        return h if last is None else mod.advance(h, last)

    out.append(slot("s-flight0", "D", []))
    out.append(slot("s-flight1", "D", ["CREATED"]))
    out.append(slot("s-flight2", "D", ["CREATED", "EVIDENCE_FROZEN"]))
    out.append(slot("s-flight3", "D", ["CREATED", "EVIDENCE_FROZEN", "DECIDED"]))
    out.append(slot("s-nocall", "D", ["CREATED", "EVIDENCE_FROZEN", "DECIDED", "NO_CALL"]))
    out.append(slot("s-failed", "D", ["CREATED"], Step(S.FAILED, T + 2_000, reason="x")))
    out.append(slot("s-fe1", "D", ["CREATED"], Step(S.FAILED_EVIDENCE, T + 2_000, reason="x")))
    out.append(slot("s-fe2", "D", [], Step(S.FAILED_EVIDENCE, T + 2_000, reason="x")))
    out.append(slot("s-late", "D", ["CREATED", "EVIDENCE_FROZEN"], Step(S.FAILED_LATE, T + 2_500, reason="x")))
    out.append(slot("s-stale", "D", ["CREATED", "EVIDENCE_FROZEN", "DECIDED"], Step(S.FAILED_STALE, T + 3_100, reason="x")))
    out.append(slot("s-lost", "D", [], Step(S.FAILED_LOST, T + 100, reason="x")))
    out.append(slot("s-committed", "D", FULL[:4]))
    out.append(slot("s-open", "D", FULL[:6]))
    out.append(slot("s-settling", "D", FULL))
    out.append(slot("s-settled", "D", [*FULL, "SETTLED"]))
    out.append(slot("s-amb", "B", [*FULL, "AMBIGUOUS"]))
    out.append(slot("s-void", "D", FULL[:4], Step(S.VOID, COMMITTED_AT + 5, reason="VOID_GAP")))
    out.append(slot("s-unset", "D", FULL, Step(S.UNSETTLEABLE, END + 1, reason="UNSETTLEABLE_MISSING")))
    return out


def check_denominator(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    d = mod.denominator(mix(mod))
    c.eq(
        "counts",
        (d.scheduled, d.in_flight, d.pending, d.settled, d.ambiguous, d.void, d.unsettleable, d.no_calls),
        (18, 4, 3, 1, 1, 1, 1, 1),
    )
    c.eq("failures by class", (d.failed, d.failed_evidence, d.failed_late, d.failed_stale, d.failed_lost), (1, 2, 1, 1, 1))
    c.eq("derived", (d.forecasts, d.failures), (7, 6))
    c.eq("the parts add up", d.scheduled, d.in_flight + d.forecasts + d.no_calls + d.failures)
    c.eq("coverage inputs", d.coverage_inputs(), (7, 1, 6))
    e = mod.denominator([])
    c.eq("nothing scheduled", (e.scheduled, e.forecasts, e.failures, e.coverage_inputs()), (0, 0, 0, (0, 0, 0)))
    c.eq("one slot", mod.denominator([mix(mod)[0]]).in_flight, 1)
    slots = mix(mod)
    c.refuses("a slot counted twice", lambda: mod.denominator([slots[0], slots[0]]), "a slot is counted once")
    c.refuses(
        "parts that do not add up",
        lambda: mod.Denominator(5, 1, 1, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0),
        "the parts of the denominator do not add up to the slots scheduled",
    )
    c.eq("parts that do", mod.Denominator(4, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0).forecasts, 2)
    c.eq("a void is still a call made", mod.denominator([slots[16]]).forecasts, 1)
    c.eq("a no-call is not a call", mod.denominator([slots[4]]).forecasts, 0)
    c.eq("a failure is not a no-call", mod.denominator([slots[5]]).no_calls, 0)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_begin,
        check_member_rules,
        check_attested_origin,
        check_idempotence,
        check_levels,
        check_records,
        check_rebuild,
        check_settlement_reasons,
        check_late_levels,
        check_denominator,
        check_every_path,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
