#!/usr/bin/env python3
"""Experiment E2c, verifier A (Python). Offline verification of a Hoshitori verification bundle.

Usage: verify_bundle.py BUNDLE_DIR  ->  one JSON verdict on stdout. Needs: cryptography, asn1crypto. No network.
It reports FOUR separate things and never merges them (contract section 24):
  integrity     the stored bytes match their commitments (canonical record, inclusion proof, signed checkpoint)
  timing        which independent time evidence the bundle carries and the strongest level it supports
  eligibility   whether the evidence could enter a scored cycle (data_mode must be live)
  not_proven    what none of the above establishes
"""
from __future__ import annotations

import base64
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "E1"))
sys.path.insert(0, str(HERE.parent / "E2a"))
sys.path.insert(0, str(HERE.parent / "E2b"))
import canon  # noqa: E402
import merkle as M  # noqa: E402
import tsa_verify as V  # noqa: E402
from cryptography.exceptions import InvalidSignature  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey  # noqa: E402

NOT_PROVEN = ["that the evidence was true or complete", "that the forecast is good", "that the agent reasoned as stored", "who controls the signing key",
              "that no other log history exists beyond the witnessed checkpoints (no witness cosignature is required by this bundle)"]


def b64d(s: str) -> bytes:
    raw = base64.b64decode(s, validate=True)
    if base64.b64encode(raw).decode() != s:
        raise ValueError("non-canonical base64")
    return raw


def parse_proof(text: bytes):
    lines = text.decode("utf-8").split("\n")
    if lines[0] != "c2sp.org/tlog-proof@v1":
        raise ValueError("bad proof header")
    i = 1
    if lines[i].startswith("extra "):
        i += 1
    if not lines[i].startswith("index "):
        raise ValueError("missing index")
    idx_s = lines[i][6:]
    if not idx_s.isdigit() or (idx_s != "0" and idx_s.startswith("0")):
        raise ValueError("bad index")
    index = int(idx_s)
    i += 1
    path = []
    while lines[i] != "":
        path.append(b64d(lines[i]))
        i += 1
    cp = "\n".join(lines[i + 1:]).encode("utf-8")
    return index, path, cp


def parse_checkpoint(cp: bytes):
    body, sep, sigs = cp.partition(b"\n\n")
    if not sep:
        raise ValueError("no signature block")
    note_text = body + b"\n"
    lines = body.decode("utf-8").split("\n")
    if len(lines) < 3 or not all(lines[:3]):
        raise ValueError("checkpoint needs three non-empty lines")
    origin, size_s, root_b64 = lines[:3]
    if not size_s.isdigit() or (size_s != "0" and size_s.startswith("0")):
        raise ValueError("bad size")
    sigs_list = []
    for ln in sigs.decode("utf-8").split("\n"):
        if not ln:
            continue
        if not ln.startswith("— "):
            raise ValueError("bad signature line")
        name, _, b = ln[2:].rpartition(" ")
        sigs_list.append((name, b64d(b)))
    return origin, int(size_s), b64d(root_b64), note_text, sigs_list


def verify_note(note_text: bytes, sigs, vkey: str, origin: str) -> bool:
    name, kid_hex, key_b64 = vkey.split("+", 2)  # base64 contains "+"; key names cannot
    keymat = b64d(key_b64)
    if keymat[0] != 1 or len(keymat) != 33 or name != origin:
        return False
    pub = keymat[1:]
    kid = hashlib.sha256(name.encode() + b"\n" + b"\x01" + pub).digest()[:4]
    if kid.hex() != kid_hex:
        return False
    for sname, sb in sigs:
        if sname == name and sb[:4] == kid and len(sb) == 68:
            try:
                Ed25519PublicKey.from_public_bytes(pub).verify(sb[4:], note_text)
                return True
            except InvalidSignature:
                return False
    return False


