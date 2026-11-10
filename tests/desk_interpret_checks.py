"""Checks of how the Desk reads what a check brought (``hoshitori.desk_interpret``), runnable against a mutant.

The rules are fixed and written down before use: a stated move is supported when the data shows at least a quarter of a percent in that
direction over the hour, contradicted when it shows as much the other way, and otherwise not found in the source checked. Several sources
for one part combine so that a disagreement is shown as one and never averaged away.
"""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hoshitori import desk_acquire, desk_menu, evidence
from hoshitori.evidence import EvidenceItem
from hoshitori.lineage import EvidenceStore
from hoshitori.venue_data import Candle, CandleSeries
from tests.evidence_checks import observation

T0 = datetime(2026, 10, 29, 11, 5, tzinfo=UTC)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def series(first_open: str, last_close: str, count: int = 60) -> CandleSeries:
    """Flat at the first price until the last bar, which closes at the last price."""
    candles = []
    for n in range(count):
        opened = T0 + timedelta(minutes=n)
        close = last_close if n == count - 1 else first_open
        start = first_open if n == 0 else "7"  # only the first bar's open is the price the hour began at
        candles.append(Candle(opened, opened + timedelta(minutes=1), start, first_open, first_open, close))
    return CandleSeries(
        "binance-spot", "BTCUSDT", 60, T0, T0 + timedelta(minutes=count), T0 + timedelta(minutes=count), tuple(candles), 0, (), ()
    )


def acquired(**over: Any) -> Any:
    values: dict[str, Any] = {
        "outcome": "OK",
        "capture_ids": ("cap-1",),
        "evidence_ids": ("ev1",),
        "error_class": None,
        "detail": "",
        "spent": 1,
        "series": None,
    }
    values.update(over)
    return desk_acquire.Acquired(**values)


def check_for(kind: str, provider: str = "binance-spot", operation: str = "candles") -> Any:
    return desk_menu.MenuCheck(
        f"{kind}:{provider}:{operation}", kind, "OBSERVATION", provider, operation, (), (), desk_menu.Cost(1, None, None, 0)
    )


PRICE = "INDEPENDENT_PRICE_CHECK"


def ryo_store(change: str | None) -> tuple[EvidenceStore, str]:
    fields = (("market.price_usd", "100.5"),) + ((("performance.change_1h_pct", change),) if change is not None else ())
    item: EvidenceItem = observation(evidence, fields=fields, unknown_fields=())
    store = EvidenceStore()
    store.add(item, "d" * 64)
    return store, item.evidence_id


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the threshold", mod.MIN_MOVE, Decimal("0.0025"))
    c.eq("the fewest bars", mod.MIN_BARS, 55)
    c.eq("the window the Desk reads, in hours", mod.WINDOW_HOURS, 1)
    c.eq(
        "reasons",
        mod.REASONS,
        (
            "MOVE_SEEN",
            "MOVE_AGAINST",
            "MOVE_BELOW_THRESHOLD",
            "TOO_FEW_BARS",
            "FIELD_UNKNOWN",
            "WINDOW_NOT_READ",
            "FUNDING_DOES_NOT_SHOW_A_MOVE",
            "NO_INTERPRETATION",
            "SOURCE_FAILED",
            "SOURCE_CANNOT_SERVE",
        ),
    )
    c.eq("frozen", mod.NodeUpdate.__dataclass_params__.frozen, True)
    return c.bad


