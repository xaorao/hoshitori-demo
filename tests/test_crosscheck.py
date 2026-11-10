"""crosscheck: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import crosscheck
from tests.crosscheck_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(crosscheck) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "candidate_checks|int+1|1|1": "a series with no time ranks after every series that has one; minus one or minus two say the same",
    "price_check|int+1|60|0": "sixty digits of working precision or sixty-one: no price the evidence model holds has more than twenty thousand digits, and the "
    "gap of two such prices is the same to the sixth place",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.crosscheck", "tests.crosscheck_checks")
    assert total > 25, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
