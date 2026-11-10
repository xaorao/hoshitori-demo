"""Every small change of ``hoshitori.desk_model`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "|int+1|28|0": "the evidence-id pattern `ev[a-z2-7]{26}` already fixes the length at 28, so the schema's own length bound is never the deciding check",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_model", "tests.desk_model_checks")
    assert total > 20, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
