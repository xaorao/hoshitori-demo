"""The clocks of a forecast: every check passes on the real module and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import timeline
from tests.mutation_sweep import survivors
from tests.timeline_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(timeline) == []


def test_every_change_of_the_code_is_noticed() -> None:
    alive, total = survivors("hoshitori.timeline", "tests.timeline_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert alive == [], f"unexplained survivors: {alive}"
