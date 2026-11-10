#!/usr/bin/env python3
"""E18: cross-check of the statistical building blocks used in E6, E8 and E10 against independent library implementations (statsmodels, scipy).
The building blocks were written from their published definitions; this compares them, on random inputs, with code written by others.
Checked: Newey-West (Bartlett) standard error of a mean (E6b hac_t); Wilson interval (E8 wilson); exact McNemar two-sided p (E8 mcnemar_exact_p).
Not checkable against a library: Newcombe method 10 for paired proportions and the normal-mixture sequential likelihood ratio (E10): listed as NOT VERIFIED."""
import json, math, os, sys
import numpy as np
import statsmodels.api as sm
from statsmodels.stats.proportion import proportion_confint
from scipy.stats import binomtest

rng = np.random.default_rng(18)
res = {}
# 1. Newey-West
worst = 0.0
for trial in range(300):
    D = int(rng.integers(20, 400)); phi = rng.uniform(-0.3, 0.9)
    e = np.empty(D); e[0] = rng.normal()
    for t in range(1, D): e[t] = phi * e[t - 1] + rng.normal()
    x = 0.1 + e
    L = int(np.floor(4 * (D / 100) ** (2 / 9))) + 1
    m = x.mean(); u = x - m; g0 = (u @ u) / D
    var = g0 + 2 * sum((1 - l / (L + 1)) * (u[l:] @ u[:-l]) / D for l in range(1, L + 1)); mine = math.sqrt(var / D)
    fit = sm.OLS(x, np.ones(D)).fit(cov_type="HAC", cov_kwds={"maxlags": L, "use_correction": False})
    theirs = float(fit.bse[0]); worst = max(worst, abs(mine - theirs) / theirs)
res["newey_west_se"] = {"trials": 300, "max_relative_difference": worst}
# 2. Wilson
Z = 1.959964
def wilson(k, n):
    p = k / n; den = 1 + Z * Z / n; c = (p + Z * Z / (2 * n)) / den; h = Z * math.sqrt(p * (1 - p) / n + Z * Z / (4 * n * n)) / den; return c - h, c + h
worst = 0.0
for n in range(5, 120):
    for k in range(0, n + 1):
        lo, hi = wilson(k, n); l2, h2 = proportion_confint(k, n, alpha=0.05, method="wilson"); worst = max(worst, abs(lo - l2), abs(hi - h2))
res["wilson"] = {"cases": sum(n + 1 for n in range(5, 120)), "max_absolute_difference": worst}
# 3. exact McNemar
def mcnemar_exact_p(b, c):
    m = b + c
    if m == 0: return 1.0
    k = min(b, c); tail = sum(math.comb(m, i) for i in range(0, k + 1)) / 2 ** m; return min(1.0, 2 * tail)
worst = 0.0
for b in range(0, 40):
    for c in range(0, 40):
        if b + c == 0: continue
        worst = max(worst, abs(mcnemar_exact_p(b, c) - binomtest(min(b, c), b + c, 0.5, alternative="two-sided").pvalue))
res["mcnemar_exact"] = {"cases": 40 * 40 - 1, "max_absolute_difference": worst}
res["not_cross_checkable"] = ["Newcombe method 10 paired difference interval (E8)", "normal-mixture sequential likelihood ratio (E10)", "alpha-nDCG as implemented in E13"]
json.dump(res, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "E18_results.json"), "w"), indent=1); print(json.dumps(res, indent=1))
