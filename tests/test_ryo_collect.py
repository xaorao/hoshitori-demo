"""Reading RYO in a cycle: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoshitori import ryo_collect
from tests.mutation_sweep import survivors
from tests.ryo_collect_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](ryo_collect, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_collect) == []


EQUIVALENT: dict[str, str] = {
    "scan|And->Or|head.startswith('candidates[') and member in ('rank', 'symbol')|0": (
        "every path of a scan answer that has a member after a closing bracket is a candidate's, and the other members it would collect "
        "are never read: only rank and symbol are taken from what is collected"
    ),
    "scan|Gt->GtE|len(rows) > MAX_ROWS|0": (
        "the reader refuses a list of more than 500 members (ryo_fields.MAX_ITEMS) before this line, so 1000 rows, the manifest's own limit, "
        "can never be reached; the guard only keeps the manifest from being the first to refuse"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ryo_collect", "tests.ryo_collect_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
