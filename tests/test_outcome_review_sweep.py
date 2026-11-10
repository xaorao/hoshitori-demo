"""Every small change of ``hoshitori.outcome_review`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "build|Gt->GtE|chance > 50|0": "a chance of exactly 50 is answered `neither` before this comparison is reached",
    "build|Gt->GtE|market > 0|0": "a BTC move of exactly zero fails the size test beside it, since the asset's move is not zero",
    "build|Gt->GtE|move > 0|0": "a move of zero is left out of the reviews before this line, so the move is never zero here",
    "build|Gt->GtE|move > 0|1": "a move of zero is left out of the reviews before this line, so the move is never zero here",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.outcome_review", "tests.outcome_review_checks")
    assert total > 80, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
