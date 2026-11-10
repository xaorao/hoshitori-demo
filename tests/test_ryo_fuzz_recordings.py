"""Every real RYO answer, damaged in many seeded ways, is either read or refused with a named error, never a crash and never evidence that
is not there (private recordings; skipped where they are not on this machine).

A damaged answer goes through the same code a cycle uses, ``ryo_evidence.items_from``, and must end one of two ways: an evidence item, or one
of the errors the collection catches and records as the tool's failure (``collect.UNREADABLE``, ``RyoEvidenceError``, ``EvidenceError``).
Anything else (a ``KeyError``, a ``TypeError``) would stop a cycle. When an item is made, a field whose value was damaged to null or removed
is never a known value: it is unknown or absent, never zero and never the old value."""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from hoshitori import ryo_evidence
from hoshitori.collect import UNREADABLE
from hoshitori.evidence import EvidenceError
from tests.test_ryo import _live_capture
from tests.test_ryo_suite_recordings import SUCCESS, load, tool_of

pytestmark = pytest.mark.skipif(not SUCCESS, reason="the private suite recordings are not on this machine")

ROUNDS = 60  # damaged versions of each answer; the seed is the file name, so a failure reproduces exactly
REFUSED = (*UNREADABLE, ryo_evidence.RyoEvidenceError, EvidenceError)


def _paths(node: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    out = [prefix]
    if isinstance(node, dict):
        for key, value in node.items():
            out += _paths(value, (*prefix, key))
    elif isinstance(node, list):
        for i, value in enumerate(node):
            out += _paths(value, (*prefix, i))
    return out


def _get(node: Any, path: tuple[Any, ...]) -> Any:
    for step in path:
        node = node[step]
    return node


def _damage(body: dict[str, Any], rng: random.Random) -> tuple[bytes, tuple[Any, ...] | None, str]:
    """One damaged copy: the bytes, the path whose value was set to null or removed (none for other damage), and what was done."""
    doc = json.loads(json.dumps(body))
    targets = [p for p in _paths(doc) if p]
    path = rng.choice(targets)
    parent, key = _get(doc, path[:-1]), path[-1]
    kind = rng.choice(("null", "drop", "text", "number", "list", "object", "huge", "nan_text", "enum", "truncate", "bool"))
    if kind == "truncate":
        raw = json.dumps(doc).encode()
        return raw[: rng.randrange(1, len(raw))], None, "truncated bytes"
    if kind == "drop" and isinstance(parent, dict):
        del parent[key]
        return json.dumps(doc).encode(), path, f"removed {path}"
    replacement: Any = {
        "null": None,
        "drop": None,
        "text": "n/a",
        "number": 12345,
        "list": [1, "x"],
        "object": {"x": 1},
        "huge": 10**400,
        "nan_text": "NaN",
        "enum": "SOMETHING_NEW",
        "bool": True,
    }[kind]
    parent[key] = replacement
    return json.dumps(doc).encode(), path if replacement is None else None, f"{kind} at {path}"


@pytest.mark.parametrize("path", SUCCESS, ids=lambda p: f"{p.parent.name}-{p.name.split('.')[0]}")
def test_a_damaged_answer_is_read_or_refused_by_name_and_nothing_is_invented(path: Path) -> None:
    rec = load(path)
    tool = tool_of(rec)
    request = rec["request"]["body"]
    body = rec["response"]["body"]
    rng = random.Random(path.name)
    read = refused = 0
    for _ in range(ROUNDS):
        raw, emptied, what = _damage(body, rng)
        damaged = dict(rec, raw=raw)
        try:
            made = ryo_evidence.items_from(_live_capture(damaged, tool), tool, request, "fuzz")
        except REFUSED:
            refused += 1
            continue
        except Exception as exc:  # anything else would stop a cycle
            pytest.fail(f"{what}: {type(exc).__name__}: {exc}")
        read += 1
        if emptied is not None and emptied[:2] == ("result", "data"):
            field = ".".join(str(p) if isinstance(p, str) else f"[{p}]" for p in emptied[2:]).replace(".[", "[")
            known = {p for p, _ in made.observation.fields} | (
                {p for p, _ in made.interpretation.fields} if made.interpretation is not None else set()
            )
            assert field not in known, f"{what}: a value that was removed or set to null is read as known"
    assert read + refused == ROUNDS
