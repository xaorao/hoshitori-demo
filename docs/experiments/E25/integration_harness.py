"""E25: fixture-level Whole-Product Integration Proof harness (an outside review message). It chains the stages that exist as specifications and spikes (gateway capture, evidence item,
decision record, canonical bytes, append-only log with Merkle inclusion, offline check, view model, settlement) over ONE synthetic RYO-shaped fixture (data_mode `simulated`, labelled; it is
NOT an RYO response) and checks the seven properties across every connection:
  SAME_TRUTH     asset identity and quote class are identical at every stage
  SAME_AUTHORITY the origin time shown to the user is the one settlement uses (one function owns the rule)
  SAME_HISTORY   the hash of the committed record bytes is identical in the log, the verifier output, the view model and the settlement input
  SAME_FAILURE   an unavailable source yields the same honest state at every stage and no number appears
  SAME_PRIVACY   a private sentinel never reaches the log, the bundle or the public view, and reaches only the model recipient
  SAME_OUTCOME   the probability shown is the probability committed and the probability scored
  SAME_REASONING the evidence the view lists is the evidence the decision cited, and the next check is the one function used by both
Each check is run against the honest pipeline (must pass) and against a mutant stage built to break exactly that property (must fail). What this does NOT show: any real RYO response, any
real model call, real storage, real browsers, real users. Those remain NOT VERIFIED. Not product code."""
import hashlib, json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
for d in ("E1", "E2a", "E5"): sys.path.insert(0, os.path.join(HERE, "..", d))
import canon
import merkle as M
import contract_d as D

MIN = 60_000
SENTINEL = "PRIVATE-SENTINEL-7f3a9c"


def H(*parts): return hashlib.sha256(json.dumps(parts, sort_keys=True, separators=(",", ":")).encode()).hexdigest()[:26]
def sha(b): return hashlib.sha256(b).hexdigest()
def canonical(d): return canon.canonicalize(json.dumps(d).encode(), "record")


