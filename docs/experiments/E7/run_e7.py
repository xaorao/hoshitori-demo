#!/usr/bin/env python3
"""Experiment E7: do the deterministic falsification tests (contract section 48) classify planted scenarios correctly, and how often do they
raise a false alarm? Synthetic 1-minute returns with fat tails (Student t, 4 df) and volatility regimes. Scenarios:
  A true announcement effect (token jumps at the announcement, market flat)
  B move already under way 40 minutes before the announcement (same total drift)
  C market-wide move at the announcement (token loads on a market proxy with beta 1.2)
  D no effect at all
Tests (each returns FAVOURS_ORIGINAL, FAVOURS_ALTERNATIVE or INCONCLUSIVE):
  T_ONSET        first minute where the cumulative return exceeds k local standard deviations versus the announcement time
  T_MARKET       beta-adjusted residual of the event window versus its local noise
  T_PLACEBO_TIME rank of the event statistic among the same statistic at random placebo times
Run: <numpy venv>/bin/python -I run_e7.py [trials]"""
import json, sys
import numpy as np

TRIALS = int(sys.argv[1]) if len(sys.argv) > 1 else 600
rng = np.random.default_rng(48)
HIST = 6 * 24 * 60          # 6 days of history before the window
WIN = 120                    # minutes before and after the announcement considered by the tests
H = 60                       # forward horizon of the event statistic (minutes)
TA = HIST + WIN              # announcement minute index
N = HIST + 2 * WIN + H + 10


def t4(size):
    return rng.standard_t(4, size) / np.sqrt(2.0)  # unit variance


def make(scn):
    vol = 0.0008 * np.exp(rng.normal(0, 0.3, N // 360 + 2)).repeat(360)[:N]   # regime vol per 6 hours
    mkt = vol * 0.8 * t4(N)
    tok = 1.2 * mkt + vol * 0.7 * t4(N)                                      # token loads on the market, plus its own noise
    jump = 4.0 * 0.0008 * np.sqrt(60)                                         # about four hourly standard deviations in total
    if scn == "A":
        tok[TA:TA + 10] += jump / 10                                          # token-specific jump right after the announcement
    elif scn == "B":
        tok[TA - 40:TA - 40 + 40] += jump / 40                                # the same drift starts 40 minutes earlier
    elif scn == "C":
        mkt[TA:TA + 10] += jump / 10 / 1.2 ; tok[TA:TA + 10] += jump / 10    # market-wide move; token follows with beta 1.2
    return tok, mkt


def local_sigma(r, end):
    x = r[end - 360:end]
    return 1.4826 * np.median(np.abs(x - np.median(x)))


def t_onset(tok, k=3.0, tol=5):
    s = local_sigma(tok, TA - WIN)
    seg = tok[TA - WIN:TA + WIN]
    cum = np.cumsum(seg)
    n = np.arange(1, len(seg) + 1)
    hit = np.where(np.abs(cum) >= k * s * np.sqrt(n))[0]
    if len(hit) == 0:
        return "INCONCLUSIVE"
    onset = hit[0] - WIN                                  # minutes relative to the announcement
    if onset < -tol:
        return "FAVOURS_ALTERNATIVE"                      # the move began before the announcement
    return "FAVOURS_ORIGINAL"


def t_onset2(tok, z_move_min=3.0, z_pre_min=2.5, z_post_min=2.5):
    """Pre-announcement drift test, one look per segment (no running maximum, so no multiple-look inflation). The window is the 60 minutes
    before to H minutes after the announcement. If it contains no significant move there is nothing to date. If the minutes before the
    announcement already carry a same-direction drift the move began earlier; if the move is carried by the minutes after, the announcement is
    where it began."""
    s = local_sigma(tok, TA - WIN)
    z_total = tok[TA - 60:TA + H].sum() / (s * np.sqrt(60 + H))
    if abs(z_total) < z_move_min:
        return "INCONCLUSIVE"
    z_pre = tok[TA - 60:TA - 5].sum() / (s * np.sqrt(55))
    z_post = tok[TA:TA + H].sum() / (s * np.sqrt(H))
    if abs(z_pre) >= z_pre_min and np.sign(z_pre) == np.sign(z_total):
        return "FAVOURS_ALTERNATIVE"
    if abs(z_post) >= z_post_min and np.sign(z_post) == np.sign(z_total):
        return "FAVOURS_ORIGINAL"
    return "INCONCLUSIVE"


def t_market(tok, mkt, k=3.0):
    hist = slice(TA - HIST + WIN - 1000, TA - WIN)       # estimation window before the event window
    x, y = mkt[hist], tok[hist]
    beta = np.cov(x, y)[0, 1] / np.var(x)
    res = tok - beta * mkt
    s = local_sigma(res, TA - WIN)
    stat = res[TA:TA + H].sum()
    if abs(stat) < k * s * np.sqrt(H):
        # residual not distinguishable from noise: but is the raw move large? only then the market explains it
        raw = tok[TA:TA + H].sum()
        s_raw = local_sigma(tok, TA - WIN)
        if abs(raw) >= k * s_raw * np.sqrt(H):
            return "FAVOURS_ALTERNATIVE"
        return "INCONCLUSIVE"
    return "FAVOURS_ORIGINAL"


def t_placebo(tok, n_placebo=200):
    stat = abs(tok[TA:TA + H].sum())
    times = rng.integers(500, TA - WIN - H - 5, n_placebo)
    pst = np.array([abs(tok[t:t + H].sum()) for t in times])
    p = (1 + np.sum(pst >= stat)) / (n_placebo + 1)
    if p > 0.2:
        return "FAVOURS_ALTERNATIVE"
    if p <= 0.05:
        return "FAVOURS_ORIGINAL"
    return "INCONCLUSIVE"


out = {}
for scn in "ABCD":
    counts = {t: {"FAVOURS_ORIGINAL": 0, "FAVOURS_ALTERNATIVE": 0, "INCONCLUSIVE": 0} for t in ("T_ONSET", "T_ONSET2", "T_MARKET", "T_PLACEBO_TIME")}
    for _ in range(TRIALS):
        tok, mkt = make(scn)
        counts["T_ONSET"][t_onset(tok)] += 1
        counts["T_ONSET2"][t_onset2(tok)] += 1
        counts["T_MARKET"][t_market(tok, mkt)] += 1
        counts["T_PLACEBO_TIME"][t_placebo(tok)] += 1
    out[scn] = {t: {k: round(v / TRIALS, 3) for k, v in c.items()} for t, c in counts.items()}
    print(scn, json.dumps(out[scn]), flush=True)
json.dump({"trials": TRIALS, "scenarios": {"A": "true announcement effect", "B": "move under way 40 minutes before", "C": "market-wide move", "D": "no effect"}, "results": out}, open("E7_results.json", "w"), indent=1)
