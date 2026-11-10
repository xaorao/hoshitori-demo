"""The practice-portfolio view: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import practice_view
from tests.mutation_sweep import survivors
from tests.practice_view_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(practice_view) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_take|int+1|0|1": "an emptied lot left in place instead of deleted adds nothing to any sum and can never cover a sale",
    "_thin|LtE->Lt|n <= MAX_POINTS|0": "with exactly as many marks as the limit the even choice picks every one of them, so keeping them all and choosing gives the same curve",
    "|int+1|60|0": "the working precision of sixty digits or sixty-one gives the same results for any amount a book can hold",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.practice_view", "tests.practice_view_checks")
    assert total > 80, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
