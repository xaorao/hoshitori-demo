#!/usr/bin/env python3
"""E12: online backup of a live SQLite database in WAL mode while a single writer commits durable transactions.
Questions: (1) does the backup API produce a consistent, restorable copy (integrity_check ok, row count between the counts before and after,
every row's hash chain intact)? (2) what does it cost the writer (commit latency p50/p99/max with and without the backup)?
(3) does a plain file copy of the main file alone (no WAL) lose committed rows or corrupt? Local disk, this machine; process-kill durability only,
not power loss. Run: python3 -I run_e12.py [rows_seed] [seconds]"""
import hashlib, json, multiprocessing as mp, os, shutil, sqlite3, statistics, sys, tempfile, time

SEED_ROWS = int(sys.argv[1]) if len(sys.argv) > 1 else 300_000
SECONDS = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
PAYLOAD = "x" * 900


def seed(path):
    c = sqlite3.connect(path); c.execute("PRAGMA journal_mode=WAL"); c.execute("PRAGMA synchronous=FULL")
    c.execute("CREATE TABLE ledger(seq INTEGER PRIMARY KEY, prev TEXT, h TEXT, body TEXT)")
    prev = "0" * 64
    c.execute("BEGIN")
    for i in range(1, SEED_ROWS + 1):
        h = hashlib.sha256((prev + str(i) + PAYLOAD).encode()).hexdigest()
        c.execute("INSERT INTO ledger VALUES(?,?,?,?)", (i, prev, h, PAYLOAD)); prev = h
    c.execute("COMMIT"); c.close()


def writer(path, stop_at, q):
    c = sqlite3.connect(path, isolation_level=None, timeout=30); c.execute("PRAGMA synchronous=FULL")
    row = c.execute("SELECT seq,h FROM ledger ORDER BY seq DESC LIMIT 1").fetchone(); seq, prev = row
    lat = []
    while time.time() < stop_at:
        t = time.perf_counter()
        c.execute("BEGIN IMMEDIATE")
        seq += 1; h = hashlib.sha256((prev + str(seq) + PAYLOAD).encode()).hexdigest()
        c.execute("INSERT INTO ledger VALUES(?,?,?,?)", (seq, prev, h, PAYLOAD)); prev = h
        c.execute("COMMIT")
        lat.append(time.perf_counter() - t)
    q.put({"commits": len(lat), "p50_ms": statistics.median(lat) * 1e3, "p99_ms": sorted(lat)[int(len(lat) * .99) - 1] * 1e3, "max_ms": max(lat) * 1e3, "last_seq": seq})


def count(path):
    c = sqlite3.connect(path); n = c.execute("SELECT count(*) FROM ledger").fetchone()[0]; c.close(); return n


def chain_ok(path):
    c = sqlite3.connect(path); prev = "0" * 64; ok = True; n = 0
    for seq, p, h, b in c.execute("SELECT seq,prev,h,body FROM ledger ORDER BY seq"):
        if p != prev or h != hashlib.sha256((p + str(seq) + b).encode()).hexdigest(): ok = False; break
        prev = h; n = seq
    ic = c.execute("PRAGMA integrity_check").fetchone()[0]; c.close(); return ok, n, ic


def run_writer(path, seconds):
    q = mp.get_context("spawn").Queue(); p = mp.get_context("spawn").Process(target=writer, args=(path, time.time() + seconds, q)); p.start(); return p, q


if __name__ == "__main__":
    d = tempfile.mkdtemp(prefix="e12-", dir=os.environ.get("CLAUDE_JOB_DIR", None) and os.path.join(os.environ["CLAUDE_JOB_DIR"], "tmp"))
    db = os.path.join(d, "live.db"); seed(db)
    size_mb = os.path.getsize(db) / 1e6
    res = {"seed_rows": SEED_ROWS, "db_mb": round(size_mb, 1)}
    # baseline: writer alone
    p, q = run_writer(db, SECONDS); r = q.get(); p.join(); res["writer_alone"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
    # with an online backup running part-way through (incremental, 2000 pages per step)
    p, q = run_writer(db, SECONDS + 4); time.sleep(1.5)
    n_before = count(db); out = os.path.join(d, "backup.db"); t0 = time.perf_counter()
    src = sqlite3.connect(db, timeout=30); dst = sqlite3.connect(out)
    src.backup(dst, pages=2000, sleep=0.0); dst.close(); src.close()
    dur = time.perf_counter() - t0; n_after = count(db)
    r = q.get(); p.join()
    ok, last, ic = chain_ok(out); nb = count(out)
    res["writer_during_backup"] = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in r.items()}
    res["backup"] = {"seconds": round(dur, 2), "rows_in_backup": nb, "rows_before": n_before, "rows_after": n_after, "between_counts": n_before <= nb <= n_after,
                     "hash_chain_ok": ok, "integrity_check": ic}
    # naive copy of the main file only while a writer runs (no -wal), then open it
    p, q = run_writer(db, 4); time.sleep(1.0); naive = os.path.join(d, "naive.db"); shutil.copyfile(db, naive)
    n_live = count(db); q.get(); p.join()
    try:
        okn, lastn, icn = chain_ok(naive); nn = count(naive)
        res["naive_main_file_copy"] = {"rows": nn, "rows_live_at_copy_time_at_least": n_live, "hash_chain_ok": okn, "integrity_check": icn}
    except Exception as e:
        res["naive_main_file_copy"] = {"error": str(e)}
    json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E12_results.json"), "w"), indent=1)
    shutil.rmtree(d, ignore_errors=True)
    print(json.dumps(res, indent=1))
