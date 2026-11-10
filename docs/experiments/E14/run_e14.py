#!/usr/bin/env python3
"""E14 driver: live positive set (several accounts, three recent posts each) and negative controls on every positive case."""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import atproto_verify as a

HANDLES = ["bsky.app", "atproto.com", "jay.bsky.team", "pfrazee.com", "why.bsky.team", "nytimes.com", "theverge.com"]
rows = []; others_key = {}
for h in HANDLES:
    try:
        did = json.loads(a.get(f"https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={h}"))["did"]
        doc = json.loads(a.get(f"https://plc.directory/{did}" if did.startswith("did:plc:") else f"https://{did[8:]}/.well-known/did.json"))
        others_key[h] = [v for v in doc["verificationMethod"] if v["id"].endswith("#atproto")][0]["publicKeyMultibase"]
        feed = json.loads(a.get(f"https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={did}&limit=8&filter=posts_no_replies"))["feed"]
    except Exception as e:
        rows.append({"handle": h, "error": f"setup: {e}"}); continue
    n = 0
    for item in feed:
        uri = item["post"]["uri"]; d, coll, rkey = uri[5:].split("/")
        if d != did: continue   # skip reposts of others' posts
        try:
            pos = a.verify_record(did, coll, rkey, did_doc=doc)
        except Exception as e:
            rows.append({"handle": h, "rkey": rkey, "error": f"verify: {type(e).__name__}: {e}"}); continue
        neg = {}
        for t in ("flip_record_byte", "change_rev", "malleate_high_s"):
            try: neg[t] = a.verify_record(did, coll, rkey, tamper=t, did_doc=doc)["result"]
            except Exception as e: neg[t] = f"REJECT(exception): {type(e).__name__}"
        other = [k for k in others_key if k != h]
        wrong = others_key[other[0]] if other else None
        if wrong:
            try: neg["wrong_key"] = a.verify_record(did, coll, rkey, did_doc=doc, use_key=wrong)["result"]
            except Exception as e: neg["wrong_key"] = f"REJECT(exception): {type(e).__name__}"
        rows.append({"handle": h, "rkey": rkey, "curve": pos.get("curve"), "result": pos["result"], "commit_rev": pos.get("commit_rev"), "record_created_at": pos.get("record_created_at"), "negatives": neg})
        n += 1
        if n >= 3: break
summary = {"positive_cases": sum(1 for r in rows if r.get("result") == "VERIFIED"), "positive_failures": [r for r in rows if "result" in r and r["result"] != "VERIFIED"],
           "errors": [r for r in rows if "error" in r], "curves": sorted({r["curve"] for r in rows if r.get("curve")}),
           "negatives_all_rejected": all(not v.startswith("VERIFIED") for r in rows for v in r.get("negatives", {}).values()),
           "negative_cases": sum(len(r.get("negatives", {})) for r in rows)}
json.dump({"summary": summary, "rows": rows}, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E14_results.json"), "w"), indent=1)
print(json.dumps(summary, indent=1))
for r in rows[:4]: print(json.dumps(r)[:600])
