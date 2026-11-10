"""Which tokens move together: the checks pass on the real module and every small change is noticed or harmless."""

from __future__ import annotations

from hoshitori import clusters
from tests.clusters_checks import EQUIVALENT_REASONS, failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(clusters) == []


EQUIVALENT: dict[str, str] = dict(EQUIVALENT_REASONS)


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.clusters", "tests.clusters_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
