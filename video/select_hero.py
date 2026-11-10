#!/usr/bin/env python3
"""Pick the hero receipt by the rule in VIDEO.md section 0.

Rule: state Settled, level C3, contract D (up or down), chance farthest from 50 %,
earliest settled among equals, receipt file present. A candidate whose receipt lacks
the sections the film crops (invalidations, settlement, a model run) is skipped and the
next one by the same order is taken; each skip is written to hero.json.
Usage: select_hero.py [record_dir] [out_json]
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REC = Path(sys.argv[1]) if len(sys.argv) > 1 else HERE / "public" / "record"
OUT = Path(sys.argv[2]) if len(sys.argv) > 2 else HERE / "public" / "real" / "hero.json"


def pct(chance):
    m = re.fullmatch(r"(\d+(?:\.\d+)?)% up", chance or "")
    return float(m.group(1)) if m else None


def main():
    rows = json.load(open(REC / "rows.json"))
    cands = []
    for r in rows:
        if r["state"] == "Settled" and r["level"] == "C3" and r["contract"] == "D":
            p = pct(r["chance"])
            if p is not None:
                cands.append((-abs(p - 50), r["settled_at"], r["forecast_id"], r))
    cands.sort(key=lambda c: c[:3])
    skipped = []
    for dist, _, fid, r in cands:
        f = REC / "receipts" / f"{fid}.json"
        if not f.exists():
            skipped.append({"forecast_id": fid, "why": "receipt file absent"})
            continue
        rc = json.load(open(f))
        missing = []
        if not rc.get("invalidations"):
            missing.append("What would prove it wrong (invalidations)")
        if not rc.get("settlement"):
            missing.append("settlement")
        if not rc.get("evidence"):
            missing.append("What it read (evidence)")
        if not rc.get("run"):
            missing.append("What the model did (run)")
        if missing:
            skipped.append({"forecast_id": fid, "arm": r["arm_name"], "chance": r["chance"],
                            "why": "receipt lacks: " + "; ".join(missing)})
            continue
        why = (f"{r['symbol']}, {r['hours']} hours, {r['arm_name']}, {r['chance']}; settled {r['result']} "
               f"at {r['settled_at']}; first of {len(cands)} settled C3 up-or-down calls by distance of the "
               f"chance from 50 % ({abs(-dist):.0f} points), then earliest settled; receipt has "
               f"invalidations, settlement, evidence and a model run")
        out = {"forecast_id": fid, "why": why, "symbol": r["symbol"], "arm_name": r["arm_name"],
               "chance": r["chance"], "result": r["result"], "settled_at": r["settled_at"],
               "fallbacks_skipped": skipped, "candidates_considered": len(cands)}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        json.dump(out, open(OUT, "w"), indent=2)
        print(json.dumps(out, indent=2)[:2500])
        return 0
    print("no hero candidate has a complete receipt", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
