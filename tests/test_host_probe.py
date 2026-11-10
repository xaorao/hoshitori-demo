"""The host's health probe: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from hoshitori import host_probe
from tests.host_probe_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](host_probe, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(host_probe) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_records|Gt->GtE|position > 0|0": (
        "for the first segment, scanning no bytes with no segment before it gives no record and no problem (segment.scan_segment), "
        "the same as stopping there"
    ),
    "|statement dropped|raise SystemExit(main())|0": "the entry guard cannot run inside a sweep; test_the_module_runs_as_a_program runs the real one",
    "|str+~|'__main__'|0": "the entry guard cannot run inside a sweep; test_the_module_runs_as_a_program runs the real one",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.host_probe", "tests.host_probe_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_the_module_runs_as_a_program(tmp_path: Path) -> None:
    done = subprocess.run(
        [sys.executable, "-m", "hoshitori.host_probe", str(tmp_path / "missing.json")],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)},
        check=False,
    )
    assert (done.returncode, done.stdout, done.stderr) == (2, "", "host_probe: the settings cannot be read: No such file or directory\n")
