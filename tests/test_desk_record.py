"""The Desk's decision-log records: the checks pass on the real module and every small change of its code is noticed or harmless."""

from __future__ import annotations

from hoshitori import desk_record
from tests.desk_record_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk_record) == []


EQUIVALENT: dict[str, str] = {
    "_check_decision|int+1|1|0": "the starting value of the gate-order counter is only ever compared with indexes of zero or more, so minus one and minus two behave alike",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_record", "tests.desk_record_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
