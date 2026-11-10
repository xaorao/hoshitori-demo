"""The Python settlement and the Node one (``settlement-js``, written from the contract text with no shared code) answer every case alike."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from hoshitori import settlement
from tests.settlement_differential import NODE_ENTRY, disagreements, generate, node_available
from tests.settlement_golden import GOLDEN, load_cases

pytestmark = pytest.mark.skipif(not node_available(), reason="Node is not installed")

SEEDS = (1, 2, 3, 4)
PER_SEED = 2500


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_cases_agree(seed: int) -> None:
    assert disagreements(settlement, seed, PER_SEED) == []


def test_generated_cases_reach_every_outcome() -> None:
    """A corpus that never reaches a rule proves nothing about it: every state, reason and outcome appears, and the finer data decides some."""
    from tests.settlement_golden import run_case

    seen: set[tuple[str, str | None, str | None]] = set()
    for case in generate(1, PER_SEED):
        answer = run_case(settlement, case)
        seen.add((case["kind"], answer.get("state", "invalid"), answer.get("reason") or answer.get("outcome")))
    for kind in ("directional", "barrier"):
        for state in ("PENDING", "SETTLED", "UNSETTLEABLE", "invalid"):
            assert any(k == kind and s == state for k, s, _ in seen), (kind, state)
    for needed in (
        "CANDLE_MISSING",
        "BAR_NOT_CLOSED",
        "UNSETTLEABLE_DISAGREE",
        "UNSETTLEABLE_MISSING",
        "VOID_TIE",
        "SAME_CANDLE",
        "TARGET",
        "STOP",
        "TIMEOUT",
    ):
        assert any(r == needed for _, _, r in seen), needed


def test_the_node_command_answers_the_golden_directory_the_same_way() -> None:
    out = subprocess.run(["node", str(NODE_ENTRY), str(GOLDEN)], capture_output=True, text=True, check=True, timeout=120)  # noqa: S607
    answers = {row["id"]: row["answer"] for row in map(json.loads, out.stdout.splitlines())}
    for case in load_cases():
        assert answers[case["id"]] == case["expected"], case["id"]


def test_node_package_has_no_dependencies() -> None:
    package = json.loads((Path(NODE_ENTRY).parent.parent / "package.json").read_text(encoding="utf-8"))
    assert "dependencies" not in package and "devDependencies" not in package
