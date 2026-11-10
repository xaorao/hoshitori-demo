"""Checks of ``exit_liquidity_check.skill``, runnable against a mutant: whole-envelope answers for hand-built scenarios (the expected
envelopes are in ``fixtures/skill_expected.json`` and were read figure by figure against hand arithmetic), the formatting helpers at their
boundaries, the states a real reply cannot produce (a missing volume or depth) and the sources tool."""

from __future__ import annotations

import datetime as dt
import json
import types
from pathlib import Path
from typing import Any

from exit_liquidity_check.errors import InputError
from exit_liquidity_check.registry import SUPPORTED, TOKENS
from exit_liquidity_check.venues import Pool, PoolDepth
from tests.checks_support import Case
from tests.skill_scenarios import REF, SCENARIOS, SIZED, pool, transport_for
from tests.support import NOW, FakeTransport, caller_for

EXPECTED: dict[str, Any] = json.loads((Path(__file__).parent / "fixtures" / "skill_expected.json").read_text())
KEY_POINTS_GAP = "pool liquidity unavailable; the exit cost cannot be modelled and is reported as null, not zero"


def _normalised(out: dict[str, Any]) -> dict[str, Any]:
    out = json.loads(json.dumps(out))
    for call in out["data"]["source_calls"]:
        call.pop("latency_ms")
        call["sha256"] = bool(call["sha256"])
        if "quote?" in call["url"]:
            call.pop("url")
    return out


def _no_network() -> Any:
    raise AssertionError("a check reached for the real network")


def _depth(mod: types.ModuleType, token: str, liq: float | None, vol: float | None, price: float | None, pools: int = 1) -> Any:
    spec = TOKENS[token]
    made = tuple(Pool("dex", f"P{n}", "USDC", liq or 1.0, vol or 1.0, price or 1.0) for n in range(pools))
    return PoolDepth(token, spec["chain"], spec["address"], spec["via"], made, (), price, liq, vol)


