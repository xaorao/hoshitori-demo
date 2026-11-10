"""Model challenge: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import model_challenge
from tests.model_challenge_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(model_challenge) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "standard_set|And->Or|r.verdict is not None and r.treated is not None|0": "a challenge has a verdict and its two tables together or neither, so either test gives the same set",
    "standard_set|True<->False|True|0": "the lists zipped are of one length by construction, so the strict flag never decides anything",
    "|statement dropped|DecideFn = Callable[[Reader, int, str], Decision]|0": "a type alias used only in annotations, which are not evaluated",
    "_gated|IsNot->Is|decision.gate is not Gate.NONE|0": "the gate indicator enters only as a comparison of two runs of one pair; flipping it for every run leaves each comparison as it was",
    "_moved|Gt->GtE|only_removed > only_anyway|0": "equal counts give an exact p of 1, which is never at or below the level, so the order test only matters when the counts differ",
    "_moved|LtE->Lt|paired_stats.mcnemar_exact(only_removed, only_anyway) <= paired_stats.ALPHA|0": "no exact test probability equals 0.05: enumerated for every split of up to 200 disagreeing pairs, none does",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.model_challenge", "tests.model_challenge_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
