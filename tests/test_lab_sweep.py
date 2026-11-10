"""Every small change of ``hoshitori.lab`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
UNUSED = "the value is returned only with no choices listed, and a draft with choices has no mandate"
EQUIVALENT: dict[str, str] = {
    "Assumption|True<->False|True|0": FROZEN,
    "Draft|True<->False|True|0": FROZEN,
    "Monitor|True<->False|True|0": FROZEN,
    "Preview|True<->False|True|0": FROZEN,
    "Version|True<->False|True|0": FROZEN,
    "_assumptions|return None|return ()|0": UNUSED,
    "_assumptions|return None|return ()|1": UNUSED,
    "_condition|return None|return {}|0": UNUSED,
    "draft_from|int+1|0|0": "the starting weight is replaced by the proposal's or, when it has none, a choice is listed and no mandate is made",
    "draft_from|statement dropped|weight = Decimal(0)|0": "the starting weight is replaced by the proposal's or, when it has none, a choice is listed and no mandate is made",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.lab", "tests.lab_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
