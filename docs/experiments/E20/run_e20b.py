"""E20b: recovery must respect externally attested checkpoints (an outside review 2). The external attestation store stands for what lives outside
the host (time-stamp tokens, witness cosignatures, a replica): it is NOT subject to the local fault injection. Protocols:
  RECONCILING   E20's recovery: repair the local tail and the index, then resume writing.
  ATTESTED      RECONCILING, then compare the segment with the highest attested checkpoint (size, Merkle root). On mismatch: restore the missing prefix
                from a replica if one holds a prefix with the attested root, otherwise enter INTEGRITY_FAILURE and refuse every write.
Faults, applied after a checkpoint of size k was attested and the writer holds n >= k records: segment rolled back below k; index rolled back alone; both rolled back
independently; partial power loss (a byte-level tail lost after fsync); a flipped byte inside the attested prefix; the attested prefix rewritten with a different
but internally valid chain. Violation = the writer resumed writing while the attested prefix is not intact in the segment and INTEGRITY_FAILURE was not raised.
Not product code. Real power loss is not shown: the faults are file edits."""
import json, os, random, shutil, sqlite3, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(HERE, "..", "E2a"))
import merkle as M
from ledger_writer import Writer, SEG, leaf


def seg_lines(d):
    data = open(os.path.join(d, SEG), "rb").read(); out = []
    for l in data.split(b"\n"):
        if not l: continue
        try: out.append(json.loads(l))
        except Exception: break
    return out


def root_of(d, k):
    ls = seg_lines(d)
    if len(ls) < k: return None
    return M.mth([M.leaf_hash(o["rec"].encode()) for o in ls[:k]]).hex() if isinstance(M.mth([]), bytes) else M.mth([M.leaf_hash(o["rec"].encode()) for o in ls[:k]])


def recover(d, att, replica, protocol):
    w = Writer(d, mode="RECONCILING"); w.recover()
    state = {"integrity_failure": False, "restored": False}
    if protocol == "ATTESTED" and att:
        k, r = att
        if root_of(d, k) != r:
            if replica and root_of(replica, k) == r:
                w.db.close(); shutil.copy(os.path.join(replica, SEG), os.path.join(d, SEG))
                os.remove(os.path.join(d, "index.db")) if os.path.exists(os.path.join(d, "index.db")) else None
                for ext in ("-wal", "-shm"):
                    p = os.path.join(d, "index.db" + ext)
                    if os.path.exists(p): os.remove(p)
                w = Writer(d, mode="RECONCILING"); w.recover(); state["restored"] = True
            else:
                state["integrity_failure"] = True
    return w, state


def one(fault, protocol, replica_available, rng, prune=False):
    d = tempfile.mkdtemp(prefix="e20b_"); rep = tempfile.mkdtemp(prefix="e20b_rep_")
    try:
        n = rng.randint(8, 30); k = rng.randint(3, n)
        w = Writer(d, mode="RECONCILING")
        snap = None
        for i in range(n):
            w.submit(f"id{i}", {"i": i, "v": rng.random()}); w.drain()
            if i + 1 == k:
                snap = (shutil.copy(os.path.join(d, "index.db"), os.path.join(d, "snap.db")))
        att = (k, root_of(d, k))                                 # attested externally: time tokens and witnesses over (k, root)
        if replica_available:
            shutil.copy(os.path.join(d, SEG), os.path.join(rep, SEG))   # a replica holding the segment at attestation time or later
        if prune: w.db.execute("DELETE FROM outbox WHERE done=1")   # production retention: finished outbox rows are pruned
        w.db.close()
        segp = os.path.join(d, SEG); data = open(segp, "rb").read(); lines = data.split(b"\n")[:-1]
        if fault == "segment_rolled_back":
            keep = rng.randint(0, k - 1); open(segp, "wb").write(b"\n".join(lines[:keep]) + (b"\n" if keep else b""))
        elif fault == "index_rolled_back_alone":
            shutil.copy(os.path.join(d, "snap.db"), os.path.join(d, "index.db"))
            for ext in ("-wal", "-shm"):
                p = os.path.join(d, "index.db" + ext)
                if os.path.exists(p): os.remove(p)
        elif fault == "both_rolled_back_independently":
            keep = rng.randint(0, k - 1); open(segp, "wb").write(b"\n".join(lines[:keep]) + (b"\n" if keep else b""))
            shutil.copy(os.path.join(d, "snap.db"), os.path.join(d, "index.db"))
            for ext in ("-wal", "-shm"):
                p = os.path.join(d, "index.db" + ext)
                if os.path.exists(p): os.remove(p)
        elif fault == "partial_power_loss":
            cut = len(b"\n".join(lines[:k - 1])) + rng.randint(1, max(2, len(lines[k - 1]) - 1)) if k > 1 else rng.randint(1, len(lines[0]) - 1)
            open(segp, "wb").write(data[:cut])
        elif fault == "flipped_byte_in_prefix":
            ln = rng.randint(0, k - 1); off = sum(len(x) + 1 for x in lines[:ln]) + len(lines[ln]) // 2
            b = bytearray(data); b[off] ^= 0x01; open(segp, "wb").write(bytes(b))
        elif fault == "prefix_rewritten_valid_chain":
            new = []
            for i, l in enumerate(lines):
                o = json.loads(l)
                if i < k:
                    rec = json.dumps({"i": i, "v": "forged"}, sort_keys=True, separators=(",", ":"))
                    o["rec"] = rec; o["leaf"] = leaf(rec.encode())
                new.append(json.dumps(o, sort_keys=True, separators=(",", ":")).encode())
            open(segp, "wb").write(b"\n".join(new) + b"\n")
        w, st = recover(d, att, rep if replica_available else None, protocol)
        resumed = False
        if not st["integrity_failure"]:
            w.submit("after", {"after": True}); w.drain(); resumed = True
        intact = root_of(d, k) == att[1]
        w.db.close()
        violation = resumed and not intact                        # resumed writing over an attested prefix that is not intact, without raising the failure
        silent_wrong_history = resumed and not intact
        return {"violation": violation, "integrity_failure": st["integrity_failure"], "restored": st["restored"], "resumed": resumed, "intact_after": intact}
    finally:
        shutil.rmtree(d, ignore_errors=True); shutil.rmtree(rep, ignore_errors=True)


if __name__ == "__main__":
    faults = ["segment_rolled_back", "index_rolled_back_alone", "both_rolled_back_independently", "partial_power_loss", "flipped_byte_in_prefix", "prefix_rewritten_valid_chain"]
    out, N = {}, 100
    for protocol, rep, prune in (("RECONCILING", False, False), ("RECONCILING", False, True), ("ATTESTED", False, True), ("ATTESTED", True, True)):
        key = f"{protocol}{'+replica' if rep else ''}{'+pruned_outbox' if prune else '+outbox_kept'}"; out[key] = {}
        for f in faults:
            rng = random.Random(2021); v = fail = rest = res = 0
            for _ in range(N):
                r = one(f, protocol, rep, rng, prune)
                v += r["violation"]; fail += r["integrity_failure"]; rest += r["restored"]; res += r["resumed"]
            out[key][f] = {"rounds": N, "violations": v, "integrity_failure_raised": fail, "restored_from_replica": rest, "resumed_writing": res}
    json.dump(out, open(os.path.join(HERE, "E20b_results.json"), "w"), indent=1); print(json.dumps(out, indent=1))
