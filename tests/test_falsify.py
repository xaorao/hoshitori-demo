"""The falsification tests: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import falsify
from tests.falsify_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(falsify) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "NotTestable.__init__|statement dropped|super().__init__(reason)|0": "an exception keeps the arguments it was built with, so its text is the reason with or without the call",
    "_sign|Gt->GtE|x > 0|0": "the sign of an exact zero is never asked: a score is read as a direction only after its size passed a threshold above zero",
    "_sign|Lt->LtE|x < 0|0": "the sign of an exact zero is never asked: a score is read as a direction only after its size passed a threshold above zero",
    "market_scores|Sub->Add|a - mx|0": "the sum of the deviations of the other factor is zero, so centring one factor of the product centres the sum",
    "market_scores|Sub->Add|b - my|0": "the sum of the deviations of the other factor is zero, so centring one factor of the product centres the sum",
    "market_scores|True<->False|True|0": "the two series have the same length at that point, so the strict flag of the pairing never fires",
    "market_scores|True<->False|True|1": "the two series have the same length at that point, so the strict flag of the pairing never fires",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.falsify", "tests.falsify_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
