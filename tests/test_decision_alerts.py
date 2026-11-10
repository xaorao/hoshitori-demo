"""The decision alerts: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import decision_alerts
from tests.decision_alerts_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](decision_alerts) == []


def test_real_module_passes_every_check() -> None:
    assert failures(decision_alerts) == []


EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.decision_alerts", "tests.decision_alerts_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
