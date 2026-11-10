"""Funding adapter: the checks pass on the real module and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import venue_funding
from tests.mutation_sweep import survivors
from tests.venue_funding_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(venue_funding) == []


def test_every_change_of_the_code_is_noticed() -> None:
    alive, total = survivors("hoshitori.venue_funding", "tests.venue_funding_checks")
    assert total > 80, f"only {total} places to change: the sweep is not looking at the module"
    assert alive == [], f"unexplained survivors: {alive}"
