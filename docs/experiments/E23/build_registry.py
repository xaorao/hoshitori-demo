"""E23: build operator_registry.json from E23_results.json and the E2b pinned roots. An entry carries structured ownership evidence only when the PINNED ROOT certificate itself has a record in the
CCADB all-certificate-records report; its `ref` is the CCADB record id, `subject_sha256` the pinned root's fingerprint (a verifier checks that it equals the root a token actually chained to).
Everything else gets null evidence: a certificate subject that names an organisation is a self-description, not evidence. Not product code."""
import csv, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
CCADB = sys.argv[1]
e2b = json.load(open(os.path.join(HERE, "..", "E2b", "E2b_results.json")))
rows = {r["SHA-256 Fingerprint"].lower().replace(":", ""): r for r in csv.DictReader(open(CCADB, newline="", encoding="utf-8", errors="ignore")) if r.get("SHA-256 Fingerprint")}
PUBLISHED = json.load(open(os.path.join(HERE, "published_root_evidence.json")))
reg, aliases = {}, {}
ENDPOINT_ONLY = {"aimoda", "entrust"}      # endpoint labels, not operators: observed answering with another operator's signer (CF-02; E23 live chains)
for r in e2b["pinned_roots"]:
    n, fp = r["name"], r["fp"].lower(); rec = rows.get(fp)
    if n in ENDPOINT_ONLY:
        aliases[n] = "endpoint label only; its tokens chained to the Sectigo Public Time Stamping root in the E23 live run"; continue
    if rec:
        reg[n] = {"parent": rec["CA Owner"], "ownership_evidence": {"kind": "ccadb_ca_owner_of_record", "ref": rec["Salesforce Record ID"], "subject_sha256": fp,
                  "ca_owner": rec["CA Owner"], "ccadb_certificate_name": rec["Certificate Name"], "ccadb_record_type": rec["Certificate Record Type"],
                  "source": "CCADB AllCertificateRecordsCSVFormatv4 (downloaded copy)",
                  "limits": "organisation of record in the CA database for this root; ultimate corporate ownership is not established"}}
    elif n in PUBLISHED and PUBLISHED[n]["matches_pinned"]:
        pr = PUBLISHED[n]
        reg[n] = {"parent": pr["organisation"], "ownership_evidence": {"kind": "operator_published_root", "ref": pr["url"], "subject_sha256": fp, "organisation": pr["organisation"], "listing_page": pr["listing_page"],
                  "source": "the operator's own HTTPS site publishes this certificate (E23 published_root_evidence.json)", "limits": "the operator's statement about its own root, not a third-party register; ultimate ownership is not established"}}
    else:
        reg[n] = {"parent": n, "ownership_evidence": None, "note": "the pinned root has no record in the CCADB all-certificate-records report; the certificate subject is a self-description"}
json.dump({"operators": reg, "endpoint_labels_not_operators": aliases}, open(os.path.join(HERE, "operator_registry.json"), "w"), indent=1)
for n, v in sorted(reg.items()): print("%-11s parent=%-22s evidence=%s" % (n, v["parent"], (v["ownership_evidence"] or {}).get("ref")))
