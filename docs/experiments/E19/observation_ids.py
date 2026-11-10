"""E19: identity of an observation versus identity of its content (an outside review). The first-written formula `ev = H(tool, canonical request, raw_sha256)`
gives two captures of identical bytes at different times one id, so the ledger either drops the second observation (equal-content no-op) or rejects it as a
conflict (its received_at differs). The fixed scheme separates: capture_id (one per network fetch, fixed before the append so replay is idempotent), content
(the raw_sha256 blob, stored once) and the evidence item id (capture_id, adapter version, item path). A tiny reference ledger applies the contract's rule:
the same (source, id) with equal content is a no-op; with different content it is a conflict, rejected and counted. Not product code."""
import hashlib, json, random, sys

def H(*parts): return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:26]

class Ledger:
    def __init__(self): self.rows, self.conflicts, self.noops, self.blobs = {}, 0, 0, {}
    def append(self, source, rid, rec):
        key = (source, rid)
        if key in self.rows:
            if self.rows[key] == rec: self.noops += 1
            else: self.conflicts += 1
            return False
        self.rows[key] = rec; return True

def fetch(tool, req, body, t, gateway_seq):
    return {"tool": tool, "request": req, "body": body, "received_at": t, "gateway_seq": gateway_seq}

def old_scheme(led, cap):
    raw = hashlib.sha256(cap["body"]).hexdigest(); led.blobs.setdefault(raw, cap["body"])
    rid = "ev" + H(cap["tool"], cap["request"], raw)
    return led.append("gateway:ryo", rid, {"tool": cap["tool"], "request": cap["request"], "raw_sha256": raw, "received_at": cap["received_at"], "known_at": cap["received_at"]})

def new_scheme(led, cap, outbox_ids):
    raw = hashlib.sha256(cap["body"]).hexdigest(); led.blobs.setdefault(raw, cap["body"])
    cid = outbox_ids.setdefault((cap["tool"], H(cap["request"]), cap["gateway_seq"]), "cp" + H("gateway:ryo", cap["tool"], cap["request"], cap["gateway_seq"], cap["received_at"]))
    rid = "ev" + H(cid, "adapter-v1", "$")
    return led.append("gateway:ryo", rid, {"capture_id": cid, "tool": cap["tool"], "request": cap["request"], "raw_sha256": raw, "received_at": cap["received_at"], "known_at": cap["received_at"], "same_content_as_previous": None})

def as_of(led, T):
    """point-in-time read: the latest observation with known_at <= T"""
    rows = [r for r in led.rows.values() if r["known_at"] <= T]
    return max(rows, key=lambda r: r["known_at"]) if rows else None

rng = random.Random(19)
res = {"trials": 0}
for scheme in ("old", "new"):
    lost_obs = conflicts = wrong_asof = replay_dups = blob_excess = 0
    for _ in range(2000):
        led, outbox = Ledger(), {}
        n = rng.randint(2, 12); bodies = [b"A" * 20, b"B" * 20]
        caps = []; t = 0
        for i in range(n):
            t += rng.randint(1, 300)
            caps.append(fetch("analyze_token", {"symbol": "SOL"}, bodies[0] if rng.random() < 0.7 else bodies[1], t, i))
        replays = [c for c in caps if rng.random() < 0.3]            # at-least-once delivery: the same capture is submitted again
        for c in caps: (old_scheme(led, c) if scheme == "old" else new_scheme(led, c, outbox))
        before = len(led.rows)
        for c in replays: (old_scheme(led, c) if scheme == "old" else new_scheme(led, c, outbox))
        if len(led.rows) != before: replay_dups += 1                  # a replay must not create a row
        if len(led.rows) != len(caps): lost_obs += 1                  # one row per distinct fetch
        conflicts += int(led.conflicts > 0)
        T = rng.randint(0, t); truth = max([c for c in caps if c["received_at"] <= T], key=lambda c: c["received_at"], default=None)
        got = as_of(led, T)
        if (truth is None) != (got is None) or (truth and got["received_at"] != truth["received_at"]): wrong_asof += 1
        blob_excess += int(len(led.blobs) != len({hashlib.sha256(c["body"]).hexdigest() for c in caps}))
        res["trials"] += 1 if scheme == "old" else 0
    res[scheme] = {"trials_where_an_observation_was_lost": lost_obs, "trials_with_a_rejected_conflict": conflicts, "trials_with_a_wrong_point_in_time_read": wrong_asof,
                   "trials_where_a_replay_created_a_row": replay_dups, "trials_with_more_than_one_blob_per_content": blob_excess, "of": 2000}
import os
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E19_results.json"), "w"), indent=1); print(json.dumps(res, indent=1))
