#!/usr/bin/env python3
"""E17: ledger-level hostile vectors (an outside review: replacement, truncation, reordering). A log of canonical records is committed by a checkpoint
(size, root) taken earlier; a hostile operator then presents altered segments. The verifier recomputes the tree head from the presented records and
compares with every earlier checkpoint through consistency proofs. Each attack is run at many sizes and positions; the expectation is 100 percent detection
when an earlier checkpoint is held, and the experiment also records what is NOT detectable without one (a pure suffix truncation before any checkpoint)."""
import json, os, random, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "E2a"))
import merkle as M

def rec(i): return json.dumps({"spec": "hoshitori.record/1", "type": "evidence", "id": f"ev{i:05d}", "n": i}, separators=(",", ":"), sort_keys=True).encode()
def leaves(rs): return [M.leaf_hash(r) for r in rs]

rng = random.Random(17)
out = {"trials": 0, "detected": 0, "undetected": []}
attacks = ["replace", "reorder", "truncate_tail", "insert_middle", "duplicate", "drop_middle", "append_fork"]
per = {a: [0, 0] for a in attacks}
for trial in range(2000):
    n = rng.randint(8, 200)
    records = [rec(i) for i in range(n)]
    ck_size = rng.randint(1, n)                   # a checkpoint a witness or time-stamp token holds
    ck_root = M.mth(leaves(records[:ck_size]))
    final_root = M.mth(leaves(records))
    a = rng.choice(attacks)
    hostile = list(records)
    if a == "replace": i = rng.randrange(ck_size); hostile[i] = rec(10_000 + i)
    elif a == "reorder":
        if ck_size < 2: continue
        i, j = rng.sample(range(ck_size), 2); hostile[i], hostile[j] = hostile[j], hostile[i]
    elif a == "truncate_tail": hostile = hostile[: rng.randint(0, ck_size - 1)] if ck_size > 0 else hostile
    elif a == "insert_middle": i = rng.randrange(ck_size); hostile.insert(i, rec(20_000))
    elif a == "duplicate":
        if ck_size < 2: continue
        i = rng.randrange(ck_size - 1); hostile.insert(i, hostile[i])   # i = ck_size - 1 would only push the copy beyond the checkpoint (an append, not an attack on the prefix); the first version of this test counted those as misses
    elif a == "drop_middle": i = rng.randrange(ck_size); del hostile[i]
    elif a == "append_fork": hostile = hostile[:ck_size] + [rec(30_000 + k) for k in range(rng.randint(1, 5))]   # honest prefix, different suffix: NOT an attack on the prefix
    per[a][0] += 1; out["trials"] += 1
    # verifier: does the presented log contain a prefix of length ck_size whose root equals the held checkpoint root?
    ok_prefix = len(hostile) >= ck_size and M.mth(leaves(hostile[:ck_size])) == ck_root
    detected = not ok_prefix
    # also check consistency between the held checkpoint and the presented head using a proof generated from the presented log
    if ok_prefix and len(hostile) > ck_size:
        proof = M.consistency_proof(ck_size, leaves(hostile))
        assert M.verify_consistency(ck_size, len(hostile), proof, ck_root, M.mth(leaves(hostile)))
    if a == "append_fork": detected = False   # prefix intact: by design not an alarm on the prefix; a second checkpoint at the larger size would disagree only if one was published
    if detected: out["detected"] += 1; per[a][1] += 1
    elif a != "append_fork": out["undetected"].append({"attack": a, "n": n, "ck_size": ck_size})
out["per_attack_trials_and_detected"] = per
# a pure truncation before any checkpoint of that size is invisible
out["note"] = "truncate_tail below the held checkpoint size is detected; truncation of records newer than every held checkpoint is not detectable from the log alone, which is why the cycle heartbeat, the outbox and the independent tokens exist (ledger spec, section 7)"
json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E17_results.json"), "w"), indent=1)
print(json.dumps({k: out[k] for k in ("trials", "detected", "per_attack_trials_and_detected")}, indent=1)); print("undetected (excluding append_fork):", len(out["undetected"]))
