"""Writes the golden settlement cases (one JSON file each) from compact definitions.

Every expected value below is written by hand from the contract text, with the reasoning in ``note``; none is produced by an implementation. The
cases are the shared specification of the two settlement implementations (Python and Node) and of nobody's code in particular.

Run: ``python tests/golden/build_settlement_cases.py`` (rewrites ``tests/golden/settlement``).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from typing import Any

M, H = 60_000, 3_600_000
DAY = 24 * H
BASE = int(datetime(2026, 10, 29, tzinfo=UTC).timestamp() * 1000)
ORIGIN = BASE + 10 * H + 2 * M  # 10:02:00, a first candle close strictly after 10:01:11 (contract section 2, the worked example)
OUT = os.path.join(os.path.dirname(__file__), "settlement")
CASES: list[dict[str, Any]] = []


def bar(open_ms: int, close: str, *, o: str | None = None, h: str | None = None, low: str | None = None) -> list[Any]:
    return [open_ms, o or close, h or close, low or close, close]


def oracle(
    venue: str,
    instrument: str,
    quote: str,
    bars: list[list[Any]],
    server: int,
    *,
    mult: str = "1",
    tick: str | None = "0.01",
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "venue": venue,
        "instrument": instrument,
        "quote_class": quote,
        "multiplier": mult,
        "tick": tick,
        "server_time_ms": server,
        "bars": bars,
    }
    return out


def expect(
    state: str,
    outcome: str | None = None,
    reason: str | None = None,
    p0: str | None = None,
    price: str | None = None,
    touch: int | None = None,
    flags: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "outcome": outcome,
        "reason": reason,
        "p0": p0,
        "outcome_price": price,
        "touch_open_ms": touch,
        "flags": sorted(flags or []),
    }


def add(case_id: str, section: str, note: str, kind: str, body: dict[str, Any]) -> None:
    case = {"id": case_id, "section": section, "note": note, "kind": kind, **body}
    expected = case["expected"]
    if "error" not in expected:
        expected["contract"] = case["contract"] if kind == "void" else {"directional": "D", "barrier": "B"}[kind]
    CASES.append(case)


def directional(
    case_id: str,
    section: str,
    note: str,
    p0: str,
    ph: str,
    want: dict[str, Any],
    *,
    s0: str | None = None,
    sh: str | None = None,
    tick: str | None = "0.01",
    m1: str = "1",
    m2: str = "1",
    server: int | None = None,
    server2: int | None = None,
    retries: bool = False,
    basis: str | None = None,
    horizon: int = H,
    quote: str = "USDT",
    forecast_quote: str | None = None,
    drop1: tuple[int, ...] = (),
    drop2: tuple[int, ...] = (),
    extra1: list[list[Any]] | None = None,
    shape: dict[str, str] | None = None,
) -> None:
    ref, out_open = ORIGIN - M, ORIGIN + horizon - M
    end = ORIGIN + horizon + 2 * M
    p_bars = [_shaped(ref, p0, shape), _shaped(out_open, ph, shape)]
    s_bars = [bar(ref, s0 or p0), bar(out_open, sh or ph)]
    p_bars = [b for b in p_bars if b[0] not in drop1] + (extra1 or [])
    s_bars = [b for b in s_bars if b[0] not in drop2]
    add(
        case_id,
        section,
        note,
        "directional",
        {
            "forecast": {"forecast_id": "f-" + case_id, "origin_ms": ORIGIN, "horizon_ms": horizon, "quote_class": forecast_quote or quote},
            "pair": {
                "pair_id": "A",
                "primary": oracle("binance-spot", "BTCUSDT", quote, p_bars, end if server is None else server, mult=m1, tick=tick),
                "second": oracle("okx", "BTC-USDT", quote, s_bars, end if server2 is None else server2, mult=m2, tick=tick),
            },
            "retries_exhausted": retries,
            "basis": basis,
            "finer": {},
            "expected": want,
        },
    )


def _shaped(open_ms: int, close: str, shape: dict[str, str] | None) -> list[Any]:
    return bar(open_ms, close, **(shape or {}))


def barrier(
    case_id: str,
    section: str,
    note: str,
    want: dict[str, Any],
    *,
    side: str = "LONG",
    target: str = "2.00",
    stop: str = "1.00",
    p0: str = "100.00",
    s0: str | None = None,
    overrides: dict[int, list[Any]] | None = None,
    flat: str = "100.00",
    horizon: int = H,
    drop: tuple[int, ...] = (),
    server: int | None = None,
    retries: bool = False,
    finer: dict[int, list[list[str]]] | None = None,
    basis: str | None = None,
    m1: str = "1",
    m2: str = "1",
    extra: list[list[Any]] | None = None,
    drop_ref1: bool = False,
    drop_ref2: bool = False,
) -> None:
    """The window is flat at ``flat`` except for the bars in ``overrides`` (keyed by the bar's index in the window)."""
    n = horizon // M
    ref = ORIGIN - M
    window = []
    for k in range(n):
        if k in drop:
            continue
        window.append(((overrides or {}).get(k) and [ORIGIN + k * M, *(overrides or {})[k]]) or bar(ORIGIN + k * M, flat))
    p_bars = ([] if drop_ref1 else [bar(ref, p0)]) + window + (extra or [])
    s_bars = [] if drop_ref2 else [bar(ref, s0 or str(p0))]
    end = ORIGIN + horizon + 2 * M if server is None else server
    add(
        case_id,
        section,
        note,
        "barrier",
        {
            "forecast": {
                "forecast_id": "f-" + case_id,
                "origin_ms": ORIGIN,
                "horizon_ms": horizon,
                "quote_class": "USDT",
                "side": side,
                "target_offset": target,
                "stop_offset": stop,
            },
            "pair": {
                "pair_id": "A",
                "primary": oracle("binance-spot", "BTCUSDT", "USDT", p_bars, end, mult=m1),
                "second": oracle("okx", "BTC-USDT", "USDT", s_bars, end, mult=m2),
            },
            "retries_exhausted": retries,
            "basis": basis,
            "finer": {str(ORIGIN + k * M): v for k, v in (finer or {}).items()},
            "expected": want,
        },
    )


def ob(o: str, h: str, low: str, c: str) -> list[str]:
    """A bar body in the order the file uses: open, high, low, close."""
    return [o, h, low, c]


# === contract D: ties at the oracle tick (section 2) =====================================================================================
directional(
    "D-tie-exact",
    "2",
    "Equal closes are a tie: excluded and counted, never scored.",
    "100.00",
    "100.00",
    expect("VOID", None, "VOID_TIE", "100.00", "100.00"),
)
directional(
    "D-tie-below-the-tick",
    "2",
    "100.004 and 100.001 are the same price at a tick of 0.01 (both round to 100.00): a tie.",
    "100.004",
    "100.001",
    expect("VOID", None, "VOID_TIE", "100.004", "100.001"),
)
directional(
    "D-tie-half-even-low",
    "2",
    "100.005 is 10000.5 ticks: half-even rounds to 10000, so it ties with 100.000.",
    "100.005",
    "100.000",
    expect("VOID", None, "VOID_TIE", "100.005", "100.000"),
)
directional(
    "D-tie-half-even-high",
    "2",
    "100.015 is 10001.5 ticks: half-even rounds to 10002, so it ties with 100.020 (and not with 100.010).",
    "100.015",
    "100.020",
    expect("VOID", None, "VOID_TIE", "100.015", "100.020"),
)
directional(
    "D-one-tick-up",
    "2",
    "One tick higher is not a tie: the event holds.",
    "100.00",
    "100.01",
    expect("SETTLED", "1", None, "100.00", "100.01"),
)
directional(
    "D-one-tick-down", "2", "One tick lower: the event does not hold.", "100.01", "100.00", expect("SETTLED", "0", None, "100.01", "100.00")
)
directional(
    "D-no-tick-exact",
    "2",
    "With no tick the closes are compared as they are: 100.004 is above 100.001.",
    "100.004",
    "100.001",
    expect("SETTLED", "0", None, "100.004", "100.001"),
    tick=None,
)
directional(
    "D-no-tick-tie",
    "2",
    "With no tick, equal numbers written differently are equal: 100.0 and 100.00.",
    "100.0",
    "100.00",
    expect("VOID", None, "VOID_TIE", "100.0", "100.00"),
    tick=None,
)
directional(
    "D-exact-ticks-on-huge-prices",
    "2",
    "Arithmetic is exact: 31 digits, one tick apart, are not a tie (a 28-digit decimal context would round them together).",
    "1000000000000000000000000000.00",
    "1000000000000000000000000000.01",
    expect("SETTLED", "1", None, "1000000000000000000000000000.00", "1000000000000000000000000000.01"),
    s0="1000000000000000000000000000.00",
    sh="1000000000000000000000000000.01",
)
directional(
    "D-exact-agreement-on-huge-prices",
    "7",
    "The second oracle is 0.5 percent and one cent above: a disagreement, which only exact arithmetic sees.",
    "100000000000000000000000000000.00",
    "100000000000000000000000000000.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100000000000000000000000000000.00", "100000000000000000000000000000.00"),
    s0="100500000000000000000000000000.01",
    sh="100000000000000000000000000000.00",
)
# === ordinary outcomes ====================================================================================================================
directional(
    "D-up",
    "2",
    "The outcome close is above the reference close: outcome 1.",
    "79000.00",
    "79120.50",
    expect("SETTLED", "1", None, "79000.00", "79120.50"),
)
directional("D-down", "2", "Below: outcome 0.", "79000.00", "78950.00", expect("SETTLED", "0", None, "79000.00", "78950.00"))
directional(
    "D-4h-up",
    "2",
    "A four hour horizon reads the candle that closes at T0 + 4h.",
    "100.00",
    "101.00",
    expect("SETTLED", "1", None, "100.00", "101.00"),
    horizon=4 * H,
)
directional("D-24h-down", "2", "A 24 hour horizon.", "100.00", "99.00", expect("SETTLED", "0", None, "100.00", "99.00"), horizon=DAY)
directional(
    "D-only-the-close-counts",
    "2",
    "The open, high and low of the two candles are irrelevant: the outcome close is below the reference close although the outcome candle's high is far above.",
    "100.00",
    "99.50",
    expect("SETTLED", "0", None, "100.00", "99.50"),
    shape={"o": "101.00", "h": "150.00", "low": "99.00"},
)
directional(
    "D-planted-future",
    "2, 14",
    "A candle after the horizon (and one before the reference) with absurd prices changes nothing: look-ahead is impossible by construction.",
    "100.00",
    "100.50",
    expect("SETTLED", "1", None, "100.00", "100.50"),
    extra1=[bar(ORIGIN + H, "1000000.00"), bar(ORIGIN - 2 * M, "0.01"), bar(ORIGIN + H + M, "1000000.00")],
)
# === unfinished bars (section 7: a bar closes before the venue's server time) ================================================================
directional(
    "D-unclosed-outcome",
    "7",
    "The server time is one millisecond before the outcome candle closes: its close is not final, nothing is settled.",
    "100.00",
    "101.00",
    expect("PENDING", None, "BAR_NOT_CLOSED"),
    server=ORIGIN + H - 1,
)
directional(
    "D-closed-at-the-instant",
    "7",
    "The server time is exactly the candle's close: it is final.",
    "100.00",
    "101.00",
    expect("SETTLED", "1", None, "100.00", "101.00"),
    server=ORIGIN + H,
    server2=ORIGIN + H,
)
directional(
    "D-second-oracle-clock-behind",
    "7",
    "The second oracle's own clock has not reached the outcome candle's close: pending, even though the primary's has.",
    "100.00",
    "101.00",
    expect("PENDING", None, "BAR_NOT_CLOSED"),
    server2=ORIGIN + H - 1,
)
# === missing candles (section 7: retries, then UNSETTLEABLE_MISSING) =========================================================================
directional(
    "D-missing-reference-retrying",
    "7",
    "The reference candle is missing and retries remain: pending, nothing is filled in.",
    "100.00",
    "101.00",
    expect("PENDING", None, "CANDLE_MISSING"),
    drop1=(ORIGIN - M,),
)
directional(
    "D-missing-reference-final",
    "7",
    "The reference candle is still missing after the retries: unsettleable.",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING"),
    drop1=(ORIGIN - M,),
    retries=True,
)
directional(
    "D-missing-outcome-final",
    "7",
    "The outcome candle is missing after the retries.",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING"),
    drop1=(ORIGIN + H - M,),
    retries=True,
)
directional(
    "D-missing-second-reference",
    "7",
    "The second oracle has no reference candle: its agreement cannot be checked, so the forecast is not settled on the primary alone.",
    "100.00",
    "101.00",
    expect("PENDING", None, "CANDLE_MISSING"),
    drop2=(ORIGIN - M,),
)
directional(
    "D-missing-second-final",
    "7",
    "And after the retries it is unsettleable.",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING"),
    drop2=(ORIGIN + H - M,),
    retries=True,
)
directional(
    "D-pending-beats-unsettleable",
    "7",
    "One candle will never come (retries are over) but another has not closed yet: the answer waits, because the unclosed one could still change it.",
    "100.00",
    "101.00",
    expect("PENDING", None, "BAR_NOT_CLOSED"),
    drop1=(ORIGIN - M,),
    retries=True,
    server2=ORIGIN + H - 1,
)
directional(
    "D-both-missing-final",
    "7",
    "The primary's reference and the second's outcome are both missing after the retries: unsettleable (the first need in order is reported).",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING"),
    drop1=(ORIGIN - M,),
    drop2=(ORIGIN + H - M,),
    retries=True,
)
# === oracle disagreement (section 7: more than 0.5 percent) =================================================================================
directional(
    "D-agree-at-the-limit",
    "7",
    "The second oracle's reference close is exactly 0.5 percent above the primary's: not more than 0.5 percent, so they agree.",
    "100.00",
    "101.00",
    expect("SETTLED", "1", None, "100.00", "101.00"),
    s0="100.50",
    sh="101.00",
)
directional(
    "D-disagree-reference",
    "7",
    "100.51 is 0.51 percent above 100.00: the forecast is unsettleable and counted.",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100.00", "101.00"),
    s0="100.51",
    sh="101.00",
)
directional(
    "D-disagree-reference-below",
    "7",
    "The second oracle is 0.6 percent below at the reference.",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100.00", "101.00"),
    s0="99.40",
    sh="101.00",
)
directional(
    "D-disagree-outcome",
    "7",
    "The reference closes agree and the outcome closes do not (101.00 against 101.60 is 0.59 percent).",
    "100.00",
    "101.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100.00", "101.00"),
    s0="100.00",
    sh="101.60",
)
directional(
    "D-agree-outcome-at-the-limit",
    "7",
    "The outcome closes differ by exactly 0.5 percent of the primary's.",
    "100.00",
    "200.00",
    expect("SETTLED", "1", None, "100.00", "200.00"),
    sh="199.00",
    s0="100.00",
)
directional(
    "D-disagree-beats-tie",
    "7",
    "A tie on the primary does not hide a disagreement: unsettleable comes first.",
    "100.00",
    "100.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100.00", "100.00"),
    s0="100.00",
    sh="101.00",
)
directional(
    "D-1000x-agree",
    "7",
    "A 1000x instrument (price of 1000 tokens) against a plain one: the closes agree once divided by the multiplier.",
    "100000.00",
    "100500.00",
    expect("SETTLED", "1", None, "100000.00", "100500.00"),
    s0="100.00",
    sh="100.50",
    m1="1000",
    m2="1",
)
directional(
    "D-1000x-disagree",
    "7",
    "The same pair with the second oracle 0.7 percent off after scaling.",
    "100000.00",
    "100500.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100000.00", "100500.00"),
    s0="100.70",
    sh="100.50",
    m1="1000",
    m2="1",
)
directional(
    "D-1000x-forgotten",
    "7",
    "A 1000x instrument compared with a plain one WITHOUT the multiplier is a disagreement (the registry's join exists to prevent this).",
    "100000.00",
    "100500.00",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100000.00", "100500.00"),
    s0="100.00",
    sh="100.50",
    m1="1",
    m2="1",
)
# === the stable-coin basis (section 7) =======================================================================================================
directional(
    "D-basis-at-the-limit",
    "7",
    "A basis of exactly 0.3 percent is not stress.",
    "100.00",
    "101.00",
    expect("SETTLED", "1", None, "100.00", "101.00"),
    basis="0.003",
)
directional(
    "D-basis-stress",
    "7",
    "0.31 percent marks the forecast BASIS_STRESS and shows it; the outcome is unchanged.",
    "100.00",
    "101.00",
    expect("SETTLED", "1", None, "100.00", "101.00", flags=["BASIS_STRESS"]),
    basis="0.0031",
)
directional(
    "D-basis-stress-negative",
    "7",
    "The basis is judged by its size, not its sign.",
    "100.00",
    "99.00",
    expect("SETTLED", "0", None, "100.00", "99.00", flags=["BASIS_STRESS"]),
    basis="-0.004",
)
directional(
    "D-basis-stress-on-a-void",
    "7",
    "A flag also travels with a void.",
    "100.00",
    "100.00",
    expect("VOID", None, "VOID_TIE", "100.00", "100.00", flags=["BASIS_STRESS"]),
    basis="0.01",
)
# === contract B (section 3) ==================================================================================================================
T = ORIGIN
barrier(
    "B-long-target-first",
    "3",
    "Long: levels 102.00 and 99.00 from a 100.00 reference. Bar 10 reaches 102.50 and nothing earlier touches: TARGET.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 10 * M),
    overrides={10: ob("100.20", "102.50", "100.10", "102.00")},
)
barrier(
    "B-long-stop-first",
    "3",
    "Bar 3's low is 98.90, at or below 99.00: STOP.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 3 * M),
    overrides={3: ob("100.00", "100.40", "98.90", "99.20")},
)
barrier(
    "B-long-timeout",
    "3",
    "No bar of the hour touches a level: TIMEOUT, and the touch time is empty.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
)
barrier(
    "B-target-inclusive",
    "3",
    "A high of exactly 102.00 touches the target.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 8 * M),
    overrides={8: ob("100.00", "102.00", "100.00", "101.00")},
)
barrier(
    "B-stop-inclusive",
    "3",
    "A low of exactly 99.00 touches the stop.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 8 * M),
    overrides={8: ob("100.00", "100.50", "99.00", "99.50")},
)
barrier(
    "B-one-tick-short",
    "3",
    "101.99 and 99.01 touch nothing: TIMEOUT.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
    overrides={8: ob("100.00", "101.99", "99.01", "100.00")},
)
barrier(
    "B-first-touch-wins",
    "3",
    "Bar 5 touches the target; bar 9 touches the stop. The earlier decides: TARGET.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.10", "100.00", "101.90"), 9: ob("100.00", "100.00", "98.00", "98.50")},
)
barrier(
    "B-gap-up-through-the-target",
    "3",
    "A candle entirely above the target (low 103.00): its high is at or above 102.00, so the target is touched.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 20 * M),
    overrides={20: ob("103.20", "103.50", "103.00", "103.10")},
)
barrier(
    "B-gap-down-through-the-stop",
    "3",
    "A candle entirely below the stop (high 97.50): the low is at or below 99.00.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 20 * M),
    overrides={20: ob("97.40", "97.50", "97.00", "97.20")},
)
barrier(
    "B-touch-in-the-last-bar",
    "3",
    "The last bar of the horizon (index 59) still counts.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 59 * M),
    overrides={59: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-after-the-horizon-does-not-count",
    "3",
    "A bar that opens at T0 + horizon is outside the window, however wild: TIMEOUT.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
    extra=[bar(T + H, "200.00", h="250.00", low="100.00")],
)
barrier(
    "B-before-the-origin-does-not-count",
    "3",
    "A bar before the origin (other than the reference) is not in the window.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
    extra=[bar(T - 2 * M, "90.00", h="150.00", low="50.00")],
)
# same candle
barrier(
    "B-both-in-one-candle",
    "3",
    "Bar 5 reaches 102.30 and 98.70: both levels in one minute, with no finer data, is AMBIGUOUS.",
    expect("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.30", "98.70", "100.00")},
)
barrier(
    "B-both-finer-target-first",
    "3",
    "The finer pieces of bar 5 in order: the first touches neither, the second reaches 102.30 with a low of 100.40: the target came first.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.30", "98.70", "100.00")},
    finer={5: [["100.50", "99.50"], ["102.30", "100.40"], ["100.00", "98.70"]]},
)
barrier(
    "B-both-finer-stop-first",
    "3",
    "The first piece reaches 98.80 (stop) with a high of 100.20: the stop came first.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.30", "98.70", "100.00")},
    finer={5: [["100.20", "98.80"], ["102.30", "100.00"]]},
)
barrier(
    "B-both-finer-still-ambiguous",
    "3",
    "A finer piece that itself reaches both levels cannot order them: it stays AMBIGUOUS.",
    expect("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.30", "98.70", "100.00")},
    finer={5: [["102.30", "98.70"]]},
)
barrier(
    "B-both-finer-no-piece-touches",
    "3",
    "Finer data whose pieces touch neither level cannot place the touches (it contradicts the bar): AMBIGUOUS, never guessed.",
    expect("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.30", "98.70", "100.00")},
    finer={5: [["101.00", "100.00"], ["101.50", "100.20"]]},
)
barrier(
    "B-both-finer-for-another-minute",
    "3",
    "Finer data for a different minute is not used for this one.",
    expect("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", "100.00", None, T + 5 * M),
    overrides={5: ob("100.00", "102.30", "98.70", "100.00")},
    finer={6: [["102.30", "100.40"]]},
)
# short side
barrier(
    "B-short-target",
    "3",
    "Short: target 98.00 and stop 101.00 from 100.00. Bar 7's low is 97.90: TARGET.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 7 * M),
    side="SHORT",
    overrides={7: ob("100.00", "100.50", "97.90", "98.20")},
)
barrier(
    "B-short-stop",
    "3",
    "Bar 7's high is 101.20: STOP.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 7 * M),
    side="SHORT",
    overrides={7: ob("100.00", "101.20", "99.50", "101.00")},
)
barrier(
    "B-short-both",
    "3",
    "Both levels in one bar for a short: AMBIGUOUS.",
    expect("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", "100.00", None, T + 7 * M),
    side="SHORT",
    overrides={7: ob("100.00", "101.50", "97.50", "100.00")},
)
barrier(
    "B-short-target-inclusive",
    "3",
    "For a short a low of exactly 98.00 touches the target.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 8 * M),
    side="SHORT",
    overrides={8: ob("100.00", "100.50", "98.00", "99.00")},
)
barrier(
    "B-short-stop-inclusive",
    "3",
    "For a short a high of exactly 101.00 touches the stop.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 8 * M),
    side="SHORT",
    overrides={8: ob("100.00", "101.00", "99.50", "100.50")},
)
barrier(
    "B-short-one-tick-short",
    "3",
    "98.01 and 100.99 touch nothing for a short.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
    side="SHORT",
    overrides={8: ob("100.00", "100.99", "98.01", "100.00")},
)
barrier(
    "B-short-gap-up-through-the-stop",
    "3",
    "For a short a bar with a high of 102.50 is far past the stop at 101.00 (and nowhere near the target at 98.00): STOP.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 7 * M),
    side="SHORT",
    overrides={7: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-long-ignores-the-shorts-levels",
    "3",
    "For a long the same bar (high 102.50) is the target.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 7 * M),
    overrides={7: ob("100.00", "102.50", "100.00", "102.00")},
)
# gaps and unfinished bars inside the window
barrier(
    "B-missing-bar-before-the-touch",
    "3, 7",
    "Bar 4 is missing and the touch is at bar 10: the first touch could have been in the missing minute; retries remain, so pending.",
    expect("PENDING", None, "CANDLE_MISSING", "100.00"),
    drop=(4,),
    overrides={10: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-missing-bar-before-the-touch-final",
    "3, 7",
    "The same after the retries: unsettleable.",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING", "100.00"),
    drop=(4,),
    retries=True,
    overrides={10: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-missing-bar-after-the-touch",
    "3, 7",
    "Bar 10 is missing but the touch was at bar 4: the answer is already known.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 4 * M),
    drop=(10,),
    overrides={4: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-timeout-needs-every-bar",
    "3, 7",
    "Only 59 of the 60 bars exist: a timeout cannot be claimed, retries remain.",
    expect("PENDING", None, "CANDLE_MISSING", "100.00"),
    drop=(59,),
)
barrier(
    "B-timeout-needs-every-bar-final",
    "3, 7",
    "And after the retries it is unsettleable.",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING", "100.00"),
    drop=(59,),
    retries=True,
)
barrier(
    "B-window-not-over",
    "3, 7",
    "The venue's clock is in the middle of bar 5: bars 0 to 4 are closed and quiet, bar 5 is not final: pending.",
    expect("PENDING", None, "BAR_NOT_CLOSED", "100.00"),
    server=T + 5 * M + 30_000,
)
barrier(
    "B-touch-before-the-clock",
    "3, 7",
    "The venue's clock is in the middle of bar 5 but the touch was at bar 3, which is closed: the target is already decided.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 3 * M),
    server=T + 5 * M + 30_000,
    overrides={3: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-touch-in-an-unclosed-bar",
    "3, 7",
    "The only touch is in the bar the clock is in the middle of: not final.",
    expect("PENDING", None, "BAR_NOT_CLOSED", "100.00"),
    server=T + 5 * M + 30_000,
    overrides={5: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-reference-missing",
    "3, 7",
    "The primary has no reference candle: no levels can be set; retries remain.",
    expect("PENDING", None, "CANDLE_MISSING"),
    drop_ref1=True,
)
barrier(
    "B-reference-missing-final",
    "3, 7",
    "After the retries: unsettleable.",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING"),
    drop_ref1=True,
    retries=True,
)
barrier(
    "B-second-reference-missing",
    "3, 7",
    "The second oracle has no reference candle.",
    expect("PENDING", None, "CANDLE_MISSING"),
    drop_ref2=True,
)
barrier(
    "B-second-reference-missing-final",
    "3, 7",
    "After the retries: unsettleable.",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING"),
    drop_ref2=True,
    retries=True,
)
# oracle disagreement, scale, basis
barrier(
    "B-reference-disagrees",
    "3, 7",
    "The second oracle's reference close is 100.60 against 100.00: the levels' origin is in doubt, unsettleable.",
    expect("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", "100.00"),
    s0="100.60",
    overrides={10: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-reference-agrees-at-the-limit",
    "3, 7",
    "100.50 is exactly 0.5 percent: agrees.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 10 * M),
    s0="100.50",
    overrides={10: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-1000x",
    "3, 7",
    "A 1000x instrument: reference 100000.00, offsets in its own scale (2000 and 1000), second oracle plain at 100.00.",
    expect("SETTLED", "TARGET", None, "100000.00", None, T + 10 * M),
    p0="100000.00",
    s0="100.00",
    flat="100000.00",
    target="2000.00",
    stop="1000.00",
    m1="1000",
    m2="1",
    overrides={10: ob("100000.00", "102500.00", "100000.00", "102000.00")},
)
barrier(
    "B-basis-stress",
    "3, 7",
    "A basis beyond 0.3 percent flags the settlement without changing it.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 10 * M, ["BASIS_STRESS"]),
    basis="0.005",
    overrides={10: ob("100.00", "102.50", "100.00", "102.00")},
)
barrier(
    "B-offsets-from-the-reference",
    "3",
    "Levels come from the reference close, not from the first bar: reference 100.00, the window starts at 90.00 (a gap down at the open), so 99.00 is already crossed in bar 0: STOP.",
    expect("SETTLED", "STOP", None, "100.00", None, T),
    flat="90.00",
    overrides={},
)
# longer horizons use runs of identical bars
barrier(
    "B-4h-timeout",
    "3",
    "A four hour horizon is 240 bars: none touches.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
    horizon=4 * H,
)
barrier(
    "B-4h-stop-late",
    "3",
    "Bar 239, the last of four hours, touches the stop.",
    expect("SETTLED", "STOP", None, "100.00", None, T + 239 * M),
    horizon=4 * H,
    overrides={239: ob("100.00", "100.20", "98.50", "99.00")},
)
barrier(
    "B-24h-timeout",
    "3",
    "A 24 hour horizon is 1440 bars: none touches.",
    expect("SETTLED", "TIMEOUT", None, "100.00", None, None),
    horizon=DAY,
)
barrier(
    "B-24h-target-late",
    "3",
    "Bar 1000 touches the target.",
    expect("SETTLED", "TARGET", None, "100.00", None, T + 1000 * M),
    horizon=DAY,
    overrides={1000: ob("100.00", "102.20", "100.00", "102.00")},
)
# === voids that come from outside the prices ==============================================================================================
for reason, why in (
    ("VOID_DELISTED", "The instrument was delisted before the horizon ended."),
    ("VOID_OUTAGE", "The venue was down across the window."),
    ("VOID_GAP", "The frozen evidence waited too long for the origin."),
    ("VOID_REDENOMINATION", "The token was redenominated."),
    ("VOID_TOKEN_EVENT", "A token event changed what the price means."),
    ("VOID_TIE", "A tie recorded by the caller."),
):
    for contract in ("D", "B"):
        if reason in ("VOID_REDENOMINATION", "VOID_TOKEN_EVENT", "VOID_TIE") and contract == "B":
            continue
        add(
            f"V-{contract}-{reason.lower()}",
            "7",
            why,
            "void",
            {"forecast_id": f"f-v-{contract}-{reason}", "contract": contract, "pair_id": "A", "expected": expect("VOID", None, reason)},
        )
# === inputs that cannot be settled at all (a defect of the caller's record, not an outcome) ======================================================================
directional(
    "E-quote-class-mismatch",
    "7",
    "A forecast made on USD cannot be settled on a USDT pair: the identity chain is broken.",
    "100.00",
    "101.00",
    {"error": "invalid"},
    forecast_quote="USD",
)
directional(
    "E-bad-horizon",
    "2",
    "A horizon under an hour cannot be settled on one-minute candles.",
    "100.00",
    "101.00",
    {"error": "invalid"},
    horizon=30 * M,
)
directional(
    "E-horizon-not-whole-minutes",
    "2",
    "A horizon that is not a whole number of minutes.",
    "100.00",
    "101.00",
    {"error": "invalid"},
    horizon=H + 30_000,
)
add(
    "E-pair-quote-classes-differ",
    "7",
    "The two oracles of a pair must share a quote class: a USDT price is never compared with a USD one.",
    "directional",
    {
        "forecast": {"forecast_id": "f-e-pair", "origin_ms": ORIGIN, "horizon_ms": H, "quote_class": "USDT"},
        "pair": {
            "pair_id": "A",
            "primary": oracle(
                "binance-spot", "BTCUSDT", "USDT", [bar(ORIGIN - M, "100.00"), bar(ORIGIN + H - M, "101.00")], ORIGIN + H + M
            ),
            "second": oracle("kraken", "XBTUSD", "USD", [bar(ORIGIN - M, "100.00"), bar(ORIGIN + H - M, "101.00")], ORIGIN + H + M),
        },
        "retries_exhausted": False,
        "basis": None,
        "finer": {},
        "expected": {"error": "invalid"},
    },
)
add(
    "E-bar-range-broken",
    "7",
    "A bar whose high is below its close is garbage from the venue, not a price.",
    "directional",
    {
        "forecast": {"forecast_id": "f-e-bar", "origin_ms": ORIGIN, "horizon_ms": H, "quote_class": "USDT"},
        "pair": {
            "pair_id": "A",
            "primary": oracle(
                "binance-spot",
                "BTCUSDT",
                "USDT",
                [[ORIGIN - M, "100.00", "99.00", "98.00", "100.00"], bar(ORIGIN + H - M, "101.00")],
                ORIGIN + H + M,
            ),
            "second": oracle("okx", "BTC-USDT", "USDT", [bar(ORIGIN - M, "100.00"), bar(ORIGIN + H - M, "101.00")], ORIGIN + H + M),
        },
        "retries_exhausted": False,
        "basis": None,
        "finer": {},
        "expected": {"error": "invalid"},
    },
)
barrier("E-barrier-offset-not-positive", "3", "A barrier offset must be a positive distance.", {"error": "invalid"}, target="0.00")


def write() -> None:
    os.makedirs(OUT, exist_ok=True)
    for name in os.listdir(OUT):
        if name.endswith(".json"):
            os.remove(os.path.join(OUT, name))
    seen: set[str] = set()
    for case in CASES:
        if case["id"] in seen:
            raise SystemExit(f"duplicate case id {case['id']}")
        seen.add(case["id"])
        with open(os.path.join(OUT, case["id"] + ".json"), "w", encoding="utf-8") as handle:
            json.dump(case, handle, indent=1, sort_keys=True)
            handle.write("\n")
    print(f"wrote {len(CASES)} cases to {OUT}")


if __name__ == "__main__":
    write()
