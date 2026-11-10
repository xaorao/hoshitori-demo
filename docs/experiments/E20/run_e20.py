#!/usr/bin/env python3
"""E20 driver: kill the writer at every crash point (real os._exit), restart it in recovery, finish the outbox, check the invariants. Then simulate
file-level power-loss outcomes (torn segment tail, lost last index commits, a flipped byte in the last line) on top of a clean run.
macOS APFS, process-kill and file-level simulation only: this does NOT show real power-loss durability."""
import json, os, random, shutil, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ledger_writer as L
HERE = os.path.dirname(os.path.abspath(__file__)); PY = sys.executable
base = os.path.join(os.environ.get("CLAUDE_JOB_DIR", tempfile.gettempdir()), "tmp") if "CLAUDE_JOB_DIR" in os.environ else tempfile.gettempdir()

def child(d, mode, crash_at, after, n, start):
    return subprocess.run([PY, "-I", os.path.join(HERE, "writer_child.py"), d, mode, crash_at or "-", str(after), str(n), str(start)], capture_output=True, text=True).returncode

def round_(mode, crash_at, rng):
    d = tempfile.mkdtemp(prefix="e20-", dir=base)
    n = rng.randint(3, 25); after = rng.randint(0, n - 1)
    rc1 = child(d, mode, crash_at, after, n, 0)            # killed somewhere
    rc2 = child(d, mode, None, 0, 0, 0)                     # restart: recovery, then drain the outbox, no new records
    r = L.check(d); shutil.rmtree(d, ignore_errors=True); r["first_exit"] = rc1; r["second_exit"] = rc2; return r

out = {}
for mode in ("NAIVE", "RECONCILING"):
    rng = random.Random(20)
    res = {}
    for cp in L.CRASH_POINTS:
        agg = {"rounds": 0, "rounds_with_any_violation": 0, "duplicate_lines_in_segment": 0, "seq_not_gapless": 0, "leaf_mismatch": 0, "torn_tail": 0, "index_differs_from_segment": 0, "outbox_pending": 0}
        for _ in range(60):
            r = round_(mode, cp, rng); agg["rounds"] += 1
            bad = [k for k in agg if k not in ("rounds", "rounds_with_any_violation") and r.get(k)]
            if bad: agg["rounds_with_any_violation"] += 1
            for k in agg:
                if k not in ("rounds", "rounds_with_any_violation"): agg[k] += int(bool(r.get(k)))
        res[cp] = agg
    out[mode] = res

# file-level power-loss simulations on the RECONCILING protocol
sim = {}
rng = random.Random(21)
def clean(n=30):
    d = tempfile.mkdtemp(prefix="e20s-", dir=base); child(d, "RECONCILING", None, 0, n, 0); return d
import sqlite3
def finish(d):
    child(d, "RECONCILING", None, 0, 0, 0); r = L.check(d); shutil.rmtree(d, ignore_errors=True); return r
def tally(name, fn, rounds=60):
    bad = 0; lost = 0
    for _ in range(rounds):
        d = clean(); fn(d); r = finish(d)
        if any(r[k] for k in ("duplicate_lines_in_segment", "seq_not_gapless", "leaf_mismatch", "torn_tail", "index_differs_from_segment", "outbox_pending")): bad += 1
        lost += int(r["lines"] != 30)
    sim[name] = {"rounds": rounds, "rounds_with_violation": bad, "rounds_where_a_record_was_lost_from_the_final_segment": lost}
def torn(d):
    p = os.path.join(d, L.SEG); size = os.path.getsize(p); k = rng.randint(1, 300)
    with open(p, "r+b") as f: f.truncate(max(0, size - k))
def lost_index(d):
    db = sqlite3.connect(os.path.join(d, "index.db"), isolation_level=None); k = rng.randint(1, 5)
    db.execute("DELETE FROM ledger WHERE seq >= (SELECT max(seq) - ? FROM ledger)", (k,))
def flip(d):
    p = os.path.join(d, L.SEG); b = bytearray(open(p, "rb").read()); b[-rng.randint(5, 60)] ^= 0xFF; open(p, "wb").write(bytes(b))
def torn_with_index(d):
    # the segment lost its tail but the index still has the rows (segment not durable, index durable): the dangerous order
    torn(d)
tally("torn segment tail (index rows still present)", torn_with_index); tally("last index commits lost (segment intact)", lost_index); tally("byte flipped in the last line", flip)
out["file_level_simulations_RECONCILING"] = sim
json.dump(out, open(os.path.join(HERE, "E20_results.json"), "w"), indent=1)
for m in ("NAIVE", "RECONCILING"):
    print(m, {cp: (v["rounds_with_any_violation"], v["rounds"]) for cp, v in out[m].items()})
print(json.dumps(sim, indent=1))
