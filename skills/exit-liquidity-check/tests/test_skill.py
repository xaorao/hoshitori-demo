"""The skill end to end on recorded and hostile replies: envelope shape, verdicts, confidence, failures, refusals."""

from __future__ import annotations

from typing import Any

import jsonschema
import pytest

from exit_liquidity_check import OUTPUT_SCHEMA, SOURCES_OUTPUT_SCHEMA, InputError, run, run_sources
from exit_liquidity_check.registry import SUPPORTED, TOKENS
from exit_liquidity_check.venues import parse_pairs
from tests.support import NOW, FakeTransport, caller_for, recorded_depth


def call(args: dict[str, Any], transport: FakeTransport | None = None) -> dict[str, Any]:
    t = transport or FakeTransport()
    out = run(args, now=NOW, caller=caller_for(t))
    jsonschema.validate(out, OUTPUT_SCHEMA)
    return out


def test_quoted_exit_outranks_the_model() -> None:
    out = call({"token": "BONK", "size_usd": 50_000})
    d = out["data"]
    assert (out["status"], out["data_mode"]) == ("ok", "live")
    assert (d["basis"], d["confidence"]) == ("quoted", "high")
    assert d["verdict"] in ("ok", "tight", "illiquid") and d["quote"]["basis"] == "quoted"
    assert d["model_check"]["quoted_cost_pct"] == d["quote"]["effective_cost_pct"]
    assert d["estimate"]["basis"] == "modelled"
    assert "Jupiter quoted" in " ".join(out["summary"]["key_points"])
    assert out["availability"] == {"pool_depth": "available", "volume": "available", "executable_quote": "available"}


def test_every_source_attempt_is_recorded_with_a_hash() -> None:
    calls = call({"token": "BONK", "size_usd": 1_000})["data"]["source_calls"]
    assert [c["source"] for c in calls] == ["dexscreener", "jupiter", "jupiter"]
    assert all(c["http_status"] == 200 and len(c["sha256"]) == 64 for c in calls)


def test_the_verdict_follows_the_ceiling_bands() -> None:
    cost = None
    for ceiling in (50, 1.0, 0.01):
        out = call({"token": "BONK", "size_usd": 50_000, "max_slippage_pct": ceiling})
        cost = out["data"]["quote"]["effective_cost_pct"]
        want = "ok" if cost <= ceiling else "tight" if cost <= 2 * ceiling else "illiquid"
        assert out["data"]["verdict"] == want
    assert cost is not None and cost > 0.01


def test_a_modelled_exit_for_a_token_without_a_keyless_quote() -> None:
    t = FakeTransport()
    # the recorded pools are BONK's; reading them as ETH's would fail the base-token check, so build ETH pools
    eth = TOKENS["ETH"]["address"]
    pools = [
        {
            "pairAddress": f"E{n}",
            "dexId": "uniswap",
            "baseToken": {"address": eth},
            "quoteToken": {"symbol": "USDC"},
            "liquidity": {"usd": 20_000_000},
            "volume": {"h24": 40_000_000},
            "priceUsd": "3000",
        }
        for n in range(2)
    ]
    t = FakeTransport(dex=[pools])
    out = call({"token": "ETH", "size_usd": 1_000_000}, t)
    d = out["data"]
    assert (d["basis"], d["confidence"], d["quote"]) == ("modelled", "moderate", None)
    assert t.used["jup"] == 0
    assert any("no keyless executable-quote venue" in n for n in d["notes"])
    assert out["availability"]["executable_quote"] == "not_requested"
    assert d["estimate"]["slippage_pct"] == pytest.approx(100 * (2 * 1e6 / 4e7) / (1 + 2 * 1e6 / 4e7), rel=1e-3)
    assert d["hours_to_exit"] == pytest.approx(24 * 1e6 / (8e7 * 0.10), rel=1e-3)


def test_past_the_model_validity_the_cost_is_called_a_floor() -> None:
    eth = TOKENS["ETH"]["address"]
    pool = {
        "pairAddress": "E",
        "dexId": "u",
        "baseToken": {"address": eth},
        "quoteToken": {"symbol": "USDC"},
        "liquidity": {"usd": 1_000_000},
        "volume": {"h24": 10_000_000},
        "priceUsd": "3000",
    }
    out = call({"token": "ETH", "size_usd": 400_000}, FakeTransport(dex=[[pool]]))
    assert out["data"]["confidence"] == "low"
    assert any("treat the modelled cost as a floor" in w for w in out["warnings"])
    assert out["data"]["estimate"]["within_model_validity"] is False


def test_no_size_gives_ceilings_without_a_verdict() -> None:
    out = call({"token": "BONK"})
    d = out["data"]
    assert d["verdict"] == "unknown" and d["estimate"] is None and d["quote"] is None
    assert d["max_size_usd"]["3pct"] and d["curve"] and d["hours_to_exit"] is None
    assert "no verdict" in out["summary"]["headline"]


def test_a_dead_depth_source_is_not_a_number() -> None:
    out = call({"token": "BONK", "size_usd": 50_000}, FakeTransport(dex=[503]))
    d = out["data"]
    assert (out["status"], out["data_mode"], d["verdict"]) == ("unavailable", "unknown", "unknown")
    assert d["estimate"] is None and d["quote"] is None and d["hours_to_exit"] is None
    assert d["pool_depth"] is None and d["max_size_usd"] == {"1pct": None, "3pct": None, "at_ceiling": None}
    assert all(i["value"] is None for i in d["inputs"])
    assert any("server error" in w for w in out["warnings"])
    assert "not a liquidity rating" in out["summary"]["headline"]


