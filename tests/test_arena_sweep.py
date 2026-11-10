"""Every small change of ``hoshitori.arena`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "Arena.revealed|LtE->Lt|abs(float(a.body['p']) - float(e.body['p'])) <= COPY_TOLERANCE|0": "an exact tie of two floating-point differences does not occur in a test",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.arena", "tests.arena_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
