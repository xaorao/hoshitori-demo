"""``address_gateway``: the checks pass on the real module, and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import address_gateway
from tests.address_gateway_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(address_gateway) == []


EQUIVALENT: dict[str, str] = {
    "SkillTransport.fetch|str+~|'utf-8'|0": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.address_gateway", "tests.address_gateway_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
