"""Technical indicators over closed candles.

Every function takes plain lists and returns a list of the same length. A
position that cannot be computed is None, never 0 and never NaN: the caller
decides what an undefined value means. Definitions follow the classic texts
and are stated in each docstring so a reader can recompute them by hand.
"""
from __future__ import annotations

import math
from collections.abc import Callable, Sequence

Series = list[float | None]


def _defined(window: Sequence[float | None]) -> bool:
    return all(v is not None for v in window)


def sma(values: Sequence[float | None], n: int) -> Series:
    """Simple moving average of the last n values; None until n values exist."""
    out: Series = [None] * len(values)
    for i in range(n - 1, len(values)):
        window = values[i - n + 1 : i + 1]
        if _defined(window):
            out[i] = math.fsum(window) / n  # type: ignore[arg-type]
    return out


def ema(values: Sequence[float | None], n: int) -> Series:
    """Exponential moving average, alpha = 2 / (n + 1).

    Seeded with the simple average of the first n consecutive defined values,
    then ema[i] = alpha * v[i] + (1 - alpha) * ema[i - 1].
    """
    out: Series = [None] * len(values)
    alpha = 2.0 / (n + 1)
    prev: float | None = None
    run = 0
    for i, v in enumerate(values):
        if v is None:
            prev, run = None, 0
            continue
        run += 1
        if prev is None:
            if run == n:
                prev = math.fsum(values[i - n + 1 : i + 1]) / n  # type: ignore[arg-type]
                out[i] = prev
        else:
            prev = alpha * v + (1.0 - alpha) * prev
            out[i] = prev
    return out


def rsi(closes: Sequence[float], n: int) -> Series:
    """Wilder's RSI.

    The first average gain and loss are the plain means of the first n price
    changes; afterwards avg = (previous * (n - 1) + current) / n. RSI is 100
    when the average loss is zero and the average gain is not, 0 in the
    mirror case, and undefined (None) when price has not moved at all.
    """
    out: Series = [None] * len(closes)
    if len(closes) <= n:
        return out
    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, len(closes))]
    avg_gain = math.fsum(gains[:n]) / n
    avg_loss = math.fsum(losses[:n]) / n
    for i in range(n, len(closes)):
        if i > n:
            avg_gain = (avg_gain * (n - 1) + gains[i - 1]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i - 1]) / n
        if avg_gain == 0.0 and avg_loss == 0.0:
            out[i] = None
        elif avg_loss == 0.0:
            out[i] = 100.0
        else:
            out[i] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def true_range(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float]) -> Series:
    """True range; undefined on the first bar because it needs a prior close."""
    out: Series = [None] * len(closes)
    for i in range(1, len(closes)):
        out[i] = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int) -> Series:
    """Wilder's ATR: seeded with the mean of true ranges 1..n, then Wilder smoothing."""
    out: Series = [None] * len(closes)
    tr = true_range(highs, lows, closes)
    if len(closes) <= n:
        return out
    prev = math.fsum(tr[1 : n + 1]) / n  # type: ignore[arg-type]
    out[n] = prev
    for i in range(n + 1, len(closes)):
        prev = (prev * (n - 1) + tr[i]) / n  # type: ignore[operator]
        out[i] = prev
    return out


def atr_pct(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], n: int) -> Series:
    """ATR as a percentage of the close on the same bar."""
    a = atr(highs, lows, closes, n)
    return [None if v is None or c == 0 else v / c * 100.0 for v, c in zip(a, closes)]


def macd(closes: Sequence[float], fast: int, slow: int, signal: int) -> dict[str, Series]:
    """MACD line = EMA(fast) - EMA(slow); signal = EMA(signal) of the line; hist = line - signal."""
    ef, es = ema(closes, fast), ema(closes, slow)
    line: Series = [None if a is None or b is None else a - b for a, b in zip(ef, es)]
    sig = ema(line, signal)
    hist: Series = [None if a is None or b is None else a - b for a, b in zip(line, sig)]
    return {"macd": line, "signal": sig, "hist": hist}


