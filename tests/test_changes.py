"""What changed since the last cycle: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import changes
from tests.changes_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](changes) == []


def test_real_module_passes_every_check() -> None:
    assert failures(changes) == []


EQUIVALENT: dict[str, str] = {
    "_as_said|Gt->GtE|given > HALF|0": "the line before returns for a chance of exactly half, so at this comparison a chance is never equal to half",
    "rank|Gt->GtE|_lean(item.up) > 0|0": "a flip needs both leans to be non-zero (a chance of exactly half has none), so the lean here is 1 or -1 and never 0",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.changes", "tests.changes_checks")
    assert total > 80, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
