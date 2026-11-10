"""End-to-end behaviour of `run` against recorded real venue responses and injected failures."""
from __future__ import annotations

import json
import re

import pytest
from conftest import FakeHttp, fx, ok

from indicator_condition_check import OUTPUT_SCHEMA, InputError, run

jsonschema = pytest.importorskip("jsonschema")

COND = {
    "all": [
        {"left": {"indicator": "rsi", "period": 14}, "op": "<", "right": {"value": 70}},
        {"left": {"indicator": "close"}, "op": ">", "right": {"indicator": "ema", "period": 50}},
    ]
}
ARGS = {"symbol": "sol", "condition": COND}


def call(http, now, sleeper=None, rng=None, **extra):
    # The recorded Binance fixture holds 300 candles (299 closed), so tests ask for 250 unless they say otherwise.
    extra.setdefault("lookback_bars", 250)
    kw = {"http_get": http, "now": now}
    if sleeper is not None:
        kw["sleep"] = sleeper
    if rng is not None:
        kw["rng"] = rng
    return run({**ARGS, **extra}, **kw)


def assert_valid(env):
    jsonschema.validate(env, OUTPUT_SCHEMA)
    json.dumps(env, allow_nan=False)  # no NaN / Infinity anywhere in the payload


# ----- success -----------------------------------------------------------------------------------------------------

def test_success_on_recorded_binance_data(all_venues_recorded, now):
    env = call(all_venues_recorded, now)
    assert_valid(env)
    assert env["status"] == "ok" and env["tool"] == "indicator_condition_check"
    # Replayed recorded data must never claim to be live.
    assert env["data_mode"] == "unknown" and any("injected data source" in w for w in env["warnings"])
    d = env["data"]
    assert d["pair"]["resolved"] == "SOLUSDT" and d["pair"]["venue_used"] == "binance"
    assert d["pair"]["unified"] == "SOL/USDT" and d["pair"]["quote"] == "USDT" and "quote_note" in d["pair"]
    assert d["pair"]["alternatives_tried"] == [{"venue": "binance", "outcome": "ok", "closed_bars": 299}]
    assert d["candles"]["forming_bar_excluded"] is True
    assert d["candles"]["last_closed_ts"] == "2026-10-19T00:00:00Z"  # the 2026-10-20 bar was still forming at capture time
    assert d["candles"]["count"] == 299 and d["history"]["window_bars"] == 250  # all closed candles feed the maths; the window only scopes the statistics
    assert [c["id"] for c in d["result"]["clauses"]] == ["c1", "c2"]
    assert env["request"]["defaults_applied"] == ["timeframe", "venue", "cross_venue_check"]
    assert set(d["methods"]) == {"rsi", "ema"}


def test_clause_values_can_be_recomputed_by_hand(all_venues_recorded, now):
    env = call(all_venues_recorded, now)
    rows = json.loads(fx("binance_SOLUSDT_1d.json"))
    closes = [float(r[4]) for r in rows[:-1]]  # drop the forming bar
    c = env["data"]["result"]["clauses"]
    assert c[1]["left"] == closes[-1]
    alpha = 2 / 51
    seed = sum(closes[:50]) / 50
    e = seed
    for x in closes[50:]:
        e = alpha * x + (1 - alpha) * e
    assert abs(c[1]["right"] - e) < 1e-9 * max(1, e)


def test_summary_is_generated_from_data_so_numbers_agree(all_venues_recorded, now):
    env = call(all_venues_recorded, now)
    pts = env["summary"]["key_points"]
    for row, text in zip(env["data"]["result"]["clauses"], pts):
        left, right = re.search(r"left (\S+), right (\S+),", text).groups()
        assert left == f"{row['left']:.4f}" and right == f"{row['right']:.4f}"
        assert f"true on {row['true_count']} of {row['defined_bars']} evaluable bars" in text
    state = {True: "TRUE", False: "FALSE", None: "UNDETERMINED"}[env["data"]["result"]["satisfied"]]
    assert f"condition is {state}" in env["summary"]["headline"]


