"""Every small change of ``hoshitori.desk_live`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "calibration_for|int+1|8|0": "the cache holds the windows of one to twenty-four hours; one more slot changes no answer, only memory",
    "asset_for|And->Or|joined.ok and joined.asset is not None|0": "a join that is OK always carries its asset (the registry returns no asset only with another code), so the second condition only narrows the type",
    "history_port.history|int+1|1|0": "one more minute is read before the window; the bar before the first return is the only one used and it is still read",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_live", "tests.desk_live_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
