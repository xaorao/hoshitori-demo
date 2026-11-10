"""Every small change of ``hoshitori.lab_propose`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
EQUIVALENT: dict[str, str] = {
    "Dropped|True<->False|True|0": FROZEN,
    "Failed|True<->False|True|0": FROZEN,
    "Proposed|True<->False|True|0": FROZEN,
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.lab_propose", "tests.lab_propose_checks")
    assert total > 500, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
