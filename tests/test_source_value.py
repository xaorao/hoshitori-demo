"""Whether a source improves the calls: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import source_value
from tests.mutation_sweep import survivors
from tests.source_value_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](source_value) == []


def test_real_module_passes_every_check() -> None:
    assert failures(source_value) == []


EQUIVALENT: dict[str, str] = {
    "build|Add->Sub|origins[fid] + hours * HOUR_MS|0": "the close only orders the pairs, and the two calls of a pair share it: any value that rises with the origin orders them alike",
    "build|Mult->Add|hours * HOUR_MS|1": "as above: the origin plus the hours plus an hour still rises with the origin and is the same for both calls of a pair",
    "build|sorted removed|sorted({calls[fid]['cycle_id'] for fid in outcomes if calls[fid]['arm_id'] in arms})|0": (
        "the order the cycles are read in reaches neither the lines (sorted by source and horizon) nor the pairs (sorted by close)"
    ),
    "|str+~|'with'|0": "the two names are keys of one mapping and only need to differ",
    "|str+~|'without'|0": "the two names are keys of one mapping and only need to differ",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.source_value", "tests.source_value_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
