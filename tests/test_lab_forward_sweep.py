"""Every small change of ``hoshitori.lab_forward`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
PRECISION = (
    "sixty-one digits of working precision instead of sixty change no result in the model's range: no amount reaches the sixtieth digit"
)
EQUIVALENT: dict[str, str] = {
    "Summary|True<->False|True|0": FROZEN,
    "Trade|True<->False|True|0": FROZEN,
    "_Held|True<->False|True|0": FROZEN,
    "_net_long|int+1|60|0": PRECISION,
    "_net_short|int+1|60|0": PRECISION,
    "_spent|int+1|60|0": PRECISION,
    "summarize|int+1|60|0": PRECISION,
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.lab_forward", "tests.lab_forward_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
