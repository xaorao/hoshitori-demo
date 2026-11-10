"""E22: pending-command disaster recovery (an outside review 5). A command is acknowledged to its producer (a receipt id is returned) and must end in the ledger or in an
explicit FAILED_LOST record; it must never vanish. The segment log is the source of truth for committed history; pending commands live elsewhere, so what protects them is tested.
Protocols:
  SQLITE_ONLY      acknowledge after the SQLite outbox commit (the first design).
  JOURNAL          acknowledge only after the intent is appended to a separate intent journal and fsynced, then the outbox commit; recovery replays journal intents that are in
                   neither the ledger nor the outbox. The journal is replicated like the segments.
  JOURNAL+RECEIPTS JOURNAL, plus a reconciliation: the producer presents the receipt ids it holds; any acknowledged id found in neither the ledger, the journal nor the outbox is written to the
                   ledger as FAILED_LOST with a reason, so it is accounted for and visible.
Faults: SQLite restored from an older online backup; the same plus the journal tail lost (replication lag); SQLite and journal both replaced from a replica of the segments only.
Metric: acknowledged commands that end neither in the ledger nor as FAILED_LOST (silently omitted); duplicates in the ledger. Not product code."""
import json, os, random, shutil, sqlite3, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "E20"))
from ledger_writer import Writer, SEG


def ledger_ids(d):
    ids = []
    for l in open(os.path.join(d, SEG), "rb").read().split(b"\n"):
        if l:
            try: ids.append(json.loads(l)["id"])
            except Exception: pass
    return ids


def journal_ids(d):
    p = os.path.join(d, "intents.jsonl")
    return [json.loads(l)["id"] for l in open(p, "rb").read().split(b"\n") if l] if os.path.exists(p) else []


def ack(w, d, protocol, rid, body):
    if protocol != "SQLITE_ONLY":
        with open(os.path.join(d, "intents.jsonl"), "ab") as j:
            j.write((json.dumps({"id": rid, "body": body}) + "\n").encode()); j.flush(); os.fsync(j.fileno())
    w.submit(rid, body)
    return rid                                                    # the receipt returned to the producer


def run(protocol, fault, rng):
    d = tempfile.mkdtemp(prefix="e22_")
    try:
        w = Writer(d, mode="RECONCILING"); receipts = []; n = rng.randint(10, 30); b = rng.randint(2, n - 3); backup_journal_len = 0
        for i in range(n):
            receipts.append(ack(w, d, protocol, f"c{i}", {"i": i}))
            if rng.random() < 0.4: w.drain()
            if i + 1 == b:
                dst = sqlite3.connect(os.path.join(d, "backup.db")); w.db.backup(dst); dst.close()          # online backup API, never a file copy
                backup_journal_len = len(journal_ids(d))
        w.db.close()
        ledger_before = set(ledger_ids(d))
        # fault: the host's SQLite is replaced by the older backup
        for ext in ("", "-wal", "-shm"):
            p = os.path.join(d, "index.db" + ext)
            if os.path.exists(p): os.remove(p)
        shutil.copy(os.path.join(d, "backup.db"), os.path.join(d, "index.db"))
        jp = os.path.join(d, "intents.jsonl")
        if fault == "sqlite_backup_and_journal_tail_lost" and os.path.exists(jp):
            keep = [l for l in open(jp, "rb").read().split(b"\n") if l][:backup_journal_len]
            open(jp, "wb").write(b"".join(x + b"\n" for x in keep))
        if fault == "sqlite_and_journal_replaced_from_segment_replica" and os.path.exists(jp):
            os.remove(jp)
            for ext in ("", "-wal", "-shm"):
                p = os.path.join(d, "index.db" + ext)
                if os.path.exists(p): os.remove(p)
        # recovery
        w = Writer(d, mode="RECONCILING"); w.recover()
        if protocol != "SQLITE_ONLY":
            have = set(ledger_ids(d)) | {r[0] for r in w.db.execute("SELECT id FROM outbox")}
            for l in (open(jp, "rb").read().split(b"\n") if os.path.exists(jp) else []):
                if l:
                    o = json.loads(l)
                    if o["id"] not in have: w.submit(o["id"], o["body"])
        w.drain()
        if protocol == "JOURNAL+RECEIPTS":
            present = set(ledger_ids(d))
            for rid in receipts:
                if rid not in present:
                    w.submit(f"lost:{rid}", {"state": "FAILED_LOST", "ref": rid, "reason": "acknowledged_before_first_append_and_not_recoverable"}); 
            w.drain()
        ids = ledger_ids(d); w.db.close()
        accounted = set(ids) | {x[5:] for x in ids if x.startswith("lost:")}
        silently_omitted = [r for r in receipts if r not in accounted]
        dup = len(ids) - len(set(ids))
        lost_before_any_recovery = [r for r in receipts if r not in ledger_before]
        return {"acked": len(receipts), "silently_omitted": len(silently_omitted), "duplicates": dup, "failed_lost_records": sum(1 for x in ids if x.startswith("lost:")),
                "pending_at_fault": len(lost_before_any_recovery)}
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    faults = ["sqlite_restored_from_older_backup", "sqlite_backup_and_journal_tail_lost", "sqlite_and_journal_replaced_from_segment_replica"]
    out, N = {}, 150
    for protocol in ("SQLITE_ONLY", "JOURNAL", "JOURNAL+RECEIPTS"):
        out[protocol] = {}
        for f in faults:
            rng = random.Random(2022); agg = {"rounds": N, "rounds_with_silent_omission": 0, "silently_omitted_commands": 0, "duplicates": 0, "failed_lost_records": 0, "acked": 0, "pending_at_fault": 0}
            for _ in range(N):
                r = run(protocol, f, rng)
                agg["rounds_with_silent_omission"] += r["silently_omitted"] > 0; agg["silently_omitted_commands"] += r["silently_omitted"]
                agg["duplicates"] += r["duplicates"]; agg["failed_lost_records"] += r["failed_lost_records"]; agg["acked"] += r["acked"]; agg["pending_at_fault"] += r["pending_at_fault"]
            out[protocol][f] = agg
    json.dump(out, open(os.path.join(HERE, "E22_results.json"), "w"), indent=1)
    for p, v in out.items():
        print(p)
        for f, a in v.items(): print("  %-52s rounds_silent %3d/%d  silent_cmds %4d  failed_lost %4d  dup %d  (acked %d, pending at fault %d)" % (f, a["rounds_with_silent_omission"], a["rounds"], a["silently_omitted_commands"], a["failed_lost_records"], a["duplicates"], a["acked"], a["pending_at_fault"]))
