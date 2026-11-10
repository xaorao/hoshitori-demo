"""Regenerate ``merkle_reference.json`` (run by hand; needs the Go reference harness built from transparency-dev/merkle).

Usage: ``GOREF=/path/to/goref python tests/vectors/make_merkle_reference.py``

The fixture holds
* ``ct``: the leaf inputs, root hashes and proofs of the Certificate Transparency reference tree, copied from the
  Apache-2.0 test data of github.com/transparency-dev/merkle (testonly/constants.go, testonly/reference_test.go);
* ``cases``: valid and mutated inclusion and consistency proofs over trees whose leaf *i* is
  ``SHA-256("hoshitori-merkle" || i as 4 bytes big-endian)`` hashed as a leaf, each with the verdict of the Go
  library (``go_ok``). Our implementations must give the same verdict on every case.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from hoshitori import merkle as M

CT_LEAVES = ["", "00", "10", "2021", "3031", "40414243", "5051525354555657", "606162636465666768696a6b6c6d6e6f"]
CT_ROOTS = [
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
    "fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125",
    "aeb6bcfe274b70a14fb067a5e5578264db0fa9b51af5e0ba159158f329e06e77",
    "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
    "4e3bbb1f7b478dcfe71fb631631519a3bca12c9aefca1612bfce4c13a86264d4",
    "76e67dadbcdf1e10e1b74ddc608abd2f98dfb16fbce75277b5232a127f2087ef",
    "ddb89be403809e325750d3d263cd78929c2942b7942a34b77e122c9594a74c8c",
    "5dc9da79a70659a9ad559cb701ded9a2ab9d823aad2f4960cfe370eff4604328",
]
CT_INCLUSION = [
    (0, 1, []),
    (0, 2, ["96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7"]),
    (1, 2, ["6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"]),
    (2, 3, ["fac54203e7cc696cf0dfcb42c92a1d9dbaf70ad9e621f4bd8d98662f00e3c125"]),
    (
        1,
        5,
        [
            "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d",
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
        ],
    ),
    (
        0,
        8,
        [
            "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4",
        ],
    ),
    (
        5,
        8,
        [
            "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
            "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
            "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
        ],
    ),
]
CT_CONSISTENCY = [
    (1, 1, []),
    (
        1,
        8,
        [
            "96a296d224f285c67bee93c30f8a309157f0daa35dc5b87e410b78630a09cfc7",
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "6b47aaf29ee3c2af9af889bc1fb9254dabd31177f16232dd6aab035ca39bf6e4",
        ],
    ),
    (
        2,
        5,
        [
            "5f083f0a1a33ca076a95279832580db3e0ef4584bdff1f54c8a360f50de3031e",
            "bc1a0643b12e4d2d7c77918f44e0f4f79a838b6cf9ec5b5c283e1f4d88599e6b",
        ],
    ),
    (
        6,
        8,
        [
            "0ebc5d3437fbe2db158b9f126a1d118e308181031d0a949f8dededebc558ef6a",
            "ca854ea128ed050b41b35ffc1b87b8eb2bde461e9e3b5596ece6b9d5975a0ae0",
            "d37ee418976dd95753c1c73862b9398fa2a2cf9b4ff0fdfe8b30cd95209614b7",
        ],
    ),
]
MAX_SIZE = 25


def leaf_data(i: int) -> bytes:
    return hashlib.sha256(b"hoshitori-merkle" + i.to_bytes(4, "big")).digest()


def go_verdicts(queries: list[dict]) -> dict[str, bool]:
    exe = os.environ["GOREF"]
    payload = "".join(json.dumps(q) + "\n" for q in queries)
    done = subprocess.run([exe], input=payload, capture_output=True, text=True, check=True, env={"PATH": "/usr/bin:/bin"})
    return {r["id"]: r["ok"] for r in (json.loads(line) for line in done.stdout.splitlines() if line.strip())}


def inclusion_computed(leaf: bytes, index: int, size: int, path: list[bytes]) -> tuple[bytes, int]:
    """The RFC 9162 2.1.3.2 loop without its final checks: the hash it computes and the leftover ``sn``."""
    fn, sn, r = index, size - 1, leaf
    for p in path:
        if sn == 0:
            break
        if fn & 1 or fn == sn:
            r = M.node_hash(p, r)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = M.node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return r, sn


def consistency_computed(first: int, second: int, path: list[bytes], first_root: bytes) -> tuple[bytes, bytes, int]:
    """The RFC 9162 2.1.4.2 loop without its final checks."""
    nodes = list(path)
    if first & (first - 1) == 0:
        nodes.insert(0, first_root)
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = nodes[0]
    for c in nodes[1:]:
        if sn == 0:
            break
        if fn & 1 or fn == sn:
            fr = M.node_hash(c, fr)
            sr = M.node_hash(c, sr)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = M.node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return fr, sr, sn


def mutate_path(path: list[bytes], rng: random.Random) -> list[bytes]:
    out = [bytes(p) for p in path]
    kind = rng.choice(["flip", "drop", "extra", "swap", "short", "long", "empty-hash"])
    if kind == "flip" and out:
        i = rng.randrange(len(out))
        b = bytearray(out[i])
        b[rng.randrange(32)] ^= 1 << rng.randrange(8)
        out[i] = bytes(b)
    elif kind == "drop" and out:
        del out[rng.randrange(len(out))]
    elif kind == "extra":
        out.insert(rng.randrange(len(out) + 1), hashlib.sha256(bytes([rng.randrange(256)])).digest())
    elif kind == "swap" and len(out) >= 2:
        i, j = rng.sample(range(len(out)), 2)
        out[i], out[j] = out[j], out[i]
    elif kind == "short" and out:
        out[rng.randrange(len(out))] = out[0][:31]
    elif kind == "long" and out:
        out[rng.randrange(len(out))] = out[0] + b"\x00"
    elif kind == "empty-hash" and out:
        out[rng.randrange(len(out))] = b""
    return out


def main() -> None:
    rng = random.Random(1029)
    tree = M.MerkleTree()
    for i in range(MAX_SIZE):
        tree.append_leaf_hash(M.leaf_hash(leaf_data(i)))
    cases: list[dict] = []
    for size in range(1, MAX_SIZE + 1):
        root = tree.root(size)
        for index in range(size):
            path = tree.inclusion_proof(index, size)
            cases.append(
                {
                    "kind": "inclusion",
                    "leaf": tree.leaf_hash_at(index).hex(),
                    "index": index,
                    "size": size,
                    "path": [p.hex() for p in path],
                    "root": root.hex(),
                }
            )
        for first in range(1, size + 1):
            path = tree.consistency_proof(first, size)
            cases.append(
                {
                    "kind": "consistency",
                    "first": first,
                    "second": size,
                    "path": [p.hex() for p in path],
                    "first_root": tree.root(first).hex(),
                    "second_root": root.hex(),
                }
            )
    valid = list(cases)
    for _ in range(900):
        base = dict(rng.choice(valid))
        kind = base["kind"]
        path = mutate_path([bytes.fromhex(p) for p in base["path"]], rng)
        base["path"] = [p.hex() for p in path]
        pick = rng.random()
        if kind == "inclusion":
            if pick < 0.2:
                base["index"] = max(0, base["index"] + rng.choice([-1, 1]))
            elif pick < 0.4:
                base["size"] = max(1, base["size"] + rng.choice([-1, 1]))
            elif pick < 0.5:
                base["root"] = hashlib.sha256(bytes.fromhex(base["root"])).hexdigest()
        else:
            if pick < 0.2:
                base["first"] = max(1, base["first"] + rng.choice([-1, 1]))
            elif pick < 0.4:
                base["second"] = max(1, base["second"] + rng.choice([-1, 1]))
            elif pick < 0.5:
                base["first_root"] = hashlib.sha256(bytes.fromhex(base["first_root"])).hexdigest()
            elif pick < 0.6:
                base["second_root"] = hashlib.sha256(bytes.fromhex(base["second_root"])).hexdigest()
        cases.append(base)
    # Targeted cases whose roots are self-consistent with a wrongly shaped path, so only the final checks reject them.
    for case in valid:
        path = [bytes.fromhex(p) for p in case["path"]]
        if case["kind"] == "inclusion" and path:
            leaf = bytes.fromhex(case["leaf"])
            for variant in (path[:-1], [*path, hashlib.sha256(b"extra").digest()]):
                root, _ = inclusion_computed(leaf, case["index"], case["size"], variant)
                cases.append(
                    {
                        "kind": "inclusion",
                        "leaf": case["leaf"],
                        "index": case["index"],
                        "size": case["size"],
                        "path": [p.hex() for p in variant],
                        "root": root.hex(),
                    }
                )
        if case["kind"] == "consistency" and path and case["first"] != case["second"]:
            first_root = bytes.fromhex(case["first_root"])
            for variant in (path[:-1], [*path, hashlib.sha256(b"extra").digest()]):
                if not variant:
                    continue
                fr, sr, _ = consistency_computed(case["first"], case["second"], variant, first_root)
                cases.append(
                    {
                        "kind": "consistency",
                        "first": case["first"],
                        "second": case["second"],
                        "path": [p.hex() for p in variant],
                        "first_root": fr.hex(),
                        "second_root": sr.hex(),
                    }
                )
            for field in ("first_root", "second_root"):
                wrong = dict(case)
                wrong[field] = hashlib.sha256(bytes.fromhex(case[field]) + b"wrong").hexdigest()
                cases.append(wrong)
    queries = []
    for n, case in enumerate(cases):
        case["id"] = str(n)
        if case["kind"] == "inclusion":
            queries.append(
                {
                    "id": case["id"],
                    "op": "verify_inclusion",
                    "leaf": case["leaf"],
                    "index": case["index"],
                    "size": case["size"],
                    "path": case["path"],
                    "root": case["root"],
                }
            )
        else:
            queries.append(
                {
                    "id": case["id"],
                    "op": "verify_consistency",
                    "first": case["first"],
                    "second": case["second"],
                    "path": case["path"],
                    "first_hash": case["first_root"],
                    "second_hash": case["second_root"],
                }
            )
    verdicts = go_verdicts(queries)
    assert len(verdicts) == len(cases)
    for case in cases:
        case["go_ok"] = verdicts[case["id"]]
    ct = {
        "leaves": CT_LEAVES,
        "roots": CT_ROOTS,
        "inclusion": [{"index": i, "size": s, "path": p} for i, s, p in CT_INCLUSION],
        "consistency": [{"first": a, "second": b, "path": p} for a, b, p in CT_CONSISTENCY],
    }
    out = {
        "source": "transparency-dev/merkle (Apache-2.0) reference tree and Go-library verdicts",
        "max_size": MAX_SIZE,
        "ct": ct,
        "cases": cases,
    }
    Path(__file__).with_name("merkle_reference.json").write_text(json.dumps(out, separators=(",", ":")))
    print(len(cases), "cases;", sum(c["go_ok"] for c in cases), "accepted by the Go library")


if __name__ == "__main__":
    main()
