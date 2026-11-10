"""Reference implementation of two mechanisms of contract section 38 / 22, for experiment E13 (not product code):
(1) movement reasons by counterfactual neutralisation; (2) slate construction by greedy impact x (1 - redundancy) with reserved slots, a soft cap and exceptions."""
import math

def score(item, w):
    return sum(w[k] * item["f"][k] for k in w)

def rank_of(i, scores):
    """1-based rank of index i; ties broken by index (stable and deterministic)."""
    order = sorted(range(len(scores)), key=lambda j: (-scores[j], j))
    return order.index(i) + 1

def movement_reason(i, prev_items, items, w):
    """prev_items and items are aligned lists. Returns (cause, detail). Causes: SELF_FEATURE(k), OTHERS_MOVED, MIXED, NO_MOVEMENT, NEW."""
    if prev_items[i] is None:
        return "NEW", {}
    s_prev = [score(x, w) for x in prev_items]; s_now = [score(x, w) for x in items]
    r0, r1 = rank_of(i, s_prev), rank_of(i, s_now)
    delta = r0 - r1
    if delta == 0:
        return "NO_MOVEMENT", {"r0": r0, "r1": r1}
    sign = 1 if delta > 0 else -1
    detail = {"r0": r0, "r1": r1, "per_feature": {}}
    # self features: neutralise one feature of item i at its previous value, keep everyone else at their new scores
    qualifying = []
    for k in w:
        it = {**items[i], "f": {**items[i]["f"], k: prev_items[i]["f"][k]}}
        sc = list(s_now); sc[i] = score(it, w)
        r_n = rank_of(i, sc)
        removed = sign * (r_n - r1) * -1  # how much of the move is undone: positive if neutralising moves the item back toward r0
        # moving back toward r0 means rank number goes from r1 toward r0
        undone = (r_n - r1) if delta > 0 else (r1 - r_n)
        detail["per_feature"][k] = undone
        if undone >= abs(delta) / 2:
            qualifying.append((undone, k))
    if qualifying:
        names = [k for _, k in sorted(qualifying, key=lambda t: (-t[0], t[1]))]
        detail["qualifying"] = names
        return "SELF_FEATURE(" + ",".join(names) + ")", detail
    # others: item i keeps its previous score, everyone else moves to their new scores
    sc = list(s_prev)
    for j in range(len(items)):
        if j != i and prev_items[j] is not None: sc[j] = s_now[j]
    r_o = rank_of(i, sc)
    others_effect = abs(r0 - r_o)
    # others moved if item i at its OLD score would still have moved at least half of the way
    if (r_o - r0) * (r1 - r0) > 0 and others_effect >= abs(delta) / 2:
        detail["others_effect"] = others_effect
        return "OTHERS_MOVED", detail
    return "MIXED", detail

def alpha_ndcg(ranked, rel, alpha=0.5, k=5):
    """rel[doc] = set of subtopics covered. Greedy ideal."""
    def dcg(order):
        seen = {}; total = 0.0
        for pos, d in enumerate(order[:k], 1):
            g = sum((1 - alpha) ** seen.get(t, 0) for t in rel[d])
            total += g / math.log2(1 + pos)
            for t in rel[d]: seen[t] = seen.get(t, 0) + 1
        return total
    # greedy ideal
    pool = list(rel); ideal = []; seen = {}
    while pool and len(ideal) < k:
        best = max(pool, key=lambda d: (sum((1 - alpha) ** seen.get(t, 0) for t in rel[d]), -ord(str(d)[0])))
        ideal.append(best); pool.remove(best)
        for t in rel[best]: seen[t] = seen.get(t, 0) + 1
    return dcg(ranked) / dcg(ideal) if dcg(ideal) else 0.0

def build_slate(items, size=5, cap=2, high_threshold=None, max_exempt=4, reserved=()):
    """items: dicts with id, impact, families (set of origin families), event (underlying event id). redundancy = share of the item's families
    already represented, and 1 for an item that reports the same underlying event. Cap: at most `cap` of the slate may overlap in information
    unless the item is exempt (different event, >= 2 families, impact >= high_threshold)."""
    chosen = []; fams = set(); events = set(); overlap_count = 0; exempt_count = 0; notes = {}
    for rid in reserved:
        it = next(x for x in items if x["id"] == rid); chosen.append(it); fams |= it["families"]; events.add(it["event"])
    pool = [x for x in items if x not in chosen]
    while len(chosen) < size and pool:
        def red(x):
            if x["event"] in events: return 1.0
            return len(x["families"] & fams) / max(1, len(x["families"]))
        cand = sorted(pool, key=lambda x: (-x["impact"] * (1 - red(x)), x["id"]))
        pick = None
        for x in cand:
            r = red(x); overlaps = r > 0
            exempt = (x["event"] not in events and len(x["families"]) >= 2 and high_threshold is not None and x["impact"] >= high_threshold)
            if overlaps and not exempt and overlap_count >= cap:
                continue
            if overlaps and exempt and exempt_count >= max_exempt:
                continue
            pick = (x, overlaps, exempt); break
        if pick is None: break
        x, overlaps, exempt = pick
        chosen.append(x); pool.remove(x); fams |= x["families"]; events.add(x["event"])
        if overlaps: overlap_count += 1
        if overlaps and exempt: exempt_count += 1; notes[x["id"]] = "included although related: a separate, independent development"
    return chosen, notes
