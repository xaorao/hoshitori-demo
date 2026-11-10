"""The calibration of the falsification tests: the checks pass on the real module, its rates agree with experiment E7, and every small change of
its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import falsify_calibration
from tests.falsify_calibration_checks import compare_with_e7, failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(falsify_calibration) == []


def test_rates_agree_with_the_experiment() -> None:
    assert compare_with_e7(falsify_calibration) == []


EQUIVALENT: dict[str, str] = {
    "run|str+~|'utf-8'|0": "the text that is hashed holds only ASCII (digits, letters and punctuation of the counts), which every encoding writes the same way",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.falsify_calibration", "tests.falsify_calibration_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