def _run_with_depth(mod: types.ModuleType, args: dict[str, Any], depth: Any, jup: list[Any] | None = None) -> dict[str, Any]:
    original = mod.fetch_depth
    mod.fetch_depth = lambda token, caller: depth
    try:
        return mod.run(args, now=NOW, caller=caller_for(FakeTransport(jup=jup)))
    finally:
        mod.fetch_depth = original


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    mod.default_transport = _no_network  # a mutant that drops the injected transport must fail, not call out

    c.eq("budget", mod.BUDGET_SECONDS, 30.0)
    c.eq(
        "verdict words",
        mod.VERDICT_WORDS,
        {
            "ok": "the exit fits inside the ceiling",
            "tight": "the exit is above the ceiling",
            "illiquid": "the exit is severe at this size",
            "unknown": "no exit cost can be stated",
        },
    )

    # formatting helpers at their boundaries
    for value, want in (
        (0.0, "$0.00"),
        (0.5, "$0.50"),
        (99.99, "$99.99"),
        (100.0, "$100"),
        (100.4, "$100"),
        (1234.6, "$1,235"),
        (5e6, "$5,000,000"),
    ):
        c.eq(f"_money({value})", mod._money(value), want)
    for share, want in (
        (0.0, "under 0.01%"),
        (0.00009, "under 0.01%"),
        (0.0001, "0.01%"),
        (0.0099, "0.99%"),
        (0.0999, "9.99%"),
        (0.1, "10.0%"),
        (0.4444, "44.4%"),
        (1.0, "100.0%"),
    ):
        c.eq(f"_pct({share})", mod._pct(share), want)
    for hours, want in (
        (0.0, "0 minutes"),
        (0.5, "30 minutes"),
        (0.999, "60 minutes"),
        (1.0, "1.0 hours"),
        (47.94, "47.9 hours"),
        (48.0, "2.0 days"),
        (72.0, "3.0 days"),
    ):
        c.eq(f"_hours({hours})", mod._hours(hours), want)
    for cost, ceiling, want in (
        (0.0, 0.03, "ok"),
        (0.03, 0.03, "ok"),
        (0.0300001, 0.03, "tight"),
        (0.06, 0.03, "tight"),
        (0.0600001, 0.03, "illiquid"),
        (0.9, 0.03, "illiquid"),
    ):
        c.eq(f"_grade({cost}, {ceiling})", mod._grade(cost, ceiling), want)

    # whole envelopes
    for name, (args, _, _) in SCENARIOS.items():
        out = mod.run(args, now=NOW, caller=caller_for(transport_for(name)))
        got = _normalised(out)
        want = EXPECTED[name]
        if got != want:
            for key in want:
                if got.get(key) != want[key]:
                    c.failed.append(f"{name}.{key}: got {got.get(key)!r}, want {want[key]!r}"[:600])
            if set(got) != set(want):
                c.failed.append(f"{name}: keys differ")
        if "latency_ms" not in (out["data"]["source_calls"] or [{"latency_ms": 0}])[0]:
            c.failed.append(f"{name}: a source call lost its latency")

    # the retry path: both attempts are recorded
    t = FakeTransport(dex=[429, 429, 429])
    out = mod.run({"token": "BONK", "size_usd": 1}, now=NOW, caller=caller_for(t))
    c.eq(
        "a rate limit is retried",
        [(x["source"], x["attempt"], x["error_kind"]) for x in out["data"]["source_calls"]],
        [("dexscreener", 1, "rate_limited"), ("dexscreener", 2, "rate_limited"), ("dexscreener", 3, "rate_limited")],
    )
    c.eq("a rate limit warns once", out["warnings"], ["dexscreener: the source is rate limiting requests"])

    # the entry without an injected caller
    out = mod.run({"token": "BONK", "size_usd": 5000}, now=NOW, transport=FakeTransport(dex=[[pool()]], jup=[REF, SIZED]))
    c.eq("transport argument is used", (out["status"], out["data"]["basis"], len(out["data"]["source_calls"])), ("ok", "quoted", 3))
    out = mod.run({"token": "BONK", "size_usd": 5000}, now=NOW, transport=FakeTransport(dex=[[pool()]], jup=[REF, SIZED]), budget_s=0.0)
    c.eq(
        "a spent budget is reported, not waited out",
        (out["status"], out["warnings"]),
        ("unavailable", ["dexscreener: the time budget for this call ran out"]),
    )
    out = mod.run({"token": "BONK"}, transport=FakeTransport(dex=[[pool()]]))
    stamped = dt.datetime.strptime(out["as_of"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.UTC)
    c.eq("as_of defaults to the present", abs((dt.datetime.now(dt.UTC) - stamped).total_seconds()) < 120, True)
    c.eq(
        "as_of keeps the given instant",
        mod.run({"token": "BONK"}, now=NOW, transport=FakeTransport(dex=[[pool()]]))["as_of"],
        "2026-11-09T12:00:00Z",
    )
    try:
        mod.run({"token": "DOGE"}, now=NOW, transport=FakeTransport())
    except InputError as err:
        c.eq("a refusal names the field", err.field, "token")
    else:
        c.failed.append("an unknown token was not refused")

    # states a real reply cannot produce, built through the typed API
    out = _run_with_depth(mod, {"token": "ETH", "size_usd": 5000}, _depth(mod, "ETH", 1e6, None, 3000.0))
    d = out["data"]
    c.eq(
        "no volume: partial",
        (out["status"], out["data_mode"], d["verdict"], d["basis"], d["confidence"]),
        ("partial", "live", "ok", "modelled", "moderate"),
    )
    c.eq("no volume: nothing about time", (d["hours_to_exit"], d["hourly_capacity_usd"]), (None, None))
    c.eq(
        "no volume: availability",
        out["availability"],
        {"pool_depth": "available", "volume": "unavailable", "executable_quote": "not_requested"},
    )
    c.eq("no volume: gap", d["gaps"], ["24 hour volume unavailable; the time to exit cannot be estimated"])
    c.eq(
        "no volume: inputs",
        [(i["name"], i["value"]) for i in d["inputs"]],
        [("liquidity_usd", 1e6), ("volume_24h_usd", None), ("price_usd", 3000.0)],
    )
    c.eq("no volume: no time in the key points", [p for p in out["summary"]["key_points"] if "volume" in p and "patient" in p], [])
    c.eq("no volume: the gap is a key point", out["summary"]["key_points"][-1], d["gaps"][0])

    out = _run_with_depth(mod, {"token": "ETH", "size_usd": 5000}, _depth(mod, "ETH", None, 5e5, 3000.0))
    d = out["data"]
    c.eq(
        "no depth: unavailable",
        (out["status"], out["data_mode"], d["verdict"], d["basis"], d["confidence"], d["estimate"]),
        ("unavailable", "live", "unknown", None, "none", None),
    )
    c.eq("no depth: time still answered", (d["hours_to_exit"], d["hourly_capacity_usd"]), (2.4, 2083.33))
    c.eq("no depth: ceilings are null", d["max_size_usd"], {"1pct": None, "3pct": None, "at_ceiling": None})
    c.eq("no depth: curve empty", d["curve"], [])
    c.eq("no depth: availability", out["availability"]["pool_depth"], "unavailable")
    c.eq(
        "no depth: headline",
        out["summary"]["headline"],
        "ETH: no exit cost could be stated; no source answered. This is not a liquidity rating.",
    )
    c.eq("no depth: gap", d["gaps"], [KEY_POINTS_GAP])
    c.eq(
        "no depth: inputs",
        [(i["name"], i["value"]) for i in d["inputs"]],
        [("liquidity_usd", None), ("volume_24h_usd", 5e5), ("price_usd", 3000.0)],
    )
    c.eq("no depth: time is a key point", out["summary"]["key_points"][0], "A patient exit at 10% of daily volume takes 2.4 hours.")

    out = _run_with_depth(mod, {"token": "ETH"}, _depth(mod, "ETH", None, 5e5, 3000.0))
    c.eq("no depth, no size: headline", out["summary"]["headline"], "ETH: no exit cost could be stated from what was read.")
    c.eq("no depth, no size: status", out["status"], "unavailable")
    c.eq("no depth, no size: capacity", out["data"]["hourly_capacity_usd"], 2083.33)

    out = _run_with_depth(mod, {"token": "BONK", "size_usd": 5000}, _depth(mod, "BONK", None, 5e5, 2e-5), [REF, SIZED])
    d = out["data"]
    c.eq(
        "quote without depth",
        (out["status"], d["verdict"], d["basis"], d["confidence"], d["estimate"], d["model_check"]),
        ("ok", "ok", "quoted", "high", None, None),
    )
    c.eq(
        "quote without depth: availability",
        out["availability"],
        {"pool_depth": "unavailable", "volume": "available", "executable_quote": "available"},
    )
    c.eq(
        "quote without depth: headline",
        out["summary"]["headline"],
        "BONK: exiting $5,000 costs 1.00% (quoted); the exit fits inside the ceiling.",
    )
    c.eq(
        "quote without depth: key points",
        out["summary"]["key_points"][:1],
        ["Jupiter quoted $4,950 back for the sale: a cost of 1.00%, fees included."],
    )

    out = _run_with_depth(mod, {"token": "ETH", "size_usd": 5000}, _depth(mod, "ETH", 1e6, 0.0, 3000.0))
    d = out["data"]
    c.eq("zero volume is a number, not a gap", (out["status"], d["gaps"], out["availability"]["volume"]), ("ok", [], "available"))
    c.eq("zero volume: no time, no capacity", (d["hours_to_exit"], d["hourly_capacity_usd"]), (None, None))
    c.eq("zero volume: no quiet-pool warning", out["warnings"], [])

    out = _run_with_depth(mod, {"token": "ETH", "size_usd": 5000}, _depth(mod, "ETH", 1e6, 5e5, 3000.0, pools=3))
    c.eq(
        "pool count in the note",
        out["data"]["notes"][1],
        "depth is the sum of 3 on-chain pools for the canonical contract; centralised-exchange depth and volume are not included",
    )

    out = _run_with_depth(mod, {"token": "BONK", "size_usd": 5000}, _depth(mod, "BONK", 1e6, 5e5, None))
    c.eq("a Solana depth without a price sizes no quote", out["data"]["notes"][0], "no price was read, so no quote was sized")
    c.eq("... and says the quote is unavailable", out["availability"]["executable_quote"], "unavailable")

    # a source error keeps its fixed text, whatever the upstream said
    out = mod.run(
        {"token": "BONK", "size_usd": 5000},
        now=NOW,
        caller=caller_for(FakeTransport(dex=[[pool()]], jup=[OSError("secret upstream text")])),
    )
    c.eq("a network error is reported in fixed words", out["warnings"], ["jupiter: the source could not be reached"])

    # the sources tool
    s = mod.run_sources({}, now=NOW)
    c.eq(
        "sources envelope",
        {k: s[k] for k in ("schema_version", "tool", "status", "data_mode", "as_of", "request", "availability", "warnings")},
        {
            "schema_version": "1.0",
            "tool": "exit_liquidity_sources",
            "status": "ok",
            "data_mode": "unknown",
            "as_of": "2026-11-09T12:00:00Z",
            "request": {},
            "availability": {"coverage": "available"},
            "warnings": [],
        },
    )
    c.eq(
        "sources headline",
        s["summary"],
        {"headline": "exit_liquidity_check covers 6 tokens by canonical contract, from 2 keyless sources.", "key_points": []},
    )
    c.eq(
        "sources tokens",
        s["data"]["tokens"],
        [{"token": t, "chain": TOKENS[t]["chain"], "contract": TOKENS[t]["address"], "measured_as": TOKENS[t]["via"]} for t in SUPPORTED],
    )
    c.eq(
        "sources list",
        s["data"]["sources"],
        [
            {"name": "dexscreener", "gives": "pool liquidity, 24 hour volume and price per pool", "keyless": True},
            {"name": "jupiter", "gives": "an executable quote for the exact size (Solana tokens only)", "keyless": True},
        ],
    )
    c.eq("sources accept and ignore extra keywords", mod.run_sources({}, now=NOW, transport=None)["status"], "ok")
    c.eq("sources default instant", mod.run_sources({})["as_of"][:2], "20")
    for label, bad in (("keys", {"x": 1}), ("list", []), ("none", None), ("text", "s"), ("empty list", [])):
        try:
            mod.run_sources(bad, now=NOW)
        except InputError as err:
            c.eq(f"sources refuses {label}", (err.field, str(err), err.example), ("arguments", "arguments: this tool takes none", {}))
        else:
            c.failed.append(f"sources did not refuse {label}")
    return c.failed
