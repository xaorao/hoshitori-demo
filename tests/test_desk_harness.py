"""The recorded harness: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import desk_harness
from tests.desk_harness_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk_harness) == []


EQUIVALENT: dict[str, str] = {
    "load_ryo_recordings|Gt->GtE|moment > latest|0": "when two moments are equal it makes no difference which is kept",
    "run_recorded|LtE->Lt|set(proposed.cites) <= set(ids)|0": "the harness proposes no cites, and an empty set is inside any set, so the boundary where they are equal is never reached",
    "run_recorded|str+~|'captures'|0": "the name of a folder inside a scratch folder that is thrown away",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_harness", "tests.desk_harness_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
