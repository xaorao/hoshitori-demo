"""The slot lifecycle: every check passes on the real module, every small change of its code is noticed, and random walks keep its laws."""

from __future__ import annotations

import itertools
import random

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import lifecycle
from tests.lifecycle_checks import COMMITTED_AT, END, FID, HORIZON, ORIGIN, SRC, T, failures, steps
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(lifecycle) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|History = tuple[Transition, ...]|0": "a type alias that only the type checker reads",
    "_need|statement dropped|raise LifecycleError('a member the history needs is missing')|0": (
        "a guard for the type checker: every member it protects is required by its state's rule or follows from the order of states"
    ),
    "_change_level|Lt->LtE|level.rank < now.rank|0": "an equal level has already been refused, so a lower one is the only case that reaches the comparison",
    "_text|str+~|'v'|0": "the name of the one member of the throw-away record that the canonical profile checks the text with",
    "_change_level|int+1|0|0": "every step of a history names the same slot, so which one is read makes no difference",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.lifecycle", "tests.lifecycle_checks")
    assert total > 400, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def pool() -> list[lifecycle.Step]:
    S, Level = lifecycle.State, lifecycle.Level
    out = list(steps(lifecycle).values())
    for index, level in enumerate(lifecycle.Level):
        for offset in (0, 1_000, 5_000, 20_000):
            out.append(lifecycle.Step(lifecycle.State.LEVEL, COMMITTED_AT + offset + index, level=level, reason="walk"))
    out.append(lifecycle.Step(lifecycle.State.LEVEL, END + 100, level=lifecycle.Level.C4))
    # ends whose reason only a settlement gives, asked of a slot at the settlement and five minutes into its window
    for at in (END + 1_000, ORIGIN + 300_000):
        out.append(lifecycle.Step(S.VOID, at, reason="VOID_TIE"))
        out.append(lifecycle.Step(S.UNSETTLEABLE, at, reason="UNSETTLEABLE_DISAGREE", settlement_id="st-2"))
    # raises at the close of the attestation phase and just after it
    out += [lifecycle.Step(S.LEVEL, COMMITTED_AT + 90_000 + late, level=level) for late in (0, 1) for level in (Level.C3, Level.C4)]
    return out


POOL = pool()


def assert_laws(history: lifecycle.History, contract: str) -> None:
    states = [t.step.state for t in history if t.step.state is not lifecycle.State.LEVEL]
    assert len(states) == len(set(states)), "a state was entered twice"
    assert [t.seq for t in history] == list(range(1, len(history) + 1))
    assert all(a.step.at_ms <= b.step.at_ms for a, b in itertools.pairwise(history)), "time went backwards"
    if lifecycle.State.COMMITTED in states:
        assert not any(state in lifecycle.FAILURES or state is lifecycle.State.NO_CALL for state in states), (
            "a committed forecast was abandoned"
        )
    ends = [state for state in states if state in lifecycle.TERMINAL]
    assert len(ends) <= 1, "more than one end"
    if ends:
        assert states[-1] is ends[0], "something happened after the end"
    if contract == "D":
        assert lifecycle.State.AMBIGUOUS not in states
    # the reason of an end fits the state it came from: a tie (contract D only) and a disagreement only from SETTLING
    for before, after in itertools.pairwise(states):
        if (after, history_reason(history, after)) in lifecycle.SETTLEMENT_ONLY:
            assert before is lifecycle.State.SETTLING, f"{after.value} came from {before.value}"
            assert contract == "D" or after is not lifecycle.State.VOID, "a barrier forecast ended in a tie"
    # a committed forecast that had not reached C3 by the close of its attestation phase never reaches it
    committed = next((t.step for t in history if t.step.state is lifecycle.State.COMMITTED), None)
    first = next((t.step for t in history if t.step.level is not None and t.step.level.independent), None)
    if committed is not None and first is not None and first.state is lifecycle.State.LEVEL:
        assert first.at_ms <= (committed.committed_at_ms or 0) + 90_000, "C3 was first reached after the attestation closed"
    # what is on disk says what the rules would have said
    records = [lifecycle.to_record(t, SRC, t.step.at_ms) for t in history]
    assert lifecycle.rebuild(records) == {FID: history}
    counted = lifecycle.denominator([history])
    assert counted.scheduled == 1
    assert counted.in_flight + counted.forecasts + counted.no_calls + counted.failures == 1


