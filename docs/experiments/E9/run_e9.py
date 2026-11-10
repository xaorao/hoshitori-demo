#!/usr/bin/env python3
"""Experiment E9: the minimal-sufficient-evidence search (contract section 50) on planted deterministic 'agents', so the search can be checked
against the truth. Groups g0..g7 each take a binary value in a recorded bundle (positive with probability 0.5 in the recorded pool). The focal
bundle has all eight positive. A set S is held at the focal values; the other groups are replaced JOINTLY by a recorded bundle drawn at random.
Precision(S) = share of replaced runs whose decision equals the focal decision. Planted decision functions:
  REDUNDANT  buy if g0 or g1            (two disjoint sufficient sets of size one)
  COMBO      buy if g2 and g3           (sufficient only together)
  DECOY      buy if g4                  (g5, g6, g7 are decoys; one necessary group)
  MAJORITY5  buy if at least five of eight are positive (borderline: a four-group set reaches about 0.94)
  MAJORITY   buy if at least six of eight are positive (no sufficient set of four or fewer)
Search: all subsets up to size 4, precision estimated with m samples; the smallest set whose Wilson lower bound reaches tau = 0.9 is chosen
('search'); it is then re-estimated on fresh draws ('held-out'). The optimism of the search estimate is the winner's curse the held-out step removes.
Run: <numpy venv>/bin/python -I run_e9.py"""
import itertools, json, math
import numpy as np

rng = np.random.default_rng(2018)
G = 8
TAU = 0.9
Z = 1.645  # one-sided 95 percent lower bound


def wilson_lower(k, n):
    p = k / n
    den = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / den
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return c - h


FUNCS = {
    "REDUNDANT": lambda v: bool(v[0] or v[1]),
    "COMBO": lambda v: bool(v[2] and v[3]),
    "DECOY": lambda v: bool(v[4]),
    "MAJORITY": lambda v: v.sum() >= 6,
    "MAJORITY5": lambda v: v.sum() >= 5,   # a set of four reaches about 0.94: borderline, which exposes the winner's curse at small m
}


def precision(fn, S, m):
    focal = np.ones(G, dtype=bool)
    target = fn(focal)
    pool = rng.random((m, G)) < 0.5
    pool[:, list(S)] = True
    return sum(fn(row) == target for row in pool) / m, target


def search(fn, m, max_size=4):
    best = None
    tried = 0
    for size in range(0, max_size + 1):
        cands = []
        for S in itertools.combinations(range(G), size):
            p, _ = precision(fn, S, m)
            tried += 1
            if wilson_lower(round(p * m), m) >= TAU:
                cands.append((S, p))
        if cands:
            return cands, tried
    return [], tried


out = {}
for name, fn in FUNCS.items():
    res = {}
    for m in (30, 100, 400):
        found, tried = search(fn, m)
        if not found:
            res[f"m={m}"] = {"result": "NO_SMALL_SET", "subsets_tried": tried}
            continue
        # held-out confirmation of the first (smallest, then highest-estimate) set
        found.sort(key=lambda t: (-t[1]))
        S, p_search = found[0]
        p_held, _ = precision(fn, S, 400)
        disjoint = []
        for S2, _ in found:
            if all(set(S2).isdisjoint(set(T)) for T in disjoint):
                disjoint.append(S2)
        # necessity: dropping any member must lower the precision below tau
        necessary = all((precision(fn, tuple(x for x in S if x != y), 400)[0] < TAU) for y in S) if len(S) > 0 else True
        res[f"m={m}"] = {"set": list(S), "search_precision": round(p_search, 3), "held_out_precision": round(p_held, 3), "optimism": round(p_search - p_held, 3),
                         "disjoint_sufficient_sets_found_at_this_size": [list(x) for x in disjoint], "members_all_necessary": bool(necessary),
                         "confirmed": bool(wilson_lower(round(p_held * 400), 400) >= TAU), "subsets_tried": tried}
    out[name] = res
    print(name, json.dumps(res), flush=True)
json.dump(out, open("E9_results.json", "w"), indent=1)
