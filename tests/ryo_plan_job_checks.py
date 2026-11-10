"""Scenarios for the settlement of arm R's plans (``hoshitori.ryo_plan_job``), runnable against the real module or a mutant of it.

A real cycle is run on a scripted market (``settle_job_checks.Rig``: the reference minute prices every token at 100; after it BTC trades at
101 and ETH at 99), reads of RYO are added for its two candidates, the 24-hour windows are settled by the real settlement pass, and then the
plan pass runs. Every expected outcome is worked out by hand from those prices and the plan's levels.
"""

from __future__ import annotations

import tempfile
import types
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import ryo_plan, ryo_read, settle_job
from tests.settle_job_checks import MINUTE, Rig

DAY = 24 * 60 * MINUTE


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


class Plans:
    """A run cycle with RYO's reads added, its windows settled, and the plan pass run by the module under test."""

    def __init__(self, root: Path, plans: dict[str, tuple[str, str, str] | None], **kw: Any) -> None:
        self.rig = Rig(root, **kw)
        manifest = self.rig.w.records("manifest")[0]["body"]
        self.ids: dict[str, str] = {}
        for entry in manifest["entries"]:
            symbol = entry["symbol"]
            if symbol not in plans:
                continue
            levels = plans[symbol]
            plan = None if levels is None else ryo_read.Plan(*levels)
            body = ryo_read.body_of(
                cycle_id=manifest["cycle_id"],
                candidate_id=entry["candidate_id"],
                symbol=symbol,
                call="cautious",
                plan=plan,
                plan_availability="available" if plan is not None else "unavailable",
                evidence_id=f"ev{symbol}",
                capture_id=f"cap-{symbol}",
            )
            record = ryo_read.to_record(body, "urn:test", self.rig.origin - 10 * MINUTE)
            self.rig.w.ledger.submit(record)
            self.ids[symbol] = str(record["id"])
        self.rig.w.ledger.append_pending()

    def settle_windows(self, at: int) -> None:
        self.rig.at(at)
        self.rig.run(settle_job)

    def plan(self, mod: types.ModuleType, at: int, write: bool = True) -> Any:
        self.rig.at(at)
        passed = mod.plan_pass(self.rig.services(settle_job), self.rig.w.records(), at, wait_s=5.0)
        if write:
            for record in passed.records:
                self.rig.w.ledger.submit(record)
            self.rig.w.ledger.append_pending()
        return passed

    def settled(self) -> dict[str, dict[str, Any]]:
        by_read = {r["body"]["read_id"]: r["body"] for r in self.rig.w.records(ryo_plan.TYPE)}
        return {symbol: by_read[rid] for symbol, rid in self.ids.items() if rid in by_read}


def brief(body: dict[str, Any]) -> tuple[Any, ...]:
    return (body["state"], body["outcome"], body["reason"], body["touch_open_ms"] is not None)


