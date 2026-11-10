"""Every small change of ``hoshitori.lab_view`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
CODEC = "Python reads a codec name by its letters and digits, so 'utf-8~' is the same codec as 'utf-8'"
DEPTH = "the depth only tells the top level (zero) from every level below it, and a minus one is as far from zero as a plus one"
EQUIVALENT: dict[str, str] = {
    "AssumptionView|True<->False|True|0": FROZEN,
    "CandleRecord|True<->False|True|0": FROZEN,
    "Document|True<->False|True|0": FROZEN,
    "EntryView|True<->False|True|0": FROZEN,
    "ForwardView|True<->False|True|0": FROZEN,
    "PreviewView|True<->False|True|0": FROZEN,
    "RunRecord|True<->False|True|0": FROZEN,
    "TradeView|True<->False|True|0": FROZEN,
    "read_candles|str+~|'utf-8'|0": CODEC,
    "read_runs|str+~|'utf-8'|0": CODEC,
    "words|Add->Sub|depth + 1|0": DEPTH,
    "words|int+1|1|1": DEPTH,
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.lab_view", "tests.lab_view_checks")
    assert total > 300, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
