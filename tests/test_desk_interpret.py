"""How the Desk reads what a check brought: the checks pass on the real module and every small change is noticed or harmless."""

from __future__ import annotations

from hoshitori import desk_interpret
from tests.desk_interpret_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk_interpret) == []


EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_interpret", "tests.desk_interpret_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
