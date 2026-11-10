"""Arm L1x: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import arm_l1x
from tests.arm_l1x_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(arm_l1x) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "aggregate|int+1|0|0": "every sample of one control is for the same candidate and horizon, so any of them can supply those two fields",
    "decide|int+1|0|2": "the usable proposals are tested for being all equal to one of them, so which one is compared against does not matter",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.arm_l1x", "tests.arm_l1x_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
