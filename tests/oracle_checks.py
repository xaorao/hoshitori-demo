"""Checks of the settlement-pair finder (``hoshitori.oracle``), runnable against a mutant. The registry is the real one."""

from __future__ import annotations

import types
from datetime import timedelta
from typing import Any

from hoshitori import protocol
from hoshitori.registry import Registry, Want
from hoshitori.venue_data import InstrumentKind
from tests.protocol_checks import make
from tests.registry_checks import DELISTED, HALTED, PERP, T0, inst, lst, t


class World:
    """Two venues that agree on BTC, ETH (halted on the second), DOGE (delisted on the second) and LINK (listed twice on the first);
    SOL on one venue; a BTC pair quoted in another currency; a perpetual."""

    def __init__(self) -> None:
        self.registry = Registry()
        first = [inst("binance-spot", f"{n}USDT", n) for n in ("BTC", "ETH", "SOL", "DOGE", "LINK", "ADA")] + [
            inst("binance-spot", "LINK2USDT", "LINK")
        ]
        second = [
            inst("okx-spot", "BTC-USDT", "BTC"),
            inst("okx-spot", "ETH-USDT", "ETH", status=HALTED),
            inst("okx-spot", "DOGE-USDT", "DOGE", status=DELISTED),
            inst("okx-spot", "LINK-USDT", "LINK"),
            inst("okx-spot", "ADA-USDC", "ADA", "USDC"),
            inst("okx-spot", "XRP-USDT", "XRP"),
        ]
        self.registry.admit(lst("binance-spot", *first), scope="spot", known_at=t(0), capture_id="cap-1")
        self.registry.admit(lst("okx-spot", *second), scope="spot", known_at=t(1), capture_id="cap-2")
        self.registry.admit(lst("coinbase", inst("coinbase", "XRP-USD", "XRP", "USD")), scope="spot", known_at=t(1.5), capture_id="cap-4")
        self.registry.admit(
            lst("binance-usdm", inst("binance-usdm", "BTCUSDT", "BTC", kind=PERP)), scope="perpetual", known_at=t(2), capture_id="cap-3"
        )
        self.protocol = make(protocol, primary_venue="binance-spot", second_venue="okx-spot")
        self.at = t(10)

    def token(self, symbol: str) -> str:
        return self.registry.resolve_symbol(symbol, as_of=self.at).token_id

    def asset_of(self, venue: str, raw: str, base: str, kind: InstrumentKind | None = None) -> str:
        instrument = inst(venue, raw, base) if kind is None else inst(venue, raw, base, kind=kind)
        found = self.registry.join(instrument.instrument_id, Want()).asset
        assert found is not None
        return found.asset_id


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def check_pick(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    got = mod.pick(w.registry, w.protocol, w.token("BTC"), w.at)
    btc = w.asset_of("binance-spot", "BTCUSDT", "BTC")
    c.eq("BTC has a pair", got is not None, True)
    assert got is not None
    c.eq("its asset is the primary instrument's", got.asset_id, btc)
    c.eq("and its token", got.token_id, w.token("BTC"))
    c.eq("the primary", (got.primary.venue, got.primary.raw_id, got.primary_id), ("binance-spot", "BTCUSDT", got.primary.instrument_id))
    c.eq("the second", (got.second.venue, got.second.raw_id, got.second_id), ("okx-spot", "BTC-USDT", got.second.instrument_id))
    for symbol, why in (
        ("ETH", "the second oracle is halted"),
        ("DOGE", "the second oracle is delisted"),
        ("LINK", "the primary venue lists the token twice in the class (the token itself is ambiguous)"),
        ("SOL", "only one venue lists it"),
        ("ADA", "the second venue lists it in another currency"),
    ):
        token = w.token(symbol)
        c.eq(f"{symbol}: no pair, because {why}", mod.pick(w.registry, w.protocol, token, w.at), None)
    halted = mod.pick(w.registry, w.protocol, w.token("ETH"), w.at, trading=False)
    c.eq(
        "settlement does not need the markets to be trading: ETH's second oracle is halted",
        None if halted is None else halted.second.status.value,
        "HALTED",
    )
    c.eq(
        "a delisted second oracle is gone, not halted: no pair even for settlement",
        mod.pick(w.registry, w.protocol, w.token("DOGE"), w.at, trading=False),
        None,
    )
    c.eq("a duplicate listing is still no pair", mod.pick(w.registry, w.protocol, w.token("LINK"), w.at, trading=False), None)
    c.eq("a pair is frozen", mod.Pick.__dataclass_params__.frozen, True)
    c.eq("a token the registry never heard of", mod.pick(w.registry, w.protocol, "tk-unknown", w.at), None)
    c.eq("before the listings were known", mod.pick(w.registry, w.protocol, w.token("BTC"), T0 - timedelta(seconds=1)), None)
    c.eq("before the second venue's list was known", mod.pick(w.registry, w.protocol, w.token("BTC"), t(0.5)), None)
    swapped = make(protocol, primary_venue="okx-spot", second_venue="binance-spot")
    flipped = mod.pick(w.registry, swapped, w.token("BTC"), w.at)
    c.eq("with the venues swapped the primary is the other one", None if flipped is None else flipped.primary.venue, "okx-spot")
    c.eq("and the asset is that instrument's", None if flipped is None else flipped.asset_id, w.asset_of("okx-spot", "BTC-USDT", "BTC"))
    usdc = make(protocol, primary_venue="binance-spot", second_venue="okx-spot", quote_class="USDC")
    c.eq("another quote class finds no pair for BTC", mod.pick(w.registry, usdc, w.token("BTC"), w.at), None)
    return c.bad


def check_resolver(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    resolve = mod.resolver(w.registry, w.protocol, w.at)
    btc = w.asset_of("binance-spot", "BTCUSDT", "BTC")
    eth = w.asset_of("binance-spot", "ETHUSDT", "ETH")
    c.eq("BTC: settleable", resolve("BTC"), mod.Resolved(btc, True, True))
    c.eq("a lower-case spelling is the same symbol", resolve("btc"), mod.Resolved(btc, True, True))
    c.eq("ETH: an asset, unambiguous, but no oracle", resolve("ETH"), mod.Resolved(eth, True, False))
    c.eq("DOGE: no oracle", resolve("DOGE").has_oracle, False)
    c.eq("LINK: ambiguous", resolve("LINK"), mod.Resolved(None, False, False))
    c.eq("SOL: one source only, not resolved", resolve("SOL"), mod.Resolved(None, False, False))
    c.eq("XRP: resolved by two venues, but the primary venue does not list it", resolve("XRP"), mod.Resolved(None, True, False))
    c.eq("a symbol nobody lists", resolve("NOPE"), mod.Resolved(None, False, False))
    early = mod.resolver(w.registry, w.protocol, T0 - timedelta(seconds=1))
    c.eq("before anything was listed", early("BTC"), mod.Resolved(None, False, False))
    only_first = mod.resolver(w.registry, w.protocol, t(0.5))("BTC")
    c.eq("with one venue's list known, BTC is not resolved", only_first, mod.Resolved(None, False, False))
    return c.bad


def check_pick_for_asset(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World()
    btc = w.asset_of("binance-spot", "BTCUSDT", "BTC")
    got = mod.pick_for_asset(w.registry, w.protocol, btc, w.at)
    c.eq("the primary oracle's asset gives the pair", None if got is None else (got.asset_id, got.second.raw_id), (btc, "BTC-USDT"))
    c.eq(
        "the second venue's asset is not the primary of its token",
        mod.pick_for_asset(w.registry, w.protocol, w.asset_of("okx-spot", "BTC-USDT", "BTC"), w.at),
        None,
    )
    c.eq(
        "a perpetual asset has no pair",
        mod.pick_for_asset(w.registry, w.protocol, w.asset_of("binance-usdm", "BTCUSDT", "BTC", PERP), w.at),
        None,
    )
    c.eq(
        "an asset whose token has no second oracle",
        mod.pick_for_asset(w.registry, w.protocol, w.asset_of("binance-spot", "ETHUSDT", "ETH"), w.at),
        None,
    )
    eth = w.asset_of("binance-spot", "ETHUSDT", "ETH")
    settling = mod.pick_for_asset(w.registry, w.protocol, eth, w.at, trading=False)
    c.eq(
        "an asset whose second oracle halted still has its pair for settlement",
        None if settling is None else settling.second.raw_id,
        "ETH-USDT",
    )
    c.eq("an unknown asset", mod.pick_for_asset(w.registry, w.protocol, "as-unknown", w.at), None)
    c.eq("before the asset was known", mod.pick_for_asset(w.registry, w.protocol, btc, T0 - timedelta(seconds=1)), None)
    return c.bad


def check_pick_sealed(mod: types.ModuleType) -> list[str]:
    """The pair a forecast settles on is the one of its seal, ended only by what the registry knew by the end of its own window."""
    c = Case(mod)
    w = World()
    btc = w.asset_of("binance-spot", "BTCUSDT", "BTC")
    whole = mod.pick_sealed(w.registry, w.protocol, btc, w.at, w.at)
    c.eq(
        "a pair that stays listed is the pair of the seal",
        None if whole is None else (whole.asset_id, whole.second.raw_id),
        (btc, "BTC-USDT"),
    )
    eth = w.asset_of("binance-spot", "ETHUSDT", "ETH")
    halted = mod.pick_sealed(w.registry, w.protocol, eth, w.at, w.at)
    c.eq("a halted market is not a delisted one", None if halted is None else halted.second.raw_id, "ETH-USDT")
    c.eq(
        "an asset nobody knew at the seal has no pair", mod.pick_sealed(w.registry, w.protocol, btc, T0 - timedelta(seconds=1), w.at), None
    )
    c.eq("and an unknown asset has none", mod.pick_sealed(w.registry, w.protocol, "as-unknown", w.at, w.at), None)
    # the second venue delists BTC, and the registry learns of it at t(20)
    w.registry.admit(
        lst("okx-spot", inst("okx-spot", "BTC-USDT", "BTC", status=DELISTED)), scope="spot", known_at=t(20), capture_id="cap-late"
    )
    before = mod.pick_sealed(w.registry, w.protocol, btc, w.at, t(15))
    c.eq(
        "a delisting learned after the end of the window does not end the forecast",
        None if before is None else before.second.raw_id,
        "BTC-USDT",
    )
    c.eq("one learned by the end of the window does", mod.pick_sealed(w.registry, w.protocol, btc, w.at, t(25)), None)
    c.eq("and at the very moment it was learned", mod.pick_sealed(w.registry, w.protocol, btc, w.at, t(20)), None)
    c.eq("a second before it was learned it had not", mod.pick_sealed(w.registry, w.protocol, btc, w.at, t(19)) is not None, True)
    # an instrument that leaves the list, as the second venue's other markets do
    left = World()
    left.registry.admit(lst("okx-spot", inst("okx-spot", "XRP-USDT", "XRP")), scope="spot", known_at=t(20), capture_id="cap-late")
    c.eq(
        "an instrument that has left the list by the end of the window ends the forecast",
        mod.pick_sealed(left.registry, left.protocol, btc, left.at, t(25)),
        None,
    )
    c.eq("and not before", mod.pick_sealed(left.registry, left.protocol, btc, left.at, t(15)) is not None, True)
    return c.bad


def check_stable_basis(mod: types.ModuleType) -> list[str]:
    """Which instrument prices the stable coin in dollars: the first connected dollar-quoted USDT pair, as the registry knew it then."""
    from hoshitori.registry import Registry as R

    c = Case(mod)
    c.eq("the venues it is read on, in the order of preference", mod.BASIS_VENUES, ("coinbase", "kraken"))

    def registry(*lists: tuple[str, list]) -> Any:
        r = R()
        for n, (venue, rows) in enumerate(lists):
            r.admit(lst(venue, *rows), scope="spot", known_at=t(n), capture_id=f"cap-{n}")
        return r

    both = registry(
        ("kraken", [inst("kraken", "USDTZUSD", "USDT", "USD"), inst("kraken", "XBTUSDT", "XBT")]),
        ("coinbase", [inst("coinbase", "USDT-USD", "USDT", "USD")]),
    )
    at = t(10)
    c.eq("both listed: coinbase first", mod.stable_basis_instrument(both, {"coinbase", "kraken"}, at), ("coinbase", "USDT-USD"))
    c.eq("coinbase not connected: kraken", mod.stable_basis_instrument(both, {"kraken"}, at), ("kraken", "USDTZUSD"))
    c.eq("neither connected: none", mod.stable_basis_instrument(both, {"binance-spot"}, at), None)
    c.eq("no connected venue at all", mod.stable_basis_instrument(both, set(), at), None)
    c.eq("before either was listed: none", mod.stable_basis_instrument(both, {"coinbase", "kraken"}, t(-5)), None)
    one = registry(("kraken", [inst("kraken", "USDTZUSD", "USDT", "USD")]))
    c.eq("a token only one venue lists is not resolved: none", mod.stable_basis_instrument(one, {"kraken"}, at), None)
    halted = registry(
        ("kraken", [inst("kraken", "USDTZUSD", "USDT", "USD")]),
        ("coinbase", [inst("coinbase", "USDT-USD", "USDT", "USD", status=HALTED)]),
    )
    c.eq(
        "a halted pair is not used: the next venue", mod.stable_basis_instrument(halted, {"coinbase", "kraken"}, at), ("kraken", "USDTZUSD")
    )
    euro = registry(("kraken", [inst("kraken", "USDTEUR", "USDT", "EUR")]), ("coinbase", [inst("coinbase", "USDT-USD", "USDT", "USD")]))
    c.eq("a euro pair is not a dollar pair", mod.stable_basis_instrument(euro, {"kraken"}, at), None)
    twice = registry(
        ("coinbase", [inst("coinbase", "USDT-USD", "USDT", "USD"), inst("coinbase", "USDT-USD2", "USDT", "USD")]),
        ("kraken", [inst("kraken", "USDTZUSD", "USDT", "USD")]),
    )
    c.eq(
        "a venue that lists two dollar pairs of the token makes it ambiguous: none",
        mod.stable_basis_instrument(twice, {"coinbase", "kraken"}, at),
        None,
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_pick, check_resolver, check_pick_for_asset, check_pick_sealed, check_stable_basis):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
