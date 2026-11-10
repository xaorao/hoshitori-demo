#!/usr/bin/env python3
"""E4: DBOS Transact (SQLite) against the plain outbox under the same kill -9 rounds. Compares: duplicates in the ledger file,
workflows left unfinished after recovery, lost records, and throughput. Run with the E4 venv python."""
import json, os, random, signal, subprocess, sys, tempfile, time
from pathlib import Path
HERE = Path(__file__).resolve().parent
PY = sys.executable
ROUNDS = int(sys.argv[1]) if len(sys.argv) > 1 else 30
work = Path(tempfile.mkdtemp(prefix="e4_"))
rng = random.Random(4)
t_alive = 0.0
for r in range(ROUNDS):
    p = subprocess.Popen([PY, "-I", str(HERE / "dbos_child.py"), str(work), str(r)], env={"PATH": "/usr/bin:/bin"}, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    life = rng.uniform(1.5, 3.0)  # startup + recovery + some work
    time.sleep(life); t_alive += life
    os.kill(p.pid, signal.SIGKILL); p.wait()
# final recovery round: start, wait for recovery to finish, stop cleanly
p = subprocess.Popen([PY, "-I", str(HERE / "dbos_child.py"), str(work), "final"], env={"PATH": "/usr/bin:/bin"}, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
time.sleep(8); os.kill(p.pid, signal.SIGKILL); p.wait()
ids = []
tail_torn = 0
for line in (work / "ledger.jsonl").read_text().splitlines():
    try:
        ids.append(json.loads(line)["id"])
    except Exception:
        tail_torn += 1
dups = len(ids) - len(set(ids))
done = (work / "done.log").read_text().split() if (work / "done.log").exists() else []
import sqlite3
con = sqlite3.connect(work / "dbos.sqlite")
rows = con.execute("select status, count(*) from workflow_status group by status").fetchall()
pending_ids = [r[0] for r in con.execute("select workflow_uuid from workflow_status where status in ('PENDING','ENQUEUED')").fetchall()]
# a workflow that was killed before its first step is still PENDING after the final round only if recovery missed it
res = {"rounds": ROUNDS, "ledger_lines": len(ids), "unique_ids": len(set(ids)), "duplicates_in_ledger": dups, "torn_lines": tail_torn,
       "done_marks": len(done), "done_unique": len(set(done)), "workflow_status_counts": dict(rows), "pending_after_final_recovery": len(pending_ids),
       "ledger_ids_without_done_mark": len(set(ids) - set(done)), "done_marks_without_ledger_row": len(set(done) - set(ids)),
       "approx_workflows_per_second_while_alive": round(len(ids) / t_alive, 1)}
(HERE / "E4_results.json").write_text(json.dumps(res, indent=1))
print(json.dumps(res, indent=1))
