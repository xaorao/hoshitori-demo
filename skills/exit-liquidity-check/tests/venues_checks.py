"""Checks of ``exit_liquidity_check.venues``: pool filtering, the weighted median, quote parsing and the two fetchers, runnable against a mutant."""

from __future__ import annotations

import json
import time
import types
from typing import Any

from exit_liquidity_check.errors import SourceError
from exit_liquidity_check.registry import TOKENS, USDC_SOLANA
from exit_liquidity_check.transport import Caller
from tests.checks_support import Case
from tests.support import NOW, FakeTransport

BONK = TOKENS["BONK"]["address"]
WETH = TOKENS["ETH"]["address"]


def pool(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "pairAddress": "P1",
        "dexId": "raydium",
        "baseToken": {"address": BONK},
        "quoteToken": {"symbol": "SOL"},
        "liquidity": {"usd": 1_000_000},
        "volume": {"h24": 500_000},
        "priceUsd": "100",
    }
    base.update(kw)
    return base


def _source_error(fn: Any) -> str | None:
    try:
        fn()
    except SourceError as err:
        return err.reason
    return None


def _caller(transport: FakeTransport) -> Caller:
    return Caller(transport, now=NOW, deadline=time.monotonic() + 60.0, sleep=lambda s: None, cooldowns={})


def failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("dexscreener url", mod.DEXSCREENER_PAIRS, "https://api.dexscreener.com/token-pairs/v1/{chain}/{address}")
    c.eq("jupiter url", mod.JUPITER_QUOTE, "https://lite-api.jup.ag/swap/v1/quote")
    c.eq("tolerance", mod.PRICE_TOLERANCE, 0.03)
    c.eq("reference size", mod.REFERENCE_QUOTE_USD, 100.0)
    c.eq("quote slippage bps", mod.QUOTE_SLIPPAGE_BPS, 300)

    # weighted median
    wm = mod.weighted_median
    c.eq("wm odd equal weights", wm([(3.0, 1.0), (1.0, 1.0), (2.0, 1.0)]), 2.0)
    c.eq("wm heavy weight wins", wm([(1.0, 1.0), (2.0, 10.0)]), 2.0)
    c.eq("wm heavy low", wm([(1.0, 10.0), (2.0, 1.0)]), 1.0)
    c.eq("wm half reached exactly", wm([(1.0, 1.0), (2.0, 1.0)]), 1.0)
    c.eq("wm sorted by value not weight", wm([(5.0, 1.0), (1.0, 1.0), (3.0, 1.0)]), 3.0)
    c.eq("wm zero weights falls back to median", wm([(1.0, 0.0), (2.0, 0.0), (3.0, 0.0), (4.0, 0.0)]), 2.5)
    c.eq("wm zero weights odd", wm([(7.0, 0.0), (1.0, 0.0), (3.0, 0.0)]), 3.0)
    c.eq("wm negative weights fall back", wm([(1.0, -1.0), (9.0, -1.0), (5.0, -1.0)]), 5.0)
    c.eq("wm single", wm([(4.0, 2.0)]), 4.0)
    c.eq("wm fallback is a float", type(wm([(1, 0), (2, 0), (3, 0)])), float)
    c.eq("wm sum of weights just above zero", wm([(1.0, 1e-9), (2.0, 3e-9)]), 2.0)

    c.eq("wm weights summing to exactly one are weights", wm([(1.0, 0.5), (3.0, 0.5)]), 1.0)
    c.eq("wm needs half the weight, not a third", wm([(1.0, 2.0), (2.0, 2.0), (3.0, 2.0)]), 2.0)
    c.eq("wm unreachable half (nan weights) takes the largest value", wm([(5.0, float("nan")), (1.0, float("nan"))]), 5.0)

    # _number
    n = mod._number
    c.eq("number int", n(3), 3.0)
    c.eq("number zero", n(0), 0.0)
    c.eq("number negative", n(-2), -2.0)
    c.eq("number str", n("1.5"), 1.5)
    c.eq("number float", n(2.5), 2.5)
    for label, bad in (
        ("bool", True),
        ("false", False),
        ("none", None),
        ("list", [1]),
        ("dict", {}),
        ("text", "abc"),
        ("nan", float("nan")),
        ("nan text", "nan"),
        ("inf", float("inf")),
        ("-inf", float("-inf")),
        ("inf text", "inf"),
        ("empty", ""),
    ):
        c.eq(f"number rejects {label}", n(bad), None)

    # parse_pairs: shape
    for label, bad in (("dict", {}), ("none", None), ("text", "x"), ("number", 5)):
        c.eq(f"parse_pairs non-list {label}", _source_error(lambda bad=bad: mod.parse_pairs("BONK", bad)), "bad_response")
    empty = mod.parse_pairs("BONK", [])
    c.eq(
        "empty payload",
        (empty.pools, empty.rejected, empty.price_usd, empty.total_liquidity_usd, empty.total_volume_24h_usd),
        ((), (), None, None, None),
    )
    c.eq("empty payload identity", (empty.token, empty.chain, empty.address, empty.via), ("BONK", "solana", BONK, None))
    eth = mod.parse_pairs("ETH", [])
    c.eq("eth identity", (eth.chain, eth.address, eth.via), ("ethereum", WETH, "WETH on Ethereum"))

    # parse_pairs: every rejection in its order
    payload = [
        pool(pairAddress="ok"),
        pool(pairAddress="quote-side", baseToken={"address": "Other"}),
        pool(pairAddress="no-base", baseToken=None),
        pool(pairAddress="no-liq", liquidity={"usd": 0}),
        pool(pairAddress="neg-liq", liquidity={"usd": -5}),
        pool(pairAddress="text-liq", liquidity={"usd": "lots"}),
        pool(pairAddress="no-liq-key", liquidity=None),
        pool(pairAddress="no-vol", volume={"h24": 0}),
        pool(pairAddress="no-vol-key", volume=None),
        pool(pairAddress="no-price", priceUsd=None),
        pool(pairAddress="zero-price", priceUsd="0"),
        pool(pairAddress="junk-quote", quoteToken={"symbol": "SCAMCOIN"}),
        pool(pairAddress="no-quote-symbol", quoteToken=None),
        pool(pairAddress="far-price", priceUsd="104"),
        "not a pool",
        None,
        7,
        pool(pairAddress=None),
    ]
    depth = mod.parse_pairs("BONK", payload)
    reasons = dict(depth.rejected)
    c.eq("accepted pairs (equal depth: by pair)", [p.pair for p in depth.pools], ["?", "ok"])
    c.eq(
        "rejected order",
        [p for p, _ in depth.rejected],
        [
            "quote-side",
            "no-base",
            "no-liq",
            "neg-liq",
            "text-liq",
            "no-liq-key",
            "no-vol",
            "no-vol-key",
            "no-price",
            "zero-price",
            "junk-quote",
            "no-quote-symbol",
            "far-price",
        ],
    )
    c.eq("rejected is a tuple of tuples", (type(depth.rejected), type(depth.rejected[0])), (tuple, tuple))
    c.eq("reason quote side", reasons["quote-side"], "the token is the quote side, not the base")
    c.eq("reason missing base", reasons["no-base"], "the token is the quote side, not the base")
    for p in ("no-liq", "neg-liq", "text-liq", "no-liq-key"):
        c.eq(f"reason {p}", reasons[p], "no reported liquidity")
    for p in ("no-vol", "no-vol-key"):
        c.eq(f"reason {p}", reasons[p], "did not trade in the last 24 hours")
    for p in ("no-price", "zero-price"):
        c.eq(f"reason {p}", reasons[p], "no usable price")
    c.eq("reason junk quote", reasons["junk-quote"], "the other side (SCAMCOIN) is not a major quote asset")
    c.eq("reason missing quote symbol", reasons["no-quote-symbol"], "the other side (?) is not a major quote asset")
    c.eq("reason far price", reasons["far-price"], "price 104 is more than 3% from the median 100")

    # filter order: a pool failing several tests gets the first reason
    both = mod.parse_pairs(
        "BONK", [pool(pairAddress="x", liquidity={"usd": 0}, volume={"h24": 0}, priceUsd="0", quoteToken={"symbol": "ZZ"})]
    )
    c.eq("liquidity is judged first", both.rejected, (("x", "no reported liquidity"),))
    v = mod.parse_pairs("BONK", [pool(pairAddress="x", volume={"h24": 0}, priceUsd="0", quoteToken={"symbol": "ZZ"})])
    c.eq("volume is judged before price", v.rejected, (("x", "did not trade in the last 24 hours"),))
    p2 = mod.parse_pairs("BONK", [pool(pairAddress="x", priceUsd="0", quoteToken={"symbol": "ZZ"})])
    c.eq("price is judged before the quote asset", p2.rejected, (("x", "no usable price"),))
    c.eq(
        "everything rejected leaves no totals",
        (v.pools, v.price_usd, v.total_liquidity_usd, v.total_volume_24h_usd),
        ((), None, None, None),
    )

    # parse_pairs: accepted pool content
    one = mod.parse_pairs(
        "BONK",
        [
            pool(
                dexId=None,
                pairAddress="A",
                liquidity={"usd": "1234.567"},
                volume={"h24": 99.999},
                priceUsd="100",
                quoteToken={"symbol": "usdc"},
                baseToken={"address": BONK.lower()},
            )
        ],
    )
    c.eq(
        "accepted pool",
        one.pools[0].as_dict(),
        {"dex": "?", "pair": "A", "quote": "usdc", "liquidity_usd": 1234.57, "volume_24h_usd": 100.0, "price_usd": 100.0},
    )
    c.eq("single pool totals", (one.total_liquidity_usd, one.total_volume_24h_usd, one.price_usd), (1234.567, 99.999, 100.0))
    c.eq("base address case-insensitive", len(one.pools), 1)
    for quote in ("USDC", "USDT", "SOL", "WSOL", "WETH", "ETH", "WBTC", "DAI", "dai"):
        c.eq(f"major quote {quote}", len(mod.parse_pairs("BONK", [pool(quoteToken={"symbol": quote})]).pools), 1)

    # price tolerance: exactly 3 percent stays, beyond goes
    edge = mod.parse_pairs("BONK", [pool(pairAddress="a", priceUsd="100", volume={"h24": 900_000}), pool(pairAddress="b", priceUsd="103")])
    c.eq("exactly 3 percent off stays", sorted(p.pair for p in edge.pools), ["a", "b"])
    over = mod.parse_pairs(
        "BONK", [pool(pairAddress="a", priceUsd="100", volume={"h24": 900_000}), pool(pairAddress="b", priceUsd="103.01")]
    )
    c.eq("beyond 3 percent goes", ([p.pair for p in over.pools], [p for p, _ in over.rejected]), (["a"], ["b"]))
    c.eq("rejected for price carries the median", over.rejected[0][1], "price 103.01 is more than 3% from the median 100")
    below = mod.parse_pairs(
        "BONK", [pool(pairAddress="a", priceUsd="100", volume={"h24": 900_000}), pool(pairAddress="b", priceUsd="96.9")]
    )
    c.eq("below the median by more than 3 percent goes", [p for p, _ in below.rejected], ["b"])
    c.eq("price is the median of the accepted", over.price_usd, 100.0)

    # ordering and totals
    many = mod.parse_pairs(
        "BONK",
        [
            pool(pairAddress="z", liquidity={"usd": 500}, volume={"h24": 10}),
            pool(pairAddress="b", liquidity={"usd": 900}, volume={"h24": 20}),
            pool(pairAddress="a", liquidity={"usd": 900}, volume={"h24": 30}),
            pool(pairAddress="m", liquidity={"usd": 100}, volume={"h24": 40}),
        ],
    )
    c.eq("deepest first, ties by pair", [p.pair for p in many.pools], ["a", "b", "z", "m"])
    c.eq("totals", (many.total_liquidity_usd, many.total_volume_24h_usd), (2400.0, 100.0))
    c.eq("pools is a tuple", type(many.pools), tuple)

    # fractional figures are real figures, not zero
    frac = mod.parse_pairs("BONK", [pool(liquidity={"usd": 0.5}, volume={"h24": 0.5}, priceUsd="0.5")])
    c.eq("fractional liquidity, volume and price are accepted", len(frac.pools), 1)
    for what, obj in (
        ("Pool", one.pools[0]),
        ("PoolDepth", one),
        ("VenueQuote", mod.parse_quote("BONK", {"inAmount": "1", "outAmount": "1"}, {"inAmount": "1", "outAmount": "1"}, size_usd=1.0)),
    ):
        try:
            setattr(obj, "dex" if what == "Pool" else "token", "x")
        except (AttributeError, TypeError):
            pass
        else:
            c.failed.append(f"{what} must be frozen")
        c.eq(f"{what} has no __dict__ (slots)", hasattr(obj, "__dict__"), False)

    # PoolDepth.as_dict
    c.eq("source", many.source, f"dexscreener:solana/{BONK}")
    c.eq(
        "depth as_dict",
        over.as_dict(),
        {
            "token": "BONK",
            "chain": "solana",
            "address": BONK,
            "via": None,
            "deepest_pool": {
                "dex": "raydium",
                "pair": "a",
                "quote": "SOL",
                "liquidity_usd": 1_000_000.0,
                "volume_24h_usd": 900_000.0,
                "price_usd": 100.0,
            },
            "pools_used": 1,
            "pools_rejected": [{"pair": "b", "reason": "price 103.01 is more than 3% from the median 100"}],
            "price_usd": 100.0,
            "total_liquidity_usd": 1_000_000.0,
            "total_volume_24h_usd": 900_000.0,
            "source": f"dexscreener:solana/{BONK}",
        },
    )
    e = empty.as_dict()
    c.eq(
        "empty depth as_dict",
        (e["deepest_pool"], e["pools_used"], e["pools_rejected"], e["price_usd"], e["total_liquidity_usd"], e["total_volume_24h_usd"]),
        (None, 0, [], None, None, None),
    )
    r = mod.parse_pairs("BONK", [pool(liquidity={"usd": 1234.5678}, volume={"h24": 9.876})]).as_dict()
    c.eq("depth totals are rounded", (r["total_liquidity_usd"], r["total_volume_24h_usd"]), (1234.57, 9.88))

    # parse_quote
    ref = {"inAmount": "1000", "outAmount": "1000000"}
    sized = {"inAmount": "10000", "outAmount": "9500000", "priceImpactPct": "0.0123", "routePlan": [1, 2, 3]}
    q = mod.parse_quote("BONK", ref, sized, size_usd=10.0)
    c.eq(
        "quote fields",
        (q.token, q.size_usd, q.in_amount, q.out_usd, q.venue_price_impact, q.route_hops),
        ("BONK", 10.0, 10000, 9.5, 0.0123, 3),
    )
    c.near("quote cost", q.effective_cost, 0.05)
    c.eq(
        "quote as_dict",
        q.as_dict(),
        {
            "venue": "jupiter",
            "basis": "quoted",
            "size_usd": 10.0,
            "out_usd": 9.5,
            "effective_cost_pct": 5.0,
            "venue_price_impact_pct": 1.23,
            "route_hops": 3,
            "source": f"jupiter:{BONK}->USDC",
        },
    )
    c.eq("quote source", q.source, f"jupiter:{BONK}->USDC")
    better = mod.parse_quote("BONK", ref, {"inAmount": 10000, "outAmount": 10_500_000}, size_usd=10.0)
    c.eq("a better-than-reference fill costs zero, never negative", better.effective_cost, 0.0)
    c.eq("no impact and no route", (better.venue_price_impact, better.route_hops), (None, 0))
    c.eq("impact absent as_dict", better.as_dict()["venue_price_impact_pct"], None)
    c.eq("route not a list", mod.parse_quote("BONK", ref, {**sized, "routePlan": "xyz"}, size_usd=1.0).route_hops, 0)
    c.eq("empty route", mod.parse_quote("BONK", ref, {**sized, "routePlan": []}, size_usd=1.0).route_hops, 0)
    c.eq("impact text junk", mod.parse_quote("BONK", ref, {**sized, "priceImpactPct": "n/a"}, size_usd=1.0).venue_price_impact, None)
    c.eq(
        "int amounts accepted",
        mod.parse_quote("BONK", {"inAmount": 1, "outAmount": 2}, {"inAmount": 1, "outAmount": 2}, size_usd=1.0).in_amount,
        1,
    )
    mixed = mod.parse_quote("BONK", {"inAmount": "100", "outAmount": "300"}, {"inAmount": "400", "outAmount": "1000"}, size_usd=1.0)
    c.near("cost uses the reference rate (fair = 1200)", mixed.effective_cost, 1.0 - 1000 / 1200)
    for label, r_, s_ in (
        ("zero ref in", {"inAmount": "0", "outAmount": "5"}, sized),
        ("zero ref out", {"inAmount": "5", "outAmount": "0"}, sized),
        ("zero size in", ref, {**sized, "inAmount": "0"}),
        ("zero size out", ref, {**sized, "outAmount": "0"}),
        ("negative ref in", {"inAmount": "-1", "outAmount": "5"}, sized),
        ("negative ref out", {"inAmount": "5", "outAmount": "-1"}, sized),
        ("negative size in", ref, {**sized, "inAmount": "-1"}),
        ("negative size out", ref, {**sized, "outAmount": "-1"}),
        ("missing ref in", {"outAmount": "5"}, sized),
        ("missing size out", ref, {"inAmount": "5"}),
        ("bool amount", {"inAmount": True, "outAmount": "5"}, sized),
        ("float amount", {"inAmount": 1.5, "outAmount": "5"}, sized),
        ("text amount", {"inAmount": "abc", "outAmount": "5"}, sized),
        ("list reference", [1], sized),
        ("none reference", None, sized),
        ("list sized", ref, [1]),
        ("none sized", ref, None),
    ):
        c.eq(f"quote rejects {label}", _source_error(lambda r_=r_, s_=s_: mod.parse_quote("BONK", r_, s_, size_usd=1.0)), "bad_response")

    ones = mod.parse_quote("BONK", {"inAmount": "1", "outAmount": "1"}, {"inAmount": "1", "outAmount": "1"}, size_usd=1.0)
    c.eq("amounts of one are valid", (ones.in_amount, ones.out_usd, ones.effective_cost), (1, 1e-6, 0.0))
    odd = mod.parse_quote(
        "BONK",
        {"inAmount": "1000", "outAmount": "1000000"},
        {"inAmount": "10000", "outAmount": "9512345", "priceImpactPct": "0.0123456"},
        size_usd=10.123456,
    ).as_dict()
    c.eq(
        "quote as_dict rounding",
        (odd["size_usd"], odd["out_usd"], odd["effective_cost_pct"], odd["venue_price_impact_pct"]),
        (10.12, 9.51, 4.8766, 1.2346),
    )

    # _json
    c.eq("json ok", mod._json(b'{"a": 1}'), {"a": 1})
    c.eq("json bad", _source_error(lambda: mod._json(b"<html>")), "bad_response")
    c.eq("json empty", _source_error(lambda: mod._json(b"")), "bad_response")
    c.eq("json bad bytes", _source_error(lambda: mod._json(b"\xff\xfe")), "bad_response")

    # fetch_depth
    t = FakeTransport(dex=[[pool()]])
    caller = _caller(t)
    d = mod.fetch_depth("BONK", caller)
    c.eq("fetch_depth url", t.urls, [f"https://api.dexscreener.com/token-pairs/v1/solana/{BONK}"])
    c.eq("fetch_depth parses", [p.pair for p in d.pools], ["P1"])
    c.eq("fetch_depth records its source", [(r.source, r.method) for r in caller.records], [("dexscreener", "GET")])
    c.eq("fetch_depth ethereum url", _urls_of("ETH", mod), [f"https://api.dexscreener.com/token-pairs/v1/ethereum/{WETH}"])
    c.eq("fetch_depth bad json", _source_error(lambda: mod.fetch_depth("BONK", _caller(FakeTransport(dex=[b"<html>"])))), "bad_response")
    c.eq(
        "fetch_depth non-list", _source_error(lambda: mod.fetch_depth("BONK", _caller(FakeTransport(dex=[{"pairs": []}])))), "bad_response"
    )
    c.eq("fetch_depth 404", _source_error(lambda: mod.fetch_depth("BONK", _caller(FakeTransport(dex=[404])))), "bad_response")

    # fetch_quote
    rq = {"inAmount": "200000000", "outAmount": "100000000"}
    sq = {"inAmount": "2000000000", "outAmount": "950000000", "routePlan": [1]}
    t2 = FakeTransport(jup=[rq, sq])
    caller2 = _caller(t2)
    quote = mod.fetch_quote("BONK", 1000.0, 0.5, caller2)
    base = f"https://lite-api.jup.ag/swap/v1/quote?inputMint={BONK}&outputMint={USDC_SOLANA}"
    c.eq("fetch_quote urls", t2.urls, [f"{base}&amount=20000000&slippageBps=300", f"{base}&amount=200000000&slippageBps=300"])
    c.eq("fetch_quote records", [r.source for r in caller2.records], ["jupiter", "jupiter"])
    c.eq("fetch_quote result", (quote.size_usd, quote.in_amount, quote.out_usd, quote.route_hops), (1000.0, 2_000_000_000, 950.0, 1))
    c.eq("fetch_quote method", [r.method for r in caller2.records], ["GET", "GET"])
    c.near("fetch_quote cost", quote.effective_cost, 1.0 - 950 / 1000)
    t3 = FakeTransport(jup=[rq, sq])
    mod.fetch_quote("SOL", 10.0, 100.0, _caller(t3))
    c.eq("fetch_quote scales by the token's decimals", [u.split("amount=")[1].split("&")[0] for u in t3.urls], ["1000000000", "100000000"])
    t4 = FakeTransport(jup=[rq, sq])
    mod.fetch_quote("BONK", 1000.0, 1e12, _caller(t4))
    c.eq("fetch_quote never asks for a zero amount", next(u.split("amount=")[1].split("&")[0] for u in t4.urls), "1")
    for label, args in (
        ("ethereum token", ("ETH", 1000.0, 1.0)),
        ("zero price", ("BONK", 1000.0, 0.0)),
        ("negative price", ("BONK", 1000.0, -1.0)),
        ("zero size", ("BONK", 0.0, 1.0)),
        ("negative size", ("BONK", -1.0, 1.0)),
    ):
        tt = FakeTransport()
        try:
            mod.fetch_quote(*args, _caller(tt))
        except ValueError as err:
            c.eq(f"fetch_quote {label} message", str(err), "a quote needs a Solana token, a size and a price")
        else:
            c.failed.append(f"fetch_quote {label}: did not raise")
        c.eq(f"fetch_quote {label} makes no call", tt.urls, [])
    tiny = FakeTransport(jup=[rq, sq])
    mod.fetch_quote("BONK", 0.000001, 1.0, _caller(tiny))
    c.eq("fetch_quote tiny size floors at one unit", tiny.urls[1].split("amount=")[1].split("&")[0], "1")
    c.eq(
        "fetch_quote junk reply",
        _source_error(lambda: mod.fetch_quote("BONK", 1000.0, 0.5, _caller(FakeTransport(jup=[b"x"])))),
        "bad_response",
    )
    c.eq("fetch_quote json round trip", json.loads(json.dumps(quote.as_dict()))["venue"], "jupiter")
    return c.failed


def _urls_of(token: str, mod: types.ModuleType) -> list[str]:
    t = FakeTransport(dex=[[]])
    mod.fetch_depth(token, _caller(t))
    return t.urls
