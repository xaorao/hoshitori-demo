"""View models: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import view_models
from tests.mutation_sweep import survivors
from tests.view_models_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(view_models) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_due_slots|GtE->Gt|earliest >= protocol.schedule_start_ms|0": "when the first cycle sits exactly on the start the range of earlier slots is empty either way",
    "_comparisons|Add->Sub|opened + made.common.horizon_ms|0": "every call of a comparison has the same horizon, so origin minus it keeps the same order and the same equality",
    "_record|str+~|'all'|1": "the regime of an excluded call is a label the bounds do not read, so any non-empty text does the same",
    "_record|str+~|'all'|0": "the regime of a directional call is a label the summary carries but no figure of the record reads, so any non-empty text does the same",
    "_record|True<->False|True|0": "the scores and their calls are built from the same list, so a strict zip has nothing to refuse",
    "_challenge|str+~|'failed_role'|0": "a run record that names a failed role holds no disagreement (the record refuses both), so the disagreement test gives the same answer",
    "_slots|statement dropped|continue|0": "a candidate the manifest excluded has no slot; skipping it only saves enumerating ids that no history uses",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.view_models", "tests.view_models_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
