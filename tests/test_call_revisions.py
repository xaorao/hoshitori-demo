"""Reversals of open calls: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import call_revisions
from tests.call_revisions_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](call_revisions) == []


def test_real_module_passes_every_check() -> None:
    assert failures(call_revisions) == []


EQUIVALENT: dict[str, str] = {
    "revisions|Gt->GtE|pa > HALF|0": "a call at exactly an even chance is left out before this line, so above and at-or-above are the same here",
    "revisions|Gt->GtE|pb > HALF|0": "a call at exactly an even chance is left out before this line, so above and at-or-above are the same here",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.call_revisions", "tests.call_revisions_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