def verify(bundle: Path) -> dict:
    out = {"verifier": "python", "integrity": {}, "timing": {}, "eligibility": {}, "errors": [], "not_proven": NOT_PROVEN}
    try:
        policy = json.loads((bundle / "policy.json").read_text())
        commit = json.loads((bundle / "commit.json").read_text())
    except Exception as e:
        out["errors"].append("policy/commit unreadable: " + str(e)[:80])
        return out
    notes = set()
    records = {}
    items = sorted((bundle / "items").glob("*.canonical.json"))
    out["integrity"]["items"] = {}
    for it in items:
        rid = it.name.split(".")[0]
        res = {"canonical": False, "inclusion": False}
        try:
            raw = it.read_bytes()
            res["canonical"] = canon.canonicalize(raw, "record") == raw
            rec = json.loads(raw)
            records[rid] = rec
            index, path, cp = parse_proof((bundle / "items" / f"{rid}.tlog-proof").read_bytes())
            origin, size, root, note_text, sigs = parse_checkpoint(cp)
            notes.add(note_text)
            res["origin_ok"] = origin == policy["log"]["origin"]
            res["signature"] = verify_note(note_text, sigs, policy["log"]["vkey"], origin)
            lh = M.leaf_hash(raw)
            res["inclusion"] = len(root) == 32 and all(len(p) == 32 for p in path) and M.verify_inclusion(lh, index, size, path, root)
        except Exception as e:
            res["error"] = type(e).__name__ + ": " + str(e)[:80]
        out["integrity"]["items"][rid] = res
    # evidence chain: each forecast's evidence ids must be proven items, and the raw bytes must match the evidence record
    chain_ok = True
    for rid, rec in records.items():
        if rec.get("type") == "forecast":
            for ev in rec.get("evidence_ids", []):
                if ev not in records:
                    chain_ok = False
        if rec.get("type") == "evidence":
            rawp = bundle / "raw" / "ryo_response.json"
            if not rawp.exists() or hashlib.sha256(rawp.read_bytes()).hexdigest() != rec.get("raw_sha256"):
                chain_ok = False
    out["integrity"]["evidence_chain"] = chain_ok and len(records) > 0
    out["integrity"]["one_checkpoint"] = len(notes) == 1
    out["integrity"]["ok"] = bool(records) and chain_ok and len(notes) == 1 and all(v.get("canonical") and v.get("inclusion") and v.get("signature") and v.get("origin_ok") for v in out["integrity"]["items"].values())
    # timing
    toks = []
    if len(notes) == 1:
        note_text = next(iter(notes))
        digest = hashlib.sha256(note_text).digest()
        pinned = set(policy["tsa"]["pinned_roots_sha256"])
        committed = datetime.fromisoformat(commit["committed_at"]).timestamp()
        opens = datetime.fromisoformat(commit["outcome_window_opens_at"]).timestamp()
        u = policy["u_constant_s"]
        for t in sorted((bundle / "tsa").glob("*.tsr")):
            r = V.verify_token(t.read_bytes(), digest, None, pinned)
            c = r["checks"]
            entry = {"file": t.name, "valid": r["ok"], "reason": None if r["ok"] else (r.get("error") or [k for k, v in c.items() if v is False and k != "ordering"])}
            if r["ok"]:
                gen = datetime.fromisoformat(c["gen_time"]).timestamp()
                entry.update({"gen_time": c["gen_time"], "signer_fp": c["signer_fp"], "root_fp": c["root_fp"], "cms_digest": c["cms_digest_alg"],
                              "timely": abs(gen - committed) <= policy["tsa"]["window_after_commit_s"] and gen < opens - u, "accuracy_claimed": c["accuracy"]})
            toks.append(entry)
    good = [t for t in toks if t["valid"] and t.get("timely")]
    groups = {t["root_fp"] for t in good}
    signers = {t["signer_fp"] for t in good}
    gens = sorted(datetime.fromisoformat(t["gen_time"]).timestamp() for t in good)
    spread = (gens[-1] - gens[0]) if gens else None
    independent = min(len(groups), len(signers))
    c3 = independent >= policy["tsa"]["required_independent"] and spread is not None and spread <= policy["tsa"]["max_spread_s"]
    out["timing"] = {"tokens": toks, "independent_operators": independent, "spread_s": spread, "level_supported": "none (integrity failed)" if not out["integrity"]["ok"] else ("C3" if c3 else ("below C3 (fewer than two independent operators)" if good else "below C3 (no valid timely token)")),
                     "assurance_class": "TA-3 (assumed clock uncertainty u=%ds plus observed spread; not proven)" % policy["u_constant_s"], "eligible_after_offset_s": (policy["u_constant_s"] + (spread or 0)) if good else None}
    modes = {r.get("data_mode") for r in records.values() if r.get("type") == "evidence"}
    out["eligibility"] = {"data_modes": sorted(m for m in modes if m), "scorable": modes == {policy["scored_requires_data_mode"]}}
    return out


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1])), indent=1))