def test_input_is_normalised_and_symbol_uppercased(all_venues_recorded, now):
    env = call(all_venues_recorded, now)
    assert env["request"]["symbol"] == "SOL"
    assert env["request"]["condition"]["all"][0]["left"] == {"indicator": "rsi", "period": 14, "source": "close"}


@pytest.mark.parametrize("tf,expected_venue", [("1d", "binance"), ("4h", "binance")])
def test_each_timeframe_on_recorded_data(all_venues_recorded, now, tf, expected_venue):
    env = call(all_venues_recorded, now, timeframe=tf)
    assert_valid(env)
    assert env["data"]["pair"]["venue_used"] == expected_venue and env["status"] == "ok"


def test_a_venue_that_returns_fewer_candles_than_asked_for_is_supplemented_by_the_longest_other_venue(all_venues_recorded, now):
    """Binance's recorded reply has 299 closed candles; asking for 300 sends the skill on to Coinbase (349 closed),
    and both attempts are disclosed. Quote conventions differ (USDT vs USD), which is why it is reported."""
    env = call(all_venues_recorded, now, lookback_bars=300)
    tried = env["data"]["pair"]["alternatives_tried"]
    assert [t["venue"] for t in tried] == ["binance", "coinbase"]
    assert env["data"]["pair"]["venue_used"] == "coinbase" and env["data"]["candles"]["count"] == 349 and env["data"]["history"]["window_bars"] == 300


# ----- fallback and failure ----------------------------------------------------------------------------------------

def test_geo_blocked_binance_falls_back_to_coinbase_and_says_so(now):
    http = FakeHttp({
        "api.binance.com": (451, {}, b'{"msg":"Service unavailable from a restricted location"}'),
        "data-api.binance.vision": (451, {}, b"{}"),
        "api.exchange.coinbase.com/products/SOL-USD/candles?granularity=86400": ok("coinbase_SOL-USD_1d.json"),
    })
    env = call(http, now)
    assert_valid(env)
    assert env["data"]["pair"]["venue_used"] == "coinbase"
    tried = env["data"]["pair"]["alternatives_tried"]
    assert tried[0] == {
        "venue": "binance", "outcome": "failed", "kind": "geo_blocked", "message": "the venue refused the request (region or access policy)",
        "http_status": 451, "retryable": False, "retry_after_s": None,
    }
    assert any("Fallback venues were used" in w for w in env["warnings"])
    assert any("data-api.binance.vision" in u for u in http.calls)  # the Binance mirror was tried before leaving Binance


def test_coinbase_has_no_4h_so_it_is_skipped_for_4h(now):
    http = FakeHttp({"api.binance.com": (500, {}, b"x"), "data-api.binance.vision": (500, {}, b"x"), "api.kraken.com/0/public/OHLC?pair=SOLUSD&interval=240": ok("kraken_SOLUSD_4h.json")})
    env = call(http, now, timeframe="4h", sleeper=lambda s: None)
    outcomes = {a["venue"]: a for a in env["data"]["pair"]["alternatives_tried"]}
    assert outcomes["coinbase"]["kind"] == "unsupported_interval"
    assert env["data"]["pair"]["venue_used"] == "kraken"
    assert not any("coinbase" in u for u in http.calls)


def test_rate_limit_is_retried_with_jittered_backoff_honouring_retry_after(now, rng):
    sleeps: list[float] = []
    http = FakeHttp({"api.binance.com": [(429, {"retry-after": "2"}, b"{}"), (429, {}, b"{}"), ok("binance_SOLUSDT_1d.json")]})
    env = call(http, now, sleeper=sleeps.append, rng=rng)
    assert env["status"] == "ok" and env["data"]["pair"]["venue_used"] == "binance"
    assert len(sleeps) == 2 and sleeps[0] >= 2.0  # Retry-After is a floor
    assert all(0 <= s <= 5.0 for s in sleeps)  # each wait fits inside the call's own deadline
    assert len([u for u in http.calls if "api.binance.com" in u]) == 3


