"""Golden vectors: our indicators against TA-Lib on real recorded candles (tolerance 1e-9 relative).

TA-Lib is an optional reference dependency (`pip install .[reference]`) that these tests need: without it they FAIL at import, so a
machine that cannot reproduce the reference never reports the indicators as checked. Set `ICC_ALLOW_MISSING_REFERENCE=1` to skip them on
purpose (the skip is then reported). Indicators TA-Lib does not define (volume_zscore, highest_prior, lowest_prior) are checked against
a numpy derivation of their documented formula."""
from __future__ import annotations

import json
import math
import os

import pytest
from conftest import fx

from indicator_condition_check import indicators as ind

if os.environ.get("ICC_ALLOW_MISSING_REFERENCE") == "1":
    talib = pytest.importorskip("talib", reason="TA-Lib reference not installed (skipped on purpose)")
    np = pytest.importorskip("numpy")
else:
    import numpy as np  # a missing reference is a failure, not a skip: pip install .[reference]
    import talib


def load_binance(name):
    rows = json.loads(fx(name))
    return {k: np.array([float(r[i]) for r in rows]) for k, i in (("o", 1), ("h", 2), ("l", 3), ("c", 4), ("v", 5))}


DATASETS = ["binance_SOLUSDT_1d.json", "binance_SOLUSDT_4h.json", "binance_BTCUSDT_1h.json"]


def close(a, b, tol=1e-9):
    if isinstance(b, float) and math.isnan(b):
        return a is None
    if a is None:
        return False
    return abs(a - b) <= tol * max(1.0, abs(b))


def assert_series(ours, ref, label):
    assert len(ours) == len(ref)
    bad = [(i, o, float(r)) for i, (o, r) in enumerate(zip(ours, ref)) if not close(o, float(r))]
    assert not bad, f"{label}: {len(bad)} mismatches, first {bad[:3]}"


@pytest.mark.parametrize("name", DATASETS)
@pytest.mark.parametrize("n", [2, 14, 21])
def test_rsi_matches_talib(name, n):
    d = load_binance(name)
    assert_series(ind.rsi(list(d["c"]), n), talib.RSI(d["c"], n), f"rsi({n})")


@pytest.mark.parametrize("name", DATASETS)
@pytest.mark.parametrize("n", [5, 14])
def test_atr_matches_talib(name, n):
    d = load_binance(name)
    assert_series(ind.atr(list(d["h"]), list(d["l"]), list(d["c"]), n), talib.ATR(d["h"], d["l"], d["c"], n), f"atr({n})")


@pytest.mark.parametrize("name", DATASETS)
@pytest.mark.parametrize("n", [10, 50, 200])
def test_sma_ema_match_talib(name, n):
    d = load_binance(name)
    assert_series(ind.sma(list(d["c"]), n), talib.SMA(d["c"], n), f"sma({n})")
    assert_series(ind.ema(list(d["c"]), n), talib.EMA(d["c"], n), f"ema({n})")


@pytest.mark.parametrize("name", DATASETS)
def test_bbands_match_talib(name):
    d = load_binance(name)
    up, mid, lo = talib.BBANDS(d["c"], 20, 2.0, 2.0, 0)
    ours = ind.bbands(list(d["c"]), 20, 2.0)
    assert_series(ours["upper"], up, "bb.upper")
    assert_series(ours["middle"], mid, "bb.middle")
    assert_series(ours["lower"], lo, "bb.lower")


@pytest.mark.parametrize("name", DATASETS)
def test_stoch_matches_talib(name):
    """TA-Lib only starts %K where %D is also defined (index 17); we expose %K from index 15. Both are checked:
    equality with TA-Lib wherever it answers, and a direct numpy derivation for the two earlier %K values."""
    d = load_binance(name)
    k, dd = talib.STOCH(d["h"], d["l"], d["c"], fastk_period=14, slowk_period=3, slowk_matype=0, slowd_period=3, slowd_matype=0)
    ours = ind.stoch(list(d["h"]), list(d["l"]), list(d["c"]), 14, 3, 3)
    first = int(np.argmax(~np.isnan(k)))
    assert first == 17
    assert_series(ours["k"][first:], k[first:], "stoch.k")
    assert_series(ours["d"], dd, "stoch.d")
    raw = [100 * (d["c"][i] - d["l"][i - 13 : i + 1].min()) / (d["h"][i - 13 : i + 1].max() - d["l"][i - 13 : i + 1].min()) for i in range(13, len(d["c"]))]
    for i in (15, 16):
        assert close(ours["k"][i], float(np.mean(raw[i - 13 - 2 : i - 13 + 1])))
    assert all(v is None for v in ours["k"][:15])


@pytest.mark.parametrize("name", DATASETS)
def test_roc_matches_talib(name):
    d = load_binance(name)
    assert_series(ind.roc(list(d["c"]), 10), talib.ROC(d["c"], 10), "roc")


@pytest.mark.parametrize("name", DATASETS)
def test_macd_matches_textbook_definition_built_from_talib_ema(name):
    """TA-Lib seeds its fast EMA differently from the textbook definition, so the reference is the textbook
    definition built from TA-Lib's own EMA: EMA(fast) - EMA(slow), signal = EMA of that line."""
    d = load_binance(name)
    ours = ind.macd(list(d["c"]), 12, 26, 9)
    line = talib.EMA(d["c"], 12) - talib.EMA(d["c"], 26)
    assert_series(ours["macd"], line, "macd.line")
    valid = line[~np.isnan(line)]
    sig = np.full(len(line), np.nan)
    sig[len(line) - len(valid) :] = talib.EMA(valid, 9)
    assert_series(ours["signal"], sig, "macd.signal")
    assert_series(ours["hist"], line - sig, "macd.hist")


def test_volume_zscore_against_numpy():
    d = load_binance("binance_SOLUSDT_1d.json")
    v = d["v"]
    n = 20
    ours = ind.volume_zscore(list(v), n)
    for i in range(len(v)):
        if i < n:
            assert ours[i] is None
            continue
        base = v[i - n : i]
        assert close(ours[i], float((v[i] - base.mean()) / base.std()))


def test_highest_lowest_prior_exclude_current_bar():
    d = load_binance("binance_SOLUSDT_1d.json")
    h, low = d["h"], d["l"]
    hp, lp = ind.highest_prior(list(h), 20), ind.lowest_prior(list(low), 20)
    for i in range(20, len(h)):
        assert hp[i] == float(h[i - 20 : i].max())
        assert lp[i] == float(low[i - 20 : i].min())
    assert hp[19] is None and lp[19] is None
