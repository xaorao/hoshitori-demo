"""Claim assumptions: the checks pass on the real module and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import claim_assumptions
from tests.claim_assumptions_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(claim_assumptions) == []


def test_every_change_of_the_code_is_noticed() -> None:
    alive, total = survivors("hoshitori.claim_assumptions", "tests.claim_assumptions_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert alive == [], f"unexplained survivors: {alive}"
