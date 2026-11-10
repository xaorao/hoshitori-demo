"""Scoring of settled forecasts: every check passes on the real module, and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import scoring
from tests.mutation_sweep import survivors
from tests.scoring_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(scoring) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_ratio_extreme|int+1|0|2": "the first trial ratio of the fractional program: any start reaches the same optimum, only the number of rounds changes",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.scoring", "tests.scoring_checks")
    assert total > 250, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
