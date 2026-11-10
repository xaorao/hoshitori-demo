"""The skill itself: validate, fetch with fallback, evaluate, return the RYO-style envelope.

Honesty rules enforced here, each covered by a test:
  * null is never false and never 0 (insufficient or undefined data stays null);
  * the forming candle is excluded and the exclusion is reported;
  * candle gaps are reported and only the contiguous suffix after the last gap is evaluated;
  * a recursively smoothed indicator (EMA, RSI, ATR, MACD) computed on too few bars is flagged as unconverged;
  * the venue actually used, every fallback attempt and every upstream call (with a body hash) are disclosed;
  * upstream response text is never copied into the envelope (fixed wording per failure kind);
  * `status` is derived from the evidence, never set by hand;
  * `summary` is generated from `data`, so the two cannot disagree;
  * `data_mode` is "live" only when every candle was fetched during this call.
"""
from __future__ import annotations

import datetime as dt
import math
import random
import time
import uuid
from collections.abc import Callable
from typing import Any

from .condition import Bars, Evaluation, clauses_of, evaluate, normalize
from .errors import InputError, VenueError
from .schema import TOOL_NAME, validate_input
from .venues import (
    ADAPTERS,
    AUTO_ORDER,
    INTERVAL_MS,
    STABLECOINS,
    CallRecord,
    Candle,
    HttpGet,
    clock_skew_s,
    default_http_get,
    fetch_candles,
    find_gaps,
)

SCHEMA_VERSION = "1.0"
TOTAL_BUDGET_SECONDS = 25.0
CROSS_VENUE_TOLERANCE_PCT = 0.5
WARMUP_EXTRA = 200  # extra candles requested before the statistics window so EMA/RSI/ATR have converged
CLOCK_SKEW_WARN_S = 2.0
STALE_CACHE_MAX_AGE_S = 6 * 3600
MAX_SOURCE_CALLS = 24

METHODS = {
    "sma": "arithmetic mean of the last n values",
    "ema": "alpha = 2/(n+1), seeded with the SMA of the first n values; stable after about 3n more bars",
    "rsi": "Wilder: first averages are plain means of the first n changes, then (prev*(n-1)+current)/n; 100 when average loss is 0, undefined when price is flat; stable after about 6n more bars",
    "atr": "Wilder ATR seeded with the mean of true ranges 1..n, then Wilder smoothing; stable after about 6n more bars",
    "atr_pct": "ATR as a percent of the close on the same bar",
    "macd": "EMA(fast) - EMA(slow); signal = EMA(signal) of that line; hist = line - signal",
    "bbands": "SMA(n) +/- k population standard deviations; percent_b = (close-lower)/(upper-lower); width = (upper-lower)/middle",
    "stoch": "slow stochastic: raw %K over k_period, %K = SMA(raw, smooth), %D = SMA(%K, d_period)",
    "volume_zscore": "(volume - mean of previous n volumes) / population std of those n volumes; the current bar is excluded from its baseline",
    "roc": "(value / value n bars ago - 1) * 100",
    "highest_prior": "highest value of the n bars before this one (current bar excluded)",
    "lowest_prior": "lowest value of the n bars before this one (current bar excluded)",
}
INJECTED_WARNING = "Candles came from an injected data source (recorded or test data), not a live fetch in this call; data_mode is therefore unknown."

# Closed candles are immutable until the next interval closes, so they can be reused safely until then.
# key: (venue, pair, timeframe) -> entry
_CACHE: dict[tuple[str, str, str], dict] = {}


def clear_cache() -> None:
    _CACHE.clear()


