"""The Desk inside the league: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import desk_hook
from tests.desk_hook_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](desk_hook) == []


def test_real_module_passes_every_check() -> None:
    assert failures(desk_hook) == []


EQUIVALENT: dict[str, str] = {
    "disputes|int+1|1|0": "quantize takes only the exponent of its argument, which is the same for Decimal(1) and Decimal(2)",
    "moves|Gt->GtE|ret > 0|0": "a return of zero has size zero, below the threshold, so it is never a move and its direction is never read",
    "moves|statement dropped|continue|1": (
        "an event no window covers then asks for the field ret_Noneh, which no features evidence has, so the skip of a missing number below "
        "drops it all the same"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_hook", "tests.desk_hook_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