def test_timeout_on_one_venue_does_not_prevent_an_answer(now):
    http = FakeHttp({
        "api.binance.com": TimeoutError("timed out"),
        "data-api.binance.vision": TimeoutError("timed out"),
        "api.exchange.coinbase.com/products/SOL-USD/candles?granularity=86400": ok("coinbase_SOL-USD_1d.json"),
    })
    env = call(http, now, sleeper=lambda s: None)
    assert env["data"]["pair"]["venue_used"] == "coinbase"
    assert env["data"]["pair"]["alternatives_tried"][0]["kind"] == "timeout"


def test_all_venues_down_is_unavailable_and_never_a_verdict(now):
    http = FakeHttp({"": (503, {}, b"down")})
    env = call(http, now, sleeper=lambda s: None)
    assert_valid(env)
    assert env["status"] == "unavailable"
    assert env["data"]["result"] is None  # no verdict, in particular not False
    assert env["availability"] == {"ohlc": "unavailable", "condition": "unavailable"}
    assert "null is not false" in " ".join(env["warnings"])
    assert [a["venue"] for a in env["data"]["pair"]["alternatives_tried"]] == ["binance", "coinbase", "kraken", "bybit"]


def test_unlisted_symbol_names_the_symbol_and_never_guesses_a_similar_one(now):
    http = FakeHttp({
        "api.binance.com": (400, {}, fx("binance_invalid.json")),
        "api.exchange.coinbase.com": (404, {}, fx("coinbase_invalid.json")),
        "api.kraken.com": (200, {}, fx("kraken_invalid.json")),
        "api.bybit.com": (200, {}, fx("bybit_invalid.json")),
    })
    env = run({"symbol": "NOTACOIN", "condition": COND}, http_get=http, now=now)
    assert env["status"] == "unavailable"
    assert "NOTACOIN is not listed" in env["summary"]["headline"] or "NOTACOIN is not listed" in " ".join(env["warnings"])
    assert {a["kind"] for a in env["data"]["pair"]["alternatives_tried"]} == {"not_listed"}
    assert all("NOTACOIN" in u or "NOTACOIN" in u.upper() for u in http.calls)


def test_stablecoin_is_refused_without_any_network_call(now):
    http = FakeHttp({})
    env = run({"symbol": "usdt", "condition": COND}, http_get=http, now=now)
    assert env["status"] == "unavailable" and http.calls == []
    assert "stablecoin" in env["warnings"][0]


@pytest.mark.parametrize(
    "body",
    [b"<html>maintenance</html>", b'{"unexpected": true}', b"[]", b'[[1,"a","b","c","d","e"]]', b""],
)
def test_malformed_venue_bodies_become_bad_response_not_crashes(now, body):
    http = FakeHttp({"": (200, {}, body)})
    env = run({"symbol": "SOL", "condition": COND, "venue": "binance"}, http_get=http, now=now, sleep=lambda s: None)
    assert_valid(env)
    assert env["status"] == "unavailable"
    assert env["data"]["pair"]["alternatives_tried"][0]["kind"] == "bad_response"


def test_implausible_candles_are_rejected(now):
    bad = json.dumps([[1700000000000, "10", "5", "9", "8", "1", 0, "0", 0, "0", "0", "0"]]).encode()  # high < low
    env = run({"symbol": "SOL", "condition": COND, "venue": "binance"}, http_get=FakeHttp({"": (200, {}, bad)}), now=now)
    assert env["data"]["pair"]["alternatives_tried"][0]["kind"] == "bad_response"


def test_budget_exhaustion_skips_remaining_venues_and_reports_it(now):
    ticks = iter([0.0] + [100.0] * 50)
    env = run(
        {"symbol": "SOL", "condition": COND}, http_get=FakeHttp({"": ok("binance_SOLUSDT_1d.json")}), now=now, clock=lambda: next(ticks), budget_seconds=5.0
    )
    assert env["status"] == "unavailable"
    assert all(a["outcome"] in ("skipped", "failed") for a in env["data"]["pair"]["alternatives_tried"])


# ----- partial and null handling -----------------------------------------------------------------------------------

