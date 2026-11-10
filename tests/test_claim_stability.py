"""Claim stability: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import claim_stability
from tests.claim_stability_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(claim_stability) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "decompose|GtE->Gt|score >= THRESHOLD|0": "a score is a share of at most eight nodes and can never equal 0.7 exactly, so only the strict and the loose comparison agree",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.claim_stability", "tests.claim_stability_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
