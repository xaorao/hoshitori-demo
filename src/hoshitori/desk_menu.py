"""The Desk's menu: the checks it may run for one candidate, fixed before any policy chooses among them.

A check is on the menu only if the product can serve it today from a registered source: an exchange adapter that offers the operation, or a
RYO tool that is in the live catalogue with the structure the product was built against. A check the product cannot serve is listed as
omitted with its reason, so the menu never promises a source that is not wired. Expected cost comes from recorded latencies, never from a
typed constant; a check with no recorded latency says its cost is unknown. The menu is data: the next-check rule ranks over it and the
gateway call is made from one of its entries, so an id that is not on the menu cannot be requested.
"""

from __future__ import annotations

import dataclasses
import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Final

from hoshitori import address_screen, canonical, ryo_catalogue
from hoshitori.ryo_pin import PIN

NODE_TYPES: Final = ("OBSERVATION", "INTERPRETATION", "CONTEXT")
CHECK_TYPES: Final = (
    "INDEPENDENT_PRICE_CHECK",
    "DERIVATIVES_CONTEXT",
    "RYO_TOKEN_READ",
    "RYO_DEEP_ANALYSIS",
    "MARKET_CONTEXT_OVERVIEW",
    "MARKET_SENTIMENT_SHIFT",
    "ADDRESS_FLAG_CHECK",
)
NOT_SERVABLE: Final = {
    "VERIFY_ORIGINAL_SOURCE": "safe fetch is not wired to a source",
    "CHECK_WALLET_IDENTITY": "no wallet source is wired",
    "NEWS_CORROBORATION": "no news source is wired",
}
OMIT_REASONS: Final = ("ASSET_UNRESOLVED", "NO_PROVIDER", "RYO_TOOL_NOT_AVAILABLE", "NOT_SERVABLE")
RYO: Final = "ryo"
SCREEN: Final = address_screen.PROVIDER
SCREEN_OPERATION: Final = "check"
_ADDRESS: Final = re.compile(r"[A-Za-z0-9]{1,128}")
DEFAULT_PREREQUISITES: Final = {"RYO_DEEP_ANALYSIS": ("RYO_TOKEN_READ",)}
_QUANTUM: Final = Decimal("0.001")


class MenuError(ValueError):
    """An argument that is not valid."""


@dataclass(frozen=True)
class Cost:
    """What one check is expected to spend: calls, seconds (median and worst of the recorded latencies) and model tokens."""

    calls: int
    seconds: str | None
    worst_seconds: str | None
    tokens: int

    @property
    def known(self) -> bool:
        return self.seconds is not None


def _decimal(text: str) -> str:
    return str(Decimal(text).quantize(_QUANTUM, rounding=ROUND_HALF_EVEN))


@dataclass(frozen=True)
class CostTable:
    entries: Mapping[tuple[str, str], Cost]

    @classmethod
    def from_latencies(cls, samples: Mapping[tuple[str, str], Sequence[float]]) -> CostTable:
        entries: dict[tuple[str, str], Cost] = {}
        for key, values in samples.items():
            if not values:
                raise MenuError("a latency list is not empty")
            numbers: list[Decimal] = []
            for value in values:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                    raise MenuError("a latency is a non-negative finite number")
                numbers.append(Decimal(str(value)))
            ordered = sorted(numbers)
            mid = len(ordered) // 2
            median = ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2
            entries[key] = Cost(1, _decimal(str(median)), _decimal(str(ordered[-1])), 0)
        return cls(entries)

    def cost(self, provider: str, operation: str) -> Cost:
        return self.entries.get((provider, operation), Cost(1, None, None, 0))


@dataclass(frozen=True)
class MenuCheck:
    check_id: str
    check_type: str
    node_type: str
    provider: str
    operation: str
    args: tuple[tuple[str, str], ...]
    prerequisites: tuple[str, ...]
    cost: Cost


