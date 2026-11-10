"""E4 child process: a three-step pipeline as a DBOS workflow on SQLite. Steps: build record, append to a ledger file
(fsync, idempotent by record id), mark done. Run until killed. Usage: dbos_child.py WORKDIR ROUND"""
import json, os, sys, time
from dbos import DBOS, SetWorkflowID

work, rnd = sys.argv[1], sys.argv[2]
DBOS(config={"name": "e4-test", "system_database_url": f"sqlite:///{work}/dbos.sqlite", "run_admin_server": False, "log_level": "ERROR"})
LEDGER = os.path.join(work, "ledger.jsonl")


@DBOS.step()
def build(rid: str) -> dict:
    return {"id": rid, "body": "x" * 200}


@DBOS.step()
def append(rec: dict) -> int:
    seen = set()
    if os.path.exists(LEDGER):
        with open(LEDGER) as f:
            for line in f:
                try:
                    seen.add(json.loads(line)["id"])
                except Exception:
                    pass  # torn last line
    if rec["id"] in seen:
        return 0  # idempotent: already appended before the crash
    with open(LEDGER, "a") as f:
        f.write(json.dumps(rec) + "\n")
        f.flush()
        os.fsync(f.fileno())
    return 1


@DBOS.step()
def mark(rid: str) -> None:
    with open(os.path.join(work, "done.log"), "a") as f:
        f.write(rid + "\n")
        f.flush()
        os.fsync(f.fileno())


@DBOS.workflow()
def pipeline(rid: str) -> None:
    rec = build(rid)
    append(rec)
    mark(rid)


if __name__ == "__main__":
    DBOS.launch()  # recovers pending workflows of earlier rounds
    i = 0
    while True:
        i += 1
        rid = f"r{rnd}-{i}"
        with SetWorkflowID(rid):
            pipeline(rid)
