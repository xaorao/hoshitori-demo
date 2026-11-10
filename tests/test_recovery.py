"""Closing the cycles a lost process left open: the checks pass on the real module and every small change of its code is noticed or harmless."""

from __future__ import annotations

from hoshitori import recovery
from tests.mutation_sweep import survivors
from tests.recovery_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(recovery) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|Judge = Callable[[list[dict[str, Any]]], Judged | None]|0": "the alias is only read in annotations, which are never evaluated here (postponed evaluation), so dropping it changes nothing the program does",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.recovery", "tests.recovery_checks")
    assert total > 50, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