def check_family(check: MenuCheck) -> str:
    """The origin family of what a check brings, as contract 40 counts it: every RYO tool is the one family ``RYO`` (their shared backend
    cannot be excluded); an exchange is its own family, and two products of one exchange (``binance-spot`` and ``binance-usdm``) are the
    one family ``EXCHANGE:binance``, because they are not known to be independent of each other."""
    if check.provider == RYO:
        return "RYO"
    if check.provider == SCREEN:
        return "PRIMARY_DOC:ofac"  # the sanctions list; the issuers' freezes are read from the chain beside it
    return f"EXCHANGE:{check.provider.partition('-')[0]}"


@dataclass(frozen=True)
class Omitted:
    check_type: str
    reason: str


@dataclass(frozen=True)
class Menu:
    asset_id: str | None
    symbol: str | None
    checks: tuple[MenuCheck, ...]
    omitted: tuple[Omitted, ...]
    sha256: str


@dataclass(frozen=True)
class MenuRules:
    prerequisites: Mapping[str, tuple[str, ...]] = field(default_factory=lambda: dict(DEFAULT_PREREQUISITES))


@dataclass(frozen=True)
class _Rule:
    check_type: str
    node_type: str
    operation: str
    needs_asset: bool
    ryo: bool
    extra_args: tuple[tuple[str, str], ...] = ()
    calls: int = 1


_RULES: Final = (
    _Rule("INDEPENDENT_PRICE_CHECK", "OBSERVATION", "candles", True, False, calls=2),  # the venue's clock, then the bars
    _Rule("DERIVATIVES_CONTEXT", "OBSERVATION", "funding", True, False),
    _Rule("RYO_TOKEN_READ", "OBSERVATION", "analyze_token", True, True),
    _Rule("RYO_DEEP_ANALYSIS", "INTERPRETATION", "deep_analysis", True, True, (("include_perp", "true"),)),
    _Rule("MARKET_CONTEXT_OVERVIEW", "CONTEXT", "market_overview", False, True),
    _Rule("MARKET_SENTIMENT_SHIFT", "CONTEXT", "monitor_market_sentiment_shift", False, True),
)


def ryo_available_from(pinned: ryo_catalogue.Catalogue, live: ryo_catalogue.Catalogue) -> frozenset[str]:
    """The RYO tools the product may call: in the live catalogue, in the pin, and with no change that makes them unusable. A tool the
    product was not built against is not offered; a reworded description does not remove one."""
    blocked = {d.tool for d in ryo_catalogue.diff(pinned, live) if d.blocks}
    return frozenset(pinned.names() & live.names()) - blocked


def _validate(asset_id: object, symbol: object, providers: object, ryo_available: object, contract: object = None) -> None:
    if (asset_id is None) != (symbol is None):
        raise MenuError("asset_id and symbol are given together or not at all")
    if symbol is not None:
        if not isinstance(symbol, str) or not symbol:
            raise MenuError("symbol is a non-empty string")
        if symbol != symbol.upper():
            raise MenuError("symbol is upper case")
        if "," in symbol or any(ch.isspace() for ch in symbol):
            raise MenuError("symbol is one token")
    if not isinstance(providers, Mapping):
        raise MenuError("providers is a mapping of name to operations")
    for name, operations in providers.items():
        if not isinstance(name, str) or not name:
            raise MenuError("a provider name is a non-empty string")
        if not isinstance(operations, frozenset):
            raise MenuError("a provider's operations are a frozenset")
    if not isinstance(ryo_available, frozenset):
        raise MenuError("ryo_available is a frozenset")
    if not ryo_available <= set(PIN):
        raise MenuError("ryo_available names only tools of the pin")
    if contract is not None and (
        not isinstance(contract, tuple)
        or len(contract) != 2
        or contract[0] not in address_screen.CHAINS
        or not isinstance(contract[1], str)
        or _ADDRESS.fullmatch(contract[1]) is None
    ):
        raise MenuError("a contract is a chain the skill covers and an address of letters and digits")


