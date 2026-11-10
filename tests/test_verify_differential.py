"""The Python and Node bundle verifiers on every case of the bundle table: the verdicts must be equal, field for field, except
for the name of the verifier and the free-text ``detail`` strings. The refusals for a string only a newer Unicode composes, for files that are not regular files, and for impossible record times carry exact
messages, so for those cases the ``detail`` strings must be equal too."""

from __future__ import annotations

import base64
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from hoshitori import verify
from tests.verify_checks import (
    CREATED_AFTER_COMMIT,
    NOT_REGULAR,
    UNASSIGNED_105D2,
    Case,
    build_cases,
    build_special_cases,
    lookup,
    run_guarded,
)

CLI = Path(__file__).resolve().parents[1] / "verifier-js" / "src" / "verify_cli.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

# case name -> (path of a detail in the verdict, the exact text both verifiers must give there)
EXACT_DETAILS = {
    "committed by hand: a record string that only a newer Unicode composes": ("integrity.items.item-00000000.detail", UNASSIGNED_105D2),
    "committed by hand: a forecast created a day after its commit": ("timing.items.item-00000001.detail", CREATED_AFTER_COMMIT),
    "committed by hand: a forecast created 1.001 s after its commit": ("timing.items.item-00000001.detail", CREATED_AFTER_COMMIT),
    "a record file one byte over the size limit is refused": ("integrity.items.item-00000001.detail", "record file"),
    "a record file that is a FIFO no process writes": ("integrity.items.item-00000001.detail", NOT_REGULAR),
    "a record file that links to the null device": ("integrity.items.item-00000001.detail", NOT_REGULAR),
    "a proof file that is a FIFO no process writes": ("integrity.items.item-00000001.detail", "item-00000001.tlog-proof"),
    "a 64 MiB proof file": ("integrity.items.item-00000001.detail", "proof file too large"),
}


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: scrub(v) for k, v in value.items() if k not in ("verifier", "detail")}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    return value


def run_node(cases: list[Case], timeout: float = 600) -> list[dict[str, Any]]:
    payload = "".join(
        json.dumps(
            {
                "directory": str(c.directory),
                "root": None if c.root is None else base64.b64encode(c.root.payload).decode(),
                "status": c.status,
            }
        )
        + "\n"
        for c in cases
    )
    done = subprocess.run([NODE or "node", str(CLI)], input=payload, capture_output=True, text=True, check=True, timeout=timeout)
    return [json.loads(line) for line in done.stdout.splitlines()]


def python_verdict(case: Case) -> dict[str, Any]:
    return run_guarded(lambda: verify.verify_bundle(case.directory, case.root, case.status), case.fifos)  # type: ignore[no-any-return]


@pytest.fixture(scope="module")
def cases(tmp_path_factory: pytest.TempPathFactory) -> list[Case]:
    return build_cases(tmp_path_factory.mktemp("differential"))


@pytest.fixture(scope="module")
def special(tmp_path_factory: pytest.TempPathFactory) -> list[Case]:
    return build_special_cases(tmp_path_factory.mktemp("special"))


def compare(cases: list[Case], answers: list[dict[str, Any]]) -> list[str]:
    assert len(answers) == len(cases)
    different = []
    for case, node in zip(cases, answers, strict=True):
        python = python_verdict(case)
        if scrub(python) != scrub(node):
            keys = [k for k in scrub(python) if scrub(python)[k] != scrub(node).get(k)]
            different.append(f"{case.name}: differs in {keys}")
        if case.name in EXACT_DETAILS:
            path, text = EXACT_DETAILS[case.name]
            if not lookup(python, path) == lookup(node, path) == text:
                different.append(
                    f"{case.name}: {path} is {lookup(python, path)!r} in Python and {lookup(node, path)!r} in Node, not {text!r}"
                )
    return different


def test_the_verdicts_are_equal(cases: list[Case]) -> None:
    assert compare(cases, run_node(cases)) == []


def test_the_verdicts_are_equal_on_files_that_are_not_regular_or_far_too_large(special: list[Case]) -> None:
    # a verifier that blocks on a FIFO no process writes is stopped by the timeout, and the test fails
    assert compare(special, run_node(special, timeout=120)) == []


def test_every_exact_detail_is_compared(cases: list[Case], special: list[Case]) -> None:
    assert set(EXACT_DETAILS) <= {c.name for c in [*cases, *special]}
