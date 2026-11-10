"""The Unicode 15.0 repertoire table: the checks pass on the real module and every small change of it is noticed or listed as harmless."""

from __future__ import annotations

from tests.mutation_sweep import survivors
from tests.unicode15_checks import failures


def test_real_module_passes_every_check() -> None:
    from hoshitori import unicode15

    assert failures(unicode15) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.unicode15", "tests.unicode15_checks")
    assert total > 50, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
