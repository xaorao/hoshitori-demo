"""The command line: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import json
import subprocess
import sys

from exit_liquidity_check import __main__ as main_module
from tests.main_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(main_module) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|raise SystemExit(main())|0": (
        "the line runs only when the module is run as a script and the sweep imports it; test_module_runs_as_a_script runs the real line"
    ),
    "|str+~|'__main__'|0": (
        "the guard is false in an imported module with either spelling; test_module_runs_as_a_script runs the real guard as a script"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("exit_liquidity_check.__main__", "tests.main_checks")
    assert total > 50, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_module_runs_as_a_script() -> None:
    """The guard and the exit code at the foot of the module, which a sweep that imports the module cannot reach."""

    def run(*argv: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "exit_liquidity_check", *argv], capture_output=True, text=True, timeout=60, check=False
        )

    example = run("example")
    assert (example.returncode, json.loads(example.stdout)) == (0, {"token": "SOL", "size_usd": 50000})
    refused = run("check", "--token", "DOGE")  # refused before any network call
    assert (refused.returncode, refused.stdout, json.loads(refused.stderr)["code"]) == (2, "", "INVALID_ARGUMENTS")
    assert run("check").returncode == 2
