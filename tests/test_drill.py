"""Drill file reader: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import drill
from tests.drill_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(drill) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "read_drills|str+~|'utf-8'|0": "Python's codec lookup ignores a leading punctuation mark, so the changed name still means UTF-8",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.drill", "tests.drill_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
