"""The venue survey: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from hoshitori import venue_survey
from tests.mutation_sweep import survivors
from tests.venue_survey_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](venue_survey, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(venue_survey) == []


_ORACLE_READS = "the protocol made here is handed only to oracle.pick, which reads its two venues, its instrument kind and its quote class"
_BOTH_WAYS = (
    "every pair is surveyed both ways round (itertools.permutations), and the oracle rule picks the same two instruments either way "
    "(oracle._one is per venue; registry._remap gives every instrument of a token one mapping, so the join on either answers the same), "
    "so the reverse pair adds this id as its other venue's"
)

# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_rules|int+1|0|0": f"the start of the schedule: {_ORACLE_READS}",
    "_rules|int+1|1|0": f"the trusted root's version: {_ORACLE_READS}",
    "_rules|str+~|'venue-survey'|0": f"the protocol's name: {_ORACLE_READS}; it is never written or shown",
    "survey|str+~|'venue-survey'|0": (
        "the caller named on each gateway Call, which nothing reads (gateway.py reads a call's provider, operation, args, priority, "
        "deadline, namespace, window and max_cache_age_s only)"
    ),
    "survey|sorted removed|sorted(watchlist)|0": (
        "the first loop only fills a dict and sets keyed by symbol with reads of the registry; the second loop, which makes the result, "
        "walks sorted(watchlist) itself"
    ),
    "survey|sorted removed|sorted(ids)|0": (
        "venue_probe.probe asks for the instruments in name order (sorted(instruments)) and returns its findings sorted by name, so the "
        "order it is given changes nothing"
    ),
    "survey|statement dropped|wanted[primary].add(pick.primary.raw_id)|0": _BOTH_WAYS,
    "survey|statement dropped|wanted[second].add(pick.second.raw_id)|0": _BOTH_WAYS,
    "|statement dropped|raise SystemExit(main())|0": "the entry guard cannot run inside a sweep; test_the_module_runs_as_a_program runs the real one",
    "|str+~|'__main__'|0": "the entry guard cannot run inside a sweep; test_the_module_runs_as_a_program runs the real one",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.venue_survey", "tests.venue_survey_checks")
    assert total > 120, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_the_module_runs_as_a_program(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "hoshitori.venue_survey", "--watchlist", "BTC", "--venues", "okx"],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (2, "", "venue_survey: a pair needs two venues, each named once\n")
