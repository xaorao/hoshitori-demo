#!/usr/bin/env python3
"""E12c: 60 online backups (sqlite3 backup API, 200 pages per step) of a live WAL database with a continuous durable writer.
Each backup is checked: integrity_check ok, hash chain intact from seq 1, row count between the counts before and after."""
import json, os, shutil, sqlite3, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import run_e12 as e
e.SEED_ROWS = 20000
if __name__ == "__main__":
    d = tempfile.mkdtemp(prefix="e12c-", dir=os.path.join(os.environ["CLAUDE_JOB_DIR"], "tmp") if "CLAUDE_JOB_DIR" in os.environ else None)
    db = os.path.join(d, "live.db"); e.seed(db)
    p, q = e.run_writer(db, 60); time.sleep(1)
    out = {"backups": 0, "all_ok": 0, "bad": 0, "between_counts_violations": 0}
    for i in range(60):
        b0 = e.count(db); dst = os.path.join(d, f"b{i}.db")
        s = sqlite3.connect(db, timeout=30); t = sqlite3.connect(dst); s.backup(t, pages=200, sleep=0.0); t.close(); s.close()
        b1 = e.count(db); ok, last, ic = e.chain_ok(dst); n = e.count(dst)
        out["backups"] += 1
        if ok and ic == "ok": out["all_ok"] += 1
        else: out["bad"] += 1
        if not (b0 <= n <= b1): out["between_counts_violations"] += 1
        os.remove(dst)
    q.get(); p.join()
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E12c_results.json"), "w"), indent=1)
    shutil.rmtree(d, ignore_errors=True); print(json.dumps(out, indent=1))
