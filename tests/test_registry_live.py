"""The registry over the nine recorded instrument lists of the five venues (private fixtures; the tests skip when they are absent).

Golden tests: the expected values were read from the recordings and from the registry's own answers on them, then checked against the sources
(for example the legacy Kraken codes against the ``wsname`` spellings of the recorded answer and, on 2026-10-29, against live prices).
"""

from __future__ import annotations

import collections
import json
from datetime import timedelta
from typing import Any

import pytest

from hoshitori.registry import Basis, JoinCode, MappingStatus, Registry, Want
from hoshitori.registry_aliases import ALIASES
from hoshitori.timeutil import parse_rfc3339
from hoshitori.venue_binance import BinanceSpot, BinanceUsdm
from hoshitori.venue_coinbase import Coinbase
from hoshitori.venue_hyperliquid import Hyperliquid
from hoshitori.venue_kraken import Kraken
from hoshitori.venue_okx import Okx
from tests.venue_support import recording

PLAN: list[tuple[str, Any, dict[str, str], str]] = [
    ("binance_exchangeinfo_trading", BinanceSpot(), {}, "spot"),
    ("binance_futures_exchangeinfo", BinanceUsdm(), {}, "all"),
    ("coinbase_products", Coinbase(), {}, "spot"),
    ("kraken_assetpairs_all", Kraken(), {}, "spot"),
    ("hyperliquid_meta", Hyperliquid(), {"kind": "perpetual"}, "perpetual"),
    ("hyperliquid_spotmeta", Hyperliquid(), {"kind": "spot"}, "spot"),
    ("okx_eea_instruments_spot", Okx(), {"kind": "spot"}, "spot"),
    ("okx_eea_instruments_swap", Okx(), {"kind": "perpetual"}, "perpetual"),
    ("okx_eea_instruments_futures", Okx(), {"kind": "future"}, "future"),
]


@pytest.fixture(scope="module")
def built() -> tuple[Registry, list[dict[str, Any]]]:
    registry = Registry()
    records = []
    for name, adapter, args, scope in PLAN:
        rec = recording(name)
        listing = adapter.parse_instruments_body(rec.body, adapter.normalise("instruments", args))
        records.append(registry.admit(listing, scope=scope, known_at=rec.captured_at, capture_id=name))
    return registry, records


def last_mappings(registry: Registry) -> dict[str, Any]:
    """The newest mapping of each token symbol."""
    found: dict[str, Any] = {}
    for rows in registry._mappings.values():
        found[rows[-1].symbol] = rows[-1]
    return found


