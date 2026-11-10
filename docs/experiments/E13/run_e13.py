#!/usr/bin/env python3
"""E13: planted tests of contract section 38 (movement reasons, slate rules) and a simulation of how often the half-of-the-move rule names a cause."""
import json, random, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from ranking import *

W = {"severity": 5.0, "contradiction": 3.0, "recency": 1.0, "corroboration": 2.0}
def it(**f): return {"f": {k: f.get(k, 0.0) for k in W}}
res = {"planted": {}}

# 1. largest weight constant, small recency change flips order
prev = [it(severity=1.0, recency=0.50), it(severity=1.0, recency=0.55), it(severity=0.2)]
now  = [it(severity=1.0, recency=0.60), it(severity=1.0, recency=0.55), it(severity=0.2)]
res["planted"]["recency_flip"] = movement_reason(0, prev, now, W)[0]
# 2. leader fell
prev = [it(severity=0.9), it(severity=1.0), it(severity=0.1)]
now  = [it(severity=0.9), it(severity=0.5), it(severity=0.1)]
res["planted"]["leader_fell"] = movement_reason(0, prev, now, W)[0]
# 3. two features each contribute less than half
prev = [it(severity=0.50, contradiction=0.50), it(severity=0.60, contradiction=0.60), it(severity=0.55), it(severity=0.2), it(severity=0.1)]
now  = [it(severity=0.60, contradiction=0.60), it(severity=0.60, contradiction=0.60), it(severity=0.55), it(severity=0.2), it(severity=0.1)]
res["planted"]["two_features"] = movement_reason(0, prev, now, W)
# 4. new item
res["planted"]["new_item"] = movement_reason(0, [None, it(severity=1)], [it(severity=1), it(severity=1)], W)[0]
# 5. no movement
res["planted"]["no_movement"] = movement_reason(0, [it(severity=1), it(severity=0.5)], [it(severity=1.1), it(severity=0.5)], W)[0]

# simulation: random walk of features, classify movements of at least two places
rng = random.Random(13)
counts = {"SELF_FEATURE": 0, "OTHERS_MOVED": 0, "MIXED": 0}; named_largest_weight = 0; named_total = 0; multi = 0; moves = 0
for trial in range(4000):
    n = 20
    prev = [{"f": {k: rng.random() for k in W}} for _ in range(n)]
    now = [{"f": {k: min(1, max(0, v + rng.gauss(0, 0.12))) for k, v in p["f"].items()}} for p in prev]
    i = rng.randrange(n)
    s0 = [score(x, W) for x in prev]; s1 = [score(x, W) for x in now]
    if abs(rank_of(i, s0) - rank_of(i, s1)) < 2: continue
    moves += 1
    cause, d = movement_reason(i, prev, now, W)
    if cause.startswith("SELF_FEATURE"):
        counts["SELF_FEATURE"] += 1; named_total += 1
        if d["qualifying"][0] == "severity": named_largest_weight += 1
        if len(d["qualifying"]) > 1: multi += 1
    else: counts[cause] += 1
res["simulation"] = {"movements_of_two_or_more_places": moves, "shares": {k: round(v / moves, 3) for k, v in counts.items()},
                     "share_of_self_causes_whose_top_feature_is_the_largest_weight": round(named_largest_weight / named_total, 3), "share_of_self_causes_with_more_than_one_qualifying_feature": round(multi / named_total, 3),
                     "note": "four features with weights 5, 3, 1, 2; features uniform on [0,1], random-walk step sd 0.12; 20 items"}

# slate tests
def mk(i, impact, fams, event): return {"id": i, "impact": impact, "families": set(fams), "event": event}
dups = [mk(f"dup{k}", 0.9 - 0.01 * k, {"X"}, "evt-A") for k in range(5)] + [mk("b", 0.5, {"Y"}, "evt-B"), mk("c", 0.45, {"Z"}, "evt-C"), mk("d", 0.4, {"W"}, "evt-D")]
sl, _ = build_slate(dups, size=5, cap=2, high_threshold=0.8)
res["planted"]["five_duplicates_slate"] = [x["id"] for x in sl]
res["planted"]["duplicates_in_top5"] = sum(1 for x in sl if x["event"] == "evt-A")
# independent developments, each above threshold and on two families, about one exchange
indep = [mk(f"e{k}", 0.95 - 0.01 * k, {f"P{k}", f"Q{k}"}, f"evt-{k}") for k in range(3)] + [mk("f", 0.5, {"R"}, "evt-F"), mk("g", 0.4, {"S"}, "evt-G")]
# make them overlap through one shared family to trigger the overlap path
for k in range(3): indep[k]["families"].add("EXCH")
sl, notes = build_slate(indep, size=5, cap=2, high_threshold=0.9)
res["planted"]["independent_overlapping_slate"] = {"ids": [x["id"] for x in sl], "exception_lines": notes}
# reserved slot is filled first
sl, _ = build_slate(dups + [mk("outage", 0.2, {"V"}, "evt-O")], size=5, cap=2, reserved=("outage",))
res["planted"]["reserved_first"] = [x["id"] for x in sl][:2]
# alpha-nDCG: independent-score sort vs slate on a planted multi-event set
rel = {x["id"]: {x["event"]} for x in dups}
by_score = [x["id"] for x in sorted(dups, key=lambda x: -x["impact"])][:5]
slate_ids = [x["id"] for x in build_slate(dups, size=5, cap=2, high_threshold=0.8)[0]]
res["planted"]["alpha_ndcg_independent_sort"] = round(alpha_ndcg(by_score, rel), 3)
res["planted"]["alpha_ndcg_slate"] = round(alpha_ndcg(slate_ids, rel), 3)
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E13_results.json"), "w"), indent=1)
print(json.dumps(res, indent=1))
