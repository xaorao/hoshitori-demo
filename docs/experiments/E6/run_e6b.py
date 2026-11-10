#!/usr/bin/env python3
"""Experiment E6b: candidate interval procedures that respect autocorrelation, on the hard cases of E6 (day effects present).
Adds a Newey-West (Bartlett kernel) t interval on equal-weight daily means and longer bootstrap blocks.
Run: <numpy venv>/bin/python -I run_e6b.py [replicates]"""
import json, sys
import numpy as np
sys.argv = sys.argv[:1] + sys.argv[1:]
REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 600
B = 400
rng = np.random.default_rng(5816)
TQ = {50: 1.677, 100: 1.660, 200: 1.653}


def simulate(D, phi, sig_day, sig_call, delta):
    e = np.empty(D); e[0] = rng.normal(0, sig_day)
    for t in range(1, D):
        e[t] = phi * e[t - 1] + rng.normal(0, sig_day * np.sqrt(1 - phi**2))
    K = np.maximum(1, rng.poisson(4.5, D))
    x = np.array([np.mean(delta + e[t] + rng.normal(0, sig_call, K[t])) for t in range(D)])
    return x, K


def hac_t(x, K, alpha=0.10):
    D = len(x); m = x.mean(); u = x - m
    L = int(np.floor(4 * (D / 100) ** (2 / 9))) + 1
    g0 = (u @ u) / D
    var = g0 + 2 * sum((1 - l / (L + 1)) * (u[l:] @ u[:-l]) / D for l in range(1, L + 1))
    se = np.sqrt(max(var, 1e-12) / D)
    t = TQ[D]
    return np.array([m - t * se, m + t * se])


def day_t(x, K, alpha=0.10):
    D = len(x); m = x.mean(); se = x.std(ddof=1) / np.sqrt(D); t = TQ[D]
    return np.array([m - t * se, m + t * se])


def block(x, K, b, alpha=0.10):
    D = len(x); nb = int(np.ceil(D / b))
    starts = rng.integers(0, D, (B, nb))
    idx = ((starts[:, :, None] + np.arange(b)[None, None, :]) % D).reshape(B, -1)[:, :D]
    return np.quantile(x[idx].mean(axis=1), [alpha / 2, 1 - alpha / 2])


METHODS = {"day-t": day_t, "hac-t": hac_t, "block5": lambda x, K: block(x, K, 5), "block10": lambda x, K: block(x, K, 10), "block20": lambda x, K: block(x, K, 20)}
out = []
for D in (50, 100, 200):
    for phi in (0.0, 0.5, 0.8):
        cover = {m: 0 for m in METHODS}; width = {m: 0.0 for m in METHODS}
        for _ in range(REPS):
            x, K = simulate(D, phi, 0.5, 1.0, 0.0)
            for m, f in METHODS.items():
                lo, hi = f(x, K); cover[m] += (lo <= 0.0 <= hi); width[m] += hi - lo
        out.append({"days": D, "phi": phi, "sigma_day": 0.5, "reps": REPS, "coverage_nominal_0.90": {m: round(cover[m] / REPS, 3) for m in METHODS}, "mean_width": {m: round(width[m] / REPS, 3) for m in METHODS}})
        print(json.dumps(out[-1]), flush=True)
json.dump(out, open("E6b_results.json", "w"), indent=1)