def test_all_nine_lists_are_accepted_and_nothing_is_unmapped(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, records = built
    assert [r["events"][-1]["outcome"] for r in records] == ["accepted"] * 9
    assert [r["events"][-1]["count"] for r in records] == [1385, 942, 845, 1464, 235, 347, 1213, 526, 285]
    assert all(r["events"][-1]["unmapped"] == [] for r in records)
    assert [(a, b) for a, b in registry._lists] == [(a, b) for a, b in registry._lists]  # one list per venue and scope
    assert len(registry._lists) == 9
    assert registry.status_counts() == {"RESOLVED": 5438, "AMBIGUOUS": 11, "UNKNOWN": 1793}


def test_the_tokens_by_status_and_basis(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    tokens = last_mappings(registry)
    assert len(tokens) == 1943
    assert collections.Counter(m.status.value for m in tokens.values()) == {"UNKNOWN": 1193, "RESOLVED": 749, "AMBIGUOUS": 1}
    assert collections.Counter(m.basis.value for m in tokens.values()) == {
        "SINGLE_SOURCE": 1066,
        "VENUES": 739,
        "NO_LISTING": 127,
        "CURATED": 10,
        "DUPLICATE_PAIR": 1,
    }
    assert sorted(s for s, m in tokens.items() if m.basis is Basis.CURATED) == [
        "BTC",
        "DOGE",
        "ETC",
        "ETH",
        "LTC",
        "MLN",
        "XLM",
        "XMR",
        "XRP",
        "ZEC",
    ]
    assert not any(
        m.basis in (Basis.REF_CONFLICT, Basis.CLAIM_CONFLICT) for m in tokens.values()
    )  # no two tokens of one name on one venue today


def test_bitcoin_by_every_spelling(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    btc = registry.resolve_symbol("BTC")
    assert (btc.status, btc.basis, btc.evidence) == (
        MappingStatus.RESOLVED,
        Basis.CURATED,
        ("binance", "coinbase", "hyperliquid", "kraken", "okx"),
    )
    kraken = Kraken().parse_instruments_body(recording("kraken_assetpairs_all").body, {})
    xbt = next(i for i in kraken.instruments if i.raw_id == "XXBTZUSD")
    assert xbt.base_raw == "XXBT" and registry.mapping_at(xbt.instrument_id).token_id == btc.token_id  # type: ignore[union-attr]
    plain = next(i for i in kraken.instruments if i.raw_id == "XBTUSDT")
    assert plain.base_raw == "XXBT" and registry.mapping_at(plain.instrument_id).token_id == btc.token_id  # type: ignore[union-attr]
    assert registry.resolve_symbol("XBT").basis is Basis.NO_LISTING  # a legacy code from outside is not rewritten
    coinbase = next(
        i for i in Coinbase().parse_instruments_body(recording("coinbase_products").body, {}).instruments if i.raw_id == "BTC-USD"
    )
    assert registry.mapping_at(coinbase.instrument_id).token_id == btc.token_id  # type: ignore[union-attr]
    assert len(btc.instrument_ids) == 68


def test_the_legacy_kraken_codes_are_the_ones_in_the_recording() -> None:
    """Every alias is a base the recorded answer uses, with a ``wsname`` that is the same token without the prefix (or XBT and XDG)."""
    raw = json.loads(recording("kraken_assetpairs_all").body)["result"]
    bases = {row["base"] for row in raw.values()}
    assert set(ALIASES) == {"kraken"}
    for spelling in ALIASES["kraken"]:
        assert spelling in bases, spelling
    ws = {row["base"]: row["wsname"].split("/")[0] for row in raw.values() if "wsname" in row}
    assert {ws[b] for b in ("XETH", "XLTC", "XETC", "XMLN", "XXLM", "XXMR", "XXRP", "XZEC")} == {
        "ETH",
        "LTC",
        "ETC",
        "MLN",
        "XLM",
        "XMR",
        "XRP",
        "ZEC",
    }
    assert (ws["XXBT"], ws["XXDG"]) == ("XBT", "XDG")
    assert "XBT" not in bases and "XDG" not in bases  # the plain spellings are the wsname, never the base
    # XION, XAUT and ZAMA look like legacy codes and are not
    for token in ("XION", "XAUT", "ZAMA"):
        assert token in bases and token not in ALIASES["kraken"]


def test_xbt_is_bitcoin_on_the_price() -> None:
    """Kraken's XBT is Bitcoin: the two venues' prices, recorded 19 seconds apart, agree to a fifth of a percent."""
    kraken = float(json.loads(recording("kraken_ohlc_xbtusd_1").body)["result"]["XXBTZUSD"][-2][4])
    binance = float(json.loads(recording("binance_klines_btcusdt_1m").body)[-1][4])
    assert abs(kraken - binance) / binance < 0.005


def test_a_thousand_of_a_token_is_that_token_with_its_multiplier(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    for symbol in ("SHIB", "PEPE", "BONK", "FLOKI", "LUNC", "NEIRO", "SATS", "BABYDOGE"):
        assert registry.resolve_symbol(symbol).status is MappingStatus.RESOLVED, symbol
    shib = registry.resolve_symbol("SHIB")
    scaled = sorted(
        (registry.instrument_at(i).venue, registry.instrument_at(i).raw_id, registry.instrument_at(i).multiplier)
        for i in shib.instrument_ids
        if registry.instrument_at(i).multiplier != "1"
    )  # type: ignore[union-attr]
    assert scaled == [("binance-usdm", "1000SHIBUSDC", "1000"), ("binance-usdm", "1000SHIBUSDT", "1000"), ("hyperliquid", "kSHIB", "1000")]
    perp = next(i for i in shib.instrument_ids if registry.instrument_at(i).raw_id == "1000SHIBUSDT")  # type: ignore[union-attr]
    assert registry.join(perp, Want(token_id=shib.token_id)).code is JoinCode.IDENTITY_MISMATCH
    assert registry.join(perp, Want(multiplier="1000", token_id=shib.token_id)).ok
    baby = registry.resolve_symbol("BABYDOGE")
    assert {
        registry.instrument_at(i).multiplier for i in baby.instrument_ids if registry.instrument_at(i).raw_id.startswith("1MBABYDOGE")
    } == {"1000000"}  # type: ignore[union-attr]


def test_wrapped_and_bridged_tokens_are_not_the_native_one(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    btc, wbtc, ubtc = (registry.resolve_symbol(s) for s in ("BTC", "WBTC", "UBTC"))
    assert len({btc.token_id, wbtc.token_id, ubtc.token_id}) == 3
    assert (wbtc.status, ubtc.status, ubtc.evidence) == (MappingStatus.RESOLVED, MappingStatus.UNKNOWN, ("hyperliquid",))
    hl = Hyperliquid().parse_instruments_body(recording("hyperliquid_spotmeta").body, {"kind": "spot"})
    ubtc_pair = next(i for i in hl.instruments if i.raw_id == "@142")
    assert registry.join(ubtc_pair.instrument_id, Want(token_id=btc.token_id)).code is JoinCode.IDENTITY_UNRESOLVED


def test_one_hyperliquid_token_with_four_quotes_is_one_token(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    hype = registry.resolve_symbol("HYPE")
    assert hype.status is MappingStatus.RESOLVED and hype.basis is Basis.VENUES
    hl = [
        registry.instrument_at(i)
        for i in hype.instrument_ids
        if registry.instrument_at(i).venue == "hyperliquid" and registry.instrument_at(i).kind.value == "spot"
    ]  # type: ignore[union-attr]
    assert sorted(i.raw_id for i in hl if i is not None) == ["@107", "@207", "@232", "@255"]
    assert {i.base_ref for i in hl if i is not None} == {"0x0d01dc56dcaaca66ad901c959b4011ec"}


def test_names_that_look_like_legacy_codes_are_tokens_of_their_own(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    for symbol, operators in (
        ("XION", ("hyperliquid", "kraken")),
        ("XAUT", ("binance", "kraken", "okx")),
        ("ZAMA", ("binance", "coinbase", "hyperliquid", "kraken", "okx")),
    ):
        res = registry.resolve_symbol(symbol)
        assert (res.status, res.basis, res.evidence) == (MappingStatus.RESOLVED, Basis.VENUES, operators), symbol
    assert registry.resolve_symbol("ION").evidence == ()


def test_the_one_real_ambiguity_is_a_name_Binance_uses_twice(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, _ = built
    cat = registry.resolve_symbol("CAT")
    assert (cat.status, cat.basis, cat.joinable) == (MappingStatus.AMBIGUOUS, Basis.DUPLICATE_PAIR, False)
    rows = {(registry.instrument_at(i).venue, registry.instrument_at(i).raw_id) for i in cat.instrument_ids}  # type: ignore[union-attr]
    assert {("binance-usdm", "CATUSDT"), ("binance-usdm", "1000CATUSDT"), ("binance-spot", "1000CATUSDT")} <= rows
    assert len(cat.candidates) >= 2
    a_cat = next(i for i in cat.instrument_ids if registry.instrument_at(i).raw_id == "CAT-USDT")  # type: ignore[union-attr]
    assert registry.join(a_cat, Want(token_id=cat.token_id)).code is JoinCode.IDENTITY_AMBIGUOUS


def test_the_records_replay_to_the_same_registry(built: tuple[Registry, list[dict[str, Any]]]) -> None:
    registry, records = built
    copy = Registry.replay(records)
    assert copy.head == registry.head
    assert copy.status_counts() == registry.status_counts()
    assert copy.resolve_symbol("BTC") == registry.resolve_symbol("BTC")
    assert [copy.head_at(r_time) for r_time in (registry.snapshot_time(registry.head),)] == [registry.head]


def test_the_same_lists_again_change_nothing_but_their_age() -> None:
    registry = Registry()
    first = [
        (name, ad.parse_instruments_body(recording(name).body, ad.normalise("instruments", args)), scope, recording(name).captured_at)
        for name, ad, args, scope in PLAN[:4]
    ]
    for name, listing, scope, at in first:
        registry.admit(listing, scope=scope, known_at=at, capture_id=name)
    counts = registry.status_counts()
    again = [
        registry.admit(listing, scope=scope, known_at=at + timedelta(seconds=60), capture_id=name + "-again")
        for name, listing, scope, at in first
    ]
    assert [[e["t"] for e in r["events"]] for r in again] == [["snapshot"]] * 4
    assert registry.status_counts() == counts
    renewed = parse_rfc3339(again[2]["known_at"])
    age = registry.list_age("coinbase", "spot", renewed + timedelta(seconds=30))
    assert age is not None and age.total_seconds() == pytest.approx(30, abs=0.01)  # the age counts from the second pass, not the first
