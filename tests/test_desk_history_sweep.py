"""Every small change of ``hoshitori.desk_history`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "|Add->Sub|_ONSET.sigma_gap + _ONSET.lookback|0": "the market test's need (570 minutes) is larger than the onset test's (480), so the maximum ignores this term",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_history", "tests.desk_history_checks")
    assert total > 15, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