def check_price(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    store = EvidenceStore()

    def read(first: str, last: str, direction: str = "UP", count: int = 60, hours: int = 1) -> Any:
        return mod.interpret(check_for(PRICE), "obs", direction, hours, acquired(series=series(first, last, count)), store)

    up = read("1000", "1002.5")
    c.eq(
        "exactly a quarter of a percent up supports an upward move",
        (up.status, up.reason, up.layer, up.node_id),
        ("SUPPORTED", "MOVE_SEEN", "DETERMINISTIC", "obs"),
    )
    c.eq("just under it does not", read("1000", "1002.49").status, "NOT_FOUND_IN_SOURCES_CHECKED")
    c.eq("and says the move is below the threshold", read("1000", "1002.49").reason, "MOVE_BELOW_THRESHOLD")
    c.eq(
        "a quarter of a percent down contradicts it",
        (read("1000", "997.5").status, read("1000", "997.5").reason),
        ("CONTRADICTED", "MOVE_AGAINST"),
    )
    c.eq("just under that is only not found", read("1000", "997.51").status, "NOT_FOUND_IN_SOURCES_CHECKED")
    c.eq(
        "a fall supports a stated fall",
        (read("1000", "997.5", "DOWN").status, read("1000", "997.5", "DOWN").reason),
        ("SUPPORTED", "MOVE_SEEN"),
    )
    c.eq("a rise contradicts a stated fall", read("1000", "1002.5", "DOWN").status, "CONTRADICTED")
    c.eq("the evidence it read is kept", up.evidence_ids, ("ev1",))
    c.eq("55 bars are enough", read("1000", "1010", count=55).status, "SUPPORTED")
    c.eq(
        "54 are not",
        (read("1000", "1010", count=54).status, read("1000", "1010", count=54).reason),
        ("NOT_FOUND_IN_SOURCES_CHECKED", "TOO_FEW_BARS"),
    )
    none = mod.interpret(check_for(PRICE), "obs", "UP", 1, acquired(series=None), store)
    c.eq("no series is too few bars", (none.status, none.reason), ("NOT_FOUND_IN_SOURCES_CHECKED", "TOO_FEW_BARS"))
    far = read("1000", "1010", hours=4)
    c.eq(
        "a window longer than the hour read cannot be checked",
        (far.status, far.reason, far.layer),
        ("NOT_CHECKABLE", "WINDOW_NOT_READ", "DETERMINISTIC"),
    )
    c.eq("a prices as exact decimals", read("0.1", "0.1003", count=60).status, "SUPPORTED")
    return c.bad


def check_other_sources(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    store, ident = ryo_store("0.30")
    token = check_for("RYO_TOKEN_READ", "ryo", "analyze_token")
    r = mod.interpret(token, "obs", "UP", 1, acquired(evidence_ids=(ident,)), store)
    c.eq("RYO's own hour supports a rise", (r.status, r.reason, r.layer), ("SUPPORTED", "MOVE_SEEN", "DETERMINISTIC"))
    c.eq(
        "a percent is a hundredth",
        mod.interpret(token, "obs", "UP", 1, acquired(evidence_ids=(ryo_store("0.25")[1],)), ryo_store("0.25")[0]).status,
        "SUPPORTED",
    )
    s2, i2 = ryo_store("0.24")
    c.eq(
        "below it is not found",
        (mod.interpret(token, "obs", "UP", 1, acquired(evidence_ids=(i2,)), s2).status),
        "NOT_FOUND_IN_SOURCES_CHECKED",
    )
    s3, i3 = ryo_store("-0.30")
    c.eq("a fall contradicts a rise", mod.interpret(token, "obs", "UP", 1, acquired(evidence_ids=(i3,)), s3).status, "CONTRADICTED")
    s4, i4 = ryo_store(None)
    r = mod.interpret(token, "obs", "UP", 1, acquired(evidence_ids=(i4,)), s4)
    c.eq("a field RYO did not give is not found", (r.status, r.reason), ("NOT_FOUND_IN_SOURCES_CHECKED", "FIELD_UNKNOWN"))
    s5, i5 = ryo_store(None)
    s5.add(observation(evidence, fields=(("performance.change_1h_pct", "0.50"),), unknown_fields=(), capture_id="cap-9"), "e" * 64)
    other_id = next(i for i in s5.ids() if i != i5)
    c.eq(
        "only the first item, the observation, is read",
        mod.interpret(token, "obs", "UP", 1, acquired(evidence_ids=(i5, other_id)), s5).reason,
        "FIELD_UNKNOWN",
    )
    r = mod.interpret(token, "obs", "UP", 4, acquired(evidence_ids=(ident,)), store)
    c.eq("a longer window is not read from RYO's hour", (r.status, r.reason), ("NOT_CHECKABLE", "WINDOW_NOT_READ"))
    f = mod.interpret(check_for("DERIVATIVES_CONTEXT", "binance-funding", "funding"), "obs", "UP", 1, acquired(), EvidenceStore())
    c.eq("funding shows no price move", (f.status, f.reason, f.layer), ("NOT_CHECKABLE", "FUNDING_DOES_NOT_SHOW_A_MOVE", "DETERMINISTIC"))
    other = mod.interpret(check_for("MARKET_CONTEXT_OVERVIEW", "ryo", "market_overview"), "obs", "UP", 1, acquired(), EvidenceStore())
    c.eq("a market read is not an interpretation of a part", (other.status, other.reason), ("NOT_CHECKABLE", "NO_INTERPRETATION"))
    return c.bad


def check_failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for error in ("CALL_FAILED", "TIMED_OUT", "UNREADABLE", "NOT_LIVE", "BINDING_MISMATCH"):
        r = mod.interpret(check_for(PRICE), "obs", "UP", 1, acquired(outcome="FAILED", error_class=error, evidence_ids=()), EvidenceStore())
        c.eq(
            f"{error}: the source was unavailable",
            (r.status, r.reason, r.layer, r.evidence_ids),
            ("SOURCE_UNAVAILABLE", "SOURCE_FAILED", "NONE", ()),
        )
    for error in ("NO_ADAPTER", "NO_ASSET", "NO_INSTRUMENT", "BAD_REQUEST", "UNKNOWN_CHECK_TYPE"):
        r = mod.interpret(check_for(PRICE), "obs", "UP", 1, acquired(outcome="FAILED", error_class=error, evidence_ids=()), EvidenceStore())
        c.eq(
            f"{error}: this source cannot serve the asset", (r.status, r.reason, r.layer), ("NOT_CHECKABLE", "SOURCE_CANNOT_SERVE", "NONE")
        )
    r = mod.interpret(
        check_for(PRICE),
        "obs",
        "UP",
        1,
        acquired(outcome="NOT_STARTED", error_class="DEADLINE_UNREACHABLE", evidence_ids=()),
        EvidenceStore(),
    )
    c.eq("a call not started cannot serve", (r.status, r.reason), ("NOT_CHECKABLE", "SOURCE_CANNOT_SERVE"))
    return c.bad


def check_combine(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    cases = (
        ([], "UNCHECKED"),
        (["SUPPORTED"], "SUPPORTED"),
        (["CONTRADICTED"], "CONTRADICTED"),
        (["SUPPORTED", "CONTRADICTED"], "MIXED"),
        (["CONTRADICTED", "SUPPORTED", "NOT_CHECKABLE"], "MIXED"),
        (["SUPPORTED", "NOT_FOUND_IN_SOURCES_CHECKED"], "SUPPORTED"),
        (["CONTRADICTED", "SOURCE_UNAVAILABLE"], "CONTRADICTED"),
        (["NOT_FOUND_IN_SOURCES_CHECKED", "NOT_CHECKABLE", "SOURCE_UNAVAILABLE"], "SOURCE_UNAVAILABLE"),
        (["NOT_FOUND_IN_SOURCES_CHECKED", "SOURCE_UNAVAILABLE"], "SOURCE_UNAVAILABLE"),
        (["NOT_CHECKABLE", "SOURCE_UNAVAILABLE"], "SOURCE_UNAVAILABLE"),
        (["SOURCE_UNAVAILABLE"], "SOURCE_UNAVAILABLE"),
        (["NOT_CHECKABLE"], "NOT_CHECKABLE"),
        (["NOT_FOUND_IN_SOURCES_CHECKED"], "NOT_FOUND_IN_SOURCES_CHECKED"),
        (["SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE"], "SOURCE_UNAVAILABLE"),
    )
    for statuses, want in cases:
        c.eq(f"{statuses}", mod.combine(statuses), want)
    c.eq("the order given does not matter", mod.combine(["NOT_CHECKABLE", "NOT_FOUND_IN_SOURCES_CHECKED"]), "NOT_FOUND_IN_SOURCES_CHECKED")
    c.eq(
        "the order of an outage does not matter", mod.combine(["SOURCE_UNAVAILABLE", "NOT_FOUND_IN_SOURCES_CHECKED"]), "SOURCE_UNAVAILABLE"
    )
    c.eq("the rules are versioned", mod.RULES_VERSION, "desk-interpret-2")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_price, check_other_sources, check_failures, check_combine):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
