"""Checks of the asset registry, runnable against a mutant of ``hoshitori.registry``.

Each function builds a small registry from made-up instruments, so every rule is exercised alone, and returns the failures it found.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori.venue_data import Instrument, InstrumentKind, InstrumentList, InstrumentStatus, Unmapped, quote_class

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
SPOT, PERP, FUTURE = InstrumentKind.SPOT, InstrumentKind.PERPETUAL, InstrumentKind.FUTURE
TRADING, HALTED, DELISTED = InstrumentStatus.TRADING, InstrumentStatus.HALTED, InstrumentStatus.DELISTED


def t(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def inst(
    venue: str,
    raw: str,
    base: str,
    quote: str = "USDT",
    *,
    kind: InstrumentKind = SPOT,
    status: InstrumentStatus = TRADING,
    mult: str = "1",
    ref: str | None = None,
    expiry: datetime | None = None,
    tick: str = "0.01",
) -> Instrument:
    return Instrument(
        venue,
        raw,
        kind,
        base,
        quote,
        quote_class(quote),
        mult,
        quote if kind is not SPOT else None,
        tick,
        "0.001",
        None,
        status,
        expiry,
        ref,
    )


def lst(venue: str, *instruments: Instrument, unmapped: tuple[Unmapped, ...] = ()) -> InstrumentList:
    return InstrumentList(venue, tuple(instruments), unmapped)


class Case:
    """Collects failures for one check function."""

    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def true(self, label: str, value: object) -> None:
        if not value:
            self.bad.append(f"{label}: expected a true value, got {value!r}")

    def raises(self, label: str, call: Callable[[], object], error: type[BaseException] | None = None, text: str | None = None) -> None:
        want = error or self.mod.RegistryError
        try:
            call()
        except want as exc:
            if text is not None and text not in str(exc):
                self.bad.append(f"{label}: raised {type(exc).__name__} without {text!r}: {exc}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: raised nothing")

    def registry(self, **kwargs: Any) -> Any:
        return self.mod.Registry(**kwargs)

    def admit(
        self, reg: Any, venue: str, scope: str, at: float, *instruments: Instrument, unmapped: tuple[Unmapped, ...] = ()
    ) -> dict[str, Any]:
        return reg.admit(lst(venue, *instruments, unmapped=unmapped), scope=scope, known_at=t(at), capture_id=f"cp-{venue}-{scope}-{at}")


def iid(instrument: Instrument) -> str:
    return instrument.instrument_id


# --- resolution --------------------------------------------------------------------------------------------------------------------------
def check_resolution(mod: types.ModuleType) -> list[str]:
    from hoshitori.ids import natural_id

    c = Case(mod)
    reg = c.registry()
    K, B, U, H = mod.MappingStatus, mod.Basis, "binance-usdm", "hyperliquid"
    kraken_btc, kraken_eth = inst("kraken", "XXBTZUSD", "XXBT", "ZUSD"), inst("kraken", "XETHZUSD", "XETH", "ZUSD")
    cb_btc, cb_eth, cb_sol = (
        inst("coinbase", "BTC-USD", "BTC", "USD"),
        inst("coinbase", "ETH-USD", "ETH", "USD"),
        inst("coinbase", "SOL-USD", "SOL", "USD"),
    )
    bn_btc, bn_sol = inst("binance-spot", "BTCUSDT", "BTC"), inst("binance-spot", "SOLUSDT", "SOL")
    c.eq("an empty registry has the hash of its name", reg.head, mod.hashlib.sha256(mod.SPEC.encode()).hexdigest())
    c.eq(
        "a symbol nobody lists", (reg.resolve_symbol("BTC").status.value, reg.resolve_symbol("BTC").basis.value), ("UNKNOWN", "NO_LISTING")
    )
    c.admit(reg, "kraken", "spot", 0, kraken_btc, kraken_eth)
    one = reg.resolve_symbol("BTC")
    c.eq(
        "one operator is not enough", (one.status, one.basis, one.joinable, one.evidence), (K.UNKNOWN, B.SINGLE_SOURCE, False, ("kraken",))
    )
    c.admit(reg, "coinbase", "spot", 1, cb_btc, cb_eth, cb_sol)
    btc = reg.resolve_symbol("BTC")
    c.eq(
        "two operators, one by a curated spelling",
        (btc.status, btc.basis, btc.joinable, btc.evidence),
        (K.RESOLVED, B.CURATED, True, ("coinbase", "kraken")),
    )
    c.eq("the token id is the symbol and its first series", btc.token_id, natural_id("tk", {"symbol": "BTC", "series": 1}))
    for spelling in ("btc", "Btc", "BTC"):
        c.eq(f"{spelling} is Bitcoin", reg.resolve_symbol(spelling).token_id, btc.token_id)
    c.eq(
        "a legacy code from outside is not rewritten",
        (reg.resolve_symbol("XBT").status, reg.resolve_symbol("XBT").basis, reg.resolve_symbol("XBT").token_id != btc.token_id),
        (K.UNKNOWN, B.NO_LISTING, True),
    )
    c.eq(
        "Ethereum by its Kraken code",
        (reg.resolve_symbol("ETH").status, reg.resolve_symbol("ETH").basis, reg.resolve_symbol("XETH").instrument_ids),
        (K.RESOLVED, B.CURATED, ()),
    )
    c.true("two tokens have two ids", reg.resolve_symbol("ETH").token_id != reg.resolve_symbol("SOL").token_id)
    c.eq("a symbol on one venue", (reg.resolve_symbol("SOL").status, reg.resolve_symbol("SOL").basis), (K.UNKNOWN, B.SINGLE_SOURCE))
    c.admit(reg, "binance-spot", "spot", 2, bn_btc, bn_sol)
    c.eq("two operators on the ticker alone", (reg.resolve_symbol("SOL").status, reg.resolve_symbol("SOL").basis), (K.RESOLVED, B.VENUES))
    c.eq("three operators", reg.resolve_symbol("BTC").evidence, ("binance", "coinbase", "kraken"))
    c.eq("the token holds every instrument", reg.instruments_of(btc.token_id), tuple(sorted(iid(i) for i in (kraken_btc, cb_btc, bn_btc))))
    mapping = reg.mapping_at(iid(kraken_btc))
    c.true("a mapping is kept", mapping is not None)
    if mapping is not None:
        c.eq(
            "the mapping of a legacy code",
            (mapping.token_id, mapping.status, mapping.basis, mapping.multiplier, mapping.known_to),
            (btc.token_id, K.RESOLVED, B.CURATED, "1", None),
        )
        c.eq("a mapping is reopened when its evidence changes", mapping.known_from, t(2))
        earlier = reg.mapping_at(iid(kraken_btc), t(1.5))
        c.eq(
            "the mapping as it was before the third operator",
            None if earlier is None else (earlier.evidence, earlier.known_from, earlier.known_to),
            (("coinbase", "kraken"), t(1), t(2)),
        )
        c.eq("before the second operator the symbol was unknown", reg.mapping_at(iid(kraken_btc), t(0.5)).status, K.UNKNOWN)  # type: ignore[union-attr]
        c.eq("before anything was known", reg.mapping_at(iid(kraken_btc), t(-1)), None)
    wrong = reg.join(iid(kraken_btc), mod.Want(quote_class="USDT", token_id=btc.token_id))
    c.eq("a Kraken USD pair against a USDT price series", (wrong.code, wrong.field), (mod.JoinCode.IDENTITY_MISMATCH, "quote_class"))
    c.eq("a Kraken USD pair against a USD series", reg.join(iid(kraken_btc), mod.Want(quote_class="USD", token_id=btc.token_id)).ok, True)
    c.eq("an instrument in the registry", reg.instrument_at(iid(cb_btc)), cb_btc)
    c.eq("an instrument that is not", reg.instrument_at("nothing"), None)

    solo = c.registry()
    c.admit(solo, "kraken", "spot", 0, inst("kraken", "ONEUSD", "ONE", "USD"))
    then = solo.resolve_symbol("ONE", as_of=t(1))
    c.eq(
        "a symbol with one instrument, as of a time",
        (then.status, then.basis, then.instrument_ids, then.evidence),
        (K.UNKNOWN, B.SINGLE_SOURCE, (iid(inst("kraken", "ONEUSD", "ONE", "USD")),), ("kraken",)),
    )

    # one operator with two venues is one source
    reg2 = c.registry()
    c.admit(reg2, U, "all", 0, inst(U, "DOGEUSDT", "DOGE", kind=PERP))
    c.admit(reg2, "binance-spot", "spot", 1, inst("binance-spot", "DOGEUSDT", "DOGE"))
    c.eq("two venues of one operator", reg2.resolve_symbol("DOGE").status, K.UNKNOWN)
    c.admit(reg2, "coinbase", "spot", 2, inst("coinbase", "DOGE-USD", "DOGE", "USD"))
    c.eq("a second operator", reg2.resolve_symbol("DOGE").status, K.RESOLVED)

    # a delisted instrument is not a source, and the legacy Kraken code of Dogecoin is Dogecoin
    reg3 = c.registry()
    c.admit(
        reg3,
        "kraken",
        "spot",
        0,
        inst("kraken", "XXDGZEUR", "XXDG", "ZEUR"),
        inst("kraken", "XXDGZUSD", "XXDG", "ZUSD"),
        inst("kraken", "XIONUSD", "XION", "USD"),
    )
    c.admit(
        reg3,
        "coinbase",
        "spot",
        1,
        inst("coinbase", "DOGE-USD", "DOGE", "USD", status=DELISTED),
        inst("coinbase", "XION-USD", "XION", "USD"),
    )
    c.eq("a delisted listing is not a source", reg3.resolve_symbol("DOGE").status, K.UNKNOWN)
    c.eq(
        "a delisted listing still maps to its token",
        reg3.mapping_at(iid(inst("coinbase", "DOGE-USD", "DOGE", "USD", status=DELISTED))) is not None,
        True,
    )
    c.eq(
        "XION is a token of its own, not Ion",
        (reg3.resolve_symbol("XION").status, reg3.resolve_symbol("XION").basis),
        (K.RESOLVED, B.VENUES),
    )
    c.true("a rule on the first letter would have turned XION into ION", reg3.resolve_symbol("ION").evidence == ())
    c.eq("two Kraken codes of one token", len(reg3.resolve_symbol("DOGE").instrument_ids), 3)
    c.admit(reg3, H, "perpetual", 2, inst(H, "kDOGS", "kDOGS", "USDC", kind=PERP, mult="1000"), inst(H, "DOGE", "DOGE", "USDC", kind=PERP))
    c.eq("a listing again", reg3.resolve_symbol("DOGE").status, K.RESOLVED)
    return c.bad


def check_multipliers_and_variants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = c.registry()
    J = mod.JoinCode
    U, H = "binance-usdm", "hyperliquid"
    shib_perp = inst(U, "1000SHIBUSDT", "1000SHIB", kind=PERP, mult="1000")
    shib_cb, shib_spot = inst("coinbase", "SHIB-USD", "SHIB", "USD"), inst("binance-spot", "SHIBUSDT", "SHIB")
    pepe_hl, pepe_spot, pepe_cb = (
        inst(H, "kPEPE", "kPEPE", "USDC", kind=PERP, mult="1000"),
        inst("binance-spot", "PEPEUSDT", "PEPE"),
        inst("coinbase", "PEPE-USD", "PEPE", "USD"),
    )
    c.admit(reg, U, "all", 0, shib_perp)
    c.admit(reg, "coinbase", "spot", 1, shib_cb, pepe_cb)
    c.admit(reg, "binance-spot", "spot", 2, shib_spot, pepe_spot)
    c.admit(reg, H, "perpetual", 3, pepe_hl)
    shib, pepe = reg.resolve_symbol("SHIB"), reg.resolve_symbol("PEPE")
    c.eq("a thousand SHIB is SHIB", (shib.status.value, len(shib.instrument_ids)), ("RESOLVED", 3))
    c.eq("a k name is the token", (pepe.status.value, iid(pepe_hl) in pepe.instrument_ids), ("RESOLVED", True))
    c.eq(
        "the k name and the digit prefix are not tokens",
        (reg.resolve_symbol("KPEPE").evidence, reg.resolve_symbol("1000SHIB").evidence),
        ((), ()),
    )

    r = reg.join(iid(shib_perp), mod.Want(token_id=shib.token_id))
    c.eq("SHIB evidence on 1000SHIB without the multiplier", (r.code, r.field), (J.IDENTITY_MISMATCH, "multiplier"))
    r = reg.join(iid(shib_perp), mod.Want(multiplier="1000", token_id=shib.token_id))
    c.eq("SHIB evidence on 1000SHIB with the multiplier", (r.code, r.asset is not None and r.asset.multiplier), (J.OK, "1000"))
    r = reg.join(iid(pepe_hl), mod.Want(token_id=pepe.token_id))
    c.eq("a kPEPE price used as the price of PEPE", (r.code, r.field), (J.IDENTITY_MISMATCH, "multiplier"))
    r = reg.join(iid(shib_cb), mod.Want(multiplier="1000", token_id=shib.token_id))
    c.eq("a thousand applied to a plain instrument", (r.code, r.field), (J.IDENTITY_MISMATCH, "multiplier"))
    r = reg.join(iid(shib_cb), mod.Want(token_id=pepe.token_id))
    c.eq("another token's evidence", (r.code, r.field), (J.IDENTITY_MISMATCH, "token"))
    r = reg.join(iid(shib_perp), mod.Want(multiplier="1000", kind=SPOT))
    c.eq("perpetual funding on a spot forecast", (r.code, r.field), (J.IDENTITY_MISMATCH, "kind"))
    r = reg.join(iid(shib_cb), mod.Want(quote_class="USDT"))
    c.eq("a USD instrument against a USDT series", (r.code, r.field), (J.IDENTITY_MISMATCH, "quote_class"))
    r = reg.join(iid(shib_cb), mod.Want(quote_class="USD", kind=SPOT, token_id=shib.token_id))
    c.eq("a join that matches on every field", (r.code, r.field, r.detail), (J.OK, None, ""))
    unknown = reg.join("nothing", mod.Want())
    c.eq(
        "an unknown instrument",
        (unknown.code, unknown.field, unknown.detail, unknown.asset),
        (J.UNKNOWN_INSTRUMENT, "instrument", "the registry does not list this instrument", None),
    )

    # wrapped and bridged names are tokens of their own
    reg2 = c.registry()
    c.admit(reg2, "coinbase", "spot", 0, inst("coinbase", "BTC-USD", "BTC", "USD"), inst("coinbase", "WBTC-USD", "WBTC", "USD"))
    c.admit(reg2, "binance-spot", "spot", 1, inst("binance-spot", "BTCUSDT", "BTC"), inst("binance-spot", "WBTCUSDT", "WBTC"))
    c.admit(reg2, H, "spot", 2, inst(H, "@142", "UBTC", "USDC", ref="0x" + "8f" * 16))
    btc, wbtc, ubtc = reg2.resolve_symbol("BTC"), reg2.resolve_symbol("WBTC"), reg2.resolve_symbol("UBTC")
    c.eq("a wrapped token is not the native one", len({btc.token_id, wbtc.token_id, ubtc.token_id}), 3)
    c.eq("a wrapped token on two venues", wbtc.status.value, "RESOLVED")
    c.eq("a bridged token on one venue", ubtc.status.value, "UNKNOWN")
    r = reg2.join(iid(inst(H, "@142", "UBTC", "USDC", ref="0x" + "8f" * 16)), mod.Want(token_id=btc.token_id))
    c.eq("a bridged price used as Bitcoin's", r.code, J.IDENTITY_UNRESOLVED)
    return c.bad


# --- admission, diffs, history ------------------------------------------------------------------------------------------------------------
def check_admission(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cfg = mod.RegistryConfig()
    c.eq(
        "default configuration",
        (cfg.max_shrink, cfg.min_list_for_shrink, cfg.max_unmapped_share, cfg.unmapped_allowance),
        (0.2, 10, 0.05, 10),
    )
    for kwargs in (
        {"max_shrink": 0.0},
        {"max_shrink": 1.0},
        {"max_unmapped_share": -0.1},
        {"max_unmapped_share": 1.0},
        {"min_list_for_shrink": 0},
        {"unmapped_allowance": -1},
    ):
        c.raises(f"configuration {kwargs}", lambda kw=kwargs: mod.RegistryConfig(**kw))
    for kwargs in (
        {"min_list_for_shrink": 1},
        {"unmapped_allowance": 0},
        {"max_unmapped_share": 0.0},
        {"max_shrink": 0.99},
        {"max_shrink": 0.01},
        {"max_unmapped_share": 0.99},
    ):
        try:
            mod.RegistryConfig(**kwargs)
        except mod.RegistryError:
            c.bad.append(f"configuration {kwargs} is valid")
    reg = c.registry()
    ten = [inst("coinbase", f"T{n}-USD", f"T{n}", "USD") for n in range(10)]
    first = c.admit(reg, "coinbase", "spot", 0, *ten)
    kinds = [e["t"] for e in first["events"]]
    c.eq(
        "a first list is all additions and a snapshot",
        (kinds[:-1] == ["added"] * 10, kinds[-1], first["events"][-1]["outcome"], first["events"][-1]["count"]),
        (True, "snapshot", "accepted", 10),
    )
    c.eq("the age of the list", reg.list_age("coinbase", "spot", t(30)), timedelta(seconds=30))
    c.eq("no list, no age", reg.list_age("coinbase", "perpetual", t(30)), None)
    c.eq("no list from another venue", reg.list_age("kraken", "spot", t(30)), None)

    def offer(
        *instruments: Instrument, venue: str = "coinbase", scope: str = "spot", at: float = 100, unmapped: tuple[Unmapped, ...] = ()
    ) -> dict[str, Any]:
        return c.admit(reg, venue, scope, at, *instruments, unmapped=unmapped)

    head = reg.head
    rejected = offer()
    c.eq("an empty list is rejected", (rejected["events"][-1]["outcome"], rejected["events"][-1]["reason"]), ("rejected", "EMPTY"))
    c.true("a rejection is a record that extends the chain", reg.head != head)
    c.eq("the instruments are still there", len(reg.instruments_of(reg.resolve_symbol("T1").token_id)), 1)
    c.eq("the list keeps its age after a rejection", reg.list_age("coinbase", "spot", t(130)), timedelta(seconds=130))
    c.eq("the rejection is remembered", reg.last_rejection("coinbase", "spot"), (t(100), "EMPTY"))
    shrunk = offer(*ten[:7], at=101)
    c.eq("a list that lost 30 percent is rejected", shrunk["events"][-1]["reason"], "SHRUNK")
    c.eq("a rejected list changes no row", [e["t"] for e in shrunk["events"]], ["snapshot"])
    accepted = offer(*ten[:8], at=102)
    c.eq("a list that lost exactly 20 percent is accepted", accepted["events"][-1]["outcome"], "accepted")
    c.eq(
        "the removed instruments",
        sorted(e["instrument_id"] for e in accepted["events"] if e["t"] == "removed"),
        sorted(iid(i) for i in ten[8:]),
    )
    c.eq("a removed instrument has no current row", (reg.instrument_at(iid(ten[9])), reg.instrument_at(iid(ten[9]), t(50))), (None, ten[9]))
    c.eq("an accepted list clears the standing rejection", reg.last_rejection("coinbase", "spot"), None)
    small = c.registry()
    c.admit(small, "kraken", "spot", 0, *[inst("kraken", f"S{n}", f"S{n}", "USD") for n in range(5)])
    c.eq(
        "a small list may shrink",
        c.admit(small, "kraken", "spot", 1, inst("kraken", "S0", "S0", "USD"))["events"][-1]["outcome"],
        "accepted",
    )

    reg2 = c.registry()
    base = [inst("kraken", f"U{n}", f"U{n}", "USD") for n in range(40)]
    c.admit(reg2, "kraken", "spot", 0, *base)
    more = tuple(Unmapped(f"X{n}", "bad") for n in range(10))
    c.eq("a few unmapped entries are normal", c.admit(reg2, "kraken", "spot", 1, *base, unmapped=more)["events"][-1]["outcome"], "accepted")
    c.eq("their reasons are kept", reg2.unmapped("kraken", "spot"), tuple((u.raw_id, u.reason) for u in more))
    surge = tuple(Unmapped(f"X{n}", "bad") for n in range(11))
    c.eq(
        "a surge of unmapped entries is rejected",
        c.admit(reg2, "kraken", "spot", 2, *base, unmapped=surge)["events"][-1]["reason"],
        "UNMAPPED_SURGE",
    )
    c.eq(
        "a rejected list does not replace the unmapped entries", reg2.unmapped("kraken", "spot"), tuple((u.raw_id, u.reason) for u in more)
    )
    big = c.registry()
    many = [inst("kraken", f"V{n}", f"V{n}", "USD") for n in range(300)]
    c.admit(big, "kraken", "spot", 0, *many)
    c.eq(
        "5 percent unmapped on a big list is accepted",
        c.admit(big, "kraken", "spot", 1, *many, unmapped=tuple(Unmapped(f"X{n}", "bad") for n in range(15)))["events"][-1]["outcome"],
        "accepted",
    )
    c.eq(
        "6 percent is not",
        c.admit(big, "kraken", "spot", 2, *many, unmapped=tuple(Unmapped(f"X{n}", "bad") for n in range(20)))["events"][-1]["reason"],
        "UNMAPPED_SURGE",
    )
    c.eq(
        "a first list with unmapped entries and no instruments is empty",
        c.admit(c.registry(), "kraken", "spot", 0, inst("kraken", "A1", "A1", "USD"), unmapped=surge)["events"][-1]["reason"],
        "UNMAPPED_SURGE",
    )

    reg3 = c.registry()
    a, b = inst("kraken", "A1", "A1", "USD"), inst("kraken", "B1", "B1", "USD")
    c.eq("a duplicate id", c.admit(reg3, "kraken", "spot", 0, a, a)["events"][-1]["reason"], "DUPLICATE")
    c.eq(
        "a foreign instrument",
        c.admit(reg3, "kraken", "spot", 1, a, inst("coinbase", "B-USD", "B", "USD"))["events"][-1]["reason"],
        "VENUE_MISMATCH",
    )
    c.eq(
        "a perpetual in a spot list",
        c.admit(reg3, "kraken", "spot", 2, a, inst("kraken", "P1", "P1", "USD", kind=PERP))["events"][-1]["reason"],
        "SCOPE_MISMATCH",
    )
    c.eq(
        "a list of the all scope takes every kind",
        c.admit(reg3, "kraken", "all", 3, a, inst("kraken", "P1", "P1", "USD", kind=PERP))["events"][-1]["outcome"],
        "accepted",
    )
    c.eq("an instrument in two scopes", c.admit(reg3, "kraken", "spot", 4, a, b)["events"][-1]["reason"], "SCOPE_OVERLAP")
    c.eq(
        "another venue may reuse a raw id",
        c.admit(reg3, "coinbase", "spot", 5, inst("coinbase", "A1", "A1", "USD"))["events"][-1]["outcome"],
        "accepted",
    )
    c.raises("an unknown scope", lambda: reg3.admit(lst("kraken", a), scope="everything", known_at=t(9), capture_id="x"), text="scope")
    c.raises("no capture id", lambda: reg3.admit(lst("kraken", a), scope="spot", known_at=t(9), capture_id=""), text="capture")
    c.raises("no venue", lambda: reg3.admit(lst("", a), scope="spot", known_at=t(9), capture_id="x"), text="venue")
    c.true("a rejected record still names the list it refused", reg3.last_rejection("kraken", "spot") is not None)
    return c.bad


def check_history(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = c.registry()
    K = mod.MappingStatus
    a = inst("kraken", "AAAUSD", "AAA", "USD", tick="0.01")
    b = inst("kraken", "BBBUSD", "BBB", "USD")
    cb = inst("coinbase", "AAA-USD", "AAA", "USD")
    c.admit(reg, "kraken", "spot", 0, a, b)
    c.admit(reg, "coinbase", "spot", 1, cb)
    a2 = inst("kraken", "AAAUSD", "AAA", "USD", tick="0.001")
    rec = c.admit(reg, "kraken", "spot", 2, a2, b)
    c.eq("one changed instrument", [(e["t"], e.get("instrument", {}).get("raw_id")) for e in rec["events"][:-1]], [("changed", "AAAUSD")])
    c.eq("the old row as it was", reg.instrument_at(iid(a), t(1)), a)
    c.eq("the new row", reg.instrument_at(iid(a)), a2)
    c.eq("the boundary belongs to the new row", reg.instrument_at(iid(a), t(2)), a2)
    c.eq("just before the boundary", reg.instrument_at(iid(a), t(2) - timedelta(milliseconds=1)), a)
    rows = reg._rows[iid(a)]
    c.eq(
        "two rows, the first closed when the second opened",
        [(r.known_from, r.known_to, r.generation) for r in rows],
        [(t(0), t(2), 1), (t(2), None, 1)],
    )
    c.eq("a changed row keeps its token", reg.mapping_at(iid(a)).status, K.RESOLVED)  # type: ignore[union-attr]
    same = c.admit(reg, "kraken", "spot", 3, a2, b)
    c.eq("the same list again is a snapshot and nothing else", [e["t"] for e in same["events"]], ["snapshot"])
    c.eq("and it renews the age", reg.list_age("kraken", "spot", t(10)), timedelta(seconds=7))

    # a status change is a new row; delisting is not a removal
    halted = inst("kraken", "BBBUSD", "BBB", "USD", status=HALTED)
    c.admit(reg, "kraken", "spot", 4, a2, halted)
    c.eq("a halted instrument stays", reg.instrument_at(iid(b)).status, HALTED)  # type: ignore[union-attr]
    c.eq("a halted instrument is still a live listing", reg.resolve_symbol("BBB").evidence, ("kraken",))

    # removing, then listing again, is a new generation and a new row; nothing is carried over
    c.admit(reg, "kraken", "spot", 5, a2)
    c.eq("a removed instrument", reg.instrument_at(iid(b)), None)
    c.eq("a removed instrument's token has no listing", reg.resolve_symbol("BBB").basis.value, "NO_LISTING")
    c.eq("a removed instrument's mapping is closed", reg.mapping_at(iid(b)), None)
    c.eq(
        "but it is there as it was", (reg.instrument_at(iid(b), t(4.5)).status, reg.mapping_at(iid(b), t(4.5)).status), (HALTED, K.UNKNOWN)
    )  # type: ignore[union-attr]
    c.admit(reg, "kraken", "spot", 6, a2, b)
    c.eq("it came back as a new generation", [r.generation for r in reg._rows[iid(b)]], [1, 1, 2])
    c.eq(
        "a name that came back after being removed is a new series",
        reg.resolve_symbol("BBB").token_id != reg.mapping_at(iid(b), t(0.5)).token_id,
        True,
    )  # type: ignore[union-attr]
    return c.bad


# --- ambiguity, claims, the RYO symbol -----------------------------------------------------------------------------------------------------
def check_ambiguity(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    K, B, J = mod.MappingStatus, mod.Basis, mod.JoinCode
    H = "hyperliquid"
    ref_a, ref_b = "0x" + "a" * 32, "0x" + "b" * 32

    # one venue, one name, one token paired with four quotes: not ambiguous
    reg = c.registry()
    hype = [inst(H, f"@{n}", "HYPE", q, ref=ref_a) for n, q in ((107, "USDC"), (207, "USDT0"), (232, "USDH"), (255, "USDE"))]
    c.admit(reg, H, "spot", 0, *hype)
    c.admit(reg, "binance-spot", "spot", 1, inst("binance-spot", "HYPEUSDT", "HYPE"))
    c.eq("one token, four quotes", (reg.resolve_symbol("HYPE").status, reg.resolve_symbol("HYPE").basis), (K.RESOLVED, B.VENUES))

    # one venue, one name, two tokens
    reg = c.registry()
    c.admit(reg, H, "spot", 0, inst(H, "@1", "PUMP", "USDC", ref=ref_a), inst(H, "@2", "PUMP", "USDC", ref=ref_b))
    c.admit(reg, "binance-spot", "spot", 1, inst("binance-spot", "PUMPUSDT", "PUMP"))
    pump = reg.resolve_symbol("PUMP")
    c.eq(
        "two tokens of one name on one venue",
        (pump.status, pump.basis, len(pump.candidates), pump.joinable),
        (K.AMBIGUOUS, B.REF_CONFLICT, 2, False),
    )
    r = reg.join(iid(inst("binance-spot", "PUMPUSDT", "PUMP")), mod.Want())
    c.eq("a join on an ambiguous symbol", (r.code, r.field), (J.IDENTITY_AMBIGUOUS, "token"))
    r = reg.join(iid(inst(H, "@1", "PUMP", "USDC", ref=ref_a)), mod.Want(token_id=pump.token_id))
    c.eq("the same on the other venue's instrument", r.code, J.IDENTITY_AMBIGUOUS)
    c.eq("a candidate for each token", len(set(pump.candidates)), 2)
    c.admit(reg, H, "spot", 2, inst(H, "@1", "PUMP", "USDC", ref=ref_a))
    c.eq("the conflict ends when one of them goes", reg.resolve_symbol("PUMP").status, K.RESOLVED)

    # one venue, one pair, two instruments
    reg = c.registry()
    c.admit(reg, "kraken", "spot", 0, inst("kraken", "AAAUSD", "AAA", "USD"), inst("kraken", "AAAZUSD", "AAA", "ZUSD"))
    c.admit(reg, "coinbase", "spot", 1, inst("coinbase", "AAA-USD", "AAA", "USD"))
    two = reg.resolve_symbol("AAA")
    c.eq("a pair listed twice with different spellings of the quote is two pairs", two.status, K.RESOLVED)
    reg = c.registry()
    c.admit(reg, "kraken", "spot", 0, inst("kraken", "AAAUSD", "AAA", "USD"), inst("kraken", "AAAUSD2", "AAA", "USD"))
    c.admit(reg, "coinbase", "spot", 1, inst("coinbase", "AAA-USD", "AAA", "USD"))
    two = reg.resolve_symbol("AAA")
    c.eq("one pair under two ids", (two.status, two.basis, len(two.candidates)), (K.AMBIGUOUS, B.DUPLICATE_PAIR, 2))
    reg = c.registry()
    soon, later = t(86400), t(90000)
    c.admit(
        reg,
        "okx",
        "future",
        0,
        inst("okx", "BTC-USD-A", "BTC", "USD", kind=FUTURE, expiry=soon),
        inst("okx", "BTC-USD-B", "BTC", "USD", kind=FUTURE, expiry=later),
    )
    c.admit(reg, "coinbase", "spot", 1, inst("coinbase", "BTC-USD", "BTC", "USD"))
    c.eq("dated futures of one pair are not a duplicate", reg.resolve_symbol("BTC").status, K.RESOLVED)
    c.admit(
        reg,
        "okx",
        "perpetual",
        2,
        inst("okx", "BTC-USD-SWAP", "BTC", "USD", kind=PERP),
        inst("okx", "BTC-USD-SWAP2", "BTC", "USD", kind=PERP),
    )
    c.eq("two perpetuals of one pair are", reg.resolve_symbol("BTC").basis, B.DUPLICATE_PAIR)
    reg = c.registry()
    c.admit(reg, "okx", "all", 0, inst("okx", "BTC-USD", "BTC", "USD"), inst("okx", "BTC-USD-SWAP", "BTC", "USD", kind=PERP))
    c.admit(reg, "coinbase", "spot", 1, inst("coinbase", "BTC-USD", "BTC", "USD"))
    c.eq("a spot and a perpetual of one pair are not a duplicate", reg.resolve_symbol("BTC").status, K.RESOLVED)

    # claims: sources that name the contract
    reg = c.registry()
    claim = mod.Claim
    c.admit(reg, "binance-spot", "spot", 0, inst("binance-spot", "ZZZUSDT", "ZZZ"))
    c.eq("a symbol with one listing", reg.resolve_symbol("ZZZ").status, K.UNKNOWN)
    reg.add_claims([claim("ZZZ", "labels", "eip155:1", "0x" + "ab" * 20)], known_at=t(1))
    zzz = reg.resolve_symbol("ZZZ")
    c.eq("a listing and a contract", (zzz.status, zzz.basis, zzz.evidence), (K.RESOLVED, B.CONTRACT, ("binance", "claim:labels")))
    reg.add_claims([claim("ZZZ", "scanner", "eip155:1", "0x" + "ab" * 20)], known_at=t(2))
    c.eq("two sources agreeing on the contract", reg.resolve_symbol("ZZZ").evidence, ("binance", "claim:labels", "claim:scanner"))
    reg.add_claims([claim("ZZZ", "other", "eip155:137", "0x" + "cd" * 20)], known_at=t(3))
    split = reg.resolve_symbol("ZZZ")
    c.eq(
        "a symbol assigned to two contracts",
        (split.status, split.basis, len(split.candidates), split.joinable),
        (K.AMBIGUOUS, B.CLAIM_CONFLICT, 2, False),
    )
    c.eq(
        "the instrument is quarantined from the join",
        reg.join(iid(inst("binance-spot", "ZZZUSDT", "ZZZ")), mod.Want()).code,
        J.IDENTITY_AMBIGUOUS,
    )
    c.eq(
        "the mapping history shows the conflict began at the third claim",
        (
            reg.mapping_at(iid(inst("binance-spot", "ZZZUSDT", "ZZZ")), t(2.5)).status,
            reg.mapping_at(iid(inst("binance-spot", "ZZZUSDT", "ZZZ"))).status,
        ),
        (K.RESOLVED, K.AMBIGUOUS),
    )  # type: ignore[union-attr]
    one = reg.resolve_symbol("zzz", chain_id="eip155:1", contract_address="0x" + "AB" * 20)
    c.eq(
        "a contract picks its token out of the conflict",
        (one.status, one.basis, one.token_id in split.candidates, one.instrument_ids),
        (K.RESOLVED, B.CONTRACT, True, ()),
    )
    other = reg.resolve_symbol("ZZZ", chain_id="eip155:137", contract_address="0x" + "cd" * 20)
    c.true("the other contract is another token", other.token_id != one.token_id and other.token_id in split.candidates)
    none = reg.resolve_symbol("ZZZ", chain_id="eip155:56", contract_address="0x" + "ef" * 20)
    c.eq("a contract nobody claims", (none.status, none.joinable), (K.UNKNOWN, False))
    c.raises("a contract needs its chain", lambda: reg.resolve_symbol("ZZZ", contract_address="0x" + "ab" * 20))
    c.raises("a chain needs its contract", lambda: reg.resolve_symbol("ZZZ", chain_id="eip155:1"))
    c.raises(
        "a contract is read as of now",
        lambda: reg.resolve_symbol("ZZZ", chain_id="eip155:1", contract_address="0x" + "ab" * 20, as_of=t(2)),
    )
    c.eq("claims alone are no listing", c.registry().resolve_symbol("ZZZ").basis, B.NO_LISTING)
    reg4 = c.registry()
    reg4.add_claims([claim("QQQ", "labels", "eip155:1", "0x" + "ab" * 20)], known_at=t(0))
    c.eq("a claim without a listing", (reg4.resolve_symbol("QQQ").status, reg4.resolve_symbol("QQQ").basis), (K.UNKNOWN, B.NO_LISTING))
    nolist = reg4.resolve_symbol("QQQ", chain_id="eip155:1", contract_address="0x" + "ab" * 20)
    c.eq(
        "a contract lookup without a listing",
        (nolist.status, nolist.basis, nolist.token_id),
        (K.UNKNOWN, B.NO_LISTING, mod.natural_id("tk", {"symbol": "QQQ", "series": 1})),
    )
    c.raises("a claim twice", lambda: reg4.add_claims([claim("QQQ", "labels", "eip155:1", "0x" + "ab" * 20)], known_at=t(1)))
    reg5 = c.registry()
    c.raises("no claims", lambda: reg5.add_claims([], known_at=t(0)))
    for label, args in (
        ("a lower-case symbol", ("qqq", "s", "eip155:1", "0x" + "ab" * 20)),
        ("no symbol", ("", "s", "eip155:1", "0x" + "ab" * 20)),
        ("no source", ("QQQ", "", "eip155:1", "0x" + "ab" * 20)),
        ("a padded source", ("QQQ", " s", "eip155:1", "0x" + "ab" * 20)),
        ("a long source", ("QQQ", "s" * 81, "eip155:1", "0x" + "ab" * 20)),
        ("no chain", ("QQQ", "s", "", "0x" + "ab" * 20)),
        ("a bad chain", ("QQQ", "s", "EIP155:1", "0x" + "ab" * 20)),
        ("a short address", ("QQQ", "s", "eip155:1", "0xabc")),
        ("an upper-case address", ("QQQ", "s", "eip155:1", "0x" + "AB" * 20)),
        ("no address", ("QQQ", "s", "eip155:1", "")),
        ("a space in the symbol", ("Q Q", "s", "eip155:1", "0x" + "ab" * 20)),
    ):
        c.raises(label, lambda a=args: claim(*a))
    c.eq("a source of 80 characters", claim("QQQ", "s" * 80, "eip155:1", "0x" + "ab" * 20).source, "s" * 80)
    c.eq(
        "a non-hexadecimal address of a chain with other addresses",
        claim("QQQ", "s", "solana", "So11111111111111111111111111111111111111112").contract_address[:4],
        "So11",
    )
    return c.bad


# --- series: a symbol that ended and came back --------------------------------------------------------------------------------------------
def check_series(mod: types.ModuleType) -> list[str]:
    from hoshitori.ids import natural_id

    c = Case(mod)
    K, J = mod.MappingStatus, mod.JoinCode

    def live(*, cb: InstrumentStatus = TRADING, kr: InstrumentStatus = TRADING) -> tuple[Instrument, Instrument]:
        return inst("coinbase", "LUNA-USD", "LUNA", "USD", status=cb), inst("kraken", "LUNAUSD", "LUNA", "USD", status=kr)

    reg = c.registry()
    cb, kr = live()
    c.admit(reg, "coinbase", "spot", 0, cb)
    c.admit(reg, "kraken", "spot", 1, kr)
    first = reg.resolve_symbol("LUNA")
    c.eq("the first series", (first.status, first.token_id), (K.RESOLVED, natural_id("tk", {"symbol": "LUNA", "series": 1})))
    evidence = reg.head
    cb, kr = live(cb=DELISTED)
    c.admit(reg, "coinbase", "spot", 2, cb)
    c.eq(
        "one venue delisting leaves one source",
        (reg.resolve_symbol("LUNA").status, reg.resolve_symbol("LUNA").token_id),
        (K.UNKNOWN, first.token_id),
    )
    cb, kr = live(cb=DELISTED, kr=DELISTED)
    c.admit(reg, "kraken", "spot", 3, kr)
    gone = reg.resolve_symbol("LUNA")
    c.eq(
        "both delisted: no listing, the same token",
        (gone.status, gone.basis.value, gone.token_id),
        (K.UNKNOWN, "NO_LISTING", first.token_id),
    )
    cb, kr = live()
    c.admit(reg, "coinbase", "spot", 4, cb)
    again = reg.resolve_symbol("LUNA")
    second_id = natural_id("tk", {"symbol": "LUNA", "series": 2})
    c.eq(
        "a listing after the end is a new series",
        (again.token_id, again.status, again.basis.value),
        (second_id, K.UNKNOWN, "SINGLE_SOURCE"),
    )
    c.admit(reg, "kraken", "spot", 5, kr)
    c.eq(
        "the new series needs its own agreement",
        (reg.resolve_symbol("LUNA").status, reg.resolve_symbol("LUNA").token_id),
        (K.RESOLVED, second_id),
    )
    c.eq("the old series keeps its instruments as they were", reg.mapping_at(iid(cb), t(2.5)).token_id, first.token_id)  # type: ignore[union-attr]
    c.eq(
        "the new mapping starts at the relisting", (reg.mapping_at(iid(cb)).token_id, reg.mapping_at(iid(cb)).known_from), (second_id, t(5))
    )  # type: ignore[union-attr]
    c.eq("a relisted instrument is a new generation", [r.generation for r in reg._rows[iid(cb)]], [1, 1, 2])
    c.eq("the old token's instruments, as they were", reg.instruments_of(first.token_id, t(1.5)), tuple(sorted((iid(cb), iid(kr)))))
    c.eq("the old token has none now", reg.instruments_of(first.token_id), ())
    c.eq("the resolution as it was", reg.resolve_symbol("LUNA", as_of=t(1.5)).token_id, first.token_id)
    c.eq("the resolution after the relisting", reg.resolve_symbol("LUNA", as_of=t(5.5)).token_id, second_id)
    between = reg.resolve_symbol("LUNA", as_of=t(4.5))
    c.eq(
        "between the two relistings the newest series is the answer",
        (between.token_id, between.instrument_ids, between.status),
        (second_id, (iid(cb),), K.UNKNOWN),
    )
    r = reg.join(iid(cb), mod.Want(token_id=first.token_id))
    c.eq("old evidence is not joined to the new series", (r.code, r.field), (J.IDENTITY_MISMATCH, "token"))
    r = reg.join(iid(cb), mod.Want(token_id=first.token_id, snapshot_hash=evidence))
    c.eq("and the snapshot it was taken under says why", (r.code, r.field), (J.SNAPSHOT_MISMATCH, "snapshot_hash"))

    # a live listing that changes is not an ending
    reg = c.registry()
    c.admit(reg, "kraken", "spot", 0, inst("kraken", "ALLUSD", "ALL", "USD"))
    c.admit(reg, "kraken", "spot", 1, inst("kraken", "ALLUSD", "ALL", "USD", status=HALTED))
    c.admit(reg, "kraken", "spot", 2, inst("kraken", "ALLUSD", "ALL", "USD", status=TRADING, tick="0.5"))
    c.admit(reg, "coinbase", "spot", 3, inst("coinbase", "ALL-USD", "ALL", "USD"))
    c.eq(
        "a halt and a change do not end a token",
        (reg.resolve_symbol("ALL").token_id, reg.resolve_symbol("ALL").status),
        (natural_id("tk", {"symbol": "ALL", "series": 1}), K.RESOLVED),
    )
    c.admit(reg, "binance-spot", "spot", 4, inst("binance-spot", "ALLUSDT", "ALL"))
    c.eq("a third listing joins the same series", reg.resolve_symbol("ALL").token_id, natural_id("tk", {"symbol": "ALL", "series": 1}))

    # first seen delisted: never seen to end, so a later listing is the same series
    reg = c.registry()
    c.admit(reg, "binance-usdm", "all", 0, inst("binance-usdm", "OMGUSDT", "OMG", kind=PERP, status=DELISTED))
    c.admit(reg, "coinbase", "spot", 1, inst("coinbase", "OMG-USD", "OMG", "USD"))
    c.eq(
        "a name first seen delisted and then listed elsewhere",
        reg.resolve_symbol("OMG").token_id,
        natural_id("tk", {"symbol": "OMG", "series": 1}),
    )
    reg.admit(
        lst("binance-usdm", inst("binance-usdm", "OMGUSDT", "OMG", kind=PERP, status=TRADING)), scope="all", known_at=t(2), capture_id="c"
    )
    c.eq(
        "a delisted instrument that trades again keeps its series if it was never seen to end",
        reg.resolve_symbol("OMG").token_id,
        natural_id("tk", {"symbol": "OMG", "series": 1}),
    )
    c.eq("but it is a new generation", [r.generation for r in reg._rows[iid(inst("binance-usdm", "OMGUSDT", "OMG", kind=PERP))]], [1, 2])

    # an instrument that was seen live, delisted, and trades again: a new series
    reg = c.registry()
    perp = inst("hyperliquid", "MATIC", "MATIC", "USDC", kind=PERP)
    c.admit(reg, "hyperliquid", "perpetual", 0, perp)
    c.admit(reg, "hyperliquid", "perpetual", 1, inst("hyperliquid", "MATIC", "MATIC", "USDC", kind=PERP, status=DELISTED))
    c.eq("still the first series while delisted", reg.resolve_symbol("MATIC").token_id, natural_id("tk", {"symbol": "MATIC", "series": 1}))
    c.admit(reg, "hyperliquid", "perpetual", 2, inst("hyperliquid", "MATIC", "MATIC", "USDC", kind=PERP, status=TRADING))
    c.eq("trading again is a new series", reg.resolve_symbol("MATIC").token_id, natural_id("tk", {"symbol": "MATIC", "series": 2}))
    c.eq("the same instrument id, a new mapping", len({m.token_id for m in reg._mappings[iid(perp)]}), 2)

    # a pair re-pointed at another token: the same id means something else
    reg = c.registry()
    old = inst("hyperliquid", "@107", "HYPE", "USDC", ref="0x" + "a" * 32)
    c.admit(reg, "hyperliquid", "spot", 0, old, inst("hyperliquid", "@200", "OTHER", "USDC", ref="0x" + "c" * 32))
    c.admit(reg, "binance-spot", "spot", 1, inst("binance-spot", "HYPEUSDT", "HYPE"), inst("binance-spot", "NEWUSDT", "NEW"))
    before = reg.head
    new = inst("hyperliquid", "@107", "NEW", "USDC", ref="0x" + "d" * 32)
    c.admit(reg, "hyperliquid", "spot", 2, new, inst("hyperliquid", "@200", "OTHER", "USDC", ref="0x" + "c" * 32))
    c.eq(
        "the pair now belongs to another token",
        reg.resolve_symbol("NEW").instrument_ids,
        tuple(sorted((iid(new), iid(inst("binance-spot", "NEWUSDT", "NEW"))))),
    )
    c.eq("and the old token lost it", reg.resolve_symbol("HYPE").instrument_ids, (iid(inst("binance-spot", "HYPEUSDT", "HYPE")),))
    c.eq("the old token has one source again", reg.resolve_symbol("HYPE").status, K.UNKNOWN)
    c.eq("a re-pointed id is a new generation", [r.generation for r in reg._rows[iid(old)]], [1, 2])
    r = reg.join(iid(old), mod.Want(snapshot_hash=before))
    c.eq(
        "the old evidence of a re-pointed id",
        (r.code, r.field, r.detail),
        (J.SNAPSHOT_MISMATCH, "snapshot_hash", "the instrument means something else than it did in that snapshot"),
    )
    r = reg.join(iid(old), mod.Want(snapshot_hash=reg.head))
    c.eq("the current snapshot", r.code, J.OK)
    r = reg.join(iid(old), mod.Want(snapshot_hash="0" * 64))
    c.eq(
        "a snapshot the registry never had",
        (r.code, r.field, r.detail),
        (J.SNAPSHOT_MISMATCH, "snapshot_hash", "the registry never had that snapshot"),
    )
    c.admit(
        reg,
        "binance-spot",
        "spot",
        3,
        inst("binance-spot", "HYPEUSDT", "HYPE"),
        inst("binance-spot", "NEWUSDT", "NEW"),
        inst("binance-spot", "LATEUSDT", "LATE"),
    )
    r = reg.join(iid(inst("binance-spot", "LATEUSDT", "LATE")), mod.Want(snapshot_hash=before))
    c.eq(
        "an instrument that was not yet in the snapshot",
        (r.code, r.field, r.detail),
        (J.SNAPSHOT_MISMATCH, "snapshot_hash", "the instrument was not in that snapshot"),
    )
    return c.bad


# --- records: replay, order, time ------------------------------------------------------------------------------------------------------------
def build(mod: types.ModuleType, c: Case) -> Any:
    reg = c.registry()
    c.admit(
        reg,
        "kraken",
        "spot",
        0,
        inst("kraken", "XXBTZUSD", "XXBT", "ZUSD"),
        inst("kraken", "AAAUSD", "AAA", "USD"),
        unmapped=(Unmapped("BAD", "no base"),),
    )
    c.admit(reg, "coinbase", "spot", 1, inst("coinbase", "BTC-USD", "BTC", "USD"), inst("coinbase", "AAA-USD", "AAA", "USD"))
    reg.add_claims([mod.Claim("AAA", "labels", "eip155:1", "0x" + "ab" * 20)], known_at=t(2))
    c.admit(reg, "kraken", "spot", 3, inst("kraken", "XXBTZUSD", "XXBT", "ZUSD", status=HALTED))
    c.admit(
        reg, "coinbase", "spot", 4, inst("coinbase", "BTC-USD", "BTC", "USD", status=DELISTED), inst("coinbase", "AAA-USD", "AAA", "USD")
    )
    return reg


def check_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    reg = build(mod, c)
    records = reg.records()
    c.eq("five records", len(records), 5)
    c.true("the records are canonical", all(mod.canonical.dumps(r) for r in records))
    copy = mod.Registry.replay(records)
    c.eq("replay gives the same snapshot hash", copy.head, reg.head)
    c.eq(
        "replay gives the same resolutions",
        [copy.resolve_symbol(s) for s in ("BTC", "AAA")],
        [reg.resolve_symbol(s) for s in ("BTC", "AAA")],
    )
    c.eq("replay gives the same counts", copy.status_counts(), reg.status_counts())
    c.eq("replay gives the same unmapped entries", copy.unmapped("kraken", "spot"), reg.unmapped("kraken", "spot"))
    c.eq(
        "replay gives the same history",
        copy.mapping_at(iid(inst("kraken", "XXBTZUSD", "XXBT", "ZUSD")), t(1.5)),
        reg.mapping_at(iid(inst("kraken", "XXBTZUSD", "XXBT", "ZUSD")), t(1.5)),
    )
    c.eq(
        "replay gives the same hashes through time",
        [copy.head_at(t(s)) for s in (-1, 0, 0.5, 1, 2, 3, 4, 9)],
        [reg.head_at(t(s)) for s in (-1, 0, 0.5, 1, 2, 3, 4, 9)],
    )
    c.eq("the hash through time", (reg.head_at(t(-1)), reg.head_at(t(9))), (mod.hashlib.sha256(mod.SPEC.encode()).hexdigest(), reg.head))
    c.eq("a hash in the middle", reg.snapshot_time(reg.head_at(t(1.5))), t(1))
    c.eq("an unknown hash has no time", reg.snapshot_time("f" * 64), None)
    c.eq("the head of a replay of the first records is a past head", mod.Registry.replay(records[:2]).head, reg.head_at(t(1)))
    c.eq(
        "the same records in the same order are one history, so the hash is stable",
        len({mod.Registry.replay(records).head for _ in range(3)}),
        1,
    )

    changed = [dict(r) for r in records]
    changed[1] = {
        **records[1],
        "events": [
            {**records[1]["events"][0], "instrument": {**records[1]["events"][0]["instrument"], "tick_size": "9"}},
            *records[1]["events"][1:],
        ],
    }
    try:
        other = mod.Registry.replay(changed)
        c.true("an edited record gives another hash or fails", other.head != reg.head)
    except mod.RegistryError:
        pass
    c.raises("records out of order", lambda: mod.Registry.replay([records[1], records[0]]), text="increasing")
    c.raises("a record twice", lambda: mod.Registry.replay([records[0], records[0]]), text="increasing")
    c.raises("not a record", lambda: mod.Registry.replay([{"spec": "x"}]), text="not a registry record")
    c.raises(
        "a record with another spec",
        lambda: mod.Registry.replay([{**records[0], "spec": "hoshitori.registry/2"}]),
        text="not a registry record",
    )
    c.raises("a record with an extra member", lambda: mod.Registry.replay([{**records[0], "extra": 1}]), text="not a registry record")
    c.raises("a record with no events", lambda: mod.Registry.replay([{**records[0], "events": []}]), text="has events")
    c.raises("events that are not a list", lambda: mod.Registry.replay([{**records[0], "events": {}}]), text="has events")
    c.raises("an event that is not an object", lambda: mod.Registry.replay([{**records[0], "events": [1]}]), text="an object")
    c.raises("an unknown event", lambda: mod.Registry.replay([{**records[0], "events": [{"t": "dropped"}]}]), text="unknown event")
    c.raises("a time without a zone", lambda: mod.Registry.replay([{**records[0], "known_at": "2026-10-29T12:00:00.000"}]))
    c.raises(
        "a time with microseconds",
        lambda: mod.Registry.replay([{**records[0], "known_at": "2026-10-29T12:00:00.000123Z"}]),
        text="millisecond",
    )
    first = records[0]["events"]
    c.raises(
        "a snapshot whose count is wrong",
        lambda: mod.Registry.replay([{**records[0], "events": [*first[:-1], {**first[-1], "count": 1}]}]),
        text="does not match",
    )
    c.raises(
        "a snapshot whose hash is wrong",
        lambda: mod.Registry.replay([{**records[0], "events": [*first[:-1], {**first[-1], "list_hash": "0" * 64}]}]),
        text="does not match",
    )
    c.raises(
        "a snapshot that names an unknown outcome",
        lambda: mod.Registry.replay([{**records[0], "events": [*first[:-1], {**first[-1], "outcome": "maybe"}]}]),
        text="outcome",
    )
    c.raises(
        "a snapshot with an unknown scope",
        lambda: mod.Registry.replay([{**records[0], "events": [*first[:-1], {**first[-1], "scope": "x"}]}]),
        text="outcome, venue and scope",
    )
    c.raises(
        "an instrument added twice", lambda: mod.Registry.replay([{**records[0], "events": [first[0], first[0], first[-1]]}]), text="twice"
    )
    c.raises(
        "an unknown field of an instrument",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{**first[0], "instrument": {**first[0]["instrument"], "x": 1}}, *first[1:]]}]
        ),
        text="exactly its fields",
    )
    c.raises(
        "a missing field of an instrument",
        lambda: mod.Registry.replay(
            [
                {
                    **records[0],
                    "events": [
                        {**first[0], "instrument": {k: v for k, v in first[0]["instrument"].items() if k != "base_ref"}},
                        *first[1:],
                    ],
                }
            ]
        ),
        text="exactly its fields",
    )
    c.raises(
        "a bad kind",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{**first[0], "instrument": {**first[0]["instrument"], "kind": "swap"}}, *first[1:]]}]
        ),
        text="not valid",
    )
    c.raises(
        "an empty field",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{**first[0], "instrument": {**first[0]["instrument"], "raw_id": ""}}, *first[1:]]}]
        ),
        text="missing or not text",
    )
    c.raises(
        "a field that is a number",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{**first[0], "instrument": {**first[0]["instrument"], "venue": 5}}, *first[1:]]}]
        ),
        text="missing or not text",
    )
    c.raises(
        "an instrument outside its scope",
        lambda: mod.Registry.replay([{**records[0], "events": [{**first[0], "scope": "perpetual"}, *first[1:]]}]),
        text="outside the scope",
    )
    c.raises(
        "an event with an unknown scope",
        lambda: mod.Registry.replay([{**records[0], "events": [{**first[0], "scope": "x"}, *first[1:]]}]),
        text="names a scope",
    )
    c.raises(
        "a change to something not listed",
        lambda: mod.Registry.replay([{**records[0], "events": [{**first[0], "t": "changed"}, *first[1:]]}]),
        text="a listed one that differs",
    )
    c.raises(
        "a removal of something not listed",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{"t": "removed", "scope": "spot", "venue": "kraken", "instrument_id": "x"}, *first[1:]]}]
        ),
        text="a listed one",
    )
    c.raises(
        "a removal naming a number",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{"t": "removed", "scope": "spot", "venue": "kraken", "instrument_id": 5}, *first[1:]]}]
        ),
        text="a listed one",
    )
    c.raises(
        "a claim with a member too many",
        lambda: mod.Registry.replay(
            [{**records[0], "events": [{"t": "claim", "symbol": "A", "source": "s", "chain_id": "c", "contract_address": "x" * 8, "x": 1}]}]
        ),
        text="exactly its fields",
    )
    c.raises(
        "a record with a member missing in an event",
        lambda: mod.Registry.replay([{**records[0], "events": [{"t": "added"}]}]),
        text="not well formed",
    )
    keyed = c.registry()
    c.raises("an event with a member missing", lambda: keyed.apply({**records[0], "events": [{"t": "added"}]}), text="not well formed")
    c.raises("the registry that failed on a missing member is not used again", lambda: keyed.apply(records[0]), text="rebuilt")

    # a registry that failed is not used again
    broken = c.registry()
    c.raises("applying a bad record", lambda: broken.apply({"spec": "x"}))
    c.raises(
        "a broken registry refuses a list",
        lambda: broken.admit(lst("kraken", inst("kraken", "A", "A", "USD")), scope="spot", known_at=t(0), capture_id="c"),
        text="rebuilt",
    )
    c.raises("a broken registry refuses a record", lambda: broken.apply(records[0]), text="rebuilt")
    c.raises(
        "a broken registry refuses claims",
        lambda: broken.add_claims([mod.Claim("A", "s", "eip155:1", "0x" + "ab" * 20)], known_at=t(0)),
        text="rebuilt",
    )

    # time: every record has an instant of its own, to the millisecond
    clock = c.registry()
    r1 = clock.admit(lst("kraken", inst("kraken", "A", "A", "USD")), scope="spot", known_at=t(0), capture_id="c")
    r2 = clock.admit(lst("kraken", inst("kraken", "B", "B", "USD")), scope="spot", known_at=t(0), capture_id="c")
    r3 = clock.admit(lst("kraken", inst("kraken", "B", "B", "USD")), scope="spot", known_at=t(-5), capture_id="c")
    r4 = clock.admit(
        lst("kraken", inst("kraken", "B", "B", "USD")), scope="spot", known_at=T0 + timedelta(seconds=1, microseconds=2999), capture_id="c"
    )
    c.eq("a time that is free is kept", r1["known_at"], "2026-10-29T12:00:00.000Z")
    c.eq("the same time is moved on by a millisecond", r2["known_at"], "2026-10-29T12:00:00.001Z")
    c.eq("an earlier time is moved on from the last record", r3["known_at"], "2026-10-29T12:00:00.002Z")
    c.eq("microseconds are dropped", r4["known_at"], "2026-10-29T12:00:01.002Z")
    c.raises(
        "a time without a zone",
        lambda: clock.admit(lst("kraken", inst("kraken", "C", "C", "USD")), scope="spot", known_at=datetime(2026, 10, 29), capture_id="c"),
        mod.TimeError if hasattr(mod, "TimeError") else ValueError,
    )
    return c.bad


# --- names, assets, configuration -------------------------------------------------------------------------------------------------------------
def check_names_and_assets(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    K, J = mod.MappingStatus, mod.JoinCode
    reg = c.registry()
    for text, want in (
        ("btc", "BTC"),
        ("Btc", "BTC"),
        ("XBT", "XBT"),
        ("xxbt", "XXBT"),
        ("XDG", "XDG"),
        ("币安人生", "币安人生"),
        ("1INCH", "1INCH"),
        ("USDT0", "USDT0"),
    ):
        c.eq(f"normal_symbol({text!r})", reg.normal_symbol(text), want)
    for bad in ("", " ", "A B", "A\tB", "A" * 41, "A\x00", "A\nB"):
        c.raises(f"symbol {bad!r}", lambda b=bad: reg.normal_symbol(b), text="symbol")
    # a symbol that looks like another one: hidden characters, and a Latin letter beside a Cyrillic or Greek one
    for look_alike in (
        "BT\u200bC",
        "BT\u2060C",
        "BTC\ufeff",
        "B\u0422C",
        "\u0391BC",
        "BT\u034fC",
        "BT\u3164C",
        "BT\U000e0041C",
        "\u0412TC",
    ):
        c.raises(f"a look-alike symbol {look_alike!r}", lambda b=look_alike: reg.normal_symbol(b), text="symbol")
        c.raises(f"a look-alike canonical symbol {look_alike!r}", lambda b=look_alike: mod.canonical_symbol(b), text="symbol")
        c.raises(
            f"a claim of a look-alike symbol {look_alike!r}",
            lambda b=look_alike: mod.Claim(b.upper(), "src", "1", "0x" + "a" * 40),
            text="symbol",
        )
    for honest in ("\u0422\u041e\u041d", "\u0391\u0392\u0393", "\u5e01\u5b89\u4eba\u751f", "1INCH", "A-B_C.D"):
        c.eq(f"an honest symbol {honest!r}", reg.normal_symbol(honest), honest.upper())
    c.eq("canonical_symbol", (mod.canonical_symbol("a-b_c.d"), mod.canonical_symbol("A" * 40)), ("A-B_C.D", "A" * 40))
    c.eq(
        "operators",
        [mod.operator_of(v) for v in ("binance-spot", "binance-usdm", "okx", "hyperliquid", "a-b-c")],
        ["binance", "binance", "okx", "hyperliquid", "a"],
    )

    # aliases are checked when the registry is built
    c.raises("an alias in lower case", lambda: mod.Registry(aliases={"x": {"ab": "CD"}}), text="alias")
    c.raises("an alias to a lower-case symbol", lambda: mod.Registry(aliases={"x": {"AB": "cd"}}), text="alias")
    c.raises("an alias to itself", lambda: mod.Registry(aliases={"x": {"AB": "AB"}}), text="alias")
    mod.Registry(aliases={"x": {"AB": "CD"}, "y": {"AB": "EF"}})  # operators are independent: each may spell its own codes
    only = mod.Registry(aliases={})
    c.admit(only, "kraken", "spot", 0, inst("kraken", "XXBTZUSD", "XXBT", "ZUSD"))
    c.admit(only, "coinbase", "spot", 1, inst("coinbase", "BTC-USD", "BTC", "USD"))
    c.eq(
        "without the curated table a legacy code is a token of its own",
        (only.resolve_symbol("BTC").status, only.resolve_symbol("XXBT").status),
        (K.UNKNOWN, K.UNKNOWN),
    )
    other = mod.Registry(aliases={"coinbase": {"BTC": "XBT"}})
    c.admit(other, "coinbase", "spot", 0, inst("coinbase", "BTC-USD", "BTC", "USD"))
    c.admit(other, "kraken", "spot", 1, inst("kraken", "XBTUSD", "XBT", "USD"))
    c.eq("an alias is by operator", other.resolve_symbol("XBT").status, K.RESOLVED)
    scoped = mod.Registry(aliases={"kraken": {"XXBT": "BTC"}})
    c.admit(scoped, "coinbase", "spot", 0, inst("coinbase", "XXBT-USD", "XXBT", "USD"))
    c.admit(scoped, "okx", "spot", 1, inst("okx", "BTC-USD", "BTC", "USD"))
    c.eq(
        "another operator's spelling is not rewritten",
        (scoped.resolve_symbol("BTC").instrument_ids, scoped.resolve_symbol("XXBT").instrument_ids),
        ((iid(inst("okx", "BTC-USD", "BTC", "USD")),), (iid(inst("coinbase", "XXBT-USD", "XXBT", "USD")),)),
    )

    # assets
    reg = c.registry()
    btc_kr, btc_cb, sol_cb = (
        inst("kraken", "XXBTZUSD", "XXBT", "ZUSD"),
        inst("coinbase", "BTC-USD", "BTC", "USD"),
        inst("coinbase", "SOL-USD", "SOL", "USD"),
    )
    c.admit(reg, "kraken", "spot", 0, btc_kr)
    c.admit(reg, "coinbase", "spot", 1, btc_cb, sol_cb)
    r = reg.join(iid(sol_cb), mod.Want())
    c.eq("a join on one source", (r.code, r.field, r.asset), (J.IDENTITY_UNRESOLVED, "token", None))
    r = reg.join(iid(btc_cb), mod.Want())
    asset = r.asset
    c.true("a resolved instrument is an asset", r.ok and asset is not None)
    if asset is not None:
        from hoshitori.ids import natural_id

        c.eq(
            "the asset id is the token and the instrument",
            asset.asset_id,
            natural_id("as", {"token": asset.token_id, "instrument": iid(btc_cb)}),
        )
        c.eq(
            "the asset's fields",
            (asset.venue, asset.raw_id, asset.kind, asset.base_raw, asset.quote_raw, asset.quote_class, asset.multiplier, asset.settle),
            ("coinbase", "BTC-USD", SPOT, "BTC", "USD", "USD", "1", None),
        )
        c.eq("the asset carries the snapshot it was made under", asset.snapshot_hash, reg.head)
        c.eq("the asset can be found again", reg.lookup_asset(asset.asset_id), asset)
        c.eq("the asset found as of an earlier time is not there", reg.lookup_asset(asset.asset_id, t(0.5)), None)
        c.eq("the asset as of the time it was resolved", reg.lookup_asset(asset.asset_id, t(1)).snapshot_hash, reg.head_at(t(1)))  # type: ignore[union-attr]
        kraken_asset = reg.join(iid(btc_kr), mod.Want()).asset
        c.true(
            "two instruments of one token are two assets",
            kraken_asset is not None and kraken_asset.asset_id != asset.asset_id and kraken_asset.token_id == asset.token_id,
        )
        c.eq("a made-up asset id", reg.lookup_asset("as" + "a" * 26), None)
        c.eq("each asset id names one instrument", len({reg.join(iid(i), mod.Want()).asset.asset_id for i in (btc_kr, btc_cb)}), 2)  # type: ignore[union-attr]
        reg.admit(
            lst("coinbase", inst("coinbase", "BTC-USD", "BTC", "USD", status=DELISTED), sol_cb), scope="spot", known_at=t(2), capture_id="c"
        )
        c.eq(
            "an asset whose listing ended is still an asset, from the registry as of then",
            reg.lookup_asset(asset.asset_id, t(1.5)) is not None,
            True,
        )
    c.eq("counts", reg.status_counts(), {"RESOLVED": 0, "AMBIGUOUS": 0, "UNKNOWN": 3})
    c.eq("counts at an earlier time", reg.status_counts(t(1)), {"RESOLVED": 2, "AMBIGUOUS": 0, "UNKNOWN": 1})
    c.eq("counts before anything", reg.status_counts(t(-1)), {"RESOLVED": 0, "AMBIGUOUS": 0, "UNKNOWN": 0})
    return c.bad


def check_edges(mod: types.ModuleType) -> list[str]:
    import re

    c = Case(mod)
    K, B, J = mod.MappingStatus, mod.Basis, mod.JoinCode
    # the words of the API are its names
    c.eq("basis values are their names", [m.value for m in B], [m.name for m in B])
    c.eq("join code values are their names", [m.value for m in J], [m.name for m in J])
    c.eq("status values are their names", [m.value for m in K], [m.name for m in K])
    c.eq("the spec name", mod.SPEC, "hoshitori.registry/1")
    c.eq("the scopes", mod.SCOPES, ("all", "spot", "perpetual", "future"))
    for cls in (
        mod.RegistryConfig,
        mod.Claim,
        mod.Row,
        mod.MappingRow,
        mod.Resolution,
        mod.Asset,
        mod.Want,
        mod.JoinResult,
        mod._ListState,
    ):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)

    reg = c.registry()
    a, b = inst("kraken", "AAAUSD", "AAA", "USD"), inst("coinbase", "AAA-USD", "AAA", "USD")
    first = c.admit(
        reg,
        "kraken",
        "spot",
        0,
        a,
        unmapped=(Unmapped(None, "no id"), Unmapped("ZED", "no base"), Unmapped("ABE", "no quote"), Unmapped(None, "another")),
    )
    head = first["events"][-1]
    c.eq(
        "the keys of an accepted snapshot event",
        set(head),
        {"t", "venue", "scope", "capture_id", "outcome", "reason", "count", "list_hash", "unmapped"},
    )
    c.eq(
        "its capture, outcome and reason",
        (head["capture_id"], head["outcome"], head["reason"], head["count"]),
        ("cp-kraken-spot-0", "accepted", None, 1),
    )
    c.true("the hash of a list", re.fullmatch("[0-9a-f]{64}", head["list_hash"]) is not None)
    c.eq(
        "unmapped entries are kept as given, entries without an id too",
        head["unmapped"],
        [[None, "no id"], ["ZED", "no base"], ["ABE", "no quote"], [None, "another"]],
    )
    again = reg.plan_snapshot(
        lst(
            "kraken",
            a,
            unmapped=(Unmapped("ABE", "no quote"), Unmapped(None, "another"), Unmapped("ZED", "no base"), Unmapped(None, "no id")),
        ),
        scope="spot",
        known_at=t(1),
        capture_id="x",
    )
    c.eq("the hash of a list does not depend on the order of its unmapped entries", again["events"][-1]["list_hash"], head["list_hash"])
    other = reg.plan_snapshot(lst("kraken", a, unmapped=(Unmapped("ABE", "no quote"),)), scope="spot", known_at=t(1), capture_id="x")
    c.true("it depends on what is unmapped", other["events"][-1]["list_hash"] != head["list_hash"])
    two = c.registry().plan_snapshot(lst("kraken", a, inst("kraken", "BBBUSD", "BBB", "USD")), scope="spot", known_at=t(1), capture_id="x")
    one = c.registry().plan_snapshot(lst("kraken", a), scope="spot", known_at=t(1), capture_id="x")
    c.true("and on the instruments", two["events"][-1]["list_hash"] != one["events"][-1]["list_hash"])
    rej = c.admit(reg, "kraken", "spot", 5)
    c.eq(
        "the keys of a rejected snapshot event",
        (set(rej["events"][-1]), rej["events"][-1]["count"], rej["events"][-1]["capture_id"]),
        ({"t", "venue", "scope", "capture_id", "outcome", "reason", "count", "list_hash", "unmapped"}, 0, "cp-kraken-spot-5"),
    )
    c.true("the diff of an accepted list has no snapshot twice", [e["t"] for e in first["events"]].count("snapshot") == 1)
    c.eq(
        "the unmapped entries of a rejected list are not kept",
        reg.unmapped("kraken", "spot"),
        ((None, "no id"), ("ZED", "no base"), ("ABE", "no quote"), (None, "another")),
    )
    c.eq("a venue with no list has none", reg.unmapped("coinbase", "spot"), ())

    # records handed out are copies; a registry cannot be changed through them
    got = reg.records()
    got[0]["known_at"] = "x"
    c.eq("records are copies", reg.records()[0]["known_at"], "2026-10-29T12:00:00.000Z")

    # a registry that failed refuses to plan as well
    broken = c.registry()
    c.raises("a bad record", lambda: broken.apply({"spec": "x"}))
    c.raises(
        "a broken registry plans no list",
        lambda: broken.plan_snapshot(lst("kraken", a), scope="spot", known_at=t(0), capture_id="c"),
        text="rebuilt",
    )
    c.raises(
        "a broken registry plans no claims",
        lambda: broken.plan_claims([mod.Claim("A", "s", "eip155:1", "0x" + "ab" * 20)], known_at=t(0)),
        text="rebuilt",
    )
    c.raises("no claims to plan", lambda: c.registry().plan_claims([], known_at=t(0)), text="at least one")
    claims = c.registry()
    rec = claims.add_claims([mod.Claim("A", "s", "eip155:1", "0x" + "ab" * 20)], known_at=t(0))
    c.eq("claims return their record", rec, claims.records()[0])
    c.eq(
        "a claims-only symbol carries its claims as evidence",
        (claims.resolve_symbol("A").evidence, claims.resolve_symbol("A").token_id),
        (("claim:s",), mod.natural_id("tk", {"symbol": "A", "series": 1})),
    )
    c.eq(
        "an unlisted symbol as of a time",
        claims.resolve_symbol("ZZ", as_of=t(1)).token_id,
        mod.natural_id("tk", {"symbol": "ZZ", "series": 1}),
    )
    c.eq("an unlisted symbol now", claims.resolve_symbol("ZZ").token_id, mod.natural_id("tk", {"symbol": "ZZ", "series": 1}))
    c.eq(
        "an unlisted symbol as of a time has no evidence",
        (
            claims.resolve_symbol("ZZ", as_of=t(1)).evidence,
            claims.resolve_symbol("ZZ", as_of=t(1)).instrument_ids,
            claims.resolve_symbol("ZZ", as_of=t(1)).basis,
        ),
        ((), (), B.NO_LISTING),
    )
    c.eq("an unknown token has no instruments", claims.instruments_of("tk" + "a" * 26), ())

    # removing a source lowers the status
    reg = c.registry()
    c.admit(reg, "kraken", "spot", 0, a)
    c.admit(reg, "coinbase", "spot", 1, b)
    c.eq("two sources", reg.mapping_at(iid(a)).status, K.RESOLVED)  # type: ignore[union-attr]
    c.admit(reg, "coinbase", "spot", 2, inst("coinbase", "OTHER-USD", "OTHER", "USD"))
    c.eq("one source after a removal", (reg.mapping_at(iid(a)).status, reg.mapping_at(iid(a)).known_from), (K.UNKNOWN, t(2)))  # type: ignore[union-attr]
    c.eq(
        "the removed one is gone at the boundary",
        (reg.instrument_at(iid(b), t(2)), reg.instrument_at(iid(b), t(2) - timedelta(milliseconds=1))),
        (None, b),
    )
    c.eq("and was a source before it", reg.mapping_at(iid(a), t(1.5)).status, K.RESOLVED)  # type: ignore[union-attr]

    # a mapping is kept while it is true
    reg = c.registry()
    c.admit(reg, "kraken", "spot", 0, a)
    c.admit(reg, "coinbase", "spot", 1, b)
    c.admit(reg, "kraken", "spot", 2, inst("kraken", "AAAUSD", "AAA", "USD", tick="0.5"))
    c.eq("a change of another member does not reopen a mapping", reg.mapping_at(iid(b)).known_from, t(1))  # type: ignore[union-attr]
    c.eq("and does not reopen the changed member's either", reg.mapping_at(iid(a)).known_from, t(1))  # type: ignore[union-attr]
    c.eq(
        "the mapping history of the first listed", [(m.known_from, m.known_to) for m in reg._mappings[iid(a)]], [(t(0), t(1)), (t(1), None)]
    )
    c.eq("the mapping history of the second listed", [(m.known_from, m.known_to) for m in reg._mappings[iid(b)]], [(t(1), None)])

    # every optional field of an instrument, none and all
    bare = Instrument("okx", "BARE", SPOT, "BARE", "USDT", "USDT", "1", None, None, None, None, TRADING)
    full = Instrument("okx", "FULL", FUTURE, "FULL", "USDT", "USDT", "1000", "USDT", "0.01", "0.001", "5", HALTED, t(3600), "0x" + "e" * 32)
    for label, one in (("bare", bare), ("full", full)):
        r = c.registry()
        r.admit(lst("okx", one), scope="all", known_at=t(0), capture_id="c")
        back = mod.Registry.replay(r.records())
        c.eq(f"an instrument with {label} optional fields survives its record", back.instrument_at(iid(one)), one)
        c.eq(f"{label}: the record round trips", mod.instrument_from_record(mod.instrument_record(one)), one)
    for name in ("venue", "raw_id", "kind", "base_raw", "quote_raw", "quote_class", "multiplier", "status"):
        broken_record = {**mod.instrument_record(bare), name: None}
        c.raises(f"{name} cannot be null", lambda r=broken_record: mod.instrument_from_record(r))
    c.raises("an instrument record that is not an object", lambda: mod.instrument_from_record("x"))  # type: ignore[arg-type]
    # an instrument that appears and disappears in one record
    both = c.registry()
    record = {
        "spec": mod.SPEC,
        "known_at": "2026-10-29T12:00:00.000Z",
        "events": [
            {"t": "added", "scope": "spot", "instrument": mod.instrument_record(a)},
            {"t": "removed", "scope": "spot", "venue": "kraken", "instrument_id": iid(a)},
        ],
    }
    both.apply(record)
    c.eq("appearing and disappearing in one record", (both.instrument_at(iid(a)), both.mapping_at(iid(a))), (None, None))

    # generations count every return
    reg = c.registry()
    for step in range(5):
        c.admit(reg, "kraken", "spot", step, *([a] if step % 2 == 0 else [inst("kraken", "KEEPUSD", "KEEP", "USD")]))
    c.eq("generations count each return", [r.generation for r in reg._rows[iid(a)]], [1, 2, 3])

    # contract lookups
    reg = c.registry()
    cl = mod.Claim
    c.admit(reg, "coinbase", "spot", 0, b)
    c.admit(reg, "kraken", "spot", 1, a)
    reg.add_claims([cl("AAA", "labels", "eip155:1", "0x" + "ab" * 20)], known_at=t(2))
    plain = reg.resolve_symbol("AAA", chain_id="eip155:1", contract_address="0x" + "AB" * 20)
    c.eq(
        "a contract that is claimed, on a listed symbol",
        (plain.status, plain.basis, plain.token_id, plain.instrument_ids, plain.evidence),
        (
            K.RESOLVED,
            B.CONTRACT,
            mod.natural_id("tk", {"symbol": "AAA", "series": 1}),
            tuple(sorted((iid(a), iid(b)))),
            ("coinbase", "kraken", "claim:labels"),
        ),
    )
    unclaimed = reg.resolve_symbol("AAA", chain_id="eip155:1", contract_address="0x" + "cd" * 20)
    c.eq(
        "a contract nobody claims, on a listed symbol",
        (unclaimed.status, unclaimed.basis, unclaimed.instrument_ids),
        (K.UNKNOWN, B.SINGLE_SOURCE, ()),
    )
    other_chain = reg.resolve_symbol("AAA", chain_id="eip155:137", contract_address="0x" + "ab" * 20)
    c.eq("the same address on another chain", other_chain.status, K.UNKNOWN)
    solana = c.registry()
    c.admit(solana, "coinbase", "spot", 0, b)
    solana.add_claims([cl("AAA", "labels", "solana", "So11111111111111111111111111111111111111112")], known_at=t(1))
    c.eq(
        "an address that is not hexadecimal is compared as written",
        solana.resolve_symbol("AAA", chain_id="solana", contract_address="So11111111111111111111111111111111111111112").status,
        K.RESOLVED,
    )
    c.eq(
        "and not folded to lower case",
        solana.resolve_symbol("AAA", chain_id="solana", contract_address="so11111111111111111111111111111111111111112").status,
        K.UNKNOWN,
    )
    hl = "hyperliquid"
    clash = c.registry()
    c.admit(clash, hl, "spot", 0, inst(hl, "@1", "PUMP", "USDC", ref="0x" + "a" * 32), inst(hl, "@2", "PUMP", "USDC", ref="0x" + "b" * 32))
    c.admit(clash, "binance-spot", "spot", 1, inst("binance-spot", "PUMPUSDT", "PUMP"))
    clash.add_claims([cl("PUMP", "labels", "eip155:1", "0x" + "ab" * 20)], known_at=t(2))
    amb = clash.resolve_symbol("PUMP", chain_id="eip155:1", contract_address="0x" + "ab" * 20)
    c.eq("a contract does not clear a conflict on the venues", (amb.status, amb.basis), (K.AMBIGUOUS, B.REF_CONFLICT))
    return c.bad


def check_order(mod: types.ModuleType) -> list[str]:
    """Nothing the registry writes or returns depends on the order a set happens to be walked in."""
    c = Case(mod)
    K = mod.MappingStatus
    cl = mod.Claim
    n = 10
    venues = [f"op{k}" for k in range(n)]
    reg = c.registry()
    listed = {v: inst(v, "ORDUSD", "ORD", "USD") for v in venues}
    for k, v in enumerate(venues):
        c.admit(reg, v, "spot", k, listed[v])
    res = reg.resolve_symbol("ORD")
    c.eq("evidence is in order", res.evidence, tuple(sorted(venues)))
    c.eq("instrument ids are in order", res.instrument_ids, tuple(sorted(iid(i) for i in listed.values())))
    c.eq("a many-operator symbol", res.status, K.RESOLVED)
    c.eq("a token's instruments are in order", reg.instruments_of(res.token_id), tuple(sorted(iid(i) for i in listed.values())))
    sources = [f"src{k}" for k in range(n)]
    reg.add_claims([cl("ORD", source, "eip155:1", "0x" + "ab" * 20) for source in sources], known_at=t(20))
    c.eq("claim sources are in order", reg.resolve_symbol("ORD").evidence, (*sorted(venues), *(f"claim:{s}" for s in sorted(sources))))
    withc = reg.resolve_symbol("ORD", chain_id="eip155:1", contract_address="0x" + "ab" * 20)
    c.eq("contract evidence is in order", withc.evidence, (*sorted(venues), *(f"claim:{s}" for s in sorted(sources))))
    c.eq("and so are its instrument ids", withc.instrument_ids, tuple(sorted(iid(i) for i in listed.values())))
    c.eq("as-of ids are in order", reg.resolve_symbol("ORD", as_of=t(30)).instrument_ids, tuple(sorted(iid(i) for i in listed.values())))

    many = c.registry()
    c.admit(many, "op0", "spot", 0, inst("op0", "CONUSD", "CON", "USD"))
    many.add_claims([cl("CON", "labels", f"eip155:{k}", "0x" + f"{k:02x}" * 20) for k in range(1, 9)], known_at=t(1))
    split = many.resolve_symbol("CON")
    c.eq(
        "contract candidates",
        (split.basis, len(set(split.candidates)), list(split.candidates) == sorted(split.candidates)),
        (mod.Basis.CLAIM_CONFLICT, 8, True),
    )

    refs = c.registry()
    c.admit(refs, "hyperliquid", "spot", 0, *[inst("hyperliquid", f"@{k}", "REF", "USDC", ref="0x" + f"{k:032x}") for k in range(1, 9)])
    clash = refs.resolve_symbol("REF")
    c.eq(
        "token candidates",
        (clash.basis, len(set(clash.candidates)), list(clash.candidates) == sorted(clash.candidates)),
        (mod.Basis.REF_CONFLICT, 8, True),
    )

    dup = c.registry()
    c.admit(dup, "kraken", "spot", 0, *[inst("kraken", f"DUP{k}", "DUP", "USD") for k in range(8)])
    doubled = dup.resolve_symbol("DUP")
    c.eq(
        "pair candidates",
        (doubled.basis, len(set(doubled.candidates)), list(doubled.candidates) == sorted(doubled.candidates)),
        (mod.Basis.DUPLICATE_PAIR, 8, True),
    )

    # the order of the events of a diff
    diff = c.registry(config=mod.RegistryConfig(max_shrink=0.95))
    before = [inst("kraken", f"A{k}USD", f"A{k}", "USD") for k in range(30)]
    first = c.admit(diff, "kraken", "spot", 0, *before)
    ids = [e["instrument"]["raw_id"] for e in first["events"] if e["t"] == "added"]
    by_id = {iid(i): i.raw_id for i in before}
    c.eq("additions are in order of instrument id", ids, [by_id[k] for k in sorted(by_id)])
    after = [inst("kraken", f"A{k}USD", f"A{k}", "USD", tick="0.5") for k in range(30)]
    second = c.admit(diff, "kraken", "spot", 1, *after)
    ids = [e["instrument"]["raw_id"] for e in second["events"] if e["t"] == "changed"]
    c.eq("changes are in order of instrument id", ids, [by_id[k] for k in sorted(by_id)])
    third = c.admit(
        diff, "kraken", "spot", 2, after[0], *[inst("kraken", f"A{k}USD", f"A{k}", "USD", tick="0.5") for k in range(1, 30) if k >= 25]
    )
    removed = [e["instrument_id"] for e in third["events"] if e["t"] == "removed"]
    c.eq("removals are in order of instrument id", removed, sorted(removed))
    c.eq("and there are as many as were dropped", len(removed), 24)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_resolution,
        check_multipliers_and_variants,
        check_admission,
        check_history,
        check_ambiguity,
        check_series,
        check_records,
        check_names_and_assets,
        check_edges,
        check_order,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
