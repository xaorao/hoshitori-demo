"""Paired statistics: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import paired_stats
from tests.mutation_sweep import survivors
from tests.paired_stats_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(paired_stats) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_bound|int+1|80|0": "80 halvings of an interval of width at most 2 leave 1e-24, past what a double can hold; a 81st changes no digit that is used",
    "tango|LtE->Lt|_score(pairs, -edge) <= z|0": "the score at the edge would have to equal 1.959964 exactly; it is a continuous function of counts and never does",
    "tango|GtE->Gt|_score(pairs, edge) >= -z|0": "the score at the edge would have to equal -1.959964 exactly; it never does",
    "label|Gt->GtE|c_low > 0|0": "the control's lower limit would have to be exactly zero, which a score interval of integer counts never gives",
    "label|Lt->LtE|c_high < 0|0": "the control's upper limit would have to be exactly zero, which a score interval of integer counts never gives",
    "label|LtE->Lt|p <= ALPHA|0": "no exact test probability equals 0.05: enumerated for every split of up to 200 disagreeing pairs, none does",
    "label|GtE->Gt|low >= -STABLE_WIDTH|0": "the lower limit would have to equal -0.15 to the last bit; it is a root found by bisection and never does",
    "label|LtE->Lt|high <= STABLE_WIDTH|0": "the upper limit would have to equal 0.15 to the last bit; it never does",
    "_bound|Gt->GtE|_score(pairs, mid) > level|0": "the score at a midpoint would have to equal the level exactly; bisection never lands on it, and one more step would move it anyway",
    "|statement dropped|Run = tuple[str, str, str]|0": "a type alias used only in annotations, which are not evaluated",
    "label_table|LtE->Lt|adjusted[k] <= ALPHA|0": "no adjusted value equals 0.05: enumerated for every split of up to 200 disagreeing pairs times every family size up to 10, none does",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.paired_stats", "tests.paired_stats_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