def bbands(closes: Sequence[float], n: int, k: float) -> dict[str, Series]:
    """Bollinger Bands: SMA(n) +/- k population standard deviations.

    percent_b = (close - lower) / (upper - lower), width = (upper - lower) / middle.
    Both are undefined when the band has zero width or the middle is zero.
    """
    mid = sma(closes, n)
    upper: Series = [None] * len(closes)
    lower: Series = [None] * len(closes)
    pb: Series = [None] * len(closes)
    width: Series = [None] * len(closes)
    for i, m in enumerate(mid):
        if m is None:
            continue
        window = closes[i - n + 1 : i + 1]
        sd = math.sqrt(math.fsum((x - m) ** 2 for x in window) / n)
        up, lo = m + k * sd, m - k * sd
        upper[i], lower[i] = up, lo
        span = up - lo
        if span > 0:
            pb[i] = (closes[i] - lo) / span
        if m != 0:
            width[i] = span / m
    return {"upper": upper, "middle": mid, "lower": lower, "percent_b": pb, "width": width}


def stoch(
    highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], k_period: int, smooth: int, d_period: int
) -> dict[str, Series]:
    """Slow stochastic. raw %K = 100 * (close - lowest low) / (highest high - lowest low) over
    k_period bars; %K = SMA(raw, smooth); %D = SMA(%K, d_period). Zero range gives None."""
    raw: Series = [None] * len(closes)
    for i in range(k_period - 1, len(closes)):
        hh = max(highs[i - k_period + 1 : i + 1])
        ll = min(lows[i - k_period + 1 : i + 1])
        if hh > ll:
            raw[i] = 100.0 * (closes[i] - ll) / (hh - ll)
    k = sma(raw, smooth)
    return {"k": k, "d": sma(k, d_period)}


def volume_zscore(volumes: Sequence[float], n: int) -> Series:
    """(volume - mean of the previous n volumes) / population std of those n volumes.

    The current bar is excluded from its own baseline so a spike cannot hide itself.
    None when fewer than n prior bars exist or the baseline has zero variance.
    """
    out: Series = [None] * len(volumes)
    for i in range(n, len(volumes)):
        base = volumes[i - n : i]
        mean = math.fsum(base) / n
        sd = math.sqrt(math.fsum((x - mean) ** 2 for x in base) / n)
        if sd > 0:
            out[i] = (volumes[i] - mean) / sd
    return out


def roc(values: Sequence[float], n: int) -> Series:
    """Rate of change in percent: (value / value n bars ago - 1) * 100."""
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        if values[i - n] != 0:
            out[i] = (values[i] / values[i - n] - 1.0) * 100.0
    return out


def highest_prior(values: Sequence[float], n: int) -> Series:
    """Highest value over the n bars before this one (current bar excluded), for breakouts."""
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        out[i] = max(values[i - n : i])
    return out


def lowest_prior(values: Sequence[float], n: int) -> Series:
    """Lowest value over the n bars before this one (current bar excluded)."""
    out: Series = [None] * len(values)
    for i in range(n, len(values)):
        out[i] = min(values[i - n : i])
    return out


# Warm-up: number of candles an indicator needs before its first defined value
# (index of the first defined output + 1). The condition evaluator uses these to
# say "needs N bars, have M" instead of guessing.
WARMUP: dict[str, Callable[..., int]] = {
    "open": lambda **_: 1,
    "high": lambda **_: 1,
    "low": lambda **_: 1,
    "close": lambda **_: 1,
    "volume": lambda **_: 1,
    "sma": lambda period, **_: period,
    "ema": lambda period, **_: period,
    "rsi": lambda period, **_: period + 1,
    "atr": lambda period, **_: period + 1,
    "atr_pct": lambda period, **_: period + 1,
    "macd": lambda slow, signal, output="macd", **_: slow if output == "macd" else slow + signal - 1,
    "bbands": lambda period, **_: period,
    "stoch": lambda k_period, smooth, d_period, output="k", **_: k_period + smooth - 1 + (d_period - 1 if output == "d" else 0),
    "volume_zscore": lambda period, **_: period + 1,
    "roc": lambda period, **_: period + 1,
    "highest_prior": lambda period, **_: period + 1,
    "lowest_prior": lambda period, **_: period + 1,
}
