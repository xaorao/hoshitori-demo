#!/usr/bin/env python3
"""Experiment E10: continuous monitoring of a paired improvement. Compares (a) a conventional p-value checked after every observation against
(b) the always-valid p-value of the normal mixture sequential probability ratio test (Johari, Pekelis and Walsh, arXiv 1512.04922; the normal-mixture
likelihood ratio is the standard form for N(theta, sigma^2) data with a N(theta0, gamma^2) mixing distribution and was NOT re-derived here: this
experiment tests it). Quantities: the share of null streams that ever cross alpha (Type I error under monitoring) and the share of streams with
a true effect that cross (power), with the mean stopping time.
Run: <numpy venv>/bin/python -I run_e10.py"""
import json, math
import numpy as np

rng = np.random.default_rng(1512)
ALPHA = 0.05
SIGMA = 1.0
M = 2000
STREAMS = 4000
GAMMA2 = 0.05 ** 2 * 100  # mixing variance: prior scale for effects of interest (an effect of about 0.2 sigma)


def norm_sf(z):
    return 0.5 * math.erfc(z / math.sqrt(2))


def run(theta):
    x = rng.normal(theta, SIGMA, (STREAMS, M))
    n = np.arange(1, M + 1)
    xbar = np.cumsum(x, axis=1) / n
    z = xbar * np.sqrt(n) / SIGMA
    naive_p = np.vectorize(lambda v: 2 * norm_sf(abs(v)))(z)
    lam = np.sqrt(SIGMA**2 / (SIGMA**2 + n * GAMMA2)) * np.exp((n**2 * GAMMA2 * xbar**2) / (2 * SIGMA**2 * (SIGMA**2 + n * GAMMA2)))
    av_p = np.minimum.accumulate(np.minimum(1.0, 1.0 / lam), axis=1)
    out = {}
    for name, p in (("naive_p_checked_every_observation", naive_p), ("mixture_sprt_always_valid_p", av_p)):
        crossed = (p <= ALPHA).any(axis=1)
        first = np.where(crossed, (p <= ALPHA).argmax(axis=1) + 1, M)
        out[name] = {"share_crossing_alpha": round(float(crossed.mean()), 4), "mean_stopping_time_if_crossed": round(float(first[crossed].mean()), 1) if crossed.any() else None}
    # a naive test checked only at the end, for reference
    out["fixed_horizon_at_M"] = {"share_crossing_alpha": round(float((naive_p[:, -1] <= ALPHA).mean()), 4)}
    return out


res = {"alpha": ALPHA, "streams": STREAMS, "max_observations": M, "mixing_variance": GAMMA2, "null": run(0.0)}
for theta in (0.05, 0.1, 0.2):
    res[f"effect_{theta}_sigma"] = run(theta)
json.dump(res, open("E10_results.json", "w"), indent=1)
print(json.dumps(res, indent=1))
