"""The validation harness of the impact ranking: every check passes on the real module and every small change of it is noticed or listed."""

from __future__ import annotations

from hoshitori import impact_validation
from tests.impact_validation_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(impact_validation) == []


EQUIVALENT: dict[str, str] = {
    "compare|Gt->GtE|ours > theirs|0": "equal scores were already counted as ties by the test before it, so the two comparisons agree",
    "sign_test_p|int+1|0|0": "with one differing case the two-sided p is 1 either way, so testing for n == 1 instead of n == 0 gives the same value",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.impact_validation", "tests.impact_validation_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