def build_menu(
    asset_id: str | None,
    symbol: str | None,
    providers: Mapping[str, frozenset[str]],
    ryo_available: frozenset[str],
    costs: CostTable,
    rules: MenuRules | None = None,
    contract: tuple[str, str] | None = None,
) -> Menu:
    """The menu for one candidate. ``asset_id`` and ``symbol`` come from the asset registry; both are ``None`` when the registry does not
    resolve the candidate, which leaves only the market-context checks."""
    _validate(asset_id, symbol, providers, ryo_available, contract)
    rules = rules or MenuRules()
    checks: list[MenuCheck] = []
    omitted: list[Omitted] = [Omitted(kind, "NOT_SERVABLE") for kind in sorted(NOT_SERVABLE)]
    token_args = (("symbol", symbol),) if symbol is not None else ()
    for rule in _RULES:
        if rule.needs_asset and symbol is None:
            omitted.append(Omitted(rule.check_type, "ASSET_UNRESOLVED"))
            continue
        args = tuple(sorted(rule.extra_args + (token_args if rule.needs_asset else ())))
        if rule.ryo:
            if rule.operation not in ryo_available:
                omitted.append(Omitted(rule.check_type, "RYO_TOOL_NOT_AVAILABLE"))
                continue
            names = [RYO]
        else:
            names = sorted(name for name, operations in providers.items() if rule.operation in operations)
            if not names:
                omitted.append(Omitted(rule.check_type, "NO_PROVIDER"))
                continue
        for name in names:
            checks.append(
                MenuCheck(
                    f"{rule.check_type}:{name}:{rule.operation}",
                    rule.check_type,
                    rule.node_type,
                    name,
                    rule.operation,
                    args,
                    (),
                    dataclasses.replace(costs.cost(name, rule.operation), calls=rule.calls),
                )
            )
    if contract is not None:  # the token's own contract, from the registry: a screen of it is a check the menu may offer
        chain, address = contract
        names = sorted(name for name, operations in providers.items() if name == SCREEN and SCREEN_OPERATION in operations)
        if not names:
            omitted.append(Omitted("ADDRESS_FLAG_CHECK", "NO_PROVIDER"))
        for name in names:
            checks.append(
                MenuCheck(
                    f"ADDRESS_FLAG_CHECK:{name}:{SCREEN_OPERATION}",
                    "ADDRESS_FLAG_CHECK",
                    "CONTEXT",
                    name,
                    SCREEN_OPERATION,
                    (("address", address), ("chain", chain)),
                    (),
                    dataclasses.replace(costs.cost(name, SCREEN_OPERATION), calls=1),
                )
            )
    present = {check.check_type: check.check_id for check in checks}
    resolved = [
        MenuCheck(
            c.check_id,
            c.check_type,
            c.node_type,
            c.provider,
            c.operation,
            c.args,
            tuple(present[t] for t in rules.prerequisites.get(c.check_type, ()) if t in present),
            c.cost,
        )
        for c in checks
    ]
    resolved.sort(key=lambda c: c.check_id)
    omitted.sort(key=lambda o: (o.check_type, o.reason))
    record: dict[str, canonical.JsonValue] = {
        "asset_id": asset_id,
        "symbol": symbol,
        "checks": [
            {
                "id": c.check_id,
                "node": c.node_type,
                "args": [[k, v] for k, v in c.args],
                "prerequisites": list(c.prerequisites),
                "calls": c.cost.calls,
                "seconds": c.cost.seconds,
                "worst": c.cost.worst_seconds,
                "tokens": c.cost.tokens,
            }
            for c in resolved
        ],
        "omitted": [[o.check_type, o.reason] for o in omitted],
    }
    digest = hashlib.sha256(canonical.dumps(record)).hexdigest()
    return Menu(asset_id, symbol, tuple(resolved), tuple(omitted), digest)
