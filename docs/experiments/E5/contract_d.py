"""Experiment E5: contract D (directional forecast) origin and settlement arithmetic, written from the contract text
(sections 2, 7, 25). Integer milliseconds only. Three implementations are compared by run_e5.py: a closed form (here),
a brute-force scan over the candle list (here), and a JavaScript closed form (contract_d.mjs).

Definitions (contract section 2):
  eligible_after = attested_at + u_total            (u_total = u_const + observed spread, milliseconds)
  T0             = close time of the first 1-minute candle that closes strictly after eligible_after
  P0             = close price of that candle
  P_h            = close price of the candle whose close time is T0 + horizon
  outcome        = 1 if P_h > P0, 0 if P_h < P0, VOID_TIE if equal at the oracle tick
A candle with open time o closes at o + 60000 (the boundary, exclusive end).
"""
from __future__ import annotations

MIN = 60_000


def t0_closed_form(eligible_after_ms: int) -> int:
    """Smallest multiple of 60 000 ms strictly greater than eligible_after."""
    return (eligible_after_ms // MIN + 1) * MIN


def t0_scan(eligible_after_ms: int, candle_opens: list[int]) -> int | None:
    """Brute force: first candle in order whose close (open + 60 000) is strictly after eligible_after."""
    for o in sorted(candle_opens):
        if o + MIN > eligible_after_ms:
            return o + MIN
    return None


def settle(candles: dict[int, tuple[str, str]], attested_at_ms: int, u_total_ms: int, horizon_ms: int):
    """candles maps open_time_ms -> (close_price_decimal_string, tick_decimal_string).
    Returns (state, T0, P0, T_h, P_h) with state in SETTLED_1, SETTLED_0, VOID_TIE, UNSETTLEABLE_MISSING_T0, UNSETTLEABLE_MISSING_H."""
    from decimal import Decimal
    ea = attested_at_ms + u_total_ms
    t0 = t0_closed_form(ea)
    c0 = candles.get(t0 - MIN)
    if c0 is None:
        return ("UNSETTLEABLE_MISSING_T0", t0, None, None, None)
    th = t0 + horizon_ms
    ch = candles.get(th - MIN)
    if ch is None:
        return ("UNSETTLEABLE_MISSING_H", t0, c0[0], th, None)
    p0, ph, tick = Decimal(c0[0]), Decimal(ch[0]), Decimal(c0[1])
    q = lambda x: (x / tick).to_integral_value() * tick  # round to the oracle tick
    p0q, phq = q(p0), q(ph)
    state = "SETTLED_1" if phq > p0q else "SETTLED_0" if phq < p0q else "VOID_TIE"
    return (state, t0, c0[0], th, ch[0])
