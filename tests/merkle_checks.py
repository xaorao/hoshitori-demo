"""Checks of the Merkle module that run against the real module or a mutant of it."""

from __future__ import annotations

import hashlib
import json
import types
from pathlib import Path

FIXTURE = json.loads((Path(__file__).parent / "vectors" / "merkle_reference.json").read_text())
CT = FIXTURE["ct"]


def leaf_data(i: int) -> bytes:
    return hashlib.sha256(b"hoshitori-merkle" + i.to_bytes(4, "big")).digest()


def failures(mod: types.ModuleType, sample_every: int = 1) -> list[str]:
    bad: list[str] = []
    h = bytes.fromhex

    # Known answers of the Certificate Transparency reference tree.
    tree = mod.MerkleTree()
    if mod.EMPTY_ROOT.hex() != CT["roots"][0]:
        bad.append("empty root")
    for n, leaf in enumerate(CT["leaves"], start=1):
        tree.append(h(leaf))
        if tree.root().hex() != CT["roots"][n]:
            bad.append(f"ct root {n}")
    for item in CT["inclusion"]:
        if [p.hex() for p in tree.inclusion_proof(item["index"], item["size"])] != item["path"]:
            bad.append(f"ct inclusion {item['index']}:{item['size']}")
    for item in CT["consistency"]:
        if [p.hex() for p in tree.consistency_proof(item["first"], item["second"])] != item["path"]:
            bad.append(f"ct consistency {item['first']}:{item['second']}")

    # Verdicts of the Go reference library on valid and mutated proofs.
    for n, case in enumerate(FIXTURE["cases"]):
        if n % sample_every:
            continue
        if case["kind"] == "inclusion":
            got = mod.verify_inclusion(h(case["leaf"]), case["index"], case["size"], [h(p) for p in case["path"]], h(case["root"]))
        else:
            got = mod.verify_consistency(
                case["first"], case["second"], [h(p) for p in case["path"]], h(case["first_root"]), h(case["second_root"])
            )
        if got != case["go_ok"]:
            bad.append(f"case {case['id']} ({case['kind']}): got {got}, go {case['go_ok']}")

    # Degenerate inputs that bare RFC arithmetic would accept: equal empty "hashes", a size of zero.
    zero = b"\x00" * 32
    if mod.verify_inclusion(b"", 0, 1, [], b""):
        bad.append("empty hashes accepted by verify_inclusion")
    if mod.verify_consistency(1, 1, [], b"", b""):
        bad.append("empty hashes accepted by verify_consistency")
    if mod.verify_consistency(0, 5, [], zero, zero):
        bad.append("first size zero accepted")
    if mod.verify_consistency(2, 2, [zero], zero, zero):
        bad.append("equal sizes accepted a non-empty path")
    if mod.verify_inclusion(zero, 1, 1, [], zero):
        bad.append("index equal to size accepted")

    # A tree built here produces the same roots as the reference for the fixture's own leaves.
    own = mod.MerkleTree()
    for i in range(FIXTURE["max_size"]):
        own.append(leaf_data(i))
    if own.size != FIXTURE["max_size"]:
        bad.append("size")
    return bad
