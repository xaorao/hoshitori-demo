#!/usr/bin/env python3
"""E12b: repeat the naive copy of only the main database file of a live WAL database 60 times (small database, continuous writer with
auto-checkpoints), and classify each copy: opens and verifies, opens but misses committed rows that were visible, or fails."""
import json, os, shutil, sqlite3, sys, tempfile, time, multiprocessing as mp
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_e12 as e
e.SEED_ROWS = 20000
if __name__ == "__main__":
    d = tempfile.mkdtemp(prefix="e12b-", dir=os.path.join(os.environ["CLAUDE_JOB_DIR"], "tmp") if "CLAUDE_JOB_DIR" in os.environ else None)
    db = os.path.join(d, "live.db"); e.seed(db)
    p, q = e.run_writer(db, 40)
    time.sleep(1)
    errs = {}
    out = {"copies": 0, "ok": 0, "integrity_not_ok": 0, "open_error": 0, "chain_broken": 0, "behind_live_rows": []}
    for i in range(60):
        live = e.count(db)
        cp = os.path.join(d, f"n{i}.db"); shutil.copyfile(db, cp)
        out["copies"] += 1
        try:
            ok, last, ic = e.chain_ok(cp)
            if ic != "ok": out["integrity_not_ok"] += 1
            elif not ok: out["chain_broken"] += 1
            else: out["ok"] += 1
            out["behind_live_rows"].append(live - e.count(cp))
        except Exception as ex:
            out["open_error"] += 1; k = type(ex).__name__ + ": " + str(ex)[:60]; errs[k] = errs.get(k, 0) + 1
        os.remove(cp); time.sleep(0.2)
    q.get(); p.join(); out["error_kinds"] = errs
    b = out["behind_live_rows"]; out["behind_live_rows"] = {"min": min(b), "median": sorted(b)[len(b)//2], "max": max(b)}
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E12b_results.json"), "w"), indent=1)
    shutil.rmtree(d, ignore_errors=True); print(json.dumps(out, indent=1))
