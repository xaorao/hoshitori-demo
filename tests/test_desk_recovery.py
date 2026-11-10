"""Restart and replay of Desk runs: the checks pass on the real module and every small change is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import desk_recovery
from tests.desk_recovery_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk_recovery) == []


EQUIVALENT: dict[str, str] = {
    "|statement dropped|Record = tuple[str, dict[str, canonical.JsonValue]]|0": "a type alias used only in annotations, which are not evaluated",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_recovery", "tests.desk_recovery_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
