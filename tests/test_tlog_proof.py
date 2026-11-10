"""The tlog-proof reader and writer, and the rules each part enforces."""

from __future__ import annotations

import pytest

from hoshitori import tlog_proof
from tests.mutation import mutant
from tests.proof_checks import failures

MUTATIONS = {
    "any header is accepted": ("    if not lines or lines[0] != HEADER:", "    if False:"),
    "extra data is not read": ('    if position < len(lines) and lines[position].startswith("extra "):', "    if False:"),
    "the index line is optional": ('    if position >= len(lines) or not lines[position].startswith("index "):', "    if False:"),
    "leading zeros are allowed": (
        '    if not text.isascii() or not text.isdigit() or (len(text) > 1 and text.startswith("0")):',
        "    if not text.isdigit():",
    ),
    "non-ASCII digits are allowed": (
        '    if not text.isascii() or not text.isdigit() or (len(text) > 1 and text.startswith("0")):',
        '    if not text.isdigit() or (len(text) > 1 and text.startswith("0")):',
    ),
    "the index is not range checked": ("    if value > MAX_TREE_SIZE:", "    if False:"),
    "path hashes may have any length": ("    if len(raw) != HASH_LENGTH:", "    if False:"),
    "the path is unbounded": ("        if len(path) > MAX_PATH:", "        if False:"),
    "the blank line is optional": (
        '    if position >= len(lines):\n        raise ProofError("the blank line',
        '    if False:\n        raise ProofError("the blank line',
    ),
    "an empty checkpoint is accepted": ("    if not note:", "    if False:"),
    "carriage returns are accepted": ('    if "\\r" in text:', "    if False:"),
    "size is not limited": ("    if len(data) > MAX_PROOF_BYTES:", "    if False:"),
    "the formatter drops extra data": (
        '    if proof.extra is not None:\n        lines.append("extra "',
        '    if False:\n        lines.append("extra "',
    ),
}


def test_scenario_passes_on_the_real_module() -> None:
    assert failures(tlog_proof) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.tlog_proof", old, new)), f"mutant not detected: {name}"
