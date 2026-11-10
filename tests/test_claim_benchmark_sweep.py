"""Every small change of ``hoshitori.claim_benchmark`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

FROZEN = "the flag only makes the object read-only; no check can tell the difference"
CODEC = "Python reads a codec name by its letters and digits, so 'utf-8~' is the same codec as 'utf-8'"
EQUIVALENT: dict[str, str] = {
    "Agreement|True<->False|True|0": FROZEN,
    "CaseScore|True<->False|True|0": FROZEN,
    "LabelledCase|True<->False|True|0": FROZEN,
    "Rate|True<->False|True|0": FROZEN,
    "Report|True<->False|True|0": FROZEN,
    "read_set|str+~|'utf-8'|0": CODEC,
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.claim_benchmark", "tests.claim_benchmark_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}"
