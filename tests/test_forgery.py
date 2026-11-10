"""The forged copy of a bundle: the checks pass on the real module and every small change of its code is noticed or harmless."""

from __future__ import annotations

from hoshitori import forgery
from tests.forgery_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(forgery) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "forge|Gt->GtE|target > 0|0": "at a changed record 0 the prefix is empty, and the root of an empty tree is the same in both ledgers, so the comparison cannot fail",
    "forge|int+1|0|4": "the clock is set to each record's own time before it is appended, so the time it starts at is never used by an append",
    "demo_root|str+~|'utf-8'|0": "Python folds punctuation in a codec's name, so 'utf-8~' still names UTF-8",
    "read_document|str+~|'utf-8'|0": "Python folds punctuation in a codec's name, so 'utf-8~' still names UTF-8",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.forgery", "tests.forgery_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
