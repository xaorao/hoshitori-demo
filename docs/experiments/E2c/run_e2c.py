#!/usr/bin/env python3
"""Experiment E2c runner: builds one real bundle (tokens from nine live endpoints over one checkpoint), derives ~30 tampered or
downgraded variants, and compares the two independently written verifiers (Python, JavaScript) on every one.
Run with the E2b virtual environment (cryptography, asn1crypto): <venv>/bin/python -W ignore -I run_e2c.py"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import common as C  # noqa: E402
import make_bundle as MB  # noqa: E402

PY = sys.executable
NODE = "/opt/homebrew/bin/node"
WORK = Path(tempfile.mkdtemp(prefix="e2c_"))
SEED = b"e2c-experiment-key-not-a-secret"
WIDE = {
    "globalsign": "http://timestamp.globalsign.com/tsa/r6advanced1", "swisssign": "http://tsa.swisssign.net", "certum": "http://time.certum.pl",
    "digicert": "http://timestamp.digicert.com", "aimoda": "https://rfc3161.ai.moda", "sectigo": "http://timestamp.sectigo.com",
    "entrust": "http://timestamp.entrust.net/TSS/RFC3161sha2TS", "freetsa": "https://freetsa.org/tsr", "apple": "http://timestamp.apple.com/ts01",
}


def run_verifiers(d: Path, trusted: Path | None = None):
    extra = ["--trusted-root", str(trusted)] if trusted else []
    a = json.loads(subprocess.run([PY, "-W", "ignore", "-I", str(HERE / "verify_bundle.py"), str(d)] + extra, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}).stdout or "{}")
    b = json.loads(subprocess.run([NODE, str(HERE / "verify_bundle.mjs"), str(d)] + extra, capture_output=True, text=True, env={"PATH": "/usr/bin:/bin"}).stdout or "{}")
    return a, b


def summary(v):
    t = v.get("timing", {})
    return {"integrity_ok": v.get("integrity", {}).get("ok"), "level": t.get("level_supported"), "independent_operators": t.get("independent_operators"), "scorable": v.get("eligibility", {}).get("scorable"),
            "root_of_trust": v.get("root_of_trust"), "bundle_policy_differs": v.get("bundle_policy_differs"), "public_authenticated": (v.get("public_commit") or {}).get("authenticated_offline")}


def variant(name: str, base: Path, edit=None, keep_tsa: list[str] | None = None) -> Path:
    d = WORK / name
    shutil.copytree(base, d)
    if keep_tsa is not None:
        for f in (d / "tsa").glob("*.tsr"):
            if f.stem not in keep_tsa:
                f.unlink()
    if edit:
        edit(d)
    return d


def fake_authorities(work: Path, digest_hex: str, names=("fake-a", "fake-b")):
    """Two self-made time-stamp authorities (own CA, own TSA certificate with the critical time-stamping key usage) and a token from each over digest_hex.
    This is a bundle forged entirely offline: no real authority is involved."""
    import hashlib
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    OSSL = MB.OSSL
    out = {}
    for n in names:
        d = work / n; d.mkdir(parents=True, exist_ok=True)
        run = lambda *a, **k: subprocess.run([OSSL, *a], cwd=d, check=True, capture_output=True, **k)
        run("req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", "ca.key", "-out", "ca.pem", "-subj", f"/CN={n} root", "-days", "30")
        run("req", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256", "-nodes", "-keyout", "tsa.key", "-out", "tsa.csr", "-subj", f"/CN={n} tsa")
        (d / "ext.cnf").write_text("basicConstraints=CA:FALSE\nkeyUsage=critical,digitalSignature\nextendedKeyUsage=critical,timeStamping\n")
        run("x509", "-req", "-in", "tsa.csr", "-CA", "ca.pem", "-CAkey", "ca.key", "-CAcreateserial", "-out", "tsa.pem", "-days", "29", "-extfile", "ext.cnf")
        (d / "ts.cnf").write_text("[tsa]\ndefault_tsa=tsa_config\n[tsa_config]\nserial=ts.srl\nsigner_cert=tsa.pem\ncerts=ca.pem\nsigner_key=tsa.key\nsigner_digest=sha256\ndefault_policy=1.2.3.4.1\ndigests=sha256\naccuracy=secs:1\nordering=no\ntsa_name=yes\ness_cert_id_alg=sha256\n")
        (d / "ts.srl").write_text("01\n")
        run("ts", "-query", "-digest", digest_hex, "-sha256", "-cert", "-out", "q.tsq")
        run("ts", "-reply", "-queryfile", "q.tsq", "-config", "ts.cnf", "-section", "tsa_config", "-out", "r.tsr")
        root_der = x509.load_pem_x509_certificate((d / "ca.pem").read_bytes()).public_bytes(Encoding.DER)
        out[n] = {"token": (d / "r.tsr").read_bytes(), "root_pem": (d / "ca.pem").read_bytes(), "root_fp": hashlib.sha256(root_der).hexdigest()}
    return out


def main():
    base_ok = WORK / "wide"
    info = MB.build(base_ok, WIDE, seed=SEED)
    fc0 = json.loads((base_ok / "items" / "fc-0001.canonical.json").read_text())
    # the wide bundle's policy must pin Apple too so that its SHA-1 token is rejected for the right reason, not for an unpinned root
    pol = json.loads((base_ok / "policy.json").read_text())
    e2b = json.loads((C.EXP / "E2b" / "E2b_results.json").read_text())
    apple = next(r for r in e2b["pinned_roots"] if r["name"] == "apple")
    pol["tsa"]["pinned_roots_sha256"][apple["fp"]] = "apple"
    pol["operators"]["apple"] = MB.REAL_REGISTRY["apple"]       # Apple publishes this root on its own site (E23): the SHA-1 case below is then rejected for the SHA-1 signer alone
    (base_ok / "policy.json").write_text(json.dumps(pol, indent=1))
    from asn1crypto import tsp
    from cryptography import x509
    from cryptography.hazmat.primitives.serialization import Encoding
    import hashlib
    pem = b""; seen = set()
    for t in (base_ok / "tsa").glob("*.tsr"):
        for c in tsp.TimeStampResp.load(t.read_bytes())["time_stamp_token"]["content"]["certificates"]:
            cert = x509.load_der_x509_certificate(c.chosen.dump())
            fp = hashlib.sha256(cert.public_bytes(Encoding.DER)).hexdigest()
            if fp in pol["tsa"]["pinned_roots_sha256"] and fp not in seen:
                pem += cert.public_bytes(Encoding.PEM); seen.add(fp)
    (base_ok / "tsa_roots.pem").write_bytes(pem)
    # the trusted root: what ships with the verifier and is published independently of any bundle
    TR = WORK / "trusted_root.json"; TR.write_text(json.dumps(pol, indent=1))

    def tr_variant(name, fn):
        p = json.loads(TR.read_text()); fn(p); f = WORK / name; f.write_text(json.dumps(p, indent=1)); return f

    def rw(d, name, fn, mode="text"):
        p = d / name
        p.write_bytes(fn(p.read_bytes()))

    cases = []  # (name, expectation, dir, trusted root path or None)
    add = lambda name, expect, d, trusted=TR: cases.append((name, expect, d, trusted))
    two = ["globalsign", "certum"]
    TWO = {k: WIDE[k] for k in two}
    add("happy: globalsign + certum (both have ownership evidence)", "C3", variant("v_happy", base_ok, keep_tsa=two))
    add("happy: digicert + globalsign", "C3", variant("v_happy2", base_ok, keep_tsa=["digicert", "globalsign"]))
    add("happy: sectigo + certum", "C3", variant("v_happy3", base_ok, keep_tsa=["sectigo", "certum"]))
    add("three operators, one without evidence (swisssign)", "C3", variant("v_three", base_ok, keep_tsa=["globalsign", "swisssign", "certum"]))
    add("globalsign + swisssign: swisssign has no ownership evidence in the registry", "BELOW", variant("v_nosw", base_ok, keep_tsa=["globalsign", "swisssign"]))
    add("globalsign + freetsa: freetsa has no ownership evidence in the registry", "BELOW", variant("v_nofree", base_ok, keep_tsa=["globalsign", "freetsa"]))
    add("alias pair: sectigo + entrust (same signer)", "BELOW", variant("v_alias1", base_ok, keep_tsa=["sectigo", "entrust"]))
    add("alias pair: digicert + aimoda (same signer)", "BELOW", variant("v_alias2", base_ok, keep_tsa=["digicert", "aimoda"]))
    add("single token", "BELOW", variant("v_single", base_ok, keep_tsa=["globalsign"]))
    add("no token", "BELOW", variant("v_none", base_ok, keep_tsa=[]))
    add("sha-1 CMS signer (apple) + globalsign", "BELOW", variant("v_apple", base_ok, keep_tsa=["apple", "globalsign"]))
    add("same token twice", "BELOW", variant("v_dup", base_ok, edit=lambda d: shutil.copy(d / "tsa/globalsign.tsr", d / "tsa/globalsign_copy.tsr"), keep_tsa=["globalsign"]))
    other = WORK / "other"
    MB.build(other, {"certum": WIDE["certum"]}, seed=SEED + b"x")
    add("token of another checkpoint replaces one", "BELOW", variant("v_swap", base_ok, edit=lambda d: shutil.copy(other / "tsa/certum.tsr", d / "tsa/certum.tsr"), keep_tsa=["globalsign", "certum"]))
    # integrity attacks (edits to proven bytes)
    R = lambda d, f, a, b: rw(d, f, lambda x: x.replace(a, b))
    add("record tampered (p 0.52 -> 0.99)", "INTEGRITY", variant("v_rec", base_ok, edit=lambda d: R(d, "items/fc-0001.canonical.json", b'"p":"0.52"', b'"p":"0.99"'), keep_tsa=two))
    add("record not canonical (space added)", "INTEGRITY", variant("v_ws", base_ok, edit=lambda d: R(d, "items/fc-0001.canonical.json", b'{"agent', b'{ "agent'), keep_tsa=two))
    add("raw response modified", "INTEGRITY", variant("v_raw", base_ok, edit=lambda d: R(d, "raw/ryo_response.json", b"143.2100", b"999.0000"), keep_tsa=two))
    add("raw response missing", "INTEGRITY", variant("v_noraw", base_ok, edit=lambda d: (d / "raw/ryo_response.json").unlink(), keep_tsa=two))
    add("checkpoint size edited", "INTEGRITY", variant("v_size", base_ok, edit=lambda d: [R(d, f"items/{n}.tlog-proof", b"\n8\n", b"\n9\n") for n in ("fc-0001", "ev-0001")], keep_tsa=two))
    def root_edit(d):
        for n in ("fc-0001", "ev-0001"):
            def f(b):
                i = b.index(b"\n8\n") + 3
                return bytes(b[:i]) + (b"Z" if b[i:i + 1] != b"Z" else b"Y") + bytes(b[i + 1:])
            rw(d, f"items/{n}.tlog-proof", f)
    add("checkpoint root edited", "INTEGRITY", variant("v_root", base_ok, edit=root_edit, keep_tsa=two))
    add("signature line removed", "INTEGRITY", variant("v_nosig", base_ok, edit=lambda d: [rw(d, f"items/{n}.tlog-proof", lambda b: b[:b.index("\u2014".encode())]) for n in ("fc-0001", "ev-0001")], keep_tsa=two))
    def path_flip(d):
        def f(b):
            j = b.index(b"\n\n") - 5
            return b[:j] + (b"A" if b[j:j + 1] != b"A" else b"B") + b[j + 1:]
        rw(d, "items/fc-0001.tlog-proof", f)
    add("inclusion path element flipped", "INTEGRITY", variant("v_path", base_ok, edit=path_flip, keep_tsa=two))
    def path_trunc(d):
        def f(b):
            parts = b.split(b"\n", 3)
            return parts[0] + b"\n" + parts[1] + b"\n" + parts[2] + b"\n" + b"\n".join(parts[3].split(b"\n")[1:])
        rw(d, "items/fc-0001.tlog-proof", f)
    add("inclusion path truncated", "INTEGRITY", variant("v_trunc", base_ok, edit=path_trunc, keep_tsa=two))
    add("index edited", "INTEGRITY", variant("v_index", base_ok, edit=lambda d: R(d, "items/fc-0001.tlog-proof", b"index 5", b"index 4"), keep_tsa=two))
    add("origin renamed", "INTEGRITY", variant("v_origin", base_ok, edit=lambda d: [R(d, f"items/{n}.tlog-proof", b"experiment-log", b"experiment-lag") for n in ("fc-0001", "ev-0001")], keep_tsa=two))
    # the signed timing facts: edits to the record that carries them break integrity (an outside review: they used to sit in an unsigned commit.json)
    def edit_rec(d, a, b):
        R(d, "items/fc-0001.canonical.json", a.encode(), b.encode())
    add("signed committed_at edited after stamping", "INTEGRITY", variant("v_ca", base_ok, edit=lambda d: edit_rec(d, fc0["committed_at"], fc0["committed_at"][:-5] + "0.000Z"), keep_tsa=two))
    add("signed outcome window edited after stamping (hide a late commit)", "INTEGRITY", variant("v_win", base_ok, edit=lambda d: edit_rec(d, fc0["outcome_window_opens_at"], "2099-01-01T00:00:00.000+00:00"), keep_tsa=two))
    # timing cases: rebuilt, because committed_at and the window are inside the signed record and the tokens cover them
    def timing(name, expect, **kw):
        d = WORK / name; MB.build(d, TWO, seed=SEED, **kw); add(name_map[name], expect, d)
    name_map = {"t_after_hour": "committed_at an hour after the tokens (tokens predate the stated commit)", "t_early_hour": "committed_at an hour earlier (tokens too late)",
                "t_pre30": "tokens 30 s before the stated commit (outside the asymmetric window)", "t_post55": "tokens 55 s after the stated commit (inside the window)",
                "t_post64": "tokens 64 s after the stated commit (outside the window)", "t_window_before": "outcome window opened before the tokens"}
    timing("t_after_hour", "BELOW", committed_shift_s=3600); timing("t_early_hour", "BELOW", committed_shift_s=-3600); timing("t_pre30", "BELOW", committed_shift_s=30)
    timing("t_post55", "C3", committed_shift_s=-55); timing("t_post64", "BELOW", committed_shift_s=-64); timing("t_window_before", "BELOW", window_after_s=-10)
    # operator registry
    add("policy pins only one operator", "BELOW", variant("v_pin1", base_ok, keep_tsa=two), tr_variant("tr_pin1.json", lambda p: p["tsa"]["pinned_roots_sha256"].pop(next(k for k, v in p["tsa"]["pinned_roots_sha256"].items() if v == "certum"))))
    add("two roots, one parent in the operator registry", "BELOW", variant("v_parent", base_ok, keep_tsa=two), tr_variant("tr_parent.json", lambda p: [p["operators"][k].update({"parent": "same-owner"}) for k in ("globalsign", "certum")]))
    add("operator entry without ownership evidence", "BELOW", variant("v_noev", base_ok, keep_tsa=two), tr_variant("tr_noev.json", lambda p: p["operators"]["certum"].update({"ownership_evidence": None})))
    add("evidence is a bare string (the earlier placeholder form)", "BELOW", variant("v_strev", base_ok, keep_tsa=two), tr_variant("tr_strev.json", lambda p: p["operators"]["certum"].update({"ownership_evidence": "claimed"})))
    add("evidence kind not accepted by the trusted policy", "BELOW", variant("v_kind", base_ok, keep_tsa=two), tr_variant("tr_kind.json", lambda p: p["operators"]["certum"]["ownership_evidence"].update({"kind": "self_declared"})))
    add("evidence without a reference", "BELOW", variant("v_noref", base_ok, keep_tsa=two), tr_variant("tr_noref.json", lambda p: p["operators"]["certum"]["ownership_evidence"].update({"ref": ""})))
    add("evidence is about another root (copied from a different operator)", "BELOW", variant("v_other", base_ok, keep_tsa=two), tr_variant("tr_other.json", lambda p: p["operators"]["certum"].update({"ownership_evidence": dict(p["operators"]["globalsign"]["ownership_evidence"])})))
    add("kind accepted list empty in the trusted policy", "BELOW", variant("v_emptykinds", base_ok, keep_tsa=two), tr_variant("tr_emptykinds.json", lambda p: p.update({"evidence_kinds_accepted": []})))
    # trust root attacks
    other_key = WORK / "attacker_key"
    MB.build(other_key, TWO, seed=b"attacker-key-not-a-secret")                      # attacker's own log key and policy, real fresh tokens
    add("WHOLE BUNDLE SUBSTITUTED (attacker log key and policy, real fresh tokens), pinned trusted root", "INTEGRITY", other_key)
    add("WHOLE BUNDLE SUBSTITUTED (same bundle), NO trusted root supplied", "UNAUTH", other_key, None)
    # a bundle forged entirely offline with two self-made time-stamp authorities
    fk = WORK / "fake_bundle"; finfo = MB.build(fk, {}, seed=b"attacker-key-not-a-secret")
    fa = fake_authorities(WORK / "fake_auth", finfo["digest"])
    pol_f = json.loads((fk / "policy.json").read_text()); roots_pem = b""
    for n, v in fa.items():
        (fk / "tsa" / f"{n}.tsr").write_bytes(v["token"]); roots_pem += v["root_pem"]
        pol_f["tsa"]["pinned_roots_sha256"][v["root_fp"]] = n; pol_f["operators"][n] = {"parent": n, "ownership_evidence": {"kind": "ccadb_ca_owner_of_record", "ref": "A-forged", "subject_sha256": v["root_fp"]}}
    (fk / "tsa_roots.pem").write_bytes(roots_pem); (fk / "policy.json").write_text(json.dumps(pol_f, indent=1))
    add("FORGED OFFLINE with two self-made time-stamp authorities, pinned trusted root", "INTEGRITY_OR_BELOW", fk)
    add("FORGED OFFLINE with two self-made time-stamp authorities, NO trusted root supplied", "UNAUTH", fk, None)
    # only the policy is substituted: records are the honest ones, the bundle's policy names other keys
    def evil_policy(d):
        p = json.loads((d / "policy.json").read_text()); p["log"]["vkey"] = C.vkey(p["log"]["origin"], bytes(32)); (d / "policy.json").write_text(json.dumps(p))
    add("only the bundle's policy.json replaced by another log key (honest records), pinned trusted root", "C3_POLICY_DIFFERS", variant("v_polsub", base_ok, edit=evil_policy, keep_tsa=two))
    add("honest bundle, NO trusted root supplied", "UNAUTH", variant("v_notrust", base_ok, keep_tsa=two), None)
    # public-commit claims are not proof
    def forge_pub(d):
        (d / "publication.json").write_text(json.dumps({"claims": [{"url": "https://example.invalid/post/1", "sha256": "0" * 64, "claimed_time": "2020-01-01T00:00:00Z"}] * 3}))
    add("forged publication claims in the bundle", "C3_PUBLIC_NOT_AUTH", variant("v_pub", base_ok, edit=forge_pub, keep_tsa=two))
    # the full operator rewrite attack: same key, different history, original tokens
    forged = WORK / "forged"
    def mut(records):
        records[5]["p"] = "0.99"
    MB.build(forged, {}, seed=SEED, mutate=mut, reuse_tsa=base_ok / "tsa", committed_at_override=fc0["committed_at"], window_override=fc0["outcome_window_opens_at"])
    shutil.copy(base_ok / "policy.json", forged / "policy.json"); shutil.copy(base_ok / "tsa_roots.pem", forged / "tsa_roots.pem")
    for f in (forged / "tsa").glob("*.tsr"):
        if f.stem not in two:
            f.unlink()
    add("FULL OPERATOR REWRITE: same key re-signs a different history, original tokens kept", "BELOW_BUT_INTEGRITY_OK", forged)

    rows, disagreements = [], []
    for name, expect, d, trusted in cases:
        a, b = run_verifiers(d, trusted)
        sa, sb = summary(a), summary(b)
        agree = sa == sb
        rows.append({"case": name, "expect": expect, "python": sa, "javascript": sb, "agree": agree})
        if not agree:
            disagreements.append(name)
    def judge(r):
        e, s = r["expect"], r["python"]; lv = s["level"] or ""
        if e == "C3":
            return bool(s["integrity_ok"]) and lv == "C3" and s["root_of_trust"] == "PINNED"
        if e == "BELOW":
            return bool(s["integrity_ok"]) and lv != "C3"
        if e == "INTEGRITY":
            return not s["integrity_ok"] and lv != "C3"
        if e == "INTEGRITY_OR_BELOW":
            return lv != "C3"
        if e == "BELOW_BUT_INTEGRITY_OK":
            return bool(s["integrity_ok"]) and lv != "C3"
        if e == "UNAUTH":
            return bool(s["integrity_ok"]) and lv.startswith("C3-if-authentic") and s["root_of_trust"] == "BUNDLE_ASSERTED" and not s["scorable"]
        if e == "C3_POLICY_DIFFERS":
            return bool(s["integrity_ok"]) and lv == "C3" and s["bundle_policy_differs"] is True
        if e == "C3_PUBLIC_NOT_AUTH":
            return bool(s["integrity_ok"]) and lv == "C3" and s["public_authenticated"] == 0
    res = {"cases": len(rows), "verifier_disagreements": len(disagreements), "expectation_failures": [r["case"] for r in rows if not judge(r)], "rows": rows,
           "tokens_on_wide_bundle": sorted(WIDE), "note": "bundle contents are synthetic; tokens are real except the two self-made authorities of the offline forgery"}
    (HERE / "E2c_results.json").write_text(json.dumps(res, indent=1))
    for r in rows:
        print(("OK  " if judge(r) else "FAIL"), ("same" if r["agree"] else "DIFF"), r["case"], "|", r["python"]["integrity_ok"], r["python"]["level"], r["python"]["independent_operators"], "|", r["javascript"]["integrity_ok"], r["javascript"]["level"])
    print(json.dumps({k: res[k] for k in ("cases", "verifier_disagreements", "expectation_failures")}))
    shutil.rmtree(WORK, ignore_errors=True)


if __name__ == "__main__":
    main()