def test_a_junk_depth_body_is_a_bad_response() -> None:
    out = call({"token": "BONK", "size_usd": 1}, FakeTransport(dex=[b"<html>captcha</html>"]))
    assert out["status"] == "unavailable"
    assert any("could not be used" in w for w in out["warnings"])


def test_a_failed_quote_falls_back_to_the_model_and_says_so() -> None:
    out = call({"token": "BONK", "size_usd": 50_000}, FakeTransport(jup=[400]))
    d = out["data"]
    assert (out["status"], d["basis"], d["confidence"]) == ("ok", "modelled", "moderate")
    assert out["availability"]["executable_quote"] == "unavailable"
    assert any(w.startswith("jupiter:") for w in out["warnings"])


def test_rate_limit_retries_then_cools_down() -> None:
    t = FakeTransport(dex=[429, 429, 429])
    cool: dict[str, float] = {}
    out = run({"token": "BONK", "size_usd": 1}, now=NOW, caller=caller_for(t, cooldowns=cool))
    assert out["status"] == "unavailable" and "api.dexscreener.com" in cool
    assert t.used["dex"] == 3  # the first try and two retries
    again = run({"token": "BONK", "size_usd": 1}, now=NOW, caller=caller_for(FakeTransport(), cooldowns=cool))
    assert any("cool-down" in w for w in again["warnings"])


def test_no_pool_passes_the_filters() -> None:
    out = call({"token": "BONK", "size_usd": 1}, FakeTransport(dex=[[]]))
    assert out["status"] == "unavailable"
    assert any("no pool passed the filters" in g for g in out["data"]["gaps"])


def test_volume_alone_answers_the_time_but_not_the_cost() -> None:
    out = call({"token": "BONK", "size_usd": 1_000}, FakeTransport(dex=[recorded_depth()]))
    assert out["data"]["hours_to_exit"] is not None
    # a depth reading whose pools report volume but no liquidity is rejected as a whole, so no half answer exists to leak a zero
    broken = [{**p, "liquidity": {"usd": 0}} for p in recorded_depth()]
    out2 = call({"token": "BONK", "size_usd": 1_000}, FakeTransport(dex=[broken]))
    assert out2["data"]["estimate"] is None and out2["data"]["hours_to_exit"] is None


@pytest.mark.parametrize(
    "args,field",
    [
        ({}, "token"),
        ({"token": ""}, "token"),
        ({"token": 5}, "token"),
        ({"token": "DOGE"}, "token"),
        ({"token": "SOL", "size_usd": -1}, "size_usd"),
        ({"token": "SOL", "size_usd": 0}, "size_usd"),
        ({"token": "SOL", "size_usd": True}, "size_usd"),
        ({"token": "SOL", "size_usd": "5"}, "size_usd"),
        ({"token": "SOL", "size_usd": float("nan")}, "size_usd"),
        ({"token": "SOL", "size_usd": 1e12}, "size_usd"),
        ({"token": "SOL", "max_slippage_pct": 0}, "max_slippage_pct"),
        ({"token": "SOL", "max_slippage_pct": 51}, "max_slippage_pct"),
        ({"token": "SOL", "participation_pct": 101}, "participation_pct"),
        ({"token": "SOL", "colour": "red"}, "colour"),
    ],
)
def test_refusals_name_the_field_and_give_an_example(args: dict[str, Any], field: str) -> None:
    t = FakeTransport()
    with pytest.raises(InputError) as err:
        run(args, now=NOW, caller=caller_for(t))
    env = err.value.to_envelope("t")
    assert env["details"]["field"] == field and env["details"]["example"] == {"token": "SOL", "size_usd": 50000}
    assert t.urls == []  # nothing was sent for a malformed request


def test_a_non_object_is_refused() -> None:
    with pytest.raises(InputError):
        run(["SOL"], now=NOW, caller=caller_for(FakeTransport()))


def test_the_refusal_for_an_unknown_token_lists_what_is_covered() -> None:
    with pytest.raises(InputError) as err:
        run({"token": "doge"}, now=NOW, caller=caller_for(FakeTransport()))
    assert all(s in str(err.value) for s in SUPPORTED) and "doge" not in str(err.value).lower()


def test_token_is_case_insensitive_and_trimmed() -> None:
    assert call({"token": " bonk "})["request"]["token"] == "BONK"


def test_sources_tool() -> None:
    out = run_sources({}, now=NOW)
    jsonschema.validate(out, SOURCES_OUTPUT_SCHEMA)
    assert [t["token"] for t in out["data"]["tokens"]] == list(SUPPORTED)
    with pytest.raises(InputError):
        run_sources({"x": 1}, now=NOW)


def test_parse_is_deterministic() -> None:
    assert parse_pairs("BONK", recorded_depth()) == parse_pairs("BONK", recorded_depth())


def test_a_redirect_is_not_an_answer() -> None:
    out = call({"token": "BONK", "size_usd": 1}, FakeTransport(dex=[302]))
    assert out["status"] == "unavailable" and any("could not be used" in w for w in out["warnings"])
    assert all(c["error_kind"] == "bad_response" for c in out["data"]["source_calls"])