def _num(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else float(x)


def _iso(ms: int) -> str:
    return dt.datetime.fromtimestamp(ms / 1000, tz=dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _f4(x: float | None) -> str:
    return "n/a" if x is None else f"{x:.4f}"


def _closed(candles: list[Candle], interval_ms: int, now_ms: int) -> tuple[list[Candle], bool]:
    closed = [c for c in candles if c.ts + interval_ms <= now_ms]
    return closed, len(closed) != len(candles)


def _bars(candles: list[Candle]) -> Bars:
    return Bars(
        ts=[c.ts for c in candles],
        open=[c.open for c in candles],
        high=[c.high for c in candles],
        low=[c.low for c in candles],
        close=[c.close for c in candles],
        volume=[c.volume for c in candles],
    )


def _attempt(venue: str, outcome: str, err: VenueError | None = None, **extra) -> dict:
    row: dict[str, Any] = {"venue": venue, "outcome": outcome}
    if err is not None:
        row.update({"kind": err.kind, "message": err.message, "http_status": err.http_status, "retryable": err.retryable, "retry_after_s": err.retry_after_s})
        if err.venue_code:
            row["venue_code"] = err.venue_code
    row.update(extra)
    return row


def _envelope(*, status, now, request, data, headline, key_points, availability, warnings, data_mode="live") -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "status": status,
        "data_mode": data_mode,
        "as_of": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "request": request,
        "data": data,
        "summary": {"headline": headline, "key_points": key_points},
        "availability": availability,
        "warnings": warnings,
    }


def _unavailable(req: dict, attempts: list[dict], reason: str, now: dt.datetime, data_mode: str, calls: list[CallRecord]) -> dict:
    notes = ["No verdict was produced; null is not false."]
    if data_mode != "live":
        notes.append(INJECTED_WARNING)
    return _envelope(
        status="unavailable",
        data_mode=data_mode,
        now=now,
        request=req,
        data={
            "pair": {"resolved": None, "venue_used": None, "alternatives_tried": attempts},
            "candles": None,
            "result": None,
            "history": None,
            "warmup": None,
            "methods": {},
            "source_calls": [c.as_dict() for c in calls[:MAX_SOURCE_CALLS]],
        },
        headline=f"{req['symbol']} {req['timeframe']}: condition could not be evaluated ({reason})",
        key_points=[f"{a['venue']}: {a['outcome']}" + (f" ({a['kind']})" if a.get("kind") else "") for a in attempts] or [reason],
        availability={"ohlc": "unavailable", "condition": "unavailable"},
        warnings=[reason, *notes],
    )


def run(
    args: dict[str, Any],
    *,
    now: dt.datetime | None = None,
    http_get: HttpGet = default_http_get,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    rng: random.Random | None = None,
    budget_seconds: float = TOTAL_BUDGET_SECONDS,
    use_cache: bool = True,
) -> dict:
    """Run the skill. Raises InputError for malformed arguments (before any network call)."""
    a = validate_input(args)
    now = now or dt.datetime.now(dt.UTC)
    now_ms = int(now.timestamp() * 1000)
    sym, tf, lookback = a["symbol"], a["timeframe"], a["lookback_bars"]
    tree, need = a["_tree"], a["_need"]
    iv = INTERVAL_MS[tf]
    clauses = clauses_of(tree)
    stable_need = max(c.stable_bars() for c in clauses)
    request = {
        "symbol": sym,
        "timeframe": tf,
        "lookback_bars": lookback,
        "venue": a["venue"],
        "cross_venue_check": a["cross_venue_check"],
        "condition": normalize(tree),
        "defaults_applied": list(a["_defaults"]),
        "required_bars": need,
        "stable_bars": stable_need,
        "notes": list(a["_notes"]),
    }
    data_mode = "live" if http_get is default_http_get else "unknown"
    all_calls: list[CallRecord] = []
    if sym in STABLECOINS:
        return _unavailable(request, [], f"{sym} is a stablecoin; USD price candles carry no signal for it", now, data_mode, all_calls)

    deadline = clock() + budget_seconds
    order = AUTO_ORDER if a["venue"] == "auto" else (a["venue"],)
    attempts: list[dict] = []
    best: tuple[str, list[Candle], bool, dict | None] | None = None  # venue, closed, dropped_forming, cache_info
    for name in order:
        adapter = ADAPTERS[name]
        if clock() >= deadline - 0.2:
            attempts.append({"venue": name, "outcome": "skipped", "kind": "timeout", "message": VenueError(name, "timeout").message})
            continue
        key = (name, adapter.pair(sym), tf)
        entry = _CACHE.get(key) if use_cache else None
        if entry and now_ms < entry["last_closed_ts"] + 2 * iv and entry["fetched_ms"] <= now_ms and len(entry["closed"]) >= min(lookback, adapter.max_bars - 1):
            closed, dropped = entry["closed"], entry["dropped"]
            hit = {"hit": True, "age_s": max(0, int((now_ms - entry["fetched_ms"]) / 1000)), "stale": False}
            attempts.append(_attempt(name, "ok", closed_bars=len(closed), source="cache"))
            if best is None or len(closed) > len(best[1]):
                best = (name, closed, dropped, hit)
            break
        try:
            res = fetch_candles(adapter, sym, tf, lookback + 1 + WARMUP_EXTRA, http_get, deadline=deadline, sleep=sleep, clock=clock, rng=rng, now_fn=lambda: now, calls=all_calls)
        except VenueError as e:
            attempts.append(_attempt(name, "failed", e))
            continue
        closed, dropped = _closed(res.candles, iv, now_ms)
        if not closed:
            attempts.append(_attempt(name, "failed", VenueError(name, "bad_response")))
            continue
        if use_cache:
            _CACHE[key] = {"closed": closed, "dropped": dropped, "last_closed_ts": closed[-1].ts, "fetched_ms": now_ms}
        attempts.append(_attempt(name, "ok", closed_bars=len(closed)))
        if best is None or len(closed) > len(best[1]):
            best = (name, closed, dropped, None)
        if len(closed) >= min(lookback, adapter.max_bars - 1):
            break

    stale_note = None
    if best is None and use_cache:  # stale-while-error: serve the newest cached closed set, clearly labelled
        stale = [(k, ent) for k, ent in _CACHE.items() if k[1] == ADAPTERS[k[0]].pair(sym) and k[2] == tf and k[0] in order and 0 <= now_ms - ent["fetched_ms"] <= STALE_CACHE_MAX_AGE_S * 1000]
        if stale:
            k, ent = max(stale, key=lambda kv: kv[1]["fetched_ms"])
            age = max(0, int((now_ms - ent["fetched_ms"]) / 1000))
            best = (k[0], ent["closed"], ent["dropped"], {"hit": True, "age_s": age, "stale": True})
            stale_note = f"Every live fetch failed; serving the last good closed candles from {k[0]} fetched {age}s ago. This is not a live read."
    if best is None:
        kinds = {x.get("kind") for x in attempts}
        if kinds == {"not_listed"}:
            why = f"{sym} is not listed against USD/USDT on {', '.join(order)}"
        elif kinds <= {"rate_limited", "cooling_down"}:
            why = "every venue is rate limiting; retry later"
        else:
            why = "no venue returned usable candles"
        return _unavailable(request, attempts, why, now, data_mode, all_calls)

    venue, closed, dropped, cache_hint = best
    cache_info: dict[str, Any] | None = cache_hint
    warnings: list[str] = [INJECTED_WARNING] if data_mode != "live" else []
    if cache_info:
        data_mode = "mixed" if data_mode == "live" else data_mode
        warnings.append(stale_note or f"Closed candles were served from this process's cache (fetched {cache_info['age_s']}s ago); closed candles do not change until the next one closes.")

    # --- candle integrity: alignment and gaps ---
    misaligned = sum(1 for c in closed if c.ts % iv != 0)
    if misaligned:
        warnings.append(f"{misaligned} candle timestamps are not aligned to the {tf} boundary; check the venue's candle convention.")
    gaps = find_gaps(closed, iv)
    if gaps:
        last_gap_before = gaps[-1]["before_ts"]
        cut = next(i for i, c in enumerate(closed) if _iso(c.ts) == last_gap_before)
        warnings.append(f"{len(gaps)} gap(s) in the candle series ({sum(g['missing'] for g in gaps)} candles missing); indicators are computed only on the {len(closed) - cut} contiguous candles after the last gap.")
        closed = closed[cut:]

    ev: Evaluation = evaluate(tree, _bars(closed), window=lookback)
    n = len(closed)
    window = min(lookback, n)
    if n < lookback:
        warnings.append(f"{venue} supplied {n} usable closed candles, fewer than lookback_bars={lookback}; history statistics cover {n} bars")
    if dropped:
        warnings.append("The forming candle was excluded; the verdict is for the last closed candle.")
    failed = [x for x in attempts if x["outcome"] != "ok"]
    if failed:
        warnings.append("Fallback venues were used: " + "; ".join(f"{x['venue']} {x.get('kind', x['outcome'])}" for x in failed))

    # --- convergence of recursive indicators ---
    warm_rows = [{"id": c.id, "expr": c.text(), "needed_stable": c.stable_bars(), "used": n, "stable": n >= c.stable_bars()} for c in clauses]
    unstable = [w for w in warm_rows if not w["stable"]]
    for w in unstable:
        warnings.append(
            f"{w['id']} {w['expr']} uses {n} bars but about {w['needed_stable']} are needed for a stable value; recursive indicators "
            "(EMA, RSI, ATR, MACD) shift until their seed is forgotten, so charting platforms may show different numbers."
        )

    overall = ev.series[-1]
    clause_rows: list[dict[str, Any]] = []
    for cr in ev.clauses:
        clause_rows.append(
            {
                "id": cr.clause.id,
                "expr": cr.clause.text(),
                "left": _num(cr.left),
                "right": _num(cr.right),
                "satisfied": cr.satisfied,
                "null_reason": cr.null_reason,
                "bars_since_last_true": cr.bars_since_last_true,
                "last_true_ts": None if cr.last_true_index is None else _iso(closed[cr.last_true_index].ts),
                "true_count": cr.true_count,
                "defined_bars": cr.defined_count,
            }
        )
    undecided = [r for r in clause_rows if r["satisfied"] is None]
    for r in undecided:
        warnings.append(f"{r['id']} undecided: {r['null_reason']}")
    last_ts = closed[-1].ts
    history = {
        "window_bars": window,
        "defined_bars": ev.defined_bars,
        "times_true": ev.true_bars,
        "last_true_ts": None if ev.last_true_index is None else _iso(closed[ev.last_true_index].ts),
        "bars_since_last_true": None if ev.last_true_index is None else n - 1 - ev.last_true_index,
    }
    base, quote = ADAPTERS[venue].unified(sym)
    pair_info: dict[str, Any] = {
        "resolved": ADAPTERS[venue].pair(sym),
        "venue_id": ADAPTERS[venue].pair(sym),
        "unified": f"{base}/{quote}",
        "base": base,
        "quote": quote,
        "venue_used": venue,
        "alternatives_tried": attempts,
    }
    if quote != "USD":
        pair_info["quote_note"] = f"Quoted in {quote}; USD venues can differ by a small basis."
    data: dict[str, Any] = {
        "pair": pair_info,
        "candles": {
            "timeframe": tf,
            "count": n,  # closed candles used to compute the indicators (>= window_bars when the venue supplied the warm-up)
            "first_ts": _iso(closed[0].ts),
            "last_closed_ts": _iso(last_ts),
            "last_close": closed[-1].close,
            "forming_bar_excluded": dropped,
            "gaps": gaps[:10],
            "gap_count": len(gaps),
        },
        "result": {"satisfied": overall, "clauses": clause_rows},
        "history": history,
        "warmup": {"available": n, "required_minimum": need, "stable_for_all_clauses": not unstable, "per_clause": warm_rows},
        "methods": {k: METHODS[k] for k in sorted({o.name for c in clauses for o in (c.left, c.right) if not o.is_const and o.name in METHODS})},
        "source_calls": [c.as_dict() for c in all_calls[:MAX_SOURCE_CALLS]],
    }
    if cache_info:
        data["cache"] = cache_info
    skew = clock_skew_s(all_calls, now)
    if skew is not None:
        data["clock"] = {"skew_s": round(skew, 2)}
        if abs(skew) > CLOCK_SKEW_WARN_S:
            warnings.append(f"The local clock differs from the venue's by {skew:.1f}s; closed-candle logic uses the local clock.")

    availability = {
        "ohlc": "available" if n >= lookback and not gaps and not (cache_info and cache_info["stale"]) else "partial",
        "condition": "complete" if not undecided and overall is not None else "partial",
        "warmup": "complete" if not unstable else "partial",
    }
    if a["cross_venue_check"]:
        data["cross_venue"], cv_warning = _cross_venue(venue, sym, tf, last_ts, closed[-1].close, http_get, deadline, sleep, clock, rng, now)
        availability["cross_venue"] = "available" if data["cross_venue"]["checked"] else "unavailable"
        if cv_warning:
            warnings.append(cv_warning)

    primary = ("ohlc", "condition", "warmup")  # optional sections (cross_venue) never lower the status
    status = "ok" if all(availability[k] in ("available", "complete") for k in primary) else "partial"
    state = {True: "TRUE", False: "FALSE", None: "UNDETERMINED"}[overall]
    headline = f"{sym} {tf}: condition is {state} on the last closed candle ({_iso(last_ts)}, {venue})"
    points = []
    for r in clause_rows:
        verdict = {True: "true", False: "false", None: "undetermined"}[r["satisfied"]]
        since = "never true in window" if r["bars_since_last_true"] is None else f"last true {r['bars_since_last_true']} bars ago"
        points.append(f"{r['id']} {r['expr']}: left {_f4(r['left'])}, right {_f4(r['right'])}, {verdict}; true on {r['true_count']} of {r['defined_bars']} evaluable bars, {since}")
    points.append(f"whole condition true on {history['times_true']} of {history['defined_bars']} evaluable bars in the last {window}")
    return _envelope(status=status, now=now, request=request, data=data, headline=headline, key_points=points, availability=availability, warnings=warnings, data_mode=data_mode)


def _cross_venue(primary, sym, tf, last_ts, last_close, http_get, deadline, sleep, clock, rng, now):
    info = {"checked": False, "venue": None, "last_close_primary": last_close, "last_close_other": None, "diff_pct": None, "within_tolerance": None, "tolerance_pct": CROSS_VENUE_TOLERANCE_PCT}
    for name in AUTO_ORDER:
        if name == primary:
            continue
        adapter = ADAPTERS[name]
        if tf not in adapter.intervals:
            continue
        try:
            res = fetch_candles(adapter, sym, tf, 6, http_get, deadline=deadline, sleep=sleep, clock=clock, rng=rng, retries=1, now_fn=lambda: now, max_pages=1)
        except VenueError:
            continue
        match = next((c for c in res.candles if c.ts == last_ts), None)
        if match is None:
            continue
        diff = abs(match.close - last_close) / last_close * 100
        ok = diff <= CROSS_VENUE_TOLERANCE_PCT
        info.update({"checked": True, "venue": name, "last_close_other": match.close, "diff_pct": diff, "within_tolerance": ok})
        return info, (None if ok else f"Cross-venue close differs by {diff:.3f}% between {primary} and {name}; quote conventions (USDT vs USD) may explain part of it.")
    return info, "Cross-venue check requested but no second venue returned a matching candle."


def error_envelope(exc: InputError) -> dict:
    return exc.to_envelope(uuid.uuid4().hex)
