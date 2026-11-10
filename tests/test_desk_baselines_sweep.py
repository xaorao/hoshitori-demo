"""Every small change of ``hoshitori.desk_baselines`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
STRICT = "the two lists are made from the same worlds and so always have the same length; `strict` only guards a mistake that cannot occur"
EQUIVALENT: dict[str, str] = {
    "Tally|True<->False|True|0": FROZEN,
    "Trial|True<->False|True|0": FROZEN,
    "World|True<->False|True|0": FROZEN,
    "compare|True<->False|True|0": STRICT,
    "compare|True<->False|True|1": STRICT,
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_baselines", "tests.desk_baselines_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
