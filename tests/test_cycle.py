"""The cycle state machine: every check passes on the real module, every small change is noticed, and random histories keep the laws."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import cycle
from tests.cycle_checks import FULL, PH, SRC, T, all_records, build, failures, steps
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(cycle) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|History = tuple[Transition, ...]|0": "a type alias that only the type checker reads",
    "_check_members|str+~|'v'|0": "the name of the one member of the throw-away record that the canonical profile checks the text with",
    "_cycle_of|int+1|0|0": "every step of a history carries the same cycle id, so any step names it",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.cycle", "tests.cycle_checks")
    assert total > 300, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


NAMES = [*steps(cycle)]


@settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.sampled_from(NAMES), max_size=24))
def test_random_step_sequences_keep_the_laws(sequence: list[str]) -> None:
    table = steps(cycle)
    history = build(cycle, [])
    for name in sequence:
        try:
            after = cycle.advance(history, table[name])
        except cycle.CycleError:
            continue
        assert after[: len(history)] == history, "history is only ever extended"
        history = after
    states = [t.step.state for t in history]
    assert len(set(states)) == len(states), "no state twice"
    order = [s for s in cycle.ORDER if s in states]
    assert [s for s in states if s is not cycle.State.ABANDONED] == order, "states keep their order"
    assert all(s not in cycle.TERMINAL for s in states[:-1]), "nothing follows the end"
    assert [t.seq for t in history] == list(range(1, len(history) + 1))
    # the ledger copy rebuilds the same history
    rebuilt = cycle.rebuild(all_records(cycle, history))
    assert rebuilt == {history[0].cycle_id: history}
    assert history[0].cycle_id == cycle.cycle_id(PH, T)
    assert SRC and FULL
