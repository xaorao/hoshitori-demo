#!/usr/bin/env python3
"""Experiment E2c, builder: a synthetic RYO-shaped evidence response -> record -> batch of leaves -> RFC 9162 tree ->
C2SP checkpoint signed with Ed25519 -> RFC 3161 tokens over the checkpoint from two independent operators ->
verification bundle directory (c2sp tlog-proof + tokens + policy). All data in the batch is synthetic and labelled so.

Usage: make_bundle.py OUTDIR [--tsa name=url ...]   (default: globalsign and swisssign)
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common as C
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

OSSL = "/opt/homebrew/bin/openssl"
DEFAULT_TSAS = {"globalsign": "http://timestamp.globalsign.com/tsa/r6advanced1", "swisssign": "http://tsa.swisssign.net"}
ORIGIN = "e2c.hoshitori.invalid/experiment-log"
RAW_SYNTHETIC = b'{"schema_version":"1.0","tool":"analyze_token","status":"ok","data_mode":"simulated","as_of":"2026-10-27T03:00:00Z","request":{"symbol":"SOL"},"data":{"asset":{"symbol":"SOL","chain":null},"market":{"price_usd":143.2100,"volume_24h_usd":2.5e9},"performance":{"change_24h_pct":-1.23},"technical_analysis":{"rsi_14":57.1}},"summary":{"headline":"synthetic","key_points":[]},"availability":{"market_data":"ok"},"warnings":[],"trace_id":"00000000000000000000000000000000"}'


def tsq_post(digest_hex: str, url: str, tmp: Path, tag: str) -> bytes:
    q = tmp / f"{tag}.tsq"
    subprocess.run([OSSL, "ts", "-query", "-digest", digest_hex, "-sha256", "-cert", "-out", str(q)], check=True, capture_output=True)
    r = subprocess.run(["curl", "-s", "-m", "30", "-H", "Content-Type: application/timestamp-query", "--data-binary", f"@{q}", url], capture_output=True)
    return r.stdout


def build(outdir: Path, tsas: dict[str, str], seed: bytes | None = None, mutate=None, reuse_tsa: Path | None = None, commit_override: dict | None = None) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "tsa").mkdir(exist_ok=True)
    (outdir / "raw").mkdir(exist_ok=True)
    (outdir / "raw" / "ryo_response.json").write_bytes(RAW_SYNTHETIC)
    raw_sha = hashlib.sha256(RAW_SYNTHETIC).hexdigest()
    parsed = json.loads(RAW_SYNTHETIC, parse_float=lambda s: s, parse_int=lambda s: s)
    # decimal strings are taken from the number tokens as spelled in the raw bytes
    f = {
        "price_usd": C.decimal_plain(parsed["data"]["market"]["price_usd"]),
        "volume_24h_usd": C.decimal_plain(parsed["data"]["market"]["volume_24h_usd"]),
        "change_24h_pct": C.decimal_plain(parsed["data"]["performance"]["change_24h_pct"]),
        "rsi_14": C.decimal_plain(parsed["data"]["technical_analysis"]["rsi_14"]),
    }
    records = []
    for i in range(4):
        records.append({"type": "evidence", "id": f"ev-{i:04d}", "tool": "analyze_token", "request": {"symbol": "SOL"}, "data_mode": parsed["data_mode"], "status": parsed["status"],
                        "as_of": parsed["as_of"], "received_at": "2026-10-27T03:00:01.250Z", "raw_sha256": raw_sha, "fields": f, "trace_id": parsed["trace_id"]})
    for i in range(4):
        records.append({"type": "forecast", "id": f"fc-{i:04d}", "contract": "D", "asset_id": "SOL-USDT-spot-binance", "horizon": ["1h", "4h", "24h", "24h"][i], "p": ["0.55", "0.52", "0.61", "0.48"][i],
                        "evidence_ids": [f"ev-{i:04d}"], "agent_version_hash": "0" * 64, "arm": "L1"})
    if mutate:
        mutate(records)
    canon_records = [C.canon.canonicalize(json.dumps(r).encode(), "record") for r in records]
    leaves = [C.M.leaf_hash(c) for c in canon_records]
    idx = 5  # the record of interest: a forecast
    root = C.M.mth(leaves)
    path = C.M.inclusion_path(idx, leaves)
    priv = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(seed).digest()) if seed else Ed25519PrivateKey.generate()  # experiment keys only
    pub = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    text = C.note_text(ORIGIN, len(leaves), root)
    note = C.signed_note(text, ORIGIN, pub, priv.sign(text))
    committed = datetime.now(timezone.utc)
    tmp = outdir / ".tmp"
    tmp.mkdir(exist_ok=True)
    digest = hashlib.sha256(text).hexdigest()
    if reuse_tsa:
        import shutil as _sh
        for f in reuse_tsa.glob("*.tsr"):
            _sh.copy(f, outdir / "tsa" / f.name)
    else:
        for name, url in tsas.items():
            tok = tsq_post(digest, url, tmp, name)
            (outdir / "tsa" / f"{name}.tsr").write_bytes(tok)
    (outdir / "items").mkdir(exist_ok=True)
    for i in (idx, 1):  # the forecast and the evidence record it cites
        rid = records[i]["id"]
        (outdir / "items" / f"{rid}.canonical.json").write_bytes(canon_records[i])
        (outdir / "items" / f"{rid}.tlog-proof").write_bytes(C.tlog_proof(i, C.M.inclusion_path(i, leaves), note))
    commit = commit_override or {"committed_at": committed.isoformat(timespec="milliseconds"), "outcome_window_opens_at": datetime.fromtimestamp(committed.timestamp() + 120, timezone.utc).isoformat(timespec="milliseconds"),
                                 "note": "committed_at is our clock at checkpoint creation; the experiment sets the window 120 s later"}
    (outdir / "commit.json").write_text(json.dumps(commit, indent=1))
    # pinned roots (SHA-256 of the DER of each operator's root, from experiment E2b) and the policy
    e2b = json.loads((C.EXP / "E2b" / "E2b_results.json").read_text())
    pinned = {r["fp"]: r["name"] for r in e2b["pinned_roots"] if r["name"] not in ("entrust", "aimoda", "apple")}
    (outdir / "policy.json").write_text(json.dumps({
        "log": {"origin": ORIGIN, "vkey": C.vkey(ORIGIN, pub)},
        "tsa": {"required_independent": 2, "pinned_roots_sha256": pinned, "min_cms_digest": "sha256", "window_after_commit_s": 60, "max_spread_s": 5, "allowed_policy_oids": None},
        "u_constant_s": 5,
        "scored_requires_data_mode": "live",
    }, indent=1))
    # pinned root certificates as PEM, taken from the tokens' own chains when their SHA-256 matches a pinned fingerprint
    from asn1crypto import tsp
    from cryptography import x509
    pem = b""
    seen = set()
    for t in (outdir / "tsa").glob("*.tsr"):
        sd = tsp.TimeStampResp.load(t.read_bytes())["time_stamp_token"]["content"]
        for c in sd["certificates"]:
            cert = x509.load_der_x509_certificate(c.chosen.dump())
            fp = hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()
            if fp in pinned and fp not in seen:
                pem += cert.public_bytes(Encoding.PEM); seen.add(fp)
    (outdir / "tsa_roots.pem").write_bytes(pem)
    (outdir / "README.txt").write_text("Synthetic experiment bundle (E2c). Verify with verify_bundle.py or verify_bundle.mjs: <verifier> <this directory>.\nAll evidence in the batch is synthetic (data_mode simulated).\n")
    import shutil
    shutil.rmtree(tmp)
    return {"records": len(leaves), "index": idx, "root": root.hex(), "digest": digest}


if __name__ == "__main__":
    out = Path(sys.argv[1])
    tsas = dict(a.split("=", 1) for a in sys.argv[2:]) or DEFAULT_TSAS
    print(json.dumps(build(out, tsas)))
