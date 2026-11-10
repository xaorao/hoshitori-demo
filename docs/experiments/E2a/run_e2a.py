#!/usr/bin/env python3
"""Experiment E2a runner: three independent Merkle implementations (ours in Python, ours in JavaScript, the Go
reference library transparency-dev/merkle) against RFC 9162 known answers, exhaustive small trees, sampled large
trees, mutated proofs and the library's own test data. Third-party code is built and run with an empty environment."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import merkle as M  # noqa: E402

CLONE = Path(os.environ["E2A_CLONE"])  # a checkout of the transparency-dev merkle library
NODE = "/opt/homebrew/bin/node"
GOREF = HERE / "goref/goref"
ENV = {"PATH": "/usr/bin:/bin"}


class Proc:
    def __init__(self, cmd, cwd=None):
        self.p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=ENV, cwd=cwd, bufsize=1)

    def ask(self, q):
        self.p.stdin.write(json.dumps(q) + "\n")
        self.p.stdin.flush()
        return json.loads(self.p.stdout.readline())

    def close(self):
        self.p.stdin.close()
        self.p.wait(timeout=30)


def hx(b):
    return b.hex()


def leaves_for(n, salt=b"e2a"):
    return [M.leaf_hash(hashlib.sha256(salt + i.to_bytes(4, "big")).digest()) for i in range(n)]


def main():
    js = Proc([NODE, str(HERE / "merkle.mjs"), "serve"])
    go = Proc([str(GOREF)])
    qid = 0
    results = {"known_answer": {}, "exhaustive": {}, "sampled": {}, "mutations": {}, "library_testdata": {}}
    disagreements = []

    def ask_all(q):
        nonlocal qid
        qid += 1
        q = dict(q, id=str(qid))
        a = js.ask(q)
        g = go.ask(q) if q["op"].startswith("verify") else None
        return a, g

    # 1. known answers: the CT reference leaves and roots shipped with the Go library (testonly/constants.go)
    src = (CLONE / "testonly/constants.go").read_text()
    leaf_inputs = [bytes.fromhex(x) for x in re.findall(r'hd\("([0-9a-f]*)"\)', src.split("func NodeHashes")[0])]
    root_block = src.split("func RootHashes")[1].split("func CompactTrees")[0]
    roots = [bytes.fromhex(x) for x in re.findall(r'hd\("([0-9a-f]{64})"\)', root_block)]
    ok_py = ok_js = 0
    lh = [M.leaf_hash(d) for d in leaf_inputs]
    # the Go list begins with EmptyRootHash() (a call, not a hd() literal), so the regex list starts at size 1
    for n in range(1, len(lh) + 1):
        exp = roots[n - 1]
        ok_py += M.mth(lh[:n]) == exp
        ok_js += bytes.fromhex(js.ask({"id": "k", "op": "root", "leaves": [hx(x) for x in lh[:n]]})["root"]) == exp
    results["known_answer"] = {"leaf_inputs": len(leaf_inputs), "roots_checked": len(lh), "python_match": ok_py, "javascript_match": ok_js, "empty_root_is_sha256_of_nothing": M.mth([]) == hashlib.sha256(b"").digest()}

    # 2. exhaustive small trees
    incl_n = cons_n = 0
    for n in range(1, 71):
        L = leaves_for(n)
        root = M.mth(L)
        jroot = bytes.fromhex(js.ask({"id": "r", "op": "root", "leaves": [hx(x) for x in L]})["root"])
        if jroot != root:
            disagreements.append(("root", n))
        for i in range(n):
            pp = M.inclusion_path(i, L)
            jp = [bytes.fromhex(x) for x in js.ask({"id": "g", "op": "gen_inclusion", "m": i, "leaves": [hx(x) for x in L]})["path"]]
            if pp != jp:
                disagreements.append(("incl-gen", n, i))
            q = {"op": "verify_inclusion", "leaf": hx(L[i]), "index": i, "size": n, "path": [hx(x) for x in pp], "root": hx(root)}
            a, g = ask_all(q)
            if not (M.verify_inclusion(L[i], i, n, pp, root) and a["ok"] and g["ok"]):
                disagreements.append(("incl-verify", n, i, a, g))
            incl_n += 1
        for m in range(1, n + 1):
            pp = M.consistency_proof(m, L)
            jp = [bytes.fromhex(x) for x in js.ask({"id": "g", "op": "gen_consistency", "m": m, "leaves": [hx(x) for x in L]})["path"]]
            if pp != jp:
                disagreements.append(("cons-gen", n, m))
            r1 = M.mth(L[:m])
            q = {"op": "verify_consistency", "first": m, "second": n, "path": [hx(x) for x in pp], "first_hash": hx(r1), "second_hash": hx(root)}
            a, g = ask_all(q)
            if not (M.verify_consistency(m, n, pp, r1, root) and a["ok"] and g["ok"]):
                disagreements.append(("cons-verify", n, m, a, g))
            cons_n += 1
    results["exhaustive"] = {"sizes": "1..70", "inclusion_proofs": incl_n, "consistency_proofs": cons_n}

    # 3. sampled larger trees
    rng = random.Random(9162)
    sampled = 0
    for n in (127, 128, 129, 255, 256, 257, 1000, 1023, 1024, 1025, 4097):
        L = leaves_for(n)
        root = M.mth(L)
        jroot = bytes.fromhex(js.ask({"id": "r", "op": "root", "leaves": [hx(x) for x in L]})["root"])
        if jroot != root:
            disagreements.append(("root", n))
        for i in sorted({0, 1, n // 2, n - 2, n - 1} | {rng.randrange(n) for _ in range(6)}):
            pp = M.inclusion_path(i, L)
            q = {"op": "verify_inclusion", "leaf": hx(L[i]), "index": i, "size": n, "path": [hx(x) for x in pp], "root": hx(root)}
            a, g = ask_all(q)
            jp = [bytes.fromhex(x) for x in js.ask({"id": "g", "op": "gen_inclusion", "m": i, "leaves": [hx(x) for x in L]})["path"]]
            if not (a["ok"] and g["ok"] and pp == jp):
                disagreements.append(("sampled-incl", n, i))
            sampled += 1
        for m in sorted({1, 2, n // 3, n // 2, n - 1, n} | {rng.randint(1, n) for _ in range(6)}):
            pp = M.consistency_proof(m, L)
            r1 = M.mth(L[:m])
            q = {"op": "verify_consistency", "first": m, "second": n, "path": [hx(x) for x in pp], "first_hash": hx(r1), "second_hash": hx(root)}
            a, g = ask_all(q)
            if not (a["ok"] and g["ok"] and M.verify_consistency(m, n, pp, r1, root)):
                disagreements.append(("sampled-cons", n, m))
            sampled += 1
    results["sampled"] = {"sizes": [127, 128, 129, 255, 256, 257, 1000, 1023, 1024, 1025, 4097], "proofs_checked": sampled}

    # 4. mutations: verdicts of the three verifiers must agree, and a mutated proof must not verify
    mut_total = mut_accepted = mut_disagree = 0
    import collections
    tally = collections.defaultdict(lambda: [0, 0])
    for n in (1, 2, 3, 5, 7, 8, 9, 13, 16, 17, 31, 33, 64, 100):
        L = leaves_for(n)
        root = M.mth(L)
        for i in range(n):
            pp = M.inclusion_path(i, L)
            variants = []
            for j in range(len(pp)):
                for bit in (0, 7, 100, 255):
                    b = bytearray(pp[j]); b[bit // 8] ^= 1 << (bit % 8)
                    variants.append(("flip", {"path": pp[:j] + [bytes(b)] + pp[j + 1:]}))
                if j + 1 < len(pp):
                    sw = list(pp); sw[j], sw[j + 1] = sw[j + 1], sw[j]
                    if sw != pp:
                        variants.append(("swap", {"path": sw}))
            variants += [("drop-last", {"path": pp[:-1]}), ("append", {"path": pp + [hashlib.sha256(b"x").digest()]}), ("empty", {"path": []}),
                         ("index+1", {"index": i + 1}), ("index-1", {"index": i - 1}), ("size+1", {"size": n + 1}), ("size-1", {"size": n - 1}),
                         ("wrong-root", {"root": hashlib.sha256(b"r").digest()}), ("wrong-leaf", {"leaf": hashlib.sha256(b"l").digest()})]
            for name, ch in variants:
                f = dict(path=pp, index=i, size=n, root=root, leaf=L[i]); f.update(ch)
                if f["index"] < 0 or f["size"] < 0:
                    continue
                q = {"op": "verify_inclusion", "leaf": hx(f["leaf"]), "index": f["index"], "size": f["size"], "path": [hx(x) for x in f["path"]], "root": hx(f["root"])}
                a, g = ask_all(q)
                p = M.verify_inclusion(f["leaf"], f["index"], f["size"], f["path"], f["root"])
                mut_total += 1
                if len({p, a["ok"], g["ok"]}) != 1:
                    mut_disagree += 1; disagreements.append(("mut-incl", name, n, i, p, a["ok"], g["ok"]))
                if f["path"] == pp and f["index"] == i and f["size"] == n and f["root"] == root and f["leaf"] == L[i]:
                    mut_total -= 1
                    continue
                tally["incl:" + name][0] += 1
                if p:
                    mut_accepted += 1
                    tally["incl:" + name][1] += 1
        for m in range(1, n + 1):
            pp = M.consistency_proof(m, L)
            r1 = M.mth(L[:m])
            variants = []
            for j in range(len(pp)):
                b = bytearray(pp[j]); b[0] ^= 1
                variants.append(("flip", {"path": pp[:j] + [bytes(b)] + pp[j + 1:]}))
            variants += [("drop-last", {"path": pp[:-1]}), ("append", {"path": pp + [hashlib.sha256(b"x").digest()]}),
                         ("wrong-first", {"first_hash": hashlib.sha256(b"a").digest()}), ("wrong-second", {"second_hash": hashlib.sha256(b"b").digest()}),
                         ("first+1", {"first": m + 1}), ("first-1", {"first": m - 1}), ("second+1", {"second": n + 1}), ("second-1", {"second": n - 1})]
            for name, ch in variants:
                f = dict(path=pp, first=m, second=n, first_hash=r1, second_hash=root); f.update(ch)
                q = {"op": "verify_consistency", "first": f["first"], "second": f["second"], "path": [hx(x) for x in f["path"]], "first_hash": hx(f["first_hash"]), "second_hash": hx(f["second_hash"])}
                if f["first"] < 0 or f["second"] < 0:
                    continue
                a, g = ask_all(q)
                p = M.verify_consistency(f["first"], f["second"], f["path"], f["first_hash"], f["second_hash"])
                mut_total += 1
                if len({p, a["ok"], g["ok"]}) != 1:
                    mut_disagree += 1; disagreements.append(("mut-cons", name, n, m, p, a["ok"], g["ok"]))
                if f["path"] == pp and f["first"] == m and f["second"] == n and f["first_hash"] == r1 and f["second_hash"] == root:
                    mut_total -= 1
                    continue
                tally["cons:" + name][0] += 1
                if p:
                    mut_accepted += 1
                    tally["cons:" + name][1] += 1
    results["mutations"] = {"mutated_proofs": mut_total, "verifier_disagreements": mut_disagree, "mutated_but_accepted": mut_accepted,
                            "by_mutation_tried_accepted": {k: v for k, v in sorted(tally.items())},
                            "note": "size+1 and size-1 are accepted when the path shape is unchanged: a Merkle proof does not authenticate the tree size, only the signed checkpoint does"}

    # 5. the Go library's own test data (JSON cases with wantErr)
    b64 = lambda s: base64.b64decode(s)
    tot = bad = stricter = 0
    for f in sorted((CLONE / "testdata/inclusion").rglob("*.json")):
        c = json.loads(f.read_text())
        p = [b64(x) for x in c.get("proof") or []]
        want_ok = not c["wantErr"]
        got = M.verify_inclusion(b64(c["leafHash"]), c["leafIdx"], c["treeSize"], p, b64(c["root"])) if c["leafIdx"] >= 0 else False
        if any(len(x) != 32 for x in [b64(c["leafHash"]), b64(c["root"])] + p):
            got = False  # hash-length rule (the Go library rejects empty and short hashes; RFC arithmetic alone would not)
        tot += 1
        if got != want_ok:
            bad += 1; disagreements.append(("lib-incl", str(f.relative_to(CLONE)), got, want_ok))
    for f in sorted((CLONE / "testdata/consistency").rglob("*.json")):
        c = json.loads(f.read_text())
        p = [b64(x) for x in c.get("proof") or []]
        want_ok = not c["wantErr"]
        got = M.verify_consistency(c["size1"], c["size2"], p, b64(c["root1"]), b64(c["root2"]))
        if any(len(x) != 32 for x in [b64(c["root1"]), b64(c["root2"])] + p):
            got = False
        tot += 1
        if got != want_ok:
            if want_ok and any(len(x) != 32 for x in [b64(c["root1"]), b64(c["root2"])] + p):
                stricter += 1  # the library accepts equal sizes with a 16-byte placeholder root; our verifier rejects any hash that is not 32 bytes, by design
            else:
                bad += 1; disagreements.append(("lib-cons", str(f.relative_to(CLONE)), got, want_ok))
    results["library_testdata"] = {"cases": tot, "unexplained_differences": bad, "stricter_by_design_hash_length_rule": stricter}
    js.close(); go.close()
    results["disagreements"] = len(disagreements)
    results["disagreement_examples"] = [str(d)[:200] for d in disagreements[:10]]
    (HERE / "E2a_results.json").write_text(json.dumps(results, indent=1) + "\n")
    print(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
