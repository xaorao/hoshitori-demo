"""The Python and Node trusted-root implementations on the same inputs: every document and update chain the scenarios present,
plus a stream of documents damaged field by field. Parsing and updating must agree on acceptance, on the rejection code and on
every fact read from an accepted document."""

from __future__ import annotations

import base64
import copy
import json
import random
import shutil
import subprocess
import types
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from hoshitori import canonical, trusted_root
from tests import root_support as R
from tests.root_checks import SCENARIOS

CLI = Path(__file__).resolve().parents[1] / "verifier-js" / "src" / "root_cli.mjs"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")


def facts(root: trusted_root.TrustedRoot) -> dict[str, Any]:
    stamp = root.time_stamp
    return {
        "version": root.version,
        "expires_ms": int(root.expires.timestamp() * 1000),
        "threshold": root.root_threshold,
        "keys": sorted(root.root_keys),
        "origin": root.origin,
        "eras": [[e.key.name, e.key.key_id.hex(), e.from_size, e.through_size] for e in root.eras],
        "pinned": [[p.sha256, p.operator, p.der is not None] for p in stamp.pinned],
        "operators": {
            name: [op.parent, None if op.evidence is None else [op.evidence.kind, op.evidence.ref, op.evidence.subject_sha256]]
            for name, op in sorted(stamp.operators.items())
        },
        "kinds": sorted(stamp.evidence_kinds_accepted),
        "rules": [
            stamp.required_independent,
            stamp.window_before_commit_s,
            stamp.window_after_commit_s,
            stamp.close_after_commit_s,
            stamp.max_spread_s,
            stamp.u_constant_s,
        ],
        "witnesses": [root.witnesses_required, [k.key_id.hex() for k in root.witness_keys]],
        "mode": root.scored_requires_data_mode,
        "sha256": root.sha256,
    }


class Recorder(types.ModuleType):
    def __init__(self) -> None:
        super().__init__("recorder")
        self.queries: list[dict[str, Any]] = []

    def __getattr__(self, name: str) -> Any:
        return getattr(trusted_root, name)

    def parse_root(self, payload: bytes) -> trusted_root.TrustedRoot:
        query: dict[str, Any] = {"op": "parse", "payload": base64.b64encode(payload).decode()}
        try:
            root = trusted_root.parse_root(payload)
        except trusted_root.RootError as exc:
            query["python"] = {"ok": False, "code": exc.code}
            self.queries.append(query)
            raise
        query["python"] = {"ok": True, "facts": facts(root)}
        self.queries.append(query)
        return root

    def update(
        self, initial: trusted_root.TrustedRoot, channels: list[dict[int, Any]], now: datetime, **options: Any
    ) -> trusted_root.UpdateResult:
        result = trusted_root.update(initial, channels, now, **options)
        self.queries.append(
            {
                "op": "update",
                "initial": base64.b64encode(initial.payload).decode(),
                "channels": [{str(k): v for k, v in c.items()} for c in channels],
                "now_ms": int(now.timestamp() * 1000),
                "max_chain": options.get("max_chain"),
                "floor": options.get("floor_version", 0),
                "python": {
                    "status": result.status,
                    "code": result.code,
                    "version": result.root.version,
                    "sha256": result.root.sha256,
                    "single": result.single_channel,
                },
            }
        )
        return result


def run_node(queries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    payload = "".join(json.dumps({k: v for k, v in q.items() if k != "python"}) + "\n" for q in queries)
    done = subprocess.run([NODE or "node", str(CLI)], input=payload, capture_output=True, text=True, check=True, timeout=600)
    return [json.loads(line) for line in done.stdout.splitlines()]


def compare(queries: list[dict[str, Any]], answers: list[dict[str, Any]]) -> list[str]:
    different = []
    assert len(queries) == len(answers)
    for index, (query, node) in enumerate(zip(queries, answers, strict=True)):
        python = query["python"]
        if python != {k: v for k, v in node.items() if k != "message"}:
            different.append(f"#{index} {query['op']}: python {json.dumps(python)[:200]} node {json.dumps(node)[:300]}")
    return different


def test_scenarios_agree() -> None:
    recorder = Recorder()
    for scenario in SCENARIOS.values():
        scenario(recorder)  # type: ignore[arg-type]
    assert len(recorder.queries) > 150
    assert compare(recorder.queries, run_node(recorder.queries)) == []


ALTERNATIVES: list[Any] = [
    None,
    True,
    False,
    0,
    1,
    -1,
    2,
    2**53 - 1,
    2**53,
    "",
    "x",
    "A" * 600,
    chr(0x1F600) * 200,
    chr(0x1F600) * 300,
    [],
    {},
    [None],
    {"a": 1},
    "2027-10-29T00:00:00",
    "2027-10-29T00:00:00Z",
]


def paths(value: Any, prefix: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    found: list[tuple[Any, ...]] = [prefix]
    if isinstance(value, dict):
        for key in value:
            found += paths(value[key], (*prefix, key))
    elif isinstance(value, list):
        for index in range(len(value)):
            found += paths(value[index], (*prefix, index))
    return found


def edit(document: dict[str, Any], path: tuple[Any, ...], action: str, replacement: Any) -> dict[str, Any] | None:
    clone = copy.deepcopy(document)
    if not path:
        return None
    node: Any = clone
    for step in path[:-1]:
        node = node[step]
    last = path[-1]
    if action == "replace":
        node[last] = replacement
    elif action == "delete" and isinstance(node, dict):
        del node[last]
    elif action == "add" and isinstance(node, dict):
        node["extra"] = replacement
    else:
        return None
    return clone


def test_damaged_documents_agree() -> None:
    rng = random.Random(20261029)
    base = R.make_document(2, root_keys=[R.new_signer(1), R.new_signer(2)], threshold=2, witnesses=(1, [R.log_signer(9).verifier.vkey()]))
    base["log"]["eras"] = [  # type: ignore[index]
        {"vkey": R.log_signer(7).verifier.vkey(), "from_size": 0, "through_size": 9},
        {"vkey": R.log_signer(8).verifier.vkey(), "from_size": 10, "through_size": None},
    ]
    queries: list[dict[str, Any]] = []
    every_path = paths(base)
    seen = 0
    for _ in range(4000):
        path = rng.choice(every_path)
        action = rng.choice(["replace", "replace", "delete", "add"])
        candidate = edit(base, path, action, rng.choice(ALTERNATIVES))
        if candidate is None:
            continue
        try:
            payload = canonical.dumps(candidate)
        except canonical.CanonicalError:
            continue
        seen += 1
        query: dict[str, Any] = {"op": "parse", "payload": base64.b64encode(payload).decode()}
        try:
            query["python"] = {"ok": True, "facts": facts(trusted_root.parse_root(payload))}
        except trusted_root.RootError as exc:
            query["python"] = {"ok": False, "code": exc.code}
        queries.append(query)
    assert seen > 2000
    assert sum(1 for q in queries if q["python"]["ok"]) > 50  # many edits are harmless; the accepted ones must agree on every fact
    assert sum(1 for q in queries if not q["python"]["ok"]) > 1000
    assert compare(queries, run_node(queries)) == []
