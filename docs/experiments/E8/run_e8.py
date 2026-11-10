#!/usr/bin/env python3
"""Experiment E8: statistics of paired replays in the Evidence Challenge Engine (contract section 13). For n paired runs, original versus
counterfactual, the outcome is whether the action class stayed the same (1) or not (0)... here modelled as a binary event per run
(action = A or not). Questions: (1) coverage of paired-difference intervals at small n (Wald versus Newcombe method 10); (2) the type I error and
power of the exact McNemar test; so that the numbers of runs (5 preliminary, 20 full) are set by measurement, not by habit.
Run: <numpy venv>/bin/python -I run_e8.py [replicates]"""
import json, math, sys
import numpy as np

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 4000
rng = np.random.default_rng(1998)
Z = 1.959964


def phi_inv(p):  # inverse normal cdf (Acklam-free: use numpy via erfinv approximation)
    from math import sqrt
    # Beasley-Springer-Moro would do; here a simple Newton iteration on erf
    x = 0.0
    for _ in range(50):
        cdf = 0.5 * (1 + math.erf(x / sqrt(2)))
        pdf = math.exp(-0.5 * x * x) / sqrt(2 * math.pi)
        x -= (cdf - p) / pdf
    return x


def wilson(k, n):
    p = k / n
    den = 1 + Z * Z / n
    c = (p + Z * Z / (2 * n)) / den
    h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den
    return c - h, c + h


def newcombe10(a, b, c, d):
    """Difference p_y - p_x for paired binary data with counts a (1,1) b (1,0) c (0,1) d (0,0), x first. Newcombe (1998) method 10."""
    n = a + b + c + d
    px, py = (a + b) / n, (a + c) / n
    lx, ux = wilson(a + b, n)
    ly, uy = wilson(a + c, n)
    denom = math.sqrt((a + b) * (c + d) * (a + c) * (b + d))
    phi = (a * d - b * c) / denom if denom > 0 else 0.0
    delta = py - px
    lo = delta - math.sqrt((py - ly) ** 2 + (ux - px) ** 2 - 2 * phi * (py - ly) * (ux - px))
    hi = delta + math.sqrt((uy - py) ** 2 + (px - lx) ** 2 - 2 * phi * (uy - py) * (px - lx))
    return lo, hi


def wald(a, b, c, d):
    n = a + b + c + d
    delta = (c - b) / n
    var = ((b + c) - (c - b) ** 2 / n) / (n * n)
    se = math.sqrt(max(var, 0.0))
    return delta - Z * se, delta + Z * se


def mcnemar_exact_p(b, c):
    m = b + c
    if m == 0:
        return 1.0
    k = min(b, c)
    from math import comb
    tail = sum(comb(m, i) for i in range(0, k + 1)) / 2**m
    return min(1.0, 2 * tail)


def sample(n, p1, p2, r):
    zx = rng.normal(size=n); zy = r * zx + math.sqrt(1 - r * r) * rng.normal(size=n)
    x = zx < phi_inv(p1); y = zy < phi_inv(p2)
    a = int(np.sum(x & y)); b = int(np.sum(x & ~y)); c = int(np.sum(~x & y)); d = int(np.sum(~x & ~y))
    return a, b, c, d


out = []
for n in (5, 10, 20, 40):
    for r in (0.0, 0.5, 0.8):
        for p1, p2 in ((0.8, 0.8), (0.8, 0.6), (0.8, 0.4), (0.8, 0.2)):
            truth = p2 - p1
            cov = {"wald": 0, "newcombe10": 0}; rej = 0
            for _ in range(REPS):
                a, b, c, d = sample(n, p1, p2, r)
                lo, hi = wald(a, b, c, d); cov["wald"] += (lo <= truth <= hi)
                lo, hi = newcombe10(a, b, c, d); cov["newcombe10"] += (lo <= truth <= hi)
                rej += (mcnemar_exact_p(b, c) <= 0.05)
            out.append({"n": n, "copula_corr": r, "p_original": p1, "p_counterfactual": p2, "true_difference": round(truth, 2),
                        "coverage_nominal_0.95": {k: round(v / REPS, 3) for k, v in cov.items()}, "mcnemar_exact_reject_rate": round(rej / REPS, 3)})
            print(json.dumps(out[-1]), flush=True)
json.dump(out, open("E8_results.json", "w"), indent=1)

# Half-width of the Newcombe interval when nothing changed (d = 0), to see what "stable within +/-0.15" requires.
hw = []
for n in (20, 40, 60, 80, 120, 200):
    for r in (0.5, 0.8):
        for p in (0.5, 0.8, 0.95):
            w = []
            for _ in range(600):
                a, b, c, d = sample(n, p, p, r)
                lo, hi = newcombe10(a, b, c, d); w.append((hi - lo) / 2)
            hw.append({"n": n, "copula_corr": r, "p": p, "median_half_width": round(float(np.median(w)), 3), "share_within_0.15": round(float(np.mean(np.array(w) <= 0.15)), 3)})
json.dump(hw, open("E8_halfwidth.json", "w"), indent=1)
for x in hw:
    print(json.dumps(x))
