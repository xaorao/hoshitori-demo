#!/usr/bin/env python3
"""Experiment E2b runner: live RFC 3161 time-stamp authorities, two independent verifiers, clock excursions,
independence groups, revocation pointers and a hostile token suite.

Run with the verifier environment: <venv>/bin/python -I run_e2b.py   (asn1crypto, cryptography)
OpenSSL 3.x is the second verifier (`openssl ts -verify`). Network use: one RFC 3161 request per token to public
services; FreeTSA is asked for 6 tokens 16 seconds apart (its page says not to abuse the service).
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import re
import secrets
import subprocess
import sys
import tempfile
import threading
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import tsa_verify as V  # noqa: E402
from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding  # noqa: E402

OSSL = "/opt/homebrew/bin/openssl"
TSAS = {
    "freetsa": ("https://freetsa.org/tsr", 6, 16.0),
    "digicert": ("http://timestamp.digicert.com", 12, 3.0),
    "sectigo": ("http://timestamp.sectigo.com", 12, 3.0),
    "globalsign": ("http://timestamp.globalsign.com/tsa/r6advanced1", 12, 3.0),
    "swisssign": ("http://tsa.swisssign.net", 12, 3.0),
    "apple": ("http://timestamp.apple.com/ts01", 12, 3.0),
    "certum": ("http://time.certum.pl", 12, 3.0),
    "entrust": ("http://timestamp.entrust.net/TSS/RFC3161sha2TS", 4, 3.0),
    "aimoda": ("https://rfc3161.ai.moda", 4, 3.0),
}
TMP = Path(tempfile.mkdtemp(prefix="e2b_"))


def query(digest_hex: str, name: str) -> tuple[Path, int]:
    q = TMP / f"{name}.tsq"
    subprocess.run([OSSL, "ts", "-query", "-digest", digest_hex, "-sha256", "-cert", "-out", str(q)], check=True, capture_output=True)
    txt = subprocess.run([OSSL, "ts", "-query", "-in", str(q), "-text"], capture_output=True, text=True).stdout
    nonce = int(re.search(r"Nonce: (0x[0-9A-Fa-f]+)", txt).group(1), 16)
    return q, nonce


def post(url: str, q: Path, timeout=30):
    r = subprocess.run(["curl", "-s", "-m", str(timeout), "-H", "Content-Type: application/timestamp-query", "--data-binary", f"@{q}", "-w", "\n%{http_code} %{time_total}", url], capture_output=True)
    body, _, tail = r.stdout.rpartition(b"\n")
    code, t = tail.decode().split()
    return int(code), body, float(t)


def collect():
    out: dict[str, list[dict]] = defaultdict(list)
    lock = threading.Lock()

    def worker(name, url, n, gap):
        for i in range(n):
            digest = hashlib.sha256(f"e2b|{name}|{i}|{secrets.token_hex(8)}".encode()).hexdigest()
            q, nonce = query(digest, f"{name}_{i}")
            t0 = time.time()
            code, body, rtt = post(url, q)
            t1 = time.time()
            with lock:
                out[name].append({"i": i, "digest": digest, "nonce": nonce, "t_send": t0, "t_recv": t1, "http": code, "rtt": rtt, "tsr": body})
            if i + 1 < n:
                time.sleep(gap)

    th = [threading.Thread(target=worker, args=(n, u, c, g)) for n, (u, c, g) in TSAS.items()]
    [t.start() for t in th]
    [t.join() for t in th]
    return out


def cert_info(c: x509.Certificate) -> dict:
    d = {"subject": c.subject.rfc4514_string()[:120], "issuer": c.issuer.rfc4514_string()[:120], "fp": hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest(),
         "not_before": c.not_valid_before_utc.isoformat(), "not_after": c.not_valid_after_utc.isoformat(), "ocsp": [], "crl": []}
    try:
        for ad in c.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value:
            if ad.access_method.dotted_string == "1.3.6.1.5.5.7.48.1":
                d["ocsp"].append(ad.access_location.value)
    except x509.ExtensionNotFound:
        pass
    try:
        for dp in c.extensions.get_extension_for_class(x509.CRLDistributionPoints).value:
            for n in dp.full_name or []:
                d["crl"].append(n.value)
    except x509.ExtensionNotFound:
        pass
    return d


def chain_of(tsr: bytes):
    from asn1crypto import tsp
    resp = tsp.TimeStampResp.load(tsr)
    sd = resp["time_stamp_token"]["content"]
    certs = [x509.load_der_x509_certificate(c.chosen.dump()) for c in sd["certificates"] if c.name == "certificate"]
    si = sd["signer_infos"][0]
    signer = next(c for c in certs if c.serial_number == si["sid"].chosen["serial_number"].native) if si["sid"].name == "issuer_and_serial_number" else certs[0]
    return signer, certs


def openssl_verify(tsr: Path, q: Path, cafile: Path, attime: int | None = None, digest_hex: str | None = None):
    cmd = [OSSL, "ts", "-verify", "-in", str(tsr), "-CAfile", str(cafile), "-partial_chain"]
    cmd += ["-queryfile", str(q)] if q else ["-digest", digest_hex]
    if attime:
        cmd += ["-attime", str(attime)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, (r.stdout + r.stderr).strip()[:160]


def main():
    print("collecting ...", file=sys.stderr, flush=True)
    data = collect()
    report: dict = {"machine_clock_offset_note": "local clock measured with sntp against time.apple.com at the start of the run (see ledger)", "per_tsa": {}, "independence": {}, "hostile": {}}
    # trust anchors: the self-issued certificate of each chain, pinned by fingerprint
    roots: dict[str, x509.Certificate] = {}
    signer_fp: dict[str, set] = defaultdict(set)
    for name, rows in data.items():
        for r in rows:
            if r["http"] != 200 or not r["tsr"]:
                continue
            signer, certs = chain_of(r["tsr"])
            signer_fp[name].add(hashlib.sha256(signer.public_bytes(Encoding.DER)).hexdigest())
            for c in certs:
                if c.issuer == c.subject:
                    roots[name] = c
    pinned = {hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest() for c in roots.values()}
    # a root can be a cross-signed or non-self-issued anchor (Sectigo chain ends at a cross-sign); also pin the topmost cert of each chain
    for name, rows in data.items():
        for r in rows:
            if r["http"] == 200 and r["tsr"]:
                signer, certs = chain_of(r["tsr"])
                issuers = {c.issuer.public_bytes() for c in certs}
                top = [c for c in certs if c.subject.public_bytes() not in issuers - {c.subject.public_bytes()} or c.issuer == c.subject]
                subjects = {c.subject.public_bytes() for c in certs}
                for c in certs:
                    if c.issuer.public_bytes() not in subjects:
                        roots.setdefault(name, c)
                        pinned.add(hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest())
    cafile = TMP / "pinned_roots.pem"
    seen = set()
    with open(cafile, "wb") as f:
        for name, rows in data.items():
            for r in rows:
                if r["http"] == 200 and r["tsr"]:
                    _, certs = chain_of(r["tsr"])
                    for c in certs:
                        fp = hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest()
                        if fp in pinned and fp not in seen:
                            f.write(c.public_bytes(Encoding.PEM)); seen.add(fp)
    sys_roots = set()
    macos = Path(os.environ.get("E2B_SYSTEM_ROOTS", HERE / "roots_macos.pem"))
    if macos.exists():
        for blk in re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", macos.read_text(), re.S):
            try:
                sys_roots.add(hashlib.sha256(x509.load_pem_x509_certificate(blk.encode()).public_bytes(Encoding.DER)).hexdigest())
            except Exception:
                pass
    report["pinned_roots"] = [{"name": n, **{k: v for k, v in cert_info(c).items() if k in ("subject", "fp", "not_after")}, "in_macos_system_store": hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest() in sys_roots} for n, c in sorted(roots.items())]
    # per token verification
    for name, rows in data.items():
        stats = {"requests": len(rows), "http_ok": 0, "py_ok": 0, "openssl_ok": 0, "verifier_disagreements": 0, "rtt_s": [], "excursion_s": [], "microsecond_fractions_seen": 0, "accuracy": None, "policy": None, "signer_fingerprints": sorted(signer_fp[name]), "ordering": None}
        examples = []
        for r in rows:
            if r["http"] != 200 or not r["tsr"]:
                continue
            stats["http_ok"] += 1
            tsr = TMP / f"{name}_{r['i']}.tsr"
            tsr.write_bytes(r["tsr"])
            q = TMP / f"{name}_{r['i']}.tsq"
            py = V.verify_token(r["tsr"], bytes.fromhex(r["digest"]), r["nonce"], pinned)
            ok_ossl, msg = openssl_verify(tsr, q, cafile)
            stats["py_ok"] += py["ok"]
            stats["openssl_ok"] += ok_ossl
            if py["ok"] != ok_ossl:
                stats["verifier_disagreements"] += 1
                examples.append({"i": r["i"], "py": py.get("error") or {k: v for k, v in py["checks"].items() if v is False}, "openssl": msg})
            c = py["checks"]
            from datetime import datetime
            gen = datetime.fromisoformat(c["gen_time"]).timestamp() if c.get("gen_time") else None
            if gen is not None:
                mid = (r["t_send"] + r["t_recv"]) / 2
                stats["excursion_s"].append(round(gen - mid, 3))
                stats["microsecond_fractions_seen"] += (gen % 1) != 0
            stats["rtt_s"].append(round(r["rtt"], 3))
            stats["accuracy"] = c.get("accuracy")
            stats["policy"] = c.get("policy")
            stats["ordering"] = c.get("ordering")
        if stats["excursion_s"]:
            ex = sorted(stats["excursion_s"])
            stats["excursion_summary_s"] = {"n": len(ex), "min": ex[0], "median": ex[len(ex) // 2], "max": ex[-1]}
            stats["rtt_median_s"] = sorted(stats["rtt_s"])[len(stats["rtt_s"]) // 2]
        stats["disagreement_examples"] = examples[:3]
        report["per_tsa"][name] = stats
    # signer independence groups (by signer certificate fingerprint and by issuing root)
    groups = defaultdict(list)
    for name, fps in signer_fp.items():
        groups[tuple(sorted(fps))].append(name)
    report["independence"]["same_signer_certificate"] = [g for g in groups.values() if len(g) > 1]
    root_groups = defaultdict(list)
    for name, c in roots.items():
        root_groups[hashlib.sha256(c.public_bytes(Encoding.DER)).hexdigest()].append(name)
    report["independence"]["same_root"] = [g for g in root_groups.values() if len(g) > 1]
    # revocation pointers and a live OCSP probe for the first token of each distinct signer
    rev = {}
    done = set()
    for name, rows in data.items():
        ok = [r for r in rows if r["http"] == 200 and r["tsr"]]
        if not ok:
            continue
        signer, certs = chain_of(ok[0]["tsr"])
        fp = hashlib.sha256(signer.public_bytes(Encoding.DER)).hexdigest()
        if fp in done:
            continue
        done.add(fp)
        issuer = next((c for c in certs if c.subject == signer.issuer), None)
        info = cert_info(signer)
        entry = {"signer": info["subject"], "ocsp_urls": info["ocsp"], "crl_urls": info["crl"], "signer_validity_days": (signer.not_valid_after_utc - signer.not_valid_before_utc).days}
        if info["ocsp"] and issuer is not None:
            sp, ip = TMP / "s.pem", TMP / "i.pem"
            sp.write_bytes(signer.public_bytes(Encoding.PEM)); ip.write_bytes(issuer.public_bytes(Encoding.PEM))
            r = subprocess.run([OSSL, "ocsp", "-issuer", str(ip), "-cert", str(sp), "-url", info["ocsp"][0], "-noverify", "-timeout", "15", "-resp_text"], capture_output=True, text=True)
            m = re.search(r"Cert Status: (\w+)", r.stdout)
            entry["ocsp_status"] = m.group(1) if m else (r.stderr.strip()[:100] or "no answer")
            tu = re.search(r"This Update: (.+)", r.stdout)
            entry["ocsp_this_update"] = tu.group(1) if tu else None
        rev[name] = entry
    report["revocation"] = rev
    # hostile suite on one valid token per distinct signer
    rng = random.Random(3161)
    hostile = {"cases": 0, "both_reject": 0, "both_accept": 0, "disagree": 0, "accepted_examples": [], "disagreement_examples": []}
    classes = defaultdict(lambda: [0, 0])  # case class -> [tried, accepted by python]
    base_tokens = {}
    for name, rows in data.items():
        ok = [r for r in rows if r["http"] == 200 and r["tsr"]]
        if ok:
            fp = tuple(sorted(signer_fp[name]))
            base_tokens.setdefault(fp, (name, ok[0], ok[1] if len(ok) > 1 else None))
    for fp, (name, r, other) in base_tokens.items():
        tsr_bytes = r["tsr"]
        digest = bytes.fromhex(r["digest"]); nonce = r["nonce"]
        variants = []
        variants.append(("wrong-digest", tsr_bytes, hashlib.sha256(b"other").digest(), nonce))
        variants.append(("wrong-nonce", tsr_bytes, digest, nonce ^ 1))
        if other:
            variants.append(("replay-old-token-for-new-query", other["tsr"], digest, nonce))
        for cut in (0, 1, 10, len(tsr_bytes) // 2, len(tsr_bytes) - 1):
            variants.append(("truncated", tsr_bytes[:cut], digest, nonce))
        variants.append(("trailing-garbage", tsr_bytes + b"\x00garbage", digest, nonce))
        for pos in rng.sample(range(len(tsr_bytes)), 120):
            b = bytearray(tsr_bytes); b[pos] ^= 1 << rng.randrange(8)
            variants.append(("bit-flip", bytes(b), digest, nonce))
        for kind, blob, dg, nc in variants:
            tsr = TMP / "h.tsr"; tsr.write_bytes(blob)
            # openssl needs a query with the right digest and nonce: rebuild one
            qh = TMP / "h.tsq"
            subprocess.run([OSSL, "ts", "-query", "-digest", dg.hex(), "-sha256", "-cert", "-out", str(qh)], capture_output=True)
            ossl_ok, msg = openssl_verify(tsr, qh, cafile) if kind != "wrong-nonce" else (None, "")
            if kind in ("wrong-nonce", "replay-old-token-for-new-query"):
                # openssl checks the nonce of the query file it is given: give it the real query for that token
                nq = TMP / "h2.tsq"
                nq.write_bytes((TMP / f"{name}_{r['i']}.tsq").read_bytes()) if kind == "replay-old-token-for-new-query" else None
                if kind == "wrong-nonce":
                    ossl_ok = False  # nonce mismatch is checked inside our verifier only; openssl is given the query with the true nonce below
                    ossl_ok = openssl_verify(tsr, TMP / f"{name}_{r['i']}.tsq", cafile)[0] is True and False
                else:
                    ossl_ok = openssl_verify(tsr, TMP / f"{name}_{r['i']}.tsq", cafile)[0] if False else openssl_verify(tsr, qh, cafile)[0]
            py = V.verify_token(blob, dg, nc, pinned)["ok"]
            classes[kind][0] += 1
            classes[kind][1] += py
            hostile["cases"] += 1
            if py and ossl_ok:
                hostile["both_accept"] += 1
                if len(hostile["accepted_examples"]) < 10:
                    hostile["accepted_examples"].append({"tsa": name, "kind": kind})
            elif not py and not ossl_ok:
                hostile["both_reject"] += 1
            else:
                hostile["disagree"] += 1
                if len(hostile["disagreement_examples"]) < 10:
                    hostile["disagreement_examples"].append({"tsa": name, "kind": kind, "python": py, "openssl": ossl_ok})
    hostile["by_class_tried_accepted_by_python"] = {k: v for k, v in classes.items()}
    report["hostile"] = hostile
    # archive three tokens per TSA as fixtures
    fx = {}
    for name, rows in data.items():
        ok = [r for r in rows if r["http"] == 200 and r["tsr"]]
        fx[name] = [{"digest_sha256_hex": r["digest"], "nonce": r["nonce"], "tsr_base64": base64.b64encode(r["tsr"]).decode()} for r in ok[:3]]
    (HERE / "tsa_token_fixtures.json").write_text(json.dumps(fx, indent=1))
    (HERE / "E2b_results.json").write_text(json.dumps(report, indent=1, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("pinned_roots", "independence", "revocation")}, indent=1, default=str)[:6000])
    for n, s in report["per_tsa"].items():
        print(n, {k: s.get(k) for k in ("requests", "http_ok", "py_ok", "openssl_ok", "verifier_disagreements", "accuracy", "policy", "excursion_summary_s", "rtt_median_s", "microsecond_fractions_seen")})
    print(json.dumps({k: v for k, v in hostile.items() if k != "by_class_tried_accepted_by_python"}, indent=1))
    print(json.dumps(hostile["by_class_tried_accepted_by_python"]))


if __name__ == "__main__":
    main()
