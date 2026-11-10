#!/usr/bin/env python3
"""Experiment E6: does the planned interval procedure (block bootstrap by market day) cover the truth when calls are
clustered within days and days are autocorrelated? Contract section 10 requires coverage to be tested on synthetic
autocorrelated data with a known effect before a scored period. Estimators compared for the mean paired score difference:
  call-iid     bootstrap over calls (ignores clustering)            - the naive procedure
  day-iid      bootstrap over market days                           - clusters respected, days independent
  day-block    circular moving-block bootstrap over days, block 5   - contract section 10
  day-t        t interval on day means                              - cluster-robust reference
Data: per day d, a day effect e_d (AR(1) with coefficient phi, unit marginal variance scaled by sigma_day), K_d calls
(Poisson mean 4.5, at least 1), each call's difference = delta_true + e_d + shock_call.
Run: <numpy venv>/bin/python -I run_e6.py [replicates]"""
import json, sys
import numpy as np

REPS = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
B = 400
rng = np.random.default_rng(20261026)


def simulate(D, phi, sig_day, sig_call, delta):
    e = np.empty(D)
    e[0] = rng.normal(0, sig_day)
    for t in range(1, D):
        e[t] = phi * e[t - 1] + rng.normal(0, sig_day * np.sqrt(1 - phi**2))
    K = np.maximum(1, rng.poisson(4.5, D))
    calls = [delta + e[t] + rng.normal(0, sig_call, K[t]) for t in range(D)]
    S = np.array([c.sum() for c in calls])
    return S, K, np.concatenate(calls)


def ci_call_iid(S, K, calls, alpha=0.10):
    n = len(calls)
    idx = rng.integers(0, n, (B, n))
    m = calls[idx].mean(axis=1)
    return np.quantile(m, [alpha / 2, 1 - alpha / 2])


def ci_day_iid(S, K, calls, alpha=0.10):
    D = len(S)
    idx = rng.integers(0, D, (B, D))
    m = S[idx].sum(axis=1) / K[idx].sum(axis=1)
    return np.quantile(m, [alpha / 2, 1 - alpha / 2])


def ci_day_block(S, K, calls, block=5, alpha=0.10):
    D = len(S)
    nb = int(np.ceil(D / block))
    starts = rng.integers(0, D, (B, nb))
    idx = ((starts[:, :, None] + np.arange(block)[None, None, :]) % D).reshape(B, -1)[:, :D]
    m = S[idx].sum(axis=1) / K[idx].sum(axis=1)
    return np.quantile(m, [alpha / 2, 1 - alpha / 2])


def ci_day_t(S, K, calls, alpha=0.10):
    D = len(S)
    dm = S / K
    mean = S.sum() / K.sum()
    se = dm.std(ddof=1) / np.sqrt(D)
    from math import sqrt
    t = {10: 1.833, 20: 1.729, 50: 1.676, 100: 1.660}.get(D, 1.645)
    return np.array([mean - t * se, mean + t * se])


METHODS = {"call-iid": ci_call_iid, "day-iid": ci_day_iid, "day-block(5)": ci_day_block, "day-t": ci_day_t}
out = []
for D in (20, 50, 100):
    for phi in (0.0, 0.5):
        for sig_day, sig_call in ((0.0, 1.0), (0.5, 1.0)):
            for delta in (0.0, 0.1):
                cover = {m: 0 for m in METHODS}; width = {m: 0.0 for m in METHODS}; excl0 = {m: 0 for m in METHODS}
                for _ in range(REPS):
                    S, K, calls = simulate(D, phi, sig_day, sig_call, delta)
                    for m, f in METHODS.items():
                        lo, hi = f(S, K, calls)
                        cover[m] += (lo <= delta <= hi); width[m] += hi - lo; excl0[m] += (lo > 0 or hi < 0)
                out.append({"days": D, "phi": phi, "sigma_day": sig_day, "delta_true": delta, "reps": REPS,
                            "coverage_nominal_0.90": {m: round(cover[m] / REPS, 3) for m in METHODS},
                            "mean_width": {m: round(width[m] / REPS, 3) for m in METHODS},
                            "excludes_zero_rate": {m: round(excl0[m] / REPS, 3) for m in METHODS}})
                print(json.dumps(out[-1]), flush=True)
json.dump(out, open("E6_results.json", "w"), indent=1)
