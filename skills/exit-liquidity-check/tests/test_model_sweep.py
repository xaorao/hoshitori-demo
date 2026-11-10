"""The exit-cost model: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from exit_liquidity_check import model
from tests.model_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(model) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "size_for_slippage|LtE->Lt|target <= 0|0": "at target == 0 the formula 0/(1-0)*L/2 is 0.0, the value the guard returns",
    "slippage_for|LtE->Lt|size_usd <= 0|0": "at size == 0 the formula gives f = 0 and 0/(1+0) = 0.0, the value the guard returns",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("exit_liquidity_check.model", "tests.model_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
