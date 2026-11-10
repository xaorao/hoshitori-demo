"""Checks of the Desk's menu of checks (``hoshitori.desk_menu``), runnable against a mutant."""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

from hoshitori import ryo_catalogue
from hoshitori.ryo_pin import PIN

PROVIDERS = {
    "binance-spot": frozenset({"candles", "instruments"}),
    "kraken": frozenset({"candles"}),
    "binance-usdm": frozenset({"funding", "candles"}),
}
ALL_RYO = frozenset(PIN)
PINNED_HASH = "b0f7b4758a97bafd0f0445e3cc5449df140d635dd854fd01640ae033d791050d"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any, says: str) -> None:
        try:
            call()
        except self.mod.MenuError as exc:
            if not str(exc).startswith(says):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected to start with {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def costs(mod: types.ModuleType) -> Any:
    return mod.CostTable.from_latencies(
        {
            ("binance-spot", "candles"): [0.2, 0.4, 0.3],
            ("kraken", "candles"): [0.5, 0.7],
            ("binance-usdm", "candles"): [0.3],
            ("binance-usdm", "funding"): [0.25],
            ("ryo", "analyze_token"): [50.0, 60.0, 55.0],
            ("ryo", "deep_analysis"): [45.0, 52.0],
            ("ryo", "market_overview"): [120.0],
            ("ryo", "monitor_market_sentiment_shift"): [59.5],
        }
    )


def menu(mod: types.ModuleType, **over: Any) -> Any:
    args: dict[str, Any] = {"asset_id": "asset:btc", "symbol": "BTC", "providers": PROVIDERS, "ryo_available": ALL_RYO, "costs": costs(mod)}
    args.update(over)
    return mod.build_menu(**args)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("node types", mod.NODE_TYPES, ("OBSERVATION", "INTERPRETATION", "CONTEXT"))
    c.eq(
        "check types",
        mod.CHECK_TYPES,
        (
            "INDEPENDENT_PRICE_CHECK",
            "DERIVATIVES_CONTEXT",
            "RYO_TOKEN_READ",
            "RYO_DEEP_ANALYSIS",
            "MARKET_CONTEXT_OVERVIEW",
            "MARKET_SENTIMENT_SHIFT",
            "ADDRESS_FLAG_CHECK",
        ),
    )
    c.eq(
        "not servable, with the reason a person reads",
        dict(mod.NOT_SERVABLE),
        {
            "VERIFY_ORIGINAL_SOURCE": "safe fetch is not wired to a source",
            "CHECK_WALLET_IDENTITY": "no wallet source is wired",
            "NEWS_CORROBORATION": "no news source is wired",
        },
    )
    c.eq("omit reasons", mod.OMIT_REASONS, ("ASSET_UNRESOLVED", "NO_PROVIDER", "RYO_TOOL_NOT_AVAILABLE", "NOT_SERVABLE"))
    return c.bad


def check_full_menu(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = menu(mod)
    ids = [x.check_id for x in m.checks]
    c.eq(
        "the checks, sorted by id",
        ids,
        [
            "DERIVATIVES_CONTEXT:binance-usdm:funding",
            "INDEPENDENT_PRICE_CHECK:binance-spot:candles",
            "INDEPENDENT_PRICE_CHECK:binance-usdm:candles",
            "INDEPENDENT_PRICE_CHECK:kraken:candles",
            "MARKET_CONTEXT_OVERVIEW:ryo:market_overview",
            "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift",
            "RYO_DEEP_ANALYSIS:ryo:deep_analysis",
            "RYO_TOKEN_READ:ryo:analyze_token",
        ],
    )
    by_id = {x.check_id: x for x in m.checks}
    f = by_id["DERIVATIVES_CONTEXT:binance-usdm:funding"]
    p = by_id["INDEPENDENT_PRICE_CHECK:kraken:candles"]
    c.eq("price check node and args", (p.node_type, p.args), ("OBSERVATION", (("symbol", "BTC"),)))
    c.eq(
        "funding node and args",
        (f.node_type, f.provider, f.operation, f.args),
        ("OBSERVATION", "binance-usdm", "funding", (("symbol", "BTC"),)),
    )
    d = by_id["RYO_DEEP_ANALYSIS:ryo:deep_analysis"]
    c.eq("deep analysis node and args", (d.node_type, d.args), ("INTERPRETATION", (("include_perp", "true"), ("symbol", "BTC"))))
    t = by_id["RYO_TOKEN_READ:ryo:analyze_token"]
    c.eq("token read node and args", (t.node_type, t.args), ("OBSERVATION", (("symbol", "BTC"),)))
    for cid in ("MARKET_CONTEXT_OVERVIEW:ryo:market_overview", "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift"):
        c.eq(f"{cid} node and args", (by_id[cid].node_type, by_id[cid].args), ("CONTEXT", ()))
    c.eq("prerequisite of the deep analysis", d.prerequisites, ("RYO_TOKEN_READ:ryo:analyze_token",))
    c.eq("others have none", [x.prerequisites for x in m.checks if x.check_id != d.check_id], [()] * 7)
    c.eq("a provider's unlisted operation gives no check", "INDEPENDENT_PRICE_CHECK:binance-spot:instruments" in ids, False)
    c.eq("the asset and the symbol", (m.asset_id, m.symbol), ("asset:btc", "BTC"))
    c.eq(
        "not servable types are listed with their reason",
        [(o.check_type, o.reason) for o in m.omitted],
        [("CHECK_WALLET_IDENTITY", "NOT_SERVABLE"), ("NEWS_CORROBORATION", "NOT_SERVABLE"), ("VERIFY_ORIGINAL_SOURCE", "NOT_SERVABLE")],
    )
    return c.bad


def check_costs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = menu(mod)
    by_id = {x.check_id: x for x in m.checks}
    c.eq("median of three", by_id["INDEPENDENT_PRICE_CHECK:binance-spot:candles"].cost.seconds, "0.300")
    c.eq("median of two is their mean", by_id["INDEPENDENT_PRICE_CHECK:kraken:candles"].cost.seconds, "0.600")
    c.eq("the worst recorded", by_id["RYO_TOKEN_READ:ryo:analyze_token"].cost.worst_seconds, "60.000")
    c.eq(
        "a price check is two calls (the venue's clock, then the bars), every other check one",
        {x.check_id: x.cost.calls for x in m.checks},
        {
            "DERIVATIVES_CONTEXT:binance-usdm:funding": 1,
            "INDEPENDENT_PRICE_CHECK:binance-spot:candles": 2,
            "INDEPENDENT_PRICE_CHECK:binance-usdm:candles": 2,
            "INDEPENDENT_PRICE_CHECK:kraken:candles": 2,
            "MARKET_CONTEXT_OVERVIEW:ryo:market_overview": 1,
            "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift": 1,
            "RYO_DEEP_ANALYSIS:ryo:deep_analysis": 1,
            "RYO_TOKEN_READ:ryo:analyze_token": 1,
        },
    )
    c.eq("no model tokens for a gateway call", {x.cost.tokens for x in m.checks}, {0})
    table = mod.CostTable.from_latencies({("kraken", "candles"): [0.5]})
    c.eq("no recorded latency is unknown, never a constant", table.cost("binance-spot", "candles").seconds, None)
    c.eq("and says so", table.cost("binance-spot", "candles").known, False)
    c.eq("a recorded one is known", table.cost("kraken", "candles").known, True)
    c.eq(
        "a recorded one is one call and no tokens at the table",
        (table.cost("kraken", "candles").calls, table.cost("kraken", "candles").tokens),
        (1, 0),
    )
    unknown = table.cost("binance-spot", "candles")
    c.eq("an unknown cost is still one call and no tokens", (unknown.calls, unknown.tokens, unknown.worst_seconds), (1, 0, None))
    c.eq("zero seconds is a valid latency", mod.CostTable.from_latencies({("a", "b"): [0]}).cost("a", "b").seconds, "0.000")
    c.eq("median of four", mod.CostTable.from_latencies({("a", "b"): [4, 1, 3, 2]}).cost("a", "b").seconds, "2.500")
    c.eq("median of five", mod.CostTable.from_latencies({("a", "b"): [5, 1, 4, 2, 9]}).cost("a", "b").seconds, "4.000")
    c.eq("exact decimal arithmetic", mod.CostTable.from_latencies({("a", "b"): [0.1, 0.2]}).cost("a", "b").seconds, "0.150")
    c.refuses("a negative latency", lambda: mod.CostTable.from_latencies({("a", "b"): [-1.0]}), "a latency is a non-negative finite number")
    c.refuses("a boolean latency", lambda: mod.CostTable.from_latencies({("a", "b"): [True]}), "a latency is a non-negative finite number")
    c.refuses("nan", lambda: mod.CostTable.from_latencies({("a", "b"): [float("nan")]}), "a latency is a non-negative finite number")
    c.refuses("an empty sample list", lambda: mod.CostTable.from_latencies({("a", "b"): []}), "a latency list is not empty")
    return c.bad


def check_availability(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    pinned = ryo_catalogue.load_pin(PIN)
    c.eq("an unchanged catalogue offers everything", mod.ryo_available_from(pinned, pinned), ALL_RYO)
    tools = dict(pinned.tools)
    del tools["compare_tokens"]
    gone = ryo_catalogue.Catalogue(tools, "x")
    c.eq("a missing tool is not offered", mod.ryo_available_from(pinned, gone), ALL_RYO - {"compare_tokens"})
    tools = dict(pinned.tools)
    spec = tools["analyze_token"]
    tools["analyze_token"] = ryo_catalogue.ToolSpec(spec.name, spec.description, {**spec.schema, "required": ["symbol", "chain"]})
    changed = ryo_catalogue.Catalogue(tools, "y")
    c.eq("a changed schema is not offered", mod.ryo_available_from(pinned, changed), ALL_RYO - {"analyze_token"})
    tools = dict(pinned.tools)
    tools["new_tool"] = spec
    added = ryo_catalogue.Catalogue(tools, "z")
    c.eq("a tool the product was not built against is not offered", mod.ryo_available_from(pinned, added), ALL_RYO)
    m = menu(mod, ryo_available=ALL_RYO - {"analyze_token"})
    ids = [x.check_id for x in m.checks]
    c.eq("no token read without the tool", "RYO_TOKEN_READ:ryo:analyze_token" in ids, False)
    d = next(x for x in m.checks if x.check_id == "RYO_DEEP_ANALYSIS:ryo:deep_analysis")
    c.eq("the prerequisite is dropped when its check is not on the menu", d.prerequisites, ())
    c.eq("and the omission is listed", ("RYO_TOKEN_READ", "RYO_TOOL_NOT_AVAILABLE") in [(o.check_type, o.reason) for o in m.omitted], True)
    none = menu(mod, ryo_available=frozenset())
    c.eq(
        "with no RYO tool only the exchange checks remain",
        sorted({x.provider for x in none.checks}),
        ["binance-spot", "binance-usdm", "kraken"],
    )
    return c.bad


def check_unresolved_and_missing(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = menu(mod, asset_id=None, symbol=None)
    c.eq(
        "an unresolved asset gets only market context",
        [x.check_type for x in m.checks],
        ["MARKET_CONTEXT_OVERVIEW", "MARKET_SENTIMENT_SHIFT"],
    )
    reasons = {(o.check_type, o.reason) for o in m.omitted}
    for kind in ("INDEPENDENT_PRICE_CHECK", "DERIVATIVES_CONTEXT", "RYO_TOKEN_READ", "RYO_DEEP_ANALYSIS"):
        c.eq(f"{kind} omitted as unresolved", (kind, "ASSET_UNRESOLVED") in reasons, True)
    c.eq("no symbol in an unresolved menu", (m.asset_id, m.symbol), (None, None))
    c.eq(
        "omissions are sorted",
        [(o.check_type, o.reason) for o in m.omitted],
        [
            ("CHECK_WALLET_IDENTITY", "NOT_SERVABLE"),
            ("DERIVATIVES_CONTEXT", "ASSET_UNRESOLVED"),
            ("INDEPENDENT_PRICE_CHECK", "ASSET_UNRESOLVED"),
            ("NEWS_CORROBORATION", "NOT_SERVABLE"),
            ("RYO_DEEP_ANALYSIS", "ASSET_UNRESOLVED"),
            ("RYO_TOKEN_READ", "ASSET_UNRESOLVED"),
            ("VERIFY_ORIGINAL_SOURCE", "NOT_SERVABLE"),
        ],
    )
    only_kraken = menu(mod, providers={"kraken": frozenset({"candles"})})
    c.eq(
        "no funding provider omits the derivatives check",
        ("DERIVATIVES_CONTEXT", "NO_PROVIDER") in [(o.check_type, o.reason) for o in only_kraken.omitted],
        True,
    )
    no_candles = menu(mod, providers={})
    c.eq(
        "no provider omits the price check",
        ("INDEPENDENT_PRICE_CHECK", "NO_PROVIDER") in [(o.check_type, o.reason) for o in no_candles.omitted],
        True,
    )
    c.eq("and nothing else is invented", sorted({x.provider for x in no_candles.checks}), ["ryo"])
    return c.bad


def check_hash(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    m = menu(mod)
    again = menu(mod, providers=dict(reversed(list(PROVIDERS.items()))))
    c.eq("the order of providers changes nothing", again.sha256, m.sha256)
    c.eq("a repeat gives the same hash", menu(mod).sha256, m.sha256)
    c.eq("hash is hexadecimal", len(m.sha256), 64)
    c.eq("another asset changes the hash", menu(mod, asset_id="asset:eth", symbol="ETH").sha256 != m.sha256, True)
    c.eq("another provider set changes the hash", menu(mod, providers={"kraken": frozenset({"candles"})}).sha256 != m.sha256, True)
    c.eq(
        "another cost changes the hash",
        menu(mod, costs=mod.CostTable.from_latencies({("ryo", "analyze_token"): [1.0]})).sha256 != m.sha256,
        True,
    )
    c.eq("a removed tool changes the hash", menu(mod, ryo_available=ALL_RYO - {"deep_analysis"}).sha256 != m.sha256, True)
    c.eq("pinned hash of the full menu", m.sha256, PINNED_HASH)
    kinds = (mod.Menu, mod.MenuCheck, mod.Cost, mod.CostTable, mod.MenuRules, mod.Omitted, mod._Rule)
    c.eq("the records are frozen", [k.__dataclass_params__.frozen for k in kinds], [True] * len(kinds))
    c.eq("a cost is a decimal string", Decimal(m.checks[0].cost.seconds or "x") >= 0, True)
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.refuses("an asset without a symbol", lambda: menu(mod, symbol=None), "asset_id and symbol are given together or not at all")
    c.refuses("a symbol without an asset", lambda: menu(mod, asset_id=None), "asset_id and symbol are given together or not at all")
    c.refuses("an empty symbol", lambda: menu(mod, symbol=""), "symbol is a non-empty string")
    c.refuses("a lower-case symbol", lambda: menu(mod, symbol="btc"), "symbol is upper case")
    c.refuses("a symbol with one space", lambda: menu(mod, symbol="BT C"), "symbol is one token")
    c.refuses(
        "providers that are not a mapping", lambda: menu(mod, providers=[("kraken", frozenset({"candles"}))]), "providers is a mapping"
    )
    c.refuses("a symbol with a comma", lambda: menu(mod, symbol="BTC,ETH"), "symbol is one token")
    c.refuses(
        "an operation list that is not a set",
        lambda: menu(mod, providers={"kraken": ["candles"]}),
        "a provider's operations are a frozenset",
    )
    c.refuses("an empty provider name", lambda: menu(mod, providers={"": frozenset({"candles"})}), "a provider name is a non-empty string")
    c.refuses("ryo tools that are not a frozenset", lambda: menu(mod, ryo_available=["analyze_token"]), "ryo_available is a frozenset")
    c.refuses(
        "a tool the product does not know",
        lambda: menu(mod, ryo_available=frozenset({"check_safety"})),
        "ryo_available names only tools of the pin",
    )
    return c.bad


def check_the_screen(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    base = menu(mod)
    plain = menu(mod, providers={**PROVIDERS, "address-flag": frozenset({"check"})})
    c.eq("a provider of screens and no contract adds nothing: the menu is what it was", plain.sha256, base.sha256)
    contract = ("ethereum", "0x000000000000000000000000000000000000dEaD")
    shown = menu(mod, providers={**PROVIDERS, "address-flag": frozenset({"check"})}, contract=contract)
    check = {x.check_id: x for x in shown.checks}.get("ADDRESS_FLAG_CHECK:address-flag:check")
    c.eq("a contract adds one check of the screen", check is not None, True)
    if check is not None:
        c.eq(
            "of the screen's own provider, about that contract",
            (check.check_type, check.node_type, check.provider, check.operation, check.args, check.prerequisites),
            ("ADDRESS_FLAG_CHECK", "CONTEXT", "address-flag", "check", (("address", contract[1]), ("chain", "ethereum")), ()),
        )
        c.eq("it is one call", check.cost.calls, 1)
        c.eq("and its origin family is the sanctions list", mod.check_family(check), "PRIMARY_DOC:ofac")
    c.eq(
        "the others are as they were",
        [x.check_id for x in shown.checks if x.check_type != "ADDRESS_FLAG_CHECK"],
        [x.check_id for x in base.checks],
    )
    c.eq("and the menu is another menu", shown.sha256 == base.sha256, False)
    other = menu(mod, providers={**PROVIDERS, "address-flag": frozenset({"check"})}, contract=("base", contract[1]))
    c.eq("a menu of another contract is another menu", other.sha256 == shown.sha256, False)
    elsewhere = menu(mod, providers={**PROVIDERS, "other": frozenset({"check"}), "address-flag": frozenset({"candles"})}, contract=contract)
    c.eq(
        "only the screen's own provider, offering its operation, serves the screen",
        [(o.check_type, o.reason) for o in elsewhere.omitted if o.check_type == "ADDRESS_FLAG_CHECK"],
        [("ADDRESS_FLAG_CHECK", "NO_PROVIDER")],
    )
    none = menu(mod, contract=contract)
    c.eq(
        "a contract and no provider of screens leaves the screen out, and says why",
        [(o.check_type, o.reason) for o in none.omitted if o.check_type == "ADDRESS_FLAG_CHECK"],
        [("ADDRESS_FLAG_CHECK", "NO_PROVIDER")],
    )
    c.eq("and adds no check", [x.check_id for x in none.checks], [x.check_id for x in base.checks])
    c.refuses("a chain the skill does not cover", lambda: menu(mod, contract=("solana", contract[1])), "a contract is a chain")
    c.refuses("an address with a space", lambda: menu(mod, contract=("ethereum", contract[1] + " ")), "a contract is a chain")
    c.refuses("a contract that is not a pair", lambda: menu(mod, contract=("ethereum",)), "a contract is a chain")  # type: ignore[arg-type]
    c.refuses("a contract that is not text", lambda: menu(mod, contract=("ethereum", 5)), "a contract is a chain")  # type: ignore[arg-type]
    c.eq("the check type is a type of the menu", "ADDRESS_FLAG_CHECK" in mod.CHECK_TYPES, True)
    return c.bad


def check_families(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    by_id = {x.check_id: x for x in menu(mod).checks}
    got = {check_id: mod.check_family(x) for check_id, x in by_id.items()}
    c.eq(
        "two products of one exchange are one origin, another exchange is another, every RYO tool is one",
        got,
        {
            "DERIVATIVES_CONTEXT:binance-usdm:funding": "EXCHANGE:binance",
            "INDEPENDENT_PRICE_CHECK:binance-spot:candles": "EXCHANGE:binance",
            "INDEPENDENT_PRICE_CHECK:binance-usdm:candles": "EXCHANGE:binance",
            "INDEPENDENT_PRICE_CHECK:kraken:candles": "EXCHANGE:kraken",
            "MARKET_CONTEXT_OVERVIEW:ryo:market_overview": "RYO",
            "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift": "RYO",
            "RYO_DEEP_ANALYSIS:ryo:deep_analysis": "RYO",
            "RYO_TOKEN_READ:ryo:analyze_token": "RYO",
        },
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_the_screen,
        check_families,
        check_constants,
        check_full_menu,
        check_costs,
        check_availability,
        check_unresolved_and_missing,
        check_hash,
        check_refusals,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
