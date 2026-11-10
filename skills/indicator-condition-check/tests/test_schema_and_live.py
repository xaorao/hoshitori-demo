"""The published input schema and the runtime validator must agree; one opt-in live smoke test."""
from __future__ import annotations

import os

import pytest

from indicator_condition_check import INPUT_SCHEMA, InputError, run
from indicator_condition_check.schema import validate_input

jsonschema = pytest.importorskip("jsonschema")

COND = {"left": {"indicator": "rsi"}, "op": "<", "right": {"value": 30}}
CASES = [
    ({"symbol": "SOL", "condition": COND}, True),
    ({"symbol": "SOL", "condition": COND, "timeframe": "4h", "lookback_bars": 50, "venue": "kraken", "cross_venue_check": True}, True),
    ({"symbol": "S", "condition": COND}, False),
    ({"symbol": "SOL"}, False),
    ({"condition": COND}, False),
    ({"symbol": "SOL", "condition": COND, "timeframe": "1w"}, False),
    ({"symbol": "SOL", "condition": COND, "lookback_bars": 49}, False),
    ({"symbol": "SOL", "condition": COND, "lookback_bars": 1001}, False),
    ({"symbol": "SOL", "condition": COND, "venue": "ftx"}, False),
    ({"symbol": "SOL", "condition": COND, "cross_venue_check": 1}, False),
    ({"symbol": "SOL", "condition": COND, "extra": 1}, False),
    ({"symbol": "0xabc", "condition": COND}, True),  # schema pattern allows it; both layers agree (letters+digits only)
    ({"symbol": "SOL-USD", "condition": COND}, False),
]


@pytest.mark.parametrize("args,valid", CASES)
def test_json_schema_and_runtime_validator_agree(args, valid):
    schema_ok = jsonschema.Draft202012Validator(INPUT_SCHEMA).is_valid(args)
    try:
        validate_input(args)
        runtime_ok = True
    except InputError:
        runtime_ok = False
    assert schema_ok == valid == runtime_ok


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the real exchange APIs")
def test_live_call_returns_an_honest_envelope():
    env = run({"symbol": "BTC", "timeframe": "1d", "cross_venue_check": True, "condition": {"all": [COND | {"op": "<", "right": {"value": 100}}, {"left": {"indicator": "close"}, "op": ">", "right": {"indicator": "sma", "period": 20}}]}})
    assert env["data_mode"] == "live" and env["status"] in ("ok", "partial")
    assert env["data"]["candles"]["forming_bar_excluded"] in (True, False)
    assert env["data"]["pair"]["venue_used"] in ("binance", "coinbase", "kraken", "bybit")


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("RUN_LIVE") != "1", reason="set RUN_LIVE=1 to call the real exchange APIs")
def test_live_coinbase_pagination_returns_more_than_one_page():
    env = run({"symbol": "BTC", "timeframe": "1d", "venue": "coinbase", "lookback_bars": 400, "condition": {"left": {"indicator": "close"}, "op": ">", "right": {"indicator": "sma", "period": 20}}})
    assert env["data_mode"] == "live" and env["data"]["candles"]["count"] > 450
    assert len(env["data"]["source_calls"]) >= 2 and env["data"]["candles"]["gap_count"] == 0
