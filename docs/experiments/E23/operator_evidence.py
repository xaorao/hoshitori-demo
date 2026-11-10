"""E23: ownership evidence for time-stamp operators from the public CA database (CCADB), an outside review point 4.
For each endpoint: request a token, extract every certificate in the token (signer first), compute SHA-256 fingerprints, and look up each in the CCADB all-certificate-records report
(read from a local copy). Records the CA Owner, Subordinate CA Owner and record id of the first certificate in the chain that CCADB knows. Evidence is a public record about the
certificate; it is not a statement by the operator. Not product code."""
import csv, hashlib, json, os, re, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
CCADB = sys.argv[1]
ENDPOINTS = {
    "digicert": "http://timestamp.digicert.com", "sectigo": "http://timestamp.sectigo.com", "globalsign": "http://timestamp.globalsign.com/tsa/r6advanced1",
    "swisssign": "http://tsa.swisssign.net", "apple": "http://timestamp.apple.com/ts01", "freetsa": "https://freetsa.org/tsr", "entrust": "http://timestamp.entrust.net/TSS/RFC3161sha2TS",
    "aimoda": "https://rfc3161.ai.moda", "certum": "http://time.certum.pl",
}
rows = list(csv.DictReader(open(CCADB, newline="", encoding="utf-8", errors="ignore")))
by_fp = {r["SHA-256 Fingerprint"].lower().replace(":", ""): r for r in rows if r.get("SHA-256 Fingerprint")}


def fetch(url, tmp):
    q = os.path.join(tmp, "q.tsq"); open(os.path.join(tmp, "d"), "wb").write(os.urandom(32))
    subprocess.run(["openssl", "ts", "-query", "-data", os.path.join(tmp, "d"), "-sha256", "-cert", "-no_nonce", "-out", q], capture_output=True, check=True)
    r = subprocess.run(["curl", "-s", "-m", "20", "-H", "Content-Type: application/timestamp-query", "--data-binary", f"@{q}", url, "-o", os.path.join(tmp, "r.tsr")], capture_output=True)
    p = os.path.join(tmp, "r.tsr")
    if not os.path.exists(p) or os.path.getsize(p) == 0: return None
    t = os.path.join(tmp, "t.tst")
    if subprocess.run(["openssl", "ts", "-reply", "-in", p, "-token_out", "-out", t], capture_output=True).returncode: return None
    pem = subprocess.run(["openssl", "pkcs7", "-inform", "DER", "-in", t, "-print_certs"], capture_output=True).stdout.decode()
    return re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----", pem, re.S)


def info(pem):
    der = subprocess.run(["openssl", "x509", "-outform", "DER"], input=pem.encode(), capture_output=True).stdout
    subj = subprocess.run(["openssl", "x509", "-noout", "-subject", "-issuer"], input=pem.encode(), capture_output=True).stdout.decode().strip().replace("\n", " | ")
    return hashlib.sha256(der).hexdigest(), subj


out = {}
for name, url in ENDPOINTS.items():
    tmp = tempfile.mkdtemp(prefix="e23_")
    try:
        certs = fetch(url, tmp)
    except Exception as e:
        certs = None
    if not certs: out[name] = {"url": url, "result": "NO_TOKEN"}; continue
    chain = []
    for pem in certs:
        fp, subj = info(pem); rec = by_fp.get(fp)
        chain.append({"sha256": fp, "subject_issuer": subj, "ccadb": None if not rec else {"ca_owner": rec["CA Owner"], "certificate_name": rec["Certificate Name"], "record_type": rec["Certificate Record Type"],
                      "subordinate_ca_owner": rec["Subordinate CA Owner"], "salesforce_record_id": rec["Salesforce Record ID"], "mozilla": rec["Mozilla Status"][:40], "parent": rec["Parent Certificate Name"]}})
    out[name] = {"url": url, "chain": chain}
json.dump(out, open(os.path.join(HERE, "E23_results.json"), "w"), indent=1)
for n, v in out.items():
    print(n, v.get("result", ""))
    for c in v.get("chain", []):
        print("   ", c["sha256"][:16], "|", c["subject_issuer"][:110], "|", (c["ccadb"] or {}).get("ca_owner"), "/", (c["ccadb"] or {}).get("certificate_name"), "/", (c["ccadb"] or {}).get("record_type"))
