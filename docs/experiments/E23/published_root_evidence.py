"""E23: evidence of kind `operator_published_root`: the operator's own HTTPS site publishes a certificate whose SHA-256 equals the pinned root. Downloads the certificate, hashes its DER form,
compares with the pinned fingerprint, writes the record. It is the operator's statement about its own root; it names the organisation by the certificate subject and the site, not by a
third-party register. Only Apple's page was retrievable; SwissSign's page answered 403 (Cloudflare challenge) to curl and to a headless browser, and no bot-detection evasion was attempted."""
import hashlib, json, os, subprocess, sys, tempfile
HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = {"apple": {"url": "https://www.apple.com/appleca/AppleIncRootCertificate.cer", "page": "https://www.apple.com/certificateauthority/", "organisation": "Apple Inc."}}
e2b = json.load(open(os.path.join(HERE, "..", "E2b", "E2b_results.json")))
pinned = {r["name"]: r["fp"].lower() for r in e2b["pinned_roots"]}
out = {}
for name, s in SOURCES.items():
    d = subprocess.run(["curl", "-sL", "-m", "40", s["url"]], capture_output=True).stdout
    der = d if d[:1] == b"\x30" else subprocess.run(["openssl", "x509", "-outform", "DER"], input=d, capture_output=True).stdout
    fp = hashlib.sha256(der).hexdigest()
    subj = subprocess.run(["openssl", "x509", "-inform", "DER", "-noout", "-subject"], input=der, capture_output=True).stdout.decode().strip()
    out[name] = {"url": s["url"], "listing_page": s["page"], "downloaded_sha256": fp, "pinned_sha256": pinned[name], "matches_pinned": fp == pinned[name], "subject": subj, "organisation": s["organisation"]}
json.dump(out, open(os.path.join(HERE, "published_root_evidence.json"), "w"), indent=1); print(json.dumps(out, indent=1))
