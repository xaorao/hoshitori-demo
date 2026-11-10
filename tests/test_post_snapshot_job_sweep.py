"""Every small change of ``hoshitori.post_snapshot_job`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "post_snapshot_pass|Gt->GtE|snapshots[0][0] > now_ms|0": (
        "at the very millisecond of the first snapshot the window (after, until] is empty, so no change is found whether the pass returns "
        "early or not"
    ),
    "post_snapshot_pass|LtE->Lt|s[0] <= event.first_seen_ms|0": (
        "NOT DISTINGUISHED by the checks: it differs only for an event first seen at the same millisecond as a later snapshot; the test world "
        "seals one snapshot, so that case is not constructible here"
    ),
    "sealed_snapshots|sorted removed|sorted(found)|0": (
        "NOT DISTINGUISHED by the checks: the order of snapshots matters only with two or more sealed cycles, and the test world seals one"
    ),
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.post_snapshot_job", "tests.post_snapshot_job_checks")
    assert total > 20, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