def history_reason(history: lifecycle.History, state: lifecycle.State) -> str | None:
    """The reason the step that entered ``state`` carried."""
    return next(t.step.reason for t in history if t.step.state is state)


def assert_probes(history: lifecycle.History, contract: str) -> None:
    """Ask a walked history for the steps the contract allows in few states, and check that each is taken only where it may be: an end
    that only a settlement gives only from SETTLING (and a tie only for contract D), and a level of C3 or more after the close of the
    attestation phase only if the forecast had reached C3 before."""
    S, last = lifecycle.State, history[-1].step.at_ms
    state = lifecycle.current_state(history)
    for probe in (lifecycle.Step(S.VOID, last, reason="VOID_TIE"), lifecycle.Step(S.UNSETTLEABLE, last, reason="UNSETTLEABLE_DISAGREE")):
        if _accepts(history, probe):
            assert state is S.SETTLING, f"{probe.reason} was taken from {state.value}"
            assert contract == "D" or probe.state is not S.VOID, "a barrier forecast ended in a tie"
    committed = next((t.step for t in history if t.step.state is S.COMMITTED), None)
    if committed is not None:
        after_close = max(last, (committed.committed_at_ms or 0) + 90_001)
        for level in (lifecycle.Level.C3, lifecycle.Level.C4):
            if _accepts(history, lifecycle.Step(S.LEVEL, after_close, level=level)):
                assert any(t.step.level is not None and t.step.level.independent for t in history), "C3 was first reached after the close"


def walk(history: lifecycle.History, step: lifecycle.Step) -> lifecycle.History:
    before = history
    try:
        history = lifecycle.advance(history, step)
    except lifecycle.LifecycleError:
        return before
    # accepted: either nothing changed (a repeat) or exactly one step was added to the end
    assert history == before or (history[:-1] == before and history[-1].step == step and history[-1].seq == len(before) + 1)
    return history


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.integers(min_value=0, max_value=len(POOL) - 1), max_size=40), st.sampled_from(["D", "B"]))
def test_random_walks_keep_the_laws(picks: list[int], contract: str) -> None:
    history = lifecycle.begin(FID, contract, HORIZON, T)
    for index in picks:
        history = walk(history, POOL[index])
    assert_laws(history, contract)
    assert_probes(history, contract)


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.randoms(use_true_random=False), st.sampled_from(["D", "B"]), st.integers(min_value=0, max_value=16))
def test_guided_walks_keep_the_laws(rng: random.Random, contract: str, length: int) -> None:
    """Walks that mostly choose a step the rules allow, so that they get deep into the life of a slot; now and then a wrong one."""
    history = lifecycle.begin(FID, contract, HORIZON, T)
    for _ in range(length):
        allowed = [step for step in POOL if _accepts(history, step)]
        pick = rng.choice(allowed) if allowed and rng.random() < 0.85 else rng.choice(POOL)
        history = walk(history, pick)
        assert_probes(history, contract)
    assert_laws(history, contract)


def test_guided_walks_end_every_way() -> None:
    """The walks above are only worth something if they get to every end, so check that a fixed set of them does."""
    rng = random.Random(29102026)
    ends: set[lifecycle.State] = set()
    for contract in ("D", "B"):
        for _ in range(1500):
            history = lifecycle.begin(FID, contract, HORIZON, T)
            for _ in range(rng.randrange(1, 14)):
                allowed = [s for s in POOL if _accepts(history, s)]
                if not allowed:
                    break
                onward = [s for s in allowed if s.state is not lifecycle.State.LEVEL and s.state not in lifecycle.TERMINAL]
                states_only = [s for s in allowed if s.state is not lifecycle.State.LEVEL]
                history = walk(history, rng.choice(onward if onward and rng.random() < 0.93 else states_only or allowed))
            state = lifecycle.current_state(history)
            if state in lifecycle.TERMINAL:
                ends.add(state)
    assert ends == set(lifecycle.TERMINAL)


def _accepts(history: lifecycle.History, step: lifecycle.Step) -> bool:
    """Whether the step is allowed and changes something (a step already taken is accepted but is a repeat)."""
    try:
        return lifecycle.advance(history, step) != history
    except lifecycle.LifecycleError:
        return False