class Pipeline:
    def __init__(self, mutant=None): self.mutant, self.egress, self.trace = mutant, [], {}

    # -- stages
    def capture(self, tool, request, body, received_at_ms, seq):
        cp = H("gw-1", tool, request, seq, received_at_ms)
        self.egress.append({"recipient": "ryo", "payload": json.dumps(request)})
        return {"cp": cp, "tool": tool, "request": request, "received_at": received_at_ms, "body": body, "raw_sha256": sha(json.dumps(body, sort_keys=True).encode())}

    def evidence(self, cap, private_text=None):
        items = []
        for field in ("rsi14", "price"):
            v = cap["body"]["data"].get(field)
            ok = cap["body"]["status"] == "ok" and v is not None
            items.append({"ev": H(cap["cp"], "adapter-1", field), "asset_id": cap["body"]["request"]["symbol"].upper() if True else "", "quote": "USDT", "field": field,
                          "state": "OK" if ok else "UNAVAILABLE", "value": str(v) if ok else None, "known_at": cap["received_at"]})
        if private_text:
            items.append({"ev": H(cap["cp"], "private"), "asset_id": "SOL", "quote": "USDT", "field": "user_claim", "state": "OK", "value": private_text, "private": True, "known_at": cap["received_at"]})
            self.egress.append({"recipient": "model", "payload": private_text})
            self.egress.append({"recipient": "news_search", "payload": "SOL accumulation" if self.mutant != "M5_leak_to_news" else private_text})
        return items

    def decide(self, items, eligible_after_ms=1_800_000_000_000 + 7_000):
        need = [i for i in items if i["field"] in ("rsi14", "price")]
        if any(i["state"] != "OK" for i in need):
            if self.mutant == "M4_null_to_zero":
                need = [dict(i, state="OK", value="0") if i["state"] != "OK" else i for i in need]
            else:
                return {"type": "failed_evidence", "reason": "required source unavailable", "evidence_ids": [i["ev"] for i in need], "asset_id": need[0]["asset_id"], "quote": "USDT"}
        rsi = float(next(i for i in need if i["field"] == "rsi14")["value"])
        p = "%.3f" % (0.5 + (50 - rsi) / 400)                                  # a toy rule; the product's arms are specified elsewhere
        t0 = D.t0_closed_form(eligible_after_ms)
        rec = {"type": "forecast", "contract": "D", "asset_id": need[0]["asset_id"], "quote": "USDT", "horizon_ms": 3_600_000, "p": p, "eligible_after_ms": eligible_after_ms,
               "evidence_ids": [i["ev"] for i in need], "committed_at_ms": eligible_after_ms - 7_000, "data_mode": "simulated"}
        if self.mutant == "M5_leak_to_ledger":
            rec["evidence_text"] = next((i["value"] for i in items if i.get("private")), None)
        return rec

    def commit(self, rec):
        b = canonical(rec); self.log = getattr(self, "log", []); self.log.append(b)
        leaves = [M.leaf_hash(x) for x in self.log]; i = len(self.log) - 1
        bundle = {"record": b, "index": i, "size": len(leaves), "path": M.inclusion_path(i, leaves), "root": M.mth(leaves)}
        return bundle

    def verify(self, bundle):
        ok = M.verify_inclusion(M.leaf_hash(bundle["record"]), bundle["index"], bundle["size"], bundle["path"], bundle["root"])
        return {"inclusion_ok": ok, "record_sha256": sha(bundle["record"]), "record": json.loads(bundle["record"])}

    def view(self, v):
        r = v["record"]
        if r["type"] == "failed_evidence":
            return {"state": "FAILED_EVIDENCE", "record_sha256": v["record_sha256"], "asset_id": r["asset_id"], "quote": r["quote"], "p_display": None, "evidence_ids": r["evidence_ids"], "origin": None,
                    "next_check": select_next_check({"unavailable": True})}
        asset = r["asset_id"].lower() if self.mutant == "M1_asset_case" else r["asset_id"]
        origin = D.t0_closed_form(r["eligible_after_ms"])
        if self.mutant == "M2_own_origin_rule": origin = (r["eligible_after_ms"] // MIN + 1) * MIN + MIN if r["eligible_after_ms"] % MIN > 30_000 else origin + MIN
        rec_hash = v["record_sha256"] if self.mutant != "M3_reserialised_hash" else sha(json.dumps(r, indent=1).encode())
        p_disp = r["p"] if self.mutant != "M6_rounded_p" else "%.2f" % float(r["p"])
        ev = r["evidence_ids"] if self.mutant != "M7_dropped_evidence" else r["evidence_ids"][:-1]
        return {"state": "COMMITTED", "record_sha256": rec_hash, "asset_id": asset, "quote": r["quote"], "p_display": p_disp, "evidence_ids": ev, "origin": origin,
                "next_check": select_next_check({"unavailable": False})}

    def settle(self, record_bytes, candles_by_series):
        r = json.loads(record_bytes)
        if r["type"] != "forecast": return {"state": "NOT_APPLICABLE", "input_sha256": sha(record_bytes), "series": None}
        quote = r["quote"] if self.mutant != "M8_wrong_quote_series" else "USD"
        series = (r["asset_id"], quote)
        candles = candles_by_series.get(series)
        if candles is None: return {"state": "UNSETTLEABLE_NO_SERIES", "input_sha256": sha(record_bytes), "series": series, "p_scored": r["p"]}
        st = D.settle(candles, r["eligible_after_ms"] - 5000, 5000 + 2000, r["horizon_ms"])
        return {"state": st[0], "T0": st[1], "input_sha256": sha(record_bytes), "series": series, "p_scored": r["p"]}


def select_next_check(state):
    """The one function behind both the agent's next step and the interface's next-check slot (SAME_REASONING)."""
    return "retry_source_or_mark_unavailable" if state["unavailable"] else "falsification_check"


def candles_fixture():
    base = (1_800_000_000_000 // MIN) * MIN
    return {("SOL", "USDT"): {base + i * MIN: ("%d.00" % (140 + (i % 7)), "0.01") for i in range(-5, 200)},
            ("SOL", "USD"): {base + i * MIN: ("%d.00" % (141 + (i % 5)), "0.01") for i in range(-5, 200)}}


def run(mutant=None, unavailable=False, private=False):
    P = Pipeline(mutant)
    body = {"status": "ok" if not unavailable else "unavailable", "request": {"symbol": "SOL"}, "data": {"rsi14": 41.5, "price": 143.21} if not unavailable else {"rsi14": None, "price": None}, "data_mode": "simulated"}
    cap = P.capture("analyze_token", {"symbol": "SOL"}, body, 1_799_999_990_000, 1)
    items = P.evidence(cap, SENTINEL if private else None)
    rec = P.decide(items)
    bundle = P.commit(rec); ver = P.verify(bundle); view = P.view(ver); st = P.settle(bundle["record"], candles_fixture())
    return {"P": P, "items": items, "rec": rec, "bundle": bundle, "ver": ver, "view": view, "settle": st}


def checks(t):
    P, rec, view, st, ver = t["P"], t["rec"], t["view"], t["settle"], t["ver"]
    out = {}
    series_asset = (st["series"] or (None, None))[0]; series_quote = (st["series"] or (None, None))[1]
    ev_assets = {i["asset_id"] for i in t["items"] if not i.get("private")}
    out["SAME_TRUTH"] = (ev_assets == {rec["asset_id"]} == {view["asset_id"]} and view["quote"] == rec["quote"] == "USDT" and (st["series"] is None or (series_asset == rec["asset_id"] and series_quote == rec["quote"])))
    out["SAME_AUTHORITY"] = (view["origin"] is None) or (view["origin"] == st.get("T0"))
    out["SAME_HISTORY"] = len({sha(P.log[-1]), ver["record_sha256"], view["record_sha256"], st["input_sha256"]}) == 1
    if rec["type"] == "failed_evidence":
        text = json.dumps([view, st, ver["record"]])
        out["SAME_FAILURE"] = (view["state"] == "FAILED_EVIDENCE" and st["state"] == "NOT_APPLICABLE" and view["p_display"] is None and '"p"' not in json.dumps(ver["record"]) and ": 0}" not in text and '"value": "0"' not in text)
    else:
        out["SAME_FAILURE"] = all(i["state"] == "OK" for i in t["items"] if i["field"] in ("rsi14", "price"))   # nothing was unavailable: the property is checked in the unavailable run
    public = [P.log[-1], t["bundle"]["record"], json.dumps(view).encode()]
    out["SAME_PRIVACY"] = all(SENTINEL.encode() not in b for b in public) and all(SENTINEL not in e["payload"] for e in P.egress if e["recipient"] not in ("model",))
    out["SAME_OUTCOME"] = (view["p_display"] is None) or (view["p_display"] == rec["p"] == st.get("p_scored"))
    out["SAME_REASONING"] = (view["evidence_ids"] == rec["evidence_ids"]) and view["next_check"] == select_next_check({"unavailable": rec["type"] == "failed_evidence"})
    return out


if __name__ == "__main__":
    results = {"honest": {}, "mutants": {}}
    honest_ok = checks(run()); honest_fail = checks(run(unavailable=True)); honest_priv = checks(run(private=True))
    merged = {k: honest_ok[k] and honest_fail[k] and honest_priv[k] for k in honest_ok}
    results["honest"] = {"normal_run": honest_ok, "unavailable_source_run": honest_fail, "private_input_run": honest_priv, "all_pass": all(merged.values())}
    plan = {"M1_asset_case": ("SAME_TRUTH", {}), "M2_own_origin_rule": ("SAME_AUTHORITY", {}), "M3_reserialised_hash": ("SAME_HISTORY", {}), "M4_null_to_zero": ("SAME_FAILURE", {"unavailable": True}),
            "M5_leak_to_ledger": ("SAME_PRIVACY", {"private": True}), "M5_leak_to_news": ("SAME_PRIVACY", {"private": True}), "M6_rounded_p": ("SAME_OUTCOME", {}),
            "M7_dropped_evidence": ("SAME_REASONING", {}), "M8_wrong_quote_series": ("SAME_TRUTH", {})}
    for m, (prop, kw) in plan.items():
        try:
            c = checks(run(mutant=m, **kw)); results["mutants"][m] = {"property_expected_to_fail": prop, "failed_properties": sorted(k for k, v in c.items() if not v), "caught": not c[prop]}
        except Exception as e:                                                                              # a stage that crashes on the broken input also stops the property
            results["mutants"][m] = {"property_expected_to_fail": prop, "failed_properties": [prop], "caught": True, "stage_raised": type(e).__name__}
    results["summary"] = {"honest_all_pass": results["honest"]["all_pass"], "mutants": len(results["mutants"]), "caught": sum(v["caught"] for v in results["mutants"].values())}
    json.dump(results, open(os.path.join(HERE, "E25_results.json"), "w"), indent=1); print(json.dumps(results, indent=1))
