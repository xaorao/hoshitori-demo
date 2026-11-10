"""Every small change of ``hoshitori.whales`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "cohort|Gt->GtE|x > 0|0": "a position of zero notional is left out of `held` first, so a zero never reaches this test",
    "cohort|Lt->LtE|x < 0|0": "a position of zero notional is left out of `held` first, so a zero never reaches this test",
    "cohort|int+1|0|5": "the start of `max` is read only when no one holds the coin, and then the gross is zero and the share is not computed",
    "parse_state|GtE->Gt|size >= 0|0": "a size of zero has a notional of zero, which adds the same whichever side it is put on",
    "parse_state|str+~|'utf-8'|0": "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.whales", "tests.whales_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
