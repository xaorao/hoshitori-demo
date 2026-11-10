"""Python and Node build identical trees and proofs, and give the same verdict on random mutations of them."""

from __future__ import annotations

import json
import random
import subprocess

import pytest

from hoshitori import merkle as M
from tests.support import NODE, VERIFIER_JS

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for the cross-language tests")


def node_lines(queries: list[dict]) -> list[dict]:  # type: ignore[type-arg]
    assert NODE is not None
    done = subprocess.run(
        [NODE, str(VERIFIER_JS / "src" / "merkle_cli.mjs")],
        input="".join(json.dumps(q) + "\n" for q in queries),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
        timeout=300,
    )
    return [json.loads(line) for line in done.stdout.splitlines() if line.strip()]


def test_trees_and_proofs_are_identical_and_cross_verify() -> None:
    rng = random.Random(1029)
    sizes = [rng.randint(1, 300) for _ in range(40)] + [1, 2, 3, 255, 256, 257]
    queries = []
    trees = []
    for n in sizes:
        leaves = [M.leaf_hash(rng.randbytes(8)) for _ in range(n)]
        trees.append(M.MerkleTree(leaves))
        queries.append(
            {
                "op": "tree",
                "leaves": [x.hex() for x in leaves],
                "queries": [{"index": rng.randrange(n), "size": n} for _ in range(8)],
                "consistency": [{"first": rng.randint(1, n), "second": n} for _ in range(8)],
            }
        )
    answers = node_lines(queries)
    verify = []
    for tree, query, answer in zip(trees, queries, answers, strict=True):
        assert answer["roots"] == [tree.root(k).hex() for k in range(tree.size + 1)]
        for q, path in zip(query["queries"], answer["inclusion"], strict=True):
            assert path == [p.hex() for p in tree.inclusion_proof(q["index"], q["size"])]
            leaf = tree.leaf_hash_at(q["index"])
            assert M.verify_inclusion(leaf, q["index"], q["size"], [bytes.fromhex(p) for p in path], tree.root(q["size"]))
            bad = bytearray(bytes.fromhex(path[0])) if path else bytearray(32)
            bad[0] ^= 1
            mutated = [bad.hex(), *path[1:]] if path else [bad.hex()]
            verify.append(
                {
                    "op": "verify_inclusion",
                    "leaf": leaf.hex(),
                    "index": q["index"],
                    "size": q["size"],
                    "path": mutated,
                    "root": tree.root(q["size"]).hex(),
                }
            )
        for q, path in zip(query["consistency"], answer["consistency"], strict=True):
            assert path == [p.hex() for p in tree.consistency_proof(q["first"], q["second"])]
            verify.append(
                {
                    "op": "verify_consistency",
                    "first": q["first"],
                    "second": q["second"],
                    "path": path,
                    "first_root": tree.root(q["first"]).hex(),
                    "second_root": tree.root(q["second"]).hex(),
                }
            )
    for item, answer in zip(verify, node_lines(verify), strict=True):
        path = [bytes.fromhex(p) for p in item["path"]]
        if item["op"] == "verify_inclusion":
            expected = M.verify_inclusion(bytes.fromhex(item["leaf"]), item["index"], item["size"], path, bytes.fromhex(item["root"]))
        else:
            expected = M.verify_consistency(
                item["first"], item["second"], path, bytes.fromhex(item["first_root"]), bytes.fromhex(item["second_root"])
            )
        assert answer["ok"] == expected
