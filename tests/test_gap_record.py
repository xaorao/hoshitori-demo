"""The age of the evidence at the origin and the drift since it: the checks pass on the real module and every small change is noticed or harmless."""

from __future__ import annotations

from hoshitori import gap_record
from tests.gap_record_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(gap_record) == []


EQUIVALENT: dict[str, str] = {
    "gaps|str+~|'committed_at'|0": "lifecycle.from_record itself drops the key the ledger adds before it validates a record, so the filter in gaps repeats it",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.gap_record", "tests.gap_record_checks")
    assert total > 20, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