def test_history_shorter_than_the_condition_needs_is_partial_with_null_clauses_not_false(now):
    short = json.dumps(json.loads(fx("binance_SOLUSDT_1d.json"))[-30:]).encode()  # 30 real candles
    env = run({"symbol": "SOL", "condition": {"left": {"indicator": "sma", "period": 100}, "op": ">", "right": {"value": 0}}, "lookback_bars": 100, "venue": "binance"},
              http_get=FakeHttp({"": (200, {}, short)}), now=now)
    assert_valid(env)
    assert env["status"] == "partial"
    row = env["data"]["result"]["clauses"][0]
    assert row["satisfied"] is None and "needs 100 bars" in row["null_reason"]
    assert env["data"]["result"]["satisfied"] is None
    assert env["data"]["history"]["times_true"] == 0
    assert "UNDETERMINED" in env["summary"]["headline"]
    assert any("fewer than lookback_bars" in w for w in env["warnings"])


def test_lookback_smaller_than_required_history_is_refused_before_any_network_call():
    http = FakeHttp({})
    with pytest.raises(InputError, match="needs at least 200 bars"):
        run({"symbol": "SOL", "condition": {"left": {"indicator": "sma", "period": 200}, "op": ">", "right": {"value": 0}}, "lookback_bars": 100}, http_get=http)
    assert http.calls == []


# ----- cross-venue check -------------------------------------------------------------------------------------------

def test_cross_venue_check_compares_the_same_candle(all_venues_recorded, now):
    env = call(all_venues_recorded, now, cross_venue_check=True)
    cv = env["data"]["cross_venue"]
    assert cv["checked"] is True and cv["venue"] == "coinbase"
    assert cv["last_close_primary"] == env["data"]["candles"]["last_close"]
    assert cv["diff_pct"] < 0.5 and cv["within_tolerance"] is True
    assert env["availability"]["cross_venue"] == "available"


def test_cross_venue_failure_never_degrades_the_primary_status(now):
    http = FakeHttp({"api.binance.com": ok("binance_SOLUSDT_1d.json"), "": (500, {}, b"x")})
    env = call(http, now, cross_venue_check=True, sleeper=lambda s: None)
    assert env["status"] == "ok" and env["data"]["cross_venue"]["checked"] is False
    assert env["availability"]["cross_venue"] == "unavailable"


# ----- input validation happens before the network -----------------------------------------------------------------

@pytest.mark.parametrize(
    "args,field",
    [
        ({}, "symbol"),
        ({"symbol": "SOL"}, "condition"),
        ({"symbol": "So1ana-USD", "condition": COND}, "symbol"),
        ({"symbol": "0x" + "a" * 40, "condition": COND}, "symbol"),
        ({"symbol": "SOL; DROP TABLE", "condition": COND}, "symbol"),
        ({"symbol": "SOL", "condition": COND, "timeframe": "5m"}, "timeframe"),
        ({"symbol": "SOL", "condition": COND, "lookback_bars": 10}, "lookback_bars"),
        ({"symbol": "SOL", "condition": COND, "lookback_bars": 1001}, "lookback_bars"),
        ({"symbol": "SOL", "condition": COND, "lookback_bars": True}, "lookback_bars"),
        ({"symbol": "SOL", "condition": COND, "venue": "ftx"}, "venue"),
        ({"symbol": "SOL", "condition": COND, "cross_venue_check": "yes"}, "cross_venue_check"),
        ({"symbol": "SOL", "condition": COND, "surprise": 1}, "arguments"),
        ("SOL", "arguments"),
        (None, "arguments"),
    ],
)
def test_malformed_arguments_raise_before_any_network_call(args, field):
    http = FakeHttp({})
    with pytest.raises(InputError) as e:
        run(args, http_get=http)
    assert e.value.field == field
    assert http.calls == []
    env = e.value.to_envelope("t")
    assert env["code"] == "INVALID_ARGUMENTS" and env["details"]["field"] == field and "example" in env["details"]


def test_injection_looking_condition_values_are_data_not_code():
    with pytest.raises(InputError):
        run({"symbol": "SOL", "condition": {"left": {"indicator": "__import__('os').system('x')"}, "op": "<", "right": {"value": 1}}}, http_get=FakeHttp({}))
