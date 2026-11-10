"""The whole skill on recorded data: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import skill
from tests.mutation_sweep import survivors
from tests.skill_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(skill) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_availability|str+~|'unavailable'|3": "the map sends 'unavailable' to 'unavailable', which is also .get's default (the state itself)",
    "_availability|str+~|'usdc_freeze'|0": (
        "USDC's reason is not_requested only on ethereum, where USDT's is then not_requested too (no freeze ran), so USDT's alone decides"
    ),
    "_ofac_record|str+~|'list_not_loaded'|0": (
        "unreachable fallback: list_store.ensure builds every ListState whose stored is None with an error, so state.error is never None here"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.skill", "tests.skill_checks")
    assert total > 320, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
