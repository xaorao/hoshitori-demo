"""E20: the ledger writer's dual write (an append-only segment file, then a SQLite index row and the outbox mark) with crash injection.
Two protocols are implemented so the difference is measured, not argued:
  NAIVE        the protocol of DOMAIN_MODEL section 7 as first written: on restart, repeat 'append, index, mark done' for every outbox row without an index row;
  RECONCILING  the segment is the source of truth: on start the tail of the segment is scanned and reconciled against the index before any append, torn tails are
               truncated, index rows pointing beyond the end of the segment are removed and their outbox rows reopened.
Records are canonical bytes; the leaf hash is SHA-256(0x00 || bytes) (RFC 9162). Not product code."""
import hashlib, json, os, sqlite3

SEG = "segment-000000000000.jsonl"
CRASH_POINTS = ("before_append", "mid_line", "after_write_before_fsync", "after_fsync_before_index", "after_index_commit")


def leaf(b: bytes) -> str: return hashlib.sha256(b"\x00" + b).hexdigest()


class Crash(Exception): pass


class Writer:
    def __init__(self, d, mode="RECONCILING", crash_at=None, crash_after=0):
        self.d, self.mode, self.crash_at, self.crash_after, self.count = d, mode, crash_at, crash_after, 0
        self.seg = os.path.join(d, SEG)
        self.db = sqlite3.connect(os.path.join(d, "index.db"), isolation_level=None, timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL"); self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("CREATE TABLE IF NOT EXISTS outbox(id TEXT PRIMARY KEY, body TEXT NOT NULL, done INTEGER NOT NULL DEFAULT 0)")
        self.db.execute("CREATE TABLE IF NOT EXISTS ledger(seq INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, leaf TEXT NOT NULL, off INTEGER NOT NULL, len INTEGER NOT NULL)")
        if not os.path.exists(self.seg): open(self.seg, "wb").close()
        self.f = open(self.seg, "ab", buffering=0)

    def hit(self, point):
        if self.crash_at == point and self.count >= self.crash_after:
            os._exit(9)      # equals SIGKILL for this process: no cleanup, no flush of anything buffered in user space

    # -- producers
    def submit(self, rid: str, rec: dict):
        self.db.execute("INSERT OR IGNORE INTO outbox(id, body) VALUES(?,?)", (rid, json.dumps(rec, sort_keys=True, separators=(",", ":"))))

    # -- recovery
    def recover(self):
        if self.mode == "NAIVE":
            return {"mode": "NAIVE"}
        data = open(self.seg, "rb").read()
        # 1. scan complete, valid lines
        good, pos, expect = [], 0, 0
        while pos < len(data):
            nl = data.find(b"\n", pos)
            if nl < 0: break                                       # torn tail: no newline
            line = data[pos:nl]
            try:
                o = json.loads(line)
                rec = o["rec"].encode()
                if o["seq"] != expect or leaf(rec) != o["leaf"]: break   # corrupt or out of order: stop, truncate here
            except Exception:
                break
            good.append((o["seq"], o["id"], o["leaf"], pos, nl + 1 - pos)); pos = nl + 1; expect += 1
        truncated = len(data) - pos
        if truncated:
            with open(self.seg, "r+b") as t: t.truncate(pos); t.flush(); os.fsync(t.fileno())
            self.f.close(); self.f = open(self.seg, "ab", buffering=0)
        # 2. index rows beyond the valid segment are removed and their outbox rows reopened
        self.db.execute("BEGIN IMMEDIATE")
        valid = {g[0] for g in good}
        stale = [r for r in self.db.execute("SELECT seq,id FROM ledger").fetchall() if r[0] not in valid]
        for seq, rid in stale:
            self.db.execute("DELETE FROM ledger WHERE seq=?", (seq,)); self.db.execute("UPDATE outbox SET done=0 WHERE id=?", (rid,))
        # 3. segment lines missing from the index are indexed (never appended again)
        indexed = {r[0] for r in self.db.execute("SELECT seq FROM ledger")}
        for seq, rid, lf, off, ln in good:
            if seq not in indexed:
                self.db.execute("INSERT INTO ledger(seq,id,leaf,off,len) VALUES(?,?,?,?,?)", (seq, rid, lf, off, ln))
            self.db.execute("INSERT OR IGNORE INTO outbox(id,body,done) VALUES(?,?,1)", (rid, "recovered"))
            self.db.execute("UPDATE outbox SET done=1 WHERE id=?", (rid,))
        self.db.execute("COMMIT")
        return {"mode": "RECONCILING", "valid_lines": len(good), "truncated_bytes": truncated, "stale_index_rows": len(stale)}

    # -- writer loop
    def drain(self):
        for (rid, body) in self.db.execute("SELECT id, body FROM outbox WHERE done=0 ORDER BY rowid").fetchall():
            if self.mode == "RECONCILING" and self.db.execute("SELECT 1 FROM ledger WHERE id=?", (rid,)).fetchone():
                self.db.execute("UPDATE outbox SET done=1 WHERE id=?", (rid,)); continue
            seq = (self.db.execute("SELECT max(seq) FROM ledger").fetchone()[0])
            seq = 0 if seq is None else seq + 1
            if self.mode == "NAIVE":
                # the first-written protocol takes the next sequence from the index, so a line appended but never indexed is invisible to it
                pass
            rec = body
            line = (json.dumps({"seq": seq, "id": rid, "leaf": leaf(rec.encode()), "rec": rec}, sort_keys=True, separators=(",", ":")) + "\n").encode()
            self.hit("before_append")
            off = os.path.getsize(self.seg)
            if self.crash_at == "mid_line" and self.count >= self.crash_after:
                self.f.write(line[: len(line) // 2]); os._exit(9)
            self.f.write(line)
            self.hit("after_write_before_fsync")
            os.fsync(self.f.fileno())
            self.hit("after_fsync_before_index")
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("INSERT INTO ledger(seq,id,leaf,off,len) VALUES(?,?,?,?,?)", (seq, rid, leaf(rec.encode()), off, len(line)))
            self.db.execute("UPDATE outbox SET done=1 WHERE id=?", (rid,))
            self.db.execute("COMMIT")
            self.hit("after_index_commit")
            self.count += 1


def check(d: str) -> dict:
    """Invariants after recovery and drain. Returns violation counts."""
    seg = os.path.join(d, SEG); data = open(seg, "rb").read()
    lines = data.split(b"\n"); torn = lines[-1] != b""
    lines = [l for l in lines if l]
    ids, seqs, leaf_bad = [], [], 0
    for l in lines:
        try:
            o = json.loads(l); ids.append(o["id"]); seqs.append(o["seq"])
            if leaf(o["rec"].encode()) != o["leaf"]: leaf_bad += 1
        except Exception: leaf_bad += 1
    db = sqlite3.connect(os.path.join(d, "index.db"))
    idx = db.execute("SELECT seq,id,leaf FROM ledger ORDER BY seq").fetchall()
    outbox_ids = {r[0] for r in db.execute("SELECT id FROM outbox")}
    pending = db.execute("SELECT count(*) FROM outbox WHERE done=0").fetchone()[0]
    rebuilt = []
    for l in lines:
        try: o = json.loads(l); rebuilt.append((o["seq"], o["id"], o["leaf"]))
        except Exception: pass
    return {"duplicate_lines_in_segment": len(ids) - len(set(ids)), "seq_not_gapless": int(seqs != list(range(len(seqs)))), "leaf_mismatch": leaf_bad,
            "torn_tail": int(torn), "index_differs_from_segment": int(idx != rebuilt), "outbox_pending": pending,
            "outbox_ids_without_line": len(outbox_ids - set(ids)), "lines": len(lines)}