def check_outcomes(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    p = Plans(root / "outcomes", {"BTC": ("100", "95", "100.5"), "ETH": ("100", "99.5", "110")})
    end = p.rig.origin + DAY
    early = p.plan(mod, p.rig.origin + MINUTE)
    c.eq("before the window is settled nothing is written and nothing waits", (early.records, early.pending, early.failed), ((), (), ()))
    p.settle_windows(end + MINUTE)
    first = p.plan(mod, end + MINUTE)
    got = p.settled()
    c.eq("BTC trades at 101 from the first minute: its target of 100.5 first", brief(got["BTC"]), ("SETTLED", "TARGET", None, True))
    c.eq("ETH trades at 99: its stop of 99.5 first", brief(got["ETH"]), ("SETTLED", "STOP", None, True))
    c.eq("both touched in the first minute of the window", {b["touch_open_ms"] for b in got.values()}, {p.rig.origin})
    c.eq("each starts at the window's price, 100", {b["p0"] for b in got.values()}, {"100"})
    c.eq("and names the 24-hour call whose window it used", all(b["window_forecast_id"].startswith("fc") for b in got.values()), True)
    c.eq(
        "records in the order of the reads",
        [r["body"]["read_id"] for r in first.records],
        sorted(p.ids.values(), key=list(p.ids.values()).index),
    )
    again = p.plan(mod, end + 2 * MINUTE)
    c.eq("a second pass writes nothing", (again.records, again.pending), ((), ()))
    made = {r["id"]: r["body"] for r in p.rig.w.records("forecast")}
    c.eq(
        "the window named is the 24-hour directional call of the token, never another horizon or kind",
        {(made[b["window_forecast_id"]]["contract"], made[b["window_forecast_id"]]["horizon_ms"]) for b in got.values()},
        {("D", DAY)},
    )
    c.eq(
        "the bars are read as this pass's own",
        {call.caller for call in p.rig.w.runner.calls if call.operation == "candles"} >= {"ryo-plan"},
        True,
    )
    try:
        first.pending = ()  # type: ignore[misc]
        c.bad.append("a pass's result can be changed after it is made")
    except AttributeError:
        pass
    ready = mod._ready(p.rig.w.records())
    c.eq("nothing is ready once every plan is settled", ready, [])
    return c.bad


def check_neither_past_and_both(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    p = Plans(root / "neither", {"BTC": ("100", "90", "110"), "ETH": ("98", "90", "99")})
    end = p.rig.origin + DAY
    p.settle_windows(end + MINUTE)
    before = len(p.rig.market.calls)
    p.plan(mod, end + MINUTE)
    got = p.settled()
    c.eq("BTC at 101 never reaches 90 or 110: neither", brief(got["BTC"]), ("SETTLED", "TIMEOUT", None, False))
    c.eq("ETH's target of 99 is below the start price of 100: already past", brief(got["ETH"]), ("ALREADY_PAST", None, None, False))
    written = [r["body"]["read_id"] for r in p.rig.w.records(ryo_plan.TYPE)]
    c.eq("written in the order of the reads, though the plan already past was decided first", written, [p.ids["BTC"], p.ids["ETH"]])
    p2 = Plans(root / "past-only", {"ETH": ("98", "90", "99")})
    p2.settle_windows(end + MINUTE)
    calls = len(p2.rig.market.calls)
    p2.plan(mod, end + MINUTE)
    c.eq("a plan already past reads no bar", len(p2.rig.market.calls), calls)
    c.eq("but the first rig read bars for BTC's plan", len(p.rig.market.calls) > before, True)
    p3 = Plans(root / "both", {"BTC": ("100", "99.5", "100.5")})
    p3.rig.market.wide[("binance-spot", "BTC", p3.rig.origin)] = (Decimal(99), Decimal(101))
    p3.settle_windows(end + MINUTE)
    p3.plan(mod, end + MINUTE)
    c.eq(
        "a first minute that touched both levels is ambiguous", brief(p3.settled()["BTC"]), ("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", True)
    )
    return c.bad


def check_windows_that_do_not_settle(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    p = Plans(root / "disagree", {"BTC": ("100", "95", "100.5")})
    end = p.rig.origin + DAY
    p.rig.market.override[("okx", "BTC", p.rig.origin - MINUTE)] = Decimal(105)  # the second oracle's start price is 5% away
    p.settle_windows(end + MINUTE)
    p.plan(mod, end + MINUTE)
    c.eq(
        "a window the oracles disagree on settles no plan: it takes the window's state and reason, and the window's start price",
        (brief(p.settled()["BTC"]), p.settled()["BTC"]["p0"]),
        (("UNSETTLEABLE", None, "UNSETTLEABLE_DISAGREE", False), "100"),
    )
    return c.bad


def check_missing_bars_and_clocks(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    p = Plans(root / "missing", {"BTC": ("100", "95", "100.5")})
    end = p.rig.origin + DAY
    p.settle_windows(end + MINUTE)
    p.rig.market.override[("binance-spot", "BTC", p.rig.origin)] = None  # the first minute of the window has no bar
    waiting = p.plan(mod, end + MINUTE)
    c.eq("a missing bar keeps the plan pending", (waiting.records, waiting.pending), ((), (p.ids["BTC"],)))
    late = p.plan(mod, end + settle_job.RETRY_WINDOW_MS)
    c.eq(
        "once the retry window has passed it is unsettleable",
        brief(p.settled()["BTC"]),
        ("UNSETTLEABLE", None, "UNSETTLEABLE_MISSING", False),
    )
    c.eq("and is written once", len(late.records), 1)
    p.rig.market.override.pop(("binance-spot", "BTC", p.rig.origin))

    q = Plans(root / "no-clock", {"BTC": ("100", "95", "100.5")})
    q.settle_windows(end + MINUTE)
    q.rig.market.no_time.add("binance-spot")
    held = q.plan(mod, end + MINUTE)
    c.eq("a venue that gives no time defers the plan", (held.records, held.pending), ((), (q.ids["BTC"],)))
    q.plan(mod, end + settle_job.RETRY_WINDOW_MS)
    c.eq("and voids it when it gave none for the whole retry window", brief(q.settled()["BTC"]), ("VOID", None, "VOID_OUTAGE", False))
    q.rig.market.no_time.discard("binance-spot")
    one_short = Plans(root / "no-clock-short", {"BTC": ("100", "95", "100.5")})
    one_short.settle_windows(end + MINUTE)
    one_short.rig.market.no_time.add("binance-spot")
    edge = one_short.plan(mod, end + settle_job.RETRY_WINDOW_MS - 1)
    c.eq("a millisecond before the retry window ends it still waits", (edge.records, edge.pending), ((), (one_short.ids["BTC"],)))
    return c.bad


def check_reads_with_nothing_to_settle(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    p = Plans(root / "no-plan", {"BTC": None, "ETH": ("100", "99.5", "110")})
    end = p.rig.origin + DAY
    p.settle_windows(end + MINUTE)
    p.plan(mod, end + MINUTE)
    c.eq("a read with no plan has no plan settlement; the other settles", sorted(p.settled()), ["ETH"])
    return c.bad


def check_ties_gaps_and_failures(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori.timeline import outcome_candle_open
    from tests.registry_checks import inst, lst, t

    c = Case(mod)
    end_of = lambda r: r.origin + DAY  # noqa: E731
    # a window that ended where it started is a tie: it has a start price, and the plan is settled on its bars all the same
    p = Plans(root / "tie", {"BTC": ("100", "95", "100.5")})
    p.rig.market.override[("binance-spot", "BTC", outcome_candle_open(p.rig.origin, DAY))] = Decimal(100)
    p.rig.market.override[("okx", "BTC", outcome_candle_open(p.rig.origin, DAY))] = Decimal(100)
    p.settle_windows(end_of(p.rig) + MINUTE)
    states = {r["body"]["reason"] for r in p.rig.w.records("settlement")}
    c.eq("the 24-hour window tied", "VOID_TIE" in states, True)
    p.plan(mod, end_of(p.rig) + MINUTE)
    c.eq("a tied window settles its plan", brief(p.settled()["BTC"]), ("SETTLED", "TARGET", None, True))
    # a read whose cycle has no 24-hour call on its token is passed over, quietly
    q = Plans(root / "no-window", {"BTC": ("100", "95", "100.5")})
    stray = ryo_read.to_record(
        ryo_read.body_of(
            cycle_id="cyelsewhere", candidate_id="cdx", symbol="BTC", call="cautious", plan=ryo_read.Plan("100", "95", "100.5"),
            plan_availability="available", evidence_id="evx", capture_id="cap-x",
        ),
        "urn:test",
        q.rig.origin,
    )  # fmt: skip
    q.rig.w.ledger.submit(stray)
    q.rig.w.ledger.append_pending()
    q.settle_windows(end_of(q.rig) + MINUTE)
    seen = q.plan(mod, end_of(q.rig) + MINUTE)
    c.eq(
        "a read with no window: nothing written for it, nothing failed, the other settles",
        (sorted(q.settled()), seen.failed, len(seen.records)),
        (["BTC"], (), 1),
    )
    # a pair the registry no longer knows at the end of a settled window is a contradiction: reported, nothing settled
    g = Plans(root / "no-pair", {"BTC": ("100", "95", "100.5"), "ETH": ("100", "99.5", "110")})
    g.settle_windows(end_of(g.rig) + MINUTE)
    g.rig.w.registry.admit(lst("okx", inst("okx", "ETH-USDT", "ETH")), scope="spot", known_at=t(400), capture_id="cap-late")
    gone = g.plan(mod, end_of(g.rig) + MINUTE)
    c.eq(
        "no pair at the end of the window: reported, not settled",
        (gone.failed, sorted(g.settled())),
        (((g.ids["BTC"], mod.NO_PAIR),), ["ETH"]),
    )
    # one plan that cannot be recorded does not stop the others, and its reason is cut to 200 characters
    for label, plans, broken in (
        ("a plan already past", {"BTC": ("98", "90", "99"), "ETH": ("100", "99.5", "110")}, "BTC"),
        ("a plan settled on its bars", {"BTC": ("100", "95", "100.5"), "ETH": ("100", "99.5", "110")}, "ETH"),
    ):
        h = Plans(root / f"broken-{broken}", plans)
        h.settle_windows(end_of(h.rig) + MINUTE)
        real = ryo_plan.body_of

        def refuse(read_id: str, *args: Any, real: Any = real, target: str = h.ids[broken]) -> Any:
            if read_id == target:
                raise ryo_plan.RyoPlanError("x" * 300)
            return real(read_id, *args)

        ryo_plan.body_of = refuse  # type: ignore[assignment]
        try:
            out = h.plan(mod, end_of(h.rig) + MINUTE)
        finally:
            ryo_plan.body_of = real  # type: ignore[assignment]
        other = "ETH" if broken == "BTC" else "BTC"
        c.eq(f"{label} that cannot be recorded: failed, cut to 200 characters", out.failed, ((h.ids[broken], "x" * 200),))
        c.eq(f"{label} that cannot be recorded: the other is settled", sorted(h.settled()), [other])
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "ties_gaps_and_failures": check_ties_gaps_and_failures,
    "outcomes": check_outcomes,
    "neither_past_and_both": check_neither_past_and_both,
    "windows_that_do_not_settle": check_windows_that_do_not_settle,
    "missing_bars_and_clocks": check_missing_bars_and_clocks,
    "reads_with_nothing_to_settle": check_reads_with_nothing_to_settle,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        with tempfile.TemporaryDirectory() as d:
            try:
                bad += [f"{name}: {message}" for message in scenario(mod, Path(d))]
            except Exception as exc:  # a crash in the scenario is a failure of the module under test
                bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
