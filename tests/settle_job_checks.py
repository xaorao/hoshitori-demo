"""Checks of the settlement pass (``hoshitori.settle_job``), runnable against a mutant.

A real cycle of the league is run first (fake exchange, real registry, ledger and commitment), so the forecasts and their histories are the
ones the cycle wrote; then the clock is moved and the pass is run over what the ledger holds. The exchange is a scripted market that gives
a price for every instrument and minute, or none; the two oracle venues are the real Binance spot and OKX adapters.
"""

from __future__ import annotations

import json
import tempfile
import types
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import canonical, forecast, league, lifecycle, settlement_record
from hoshitori.collect import Environment
from hoshitori.evidence import record_digest
from hoshitori.gateway import Call, Outcome
from hoshitori.lineage import EvidenceStore
from hoshitori.timeline import origin, to_ms
from hoshitori.timeutil import parse_rfc3339 as parse
from hoshitori.venue_okx import Okx
from tests.collect_checks import Reply
from tests.league_checks import HOUR, NOW, URN, World, states

MINUTE = 60_000
LIVE = (lifecycle.State.AWAITING_ORIGIN, lifecycle.State.OPEN, lifecycle.State.SETTLING)  # the slots a scheduler hands to the pass


class Market:
    """Prices by (base token, minute the bar opens): the reference minute is 100 for everyone, the outcome minute is 101 for BTC and 99 for
    ETH, and anything can be overridden or taken away. A venue's clock can be set or made to fail."""

    def __init__(self, world: World, reference_open: int) -> None:
        self.world, self.reference_open = world, reference_open
        self.override: dict[tuple[str, str, int], Decimal | None] = {}  # (venue, base, minute) -> price, or None for no bar
        self.wide: dict[
            tuple[str, str, int], tuple[Decimal, Decimal]
        ] = {}  # (venue, base, minute) -> (low, high) of a bar that is not flat
        self.skew: dict[str, int] = {}
        self.no_time: set[str] = set()
        self.garbage_time: set[str] = set()
        self.no_candles: set[str] = set()
        self.garbage_candles: set[str] = set()
        self.deadlines: list[Any] = []
        self.okx = Okx()
        self.extra: set[tuple[str, str, int]] = set()  # bars given beyond the end of the window asked for (a venue that answers too much)
        self.calls: list[tuple[str, str, str, dict[str, object]]] = []  # (venue, operation, priority, args) of every call it was asked

    def price(self, venue: str, base: str, minute: int) -> Decimal | None:
        if (venue, base, minute) in self.override:
            return self.override[(venue, base, minute)]
        if minute == self.reference_open:
            return Decimal(100)
        return Decimal(101) if base == "BTC" else Decimal(99)

    def script(self, call: Call, world: World) -> Reply:
        okx = call.provider == self.okx.provider
        venue = "okx" if okx else "binance-spot"
        self.calls.append((venue, call.operation, call.priority.name, dict(call.args)))
        self.deadlines.append(call.deadline)
        if call.operation == "time":
            if venue in self.no_time:
                return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
            if venue in self.garbage_time:
                return Reply(b"not json")
            ms = to_ms(world.clock.now()) + self.skew.get(venue, 0)
            return Reply(
                json.dumps({"code": "0", "msg": "", "data": [{"ts": str(ms)}]}).encode() if okx else f'{{"serverTime":{ms}}}'.encode()
            )
        if venue in self.no_candles:
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
        if venue in self.garbage_candles:
            return Reply(b"not json")
        raw = str(call.args["instrument"])
        base = raw.split("-")[0] if okx else raw.removesuffix("USDT")
        from hoshitori.timeutil import parse_rfc3339

        start, end = to_ms(parse_rfc3339(str(call.args["start"]))), to_ms(parse_rfc3339(str(call.args["end"])))
        rows = []
        step = int(call.args["interval_s"]) * 1000
        window = list(range(start, end, step)) + sorted(m for v, b, m in self.extra if (v, b) == (venue, base))
        for minute in window:
            p = self.price(venue, base, minute)
            if p is None:
                continue
            low, high = self.wide.get((venue, base, minute), (p, p))
            if okx:
                rows.append([str(minute), str(p), str(high), str(low), str(p), "2.5", "250", "260", "1"])
            else:
                rows.append([minute, str(p), str(high), str(low), str(p), "12.5", minute + step - 1, "987.6", 120, "6.1", "482.0", "0"])
        body = json.dumps({"code": "0", "msg": "", "data": list(reversed(rows))} if okx else rows)
        return Reply(body.encode())


class Rig:
    """A cycle that has been run, a market, and the services of the pass."""

    def __init__(self, root: Path, barrier: Any = None, **kw: Any) -> None:
        if barrier is not None:
            kw["protocol"] = {**kw.get("protocol", {}), "barrier_arms": (("L0", barrier.sha256),)}
        self.w = World(root, **kw)
        self.out = self.w.run(league, **({} if barrier is None else {"barrier_mappings": {"L0": barrier}}))
        assert self.out.state.value == "COMMITTED", self.out.reason
        assert self.out.commitment is not None
        self.origin = origin(self.out.commitment.attestation.eligible_after_ms)
        self.market = Market(self.w, self.origin - MINUTE)
        self.w.script = self.market.script
        self.adapters = {"binance-spot": self.w.adapter, "okx": self.market.okx}

    def services(self, mod: types.ModuleType, **over: Any) -> Any:
        env = Environment(self.w.runner, self.w.captures, self.adapters, self.w.registry, EvidenceStore(), self.w.clock)
        values = {"protocol": self.w.protocol, "env": env, "source_urn": URN}
        values.update(over)
        return mod.SettleServices(**values)

    def at(self, ms: int) -> None:
        self.w.clock.set(from_ms_(ms))

    def waiting(self, mod: types.ModuleType) -> list[Any]:
        """Every forecast with its record's digest and its history; a slot still in play also gets the settlement record the ledger holds
        for it, if there is one (a pass that stopped before the step that ends the slot)."""
        records = self.w.records("forecast")
        histories = lifecycle.rebuild(self.w.records("forecast_state"))
        settled = {rec["body"]["forecast_id"]: rec for rec in self.w.records("settlement")}
        out = []
        for rec in records:
            clean = {k: v for k, v in rec.items() if k != "committed_at"}
            history = histories[rec["id"]]
            extra = {"settlement": settled[rec["id"]]} if rec["id"] in settled and lifecycle.current_state(history) in LIVE else {}
            out.append(mod.Waiting(forecast.from_record(clean), record_digest(clean), history, **extra))
        return sorted(out, key=lambda x: x.forecast.common.forecast_id)

    def run(self, mod: types.ModuleType, **over: Any) -> Any:
        passed = mod.settle_pass(self.services(mod), self.waiting(mod), **over)
        for record in passed.records:
            self.w.ledger.submit(record)
        self.w.ledger.append_pending()
        return passed

    def ends(self) -> dict[str, str]:
        return {fid: states(h)[-1] for fid, h in lifecycle.rebuild(self.w.records("forecast_state")).items()}


def from_ms_(ms: int) -> Any:
    from hoshitori.timeline import from_ms

    return from_ms(ms)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.SettleJobError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def by_hours(rig: Rig, hours: int) -> list[str]:
    return sorted(s.forecast_id for s in rig.out.slots if s.hours == hours)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a missing bar is looked for for an hour", mod.RETRY_WINDOW_MS, 3_600_000)
    for cls in (mod.SettleServices, mod.Waiting, mod.Passed, mod._Need, mod.Read):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    # a candle's volume reaches the bar the practice fills read: given, as it was, or unknown when the venue gave none (never zero)
    from datetime import UTC, datetime

    from hoshitori.venue_data import Candle

    opened = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
    base = {"open_time": opened, "close_time": opened.replace(minute=1), "open": "100", "high": "101", "low": "99", "close": "100"}
    c.eq("a candle with a volume gives a bar with it", mod._bar(Candle(**base, volume="3.5")).volume, Decimal("3.5"))  # type: ignore[union-attr]
    c.eq("a candle with none gives a bar with none, not zero", mod._bar(Candle(**base)).volume, None)  # type: ignore[union-attr]
    c.eq("and a candle of volume zero gives a bar of volume zero", mod._bar(Candle(**base, volume="0")).volume, Decimal(0))  # type: ignore[union-attr]
    return c.bad


def check_progress(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    r = Rig(root)
    first = r.run(mod)
    c.eq(
        "before the origin nothing moves",
        (first.records, first.histories, first.pending, first.deferred, r.w.calls[-1].operation),
        ((), {}, (), (), r.w.calls[-1].operation),
    )
    calls_before = len(r.w.calls)
    c.eq("and nothing is asked of the exchanges", calls_before, len(r.w.calls))
    r.at(r.origin - 1)
    c.eq("a millisecond before the origin, still nothing", r.run(mod).records, ())
    r.at(r.origin)
    opened = r.run(mod)
    c.eq(
        "at the origin all six slots open, with six records",
        (len(opened.records), sorted(states(h)[-1] for h in opened.histories.values())),
        (6, ["OPEN"] * 6),
    )
    c.eq("each opens at its origin", {h[-1].step.origin_ms for h in opened.histories.values()}, {r.origin})
    c.eq("no candle was asked for yet", [x.operation for x in r.w.calls[calls_before:]], [])
    again = r.run(mod)
    c.eq("a second pass at the same moment writes nothing", (again.records, again.histories), ((), {}))
    r.at(r.origin + HOUR - 1)
    c.eq("a millisecond before the one-hour horizon it is still open", r.run(mod).records, ())
    r.at(r.origin + HOUR)
    mark = len(r.w.calls)
    done = r.run(mod)
    one = by_hours(r, 1)
    c.eq(
        "the one-hour forecasts settle, the others stay open",
        dict(r.ends().items()),
        {**dict.fromkeys(one, "SETTLED"), **dict.fromkeys(by_hours(r, 4) + by_hours(r, 24), "OPEN")},
    )
    c.eq("pending and deferred are empty", (done.pending, done.deferred), ((), ()))
    asked = r.w.calls[mark:]
    c.eq(
        "one clock call to each venue, then one bar of each instrument and minute, no more",
        sorted((x.provider, x.operation) for x in asked),
        sorted(
            [(r.w.adapter.provider, "time"), (r.market.okx.provider, "time")]
            + [(p, "candles") for p in (r.w.adapter.provider, r.market.okx.provider) for _ in range(4)]
        ),
    )
    c.eq("at the highest priority, as the settler", {(x.priority.value, x.caller) for x in asked}, {("P0", "settle")})
    windows = sorted((x.provider, x.args["instrument"], x.args["start"], x.args["end"]) for x in asked if x.operation == "candles")
    ref_start = from_ms_(r.origin - MINUTE).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    out_start = from_ms_(r.origin + HOUR - MINUTE).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    c.eq(
        "the reference minute and the outcome minute of the first-hour horizon",
        sorted({(w[2]) for w in windows}),
        sorted([ref_start, out_start]),
    )
    # what was recorded
    records = {rec["body"]["forecast_id"]: rec for rec in r.w.records("settlement")}
    forecasts = {rec["id"]: record_digest({k: v for k, v in rec.items() if k != "committed_at"}) for rec in r.w.records("forecast")}
    c.eq("a settlement record for each", sorted(records), one)
    for fid, rec in records.items():
        s, digest = settlement_record.from_record({k: v for k, v in rec.items() if k != "committed_at"})
        symbol = next(x.symbol for x in r.out.slots if x.forecast_id == fid)
        want_outcome = "1" if symbol == "BTC" else "0"
        c.eq(
            f"{symbol}: the outcome, the prices and the digest",
            (s.state.value, s.outcome, s.p0, s.outcome_price, digest),
            ("SETTLED", want_outcome, "100", "101" if symbol == "BTC" else "99", forecasts[fid]),
        )
    histories = lifecycle.rebuild(r.w.records("forecast_state"))
    for fid in one:
        h = histories[fid]
        c.eq("the history of a settled slot", states(h)[-4:], ["AWAITING_ORIGIN", "OPEN", "SETTLING", "SETTLED"])
        c.eq("it names the settlement", h[-1].step.settlement_id, records[fid]["id"])
    # later horizons
    r.at(r.origin + 4 * HOUR)
    four = r.run(mod)
    c.eq(
        "four hours on, the four-hour forecasts settle",
        sorted(fid for fid, e in r.ends().items() if e == "SETTLED"),
        sorted(one + by_hours(r, 4)),
    )
    c.eq("and their records were new ones: settling, the settlement, the end, for each", len(four.records), 2 * 3)
    r.w.close()
    return c.bad


def check_outcomes(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    r = Rig(root)
    out_minute = r.origin + HOUR - MINUTE
    # a tie for BTC, disagreement for ETH on the second oracle
    r.market.override[("binance-spot", "BTC", out_minute)] = Decimal(100)
    r.market.override[("okx", "BTC", out_minute)] = Decimal(100)
    r.market.override[("okx", "ETH", out_minute)] = Decimal("101")  # the primary says 99: more than half a percent apart
    r.at(r.origin)
    r.run(mod)
    r.at(r.origin + HOUR)
    r.run(mod)
    ends = {x.symbol: r.ends()[x.forecast_id] for x in r.out.slots if x.hours == 1}
    c.eq("a tie is void, oracles that disagree are unsettleable", ends, {"BTC": "VOID", "ETH": "UNSETTLEABLE"})
    reasons = {rec["body"]["forecast_id"]: (rec["body"]["state"], rec["body"]["reason"]) for rec in r.w.records("settlement")}
    c.eq("the reasons are recorded", sorted(reasons.values()), [("UNSETTLEABLE", "UNSETTLEABLE_DISAGREE"), ("VOID", "VOID_TIE")])
    histories = lifecycle.rebuild(r.w.records("forecast_state"))
    c.eq(
        "and the steps carry them",
        sorted((h[-1].step.state.value, h[-1].step.reason) for fid, h in histories.items() if fid in by_hours(r, 1)),
        [("UNSETTLEABLE", "UNSETTLEABLE_DISAGREE"), ("VOID", "VOID_TIE")],
    )
    r.w.close()
    return c.bad


def check_missing(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    r = Rig(root)
    out_minute = r.origin + HOUR - MINUTE
    r.market.override[("okx", "BTC", out_minute)] = None  # the second oracle has no bar for the outcome minute of BTC
    r.at(r.origin)
    r.run(mod)
    r.at(r.origin + HOUR)
    first = r.run(mod)
    btc = [x.forecast_id for x in r.out.slots if x.hours == 1 and x.symbol == "BTC"]
    eth = [x.forecast_id for x in r.out.slots if x.hours == 1 and x.symbol == "ETH"]
    c.eq("one waits, the other is settled", (list(first.pending), r.ends()[eth[0]], r.ends()[btc[0]]), (btc, "SETTLED", "SETTLING"))
    c.eq(
        "the waiting one wrote its settling step and nothing else",
        [rec["body"]["state"] for rec in r.w.records("forecast_state") if rec["body"]["forecast_id"] == btc[0]][-1],
        "SETTLING",
    )
    r.at(r.origin + HOUR + 59 * MINUTE + 59_999)
    c.eq("just inside the hour it still waits", (list(r.run(mod).pending), r.ends()[btc[0]]), (btc, "SETTLING"))
    r.at(r.origin + 2 * HOUR)
    last = r.run(mod)
    c.eq(
        "an hour after settling began, a bar that is still missing makes it unsettleable",
        (last.pending, r.ends()[btc[0]]),
        ((), "UNSETTLEABLE"),
    )
    rec = next(x for x in r.w.records("settlement") if x["body"]["forecast_id"] == btc[0])
    c.eq("with its reason", (rec["body"]["state"], rec["body"]["reason"]), ("UNSETTLEABLE", "UNSETTLEABLE_MISSING"))
    r.w.close()
    # a venue that gives no time, and a venue that answers no candle
    d = Rig(root / "d")
    d.market.no_time.add("okx")
    d.at(d.origin)
    d.run(mod)
    d.at(d.origin + HOUR)
    out = d.run(mod)
    c.eq(
        "without a venue's clock nothing is said about the slots that need it",
        (sorted(out.deferred), out.pending, d.w.records("settlement")),
        (by_hours(d, 1), (), []),
    )
    c.eq("they stay settling", {d.ends()[f] for f in by_hours(d, 1)}, {"SETTLING"})
    d.market.no_time.clear()
    d.market.no_candles.add("okx")
    out = d.run(mod)
    c.eq("a venue that answers no candle leaves them pending", (out.deferred, sorted(out.pending)), ((), by_hours(d, 1)))
    d.market.no_candles.clear()
    done = d.run(mod)
    c.eq("and when it answers they settle", ({d.ends()[f] for f in by_hours(d, 1)}, done.pending), ({"SETTLED"}, ()))
    d.w.close()
    return c.bad


def check_no_clock(mod: types.ModuleType, root: Path) -> list[str]:
    """A venue that never tells its time, the primary's or the second's: the slots that need it wait as long as a missing bar does, and
    when the retry window has passed they are void for the outage (contract section 7), never unsettleable for bars that were missing."""
    c = Case(mod)
    for venue in ("binance-spot", "okx"):
        c.bad += [f"{venue} gives no time: {line}" for line in _no_clock(mod, root / venue, venue)]
    return c.bad


def _no_clock(mod: types.ModuleType, root: Path, venue: str) -> list[str]:
    c = Case(mod)
    r = Rig(root)
    r.market.no_time.add(venue)
    r.at(r.origin)
    r.run(mod)
    one = by_hours(r, 1)
    r.at(r.origin + HOUR)
    first = r.run(mod)
    c.eq(
        "at the horizon they wait, and only their settling steps are written",
        (sorted(first.deferred), first.pending, [rec["body"]["state"] for rec in first.records], r.w.records("settlement")),
        (one, (), ["SETTLING", "SETTLING"], []),
    )
    r.at(r.origin + 2 * HOUR - 1)
    inside = r.run(mod)
    c.eq(
        "a millisecond before the window has passed they still wait",
        (sorted(inside.deferred), inside.pending, inside.records, {r.ends()[f] for f in one}),
        (one, (), (), {"SETTLING"}),
    )
    r.at(r.origin + 2 * HOUR)
    last = r.run(mod)
    c.eq(
        "an hour after settling began they end void for the outage",
        (last.deferred, last.pending, {r.ends()[f] for f in one}),
        ((), (), {"VOID"}),
    )
    written = {rec["body"]["forecast_id"]: rec for rec in r.w.records("settlement")}
    members = ("state", "reason", "outcome", "p0", "outcome_price", "touch_open_ms", "flags")
    c.eq(
        "with a settlement record each that says the venue was out, and nothing else",
        {fid: tuple(rec["body"][k] for k in members) for fid, rec in written.items()},
        dict.fromkeys(one, ("VOID", "VOID_OUTAGE", None, None, None, None, [])),
    )
    histories = lifecycle.rebuild(r.w.records("forecast_state"))
    c.eq(
        "and the step that ends each slot names its settlement and carries the reason",
        {fid: (states(histories[fid])[-2:], histories[fid][-1].step.reason, histories[fid][-1].step.settlement_id) for fid in one},
        {fid: (["SETTLING", "VOID"], "VOID_OUTAGE", written.get(fid, {}).get("id")) for fid in one},
    )
    r.w.close()
    return c.bad


def check_unknown_pair(mod: types.ModuleType, root: Path) -> list[str]:
    from tests.registry_checks import inst, lst, t

    c = Case(mod)
    r = Rig(root)
    # the second venue stops listing BTC: its pair is gone
    r.w.registry.admit(lst("okx", inst("okx", "ETH-USDT", "ETH")), scope="spot", known_at=t(400), capture_id="cap-late")
    r.at(r.origin)
    r.run(mod)
    r.at(r.origin + HOUR)
    out = r.run(mod)
    btc = [x.forecast_id for x in r.out.slots if x.hours == 1 and x.symbol == "BTC"]
    c.eq("a token with no pair waits", (sorted(out.pending), r.ends()[btc[0]]), (btc, "SETTLING"))
    r.at(r.origin + 2 * HOUR)
    r.run(mod)
    rec = next(x for x in r.w.records("settlement") if x["body"]["forecast_id"] == btc[0])
    c.eq(
        "and after the hour is void as delisted",
        (r.ends()[btc[0]], rec["body"]["state"], rec["body"]["reason"]),
        ("VOID", "VOID", "VOID_DELISTED"),
    )
    r.w.close()
    return c.bad


def check_delisting_window(mod: types.ModuleType, root: Path) -> list[str]:
    """A delisting voids a forecast only if it was known by the end of the forecast's own window (origin plus horizon); one known after the
    horizon does not take away a forecast that could be settled. The pair is the one the forecast named when it was sealed."""
    from tests.registry_checks import inst, lst

    c = Case(mod)
    for label, known_after_origin_ms, want in (
        ("a delisting known inside the window", HOUR // 2, ("VOID", "VOID_DELISTED")),
        ("a delisting known after the horizon", HOUR + 2 * MINUTE, ("SETTLED", None)),
    ):
        (root / label.replace(" ", "_")).mkdir()
        r = Rig(root / label.replace(" ", "_"))
        r.w.registry.admit(
            lst("okx", inst("okx", "ETH-USDT", "ETH")),
            scope="spot",
            known_at=from_ms_(r.origin + known_after_origin_ms),
            capture_id="cap-late",
        )
        r.at(r.origin)
        r.run(mod)
        r.at(r.origin + 3 * HOUR)
        r.run(mod)
        r.at(r.origin + 4 * HOUR)
        r.run(mod)
        btc = [x.forecast_id for x in r.out.slots if x.hours == 1 and x.symbol == "BTC" and x.arm_id == "L0"]
        rec = next(x for x in r.w.records("settlement") if x["body"]["forecast_id"] == btc[0])
        c.eq(f"{label}: the one-hour BTC forecast ends", r.ends()[btc[0]], want[0])
        c.eq(f"{label}: and its settlement says", (rec["body"]["state"], rec["body"].get("reason")), (want[0], want[1]))
        r.w.close()
    return c.bad


def check_interrupted(mod: types.ModuleType, root: Path) -> list[str]:
    """A pass that stopped after one slot's settlement record and before the step that ends the slot: the next pass ends that slot with
    the record as it is and writes nothing else for it, the ledger takes all it writes, and the other slot settles."""
    c = Case(mod)
    r = Rig(root)
    r.at(r.origin)
    r.run(mod)
    r.at(r.origin + HOUR)
    passed = mod.settle_pass(r.services(mod), r.waiting(mod))
    stop = next(i for i, rec in enumerate(passed.records) if rec["type"] == "settlement")
    for record in passed.records[: stop + 1]:  # the pass stops here: the slot's end and the other slot's records are lost
        r.w.ledger.submit(record)
    r.w.ledger.append_pending()
    written = passed.records[stop]
    fid = str(written["body"]["forecast_id"])
    other = next(f for f in by_hours(r, 1) if f != fid)
    symbol, other_symbol = (next(x.symbol for x in r.out.slots if x.forecast_id == f) for f in (fid, other))
    c.eq(
        "the stopped pass left both one-hour slots settling and one settlement written",
        ({r.ends()[f] for f in by_hours(r, 1)}, [rec["id"] for rec in r.w.records("settlement")]),
        ({"SETTLING"}, [written["id"]]),
    )
    # a settlement record that is not the slot's own is refused
    slots = {w.forecast.common.forecast_id: w for w in r.waiting(mod)}
    says = "a slot's settlement record names the slot's forecast record by its id and digest"
    stray, own = slots[other], slots[fid]
    c.refuses(
        "another forecast's settlement record",
        lambda: mod.settle_pass(r.services(mod), [mod.Waiting(stray.forecast, stray.digest, stray.history, own.settlement)]),
        says,
    )
    forged = {**own.settlement, "refs": [{"id": fid, "sha256": "0" * 64}]}
    c.refuses(
        "a settlement record that names another record under the slot's forecast id",
        lambda: mod.settle_pass(r.services(mod), [mod.Waiting(own.forecast, own.digest, own.history, forged)]),
        says,
    )
    # the market moves on, so that settling again would now give the other outcome, and the second venue gives no time
    for venue in ("binance-spot", "okx"):
        r.market.override[(venue, symbol, r.origin + HOUR - MINUTE)] = Decimal(99) if symbol == "BTC" else Decimal(101)
    r.market.no_time.add("okx")
    r.at(r.origin + HOUR + MINUTE)
    mark = len(r.w.calls)
    again = r.run(mod)  # every record goes to the ledger, which refuses a second settlement under the same id with other content
    c.eq(
        "the next pass writes the step that ends the slot and nothing else for it",
        [(rec["type"], rec["body"]["forecast_id"], rec["body"]["state"]) for rec in again.records],
        [("forecast_state", fid, "SETTLED")],
    )
    c.eq(
        "and reads nothing for it",
        sorted({x.args["instrument"] for x in r.w.calls[mark:] if x.operation == "candles"}),
        [f"{other_symbol}USDT"],
    )
    histories = lifecycle.rebuild(r.w.records("forecast_state"))
    end = histories[fid][-1].step
    c.eq(
        "the slot ends with the record the stopped pass wrote, which stays as it was",
        (
            states(histories[fid])[-2:],
            end.settlement_id,
            end.at_ms,
            [{k: v for k, v in rec.items() if k != "committed_at"} for rec in r.w.records("settlement")],
        ),
        (["SETTLING", "SETTLED"], written["id"], r.origin + HOUR + MINUTE, [written]),
    )
    c.eq(
        "the history the pass returns for the slot is the one it wrote",
        [(t.seq, t.step) for t in again.histories.get(fid, ())],
        [(t.seq, t.step) for t in histories[fid]],
    )
    c.eq("the other slot waits for the venue that gives no time", (again.deferred, again.pending), ((other,), ()))
    r.market.no_time.clear()
    r.at(r.origin + HOUR + 2 * MINUTE)
    last = r.run(mod)
    c.eq(
        "and settles when it gives it again, with records of its own",
        ([(rec["type"], rec["body"]["forecast_id"]) for rec in last.records], {r.ends()[f] for f in by_hours(r, 1)}),
        ([("settlement", other), ("forecast_state", other)], {"SETTLED"}),
    )
    c.eq(
        "one settlement record for each one-hour forecast",
        sorted(rec["body"]["forecast_id"] for rec in r.w.records("settlement")),
        sorted(by_hours(r, 1)),
    )
    r.w.close()
    return c.bad


def check_edges(mod: types.ModuleType, root: Path) -> list[str]:
    from tests.registry_checks import HALTED, inst, lst, t

    c = Case(mod)
    # a pass that finds a slot still waiting for its origin, after its horizon has already passed: open, start settling and settle at once
    a = Rig(root / "a")
    a.at(a.origin + HOUR)
    mark = len(a.w.calls)
    jumped = a.run(mod, wait_s=7.5)
    one = by_hours(a, 1)
    c.eq("one pass can do all of it", {fid: e for fid, e in a.ends().items() if fid in one}, dict.fromkeys(one, "SETTLED"))
    c.eq(
        "and the slots that moved are returned with their new histories",
        sorted((fid, states(h)[-1]) for fid, h in jumped.histories.items() if fid in one),
        [(fid, "SETTLED") for fid in one],
    )
    c.eq("the others are returned open", sorted({states(h)[-1] for fid, h in jumped.histories.items() if fid not in one}), ["OPEN"])
    c.eq(
        "the calls wait as long as they are told to",
        {x.deadline for x in a.w.calls[mark:]},
        {from_ms_(a.origin + HOUR) + timedelta(seconds=7.5)},
    )
    a.w.close()
    # a halted second oracle still has the candles of the minutes that matter
    h = Rig(root / "h")
    h.w.registry.admit(
        lst("okx", inst("okx", "BTC-USDT", "BTC", status=HALTED), inst("okx", "ETH-USDT", "ETH")),
        scope="spot",
        known_at=t(400),
        capture_id="cap-halt",
    )
    h.at(h.origin + HOUR)
    h.run(mod)
    c.eq("a market that halted after the forecast is still read", {h.ends()[f] for f in by_hours(h, 1)}, {"SETTLED"})
    h.w.close()
    # a clock that is not a clock
    g = Rig(root / "g")
    g.market.garbage_time.add("okx")
    g.at(g.origin + HOUR)
    out = g.run(mod)
    c.eq("a venue clock that cannot be read is no clock", (sorted(out.deferred), out.pending), (by_hours(g, 1), ()))
    g.w.close()
    # a pair whose venue has no adapter here
    m = Rig(root / "m")
    m.adapters = {"binance-spot": m.w.adapter}
    m.at(m.origin + HOUR)
    out = m.run(mod)
    c.eq(
        "a venue without an adapter leaves the slot waiting",
        (sorted(out.deferred), m.ends()[by_hours(m, 1)[0]]),
        (by_hours(m, 1), "SETTLING"),
    )
    m.w.close()
    # prices rounded to the instrument's tick before they are compared
    k = Rig(root / "tick")
    minute_out = k.origin + HOUR - MINUTE
    for venue in ("binance-spot", "okx"):
        k.market.override[(venue, "BTC", k.origin - MINUTE)] = Decimal("100.001")
        k.market.override[(venue, "BTC", minute_out)] = Decimal("100.004")
    k.at(k.origin + HOUR)
    k.run(mod)
    btc = next(x.forecast_id for x in k.out.slots if x.hours == 1 and x.symbol == "BTC")
    c.eq("a move smaller than the tick is a tie", k.ends()[btc], "VOID")
    k.w.close()
    f = Rig(root / "notick", tick=None)
    for venue in ("binance-spot", "okx"):
        f.market.override[(venue, "BTC", f.origin - MINUTE)] = Decimal("100.001")
        f.market.override[(venue, "BTC", f.origin + HOUR - MINUTE)] = Decimal("100.004")
    f.at(f.origin + HOUR)
    f.run(mod)
    btc = next(x.forecast_id for x in f.out.slots if x.hours == 1 and x.symbol == "BTC")
    c.eq("with no tick the prices are compared as they are", f.ends()[btc], "SETTLED")
    f.w.close()
    return c.bad


def barrier_atr(r: Rig) -> Decimal:
    return Decimal(next(rec["body"]["atr"] for rec in r.w.records("forecast") if rec["body"]["contract"] == "B"))


def b_slots(r: Rig, hours: int) -> dict[str, str]:
    """The barrier forecast ids of a horizon by symbol."""
    return {x.symbol: x.forecast_id for x in r.out.slots if x.contract == "B" and x.hours == hours}


def check_barrier(mod: types.ModuleType, root: Path) -> list[str]:
    from tests.league_checks import barrier_map

    c = Case(mod)
    r = Rig(root / "a", barrier=barrier_map())
    atr = barrier_atr(r)
    target, stop = Decimal(100) + Decimal("1.5") * atr, Decimal(100) - Decimal("1.5") * atr
    c.eq("the levels lie apart from the prices the market gives by default", (target > 101, stop < 99), (True, True))
    touch = r.origin + 10 * MINUTE
    r.market.override[("binance-spot", "BTC", touch)] = target  # the target is reached exactly, at the eleventh minute
    r.market.wide[("binance-spot", "ETH", r.origin + 5 * MINUTE)] = (stop - 1, target + 1)  # one minute touches both levels
    r.at(r.origin)
    r.run(mod)
    r.at(r.origin + HOUR)
    mark = len(r.w.calls)
    r.run(mod)
    ones = b_slots(r, 1)
    ends = r.ends()
    c.eq(
        "the barrier forecasts of the hour: a target reached, a minute that touched both",
        {k: ends[v] for k, v in ones.items()},
        {"BTC": "SETTLED", "ETH": "AMBIGUOUS"},
    )
    records = {rec["body"]["forecast_id"]: rec for rec in r.w.records("settlement")}
    btc = records[ones["BTC"]]["body"]
    c.eq(
        "the target: outcome, the reference price, the minute of the touch",
        (btc["contract"], btc["state"], btc["outcome"], btc["p0"], btc["touch_open_ms"]),
        ("B", "SETTLED", "TARGET", "100", touch),
    )
    eth = records[ones["ETH"]]["body"]
    c.eq(
        "the ambiguous minute",
        (eth["state"], eth["outcome"], eth["reason"], eth["touch_open_ms"]),
        ("AMBIGUOUS", "AMBIGUOUS", "SAME_CANDLE", r.origin + 5 * MINUTE),
    )
    histories = lifecycle.rebuild(r.w.records("forecast_state"))
    last = histories[ones["ETH"]][-1].step
    c.eq(
        "and its slot ends ambiguous, naming the settlement and with no reason of its own",
        (last.state.value, last.settlement_id, last.reason),
        ("AMBIGUOUS", records[ones["ETH"]]["id"], None),
    )
    c.eq(
        "the directional forecasts of the hour settled beside them",
        sorted({ends[x.forecast_id] for x in r.out.slots if x.contract == "D" and x.hours == 1}),
        ["SETTLED"],
    )
    asked = [x for x in r.w.calls[mark:] if x.operation == "candles"]
    spans = sorted(
        (x.provider, x.args["instrument"], (to_ms(parse(x.args["end"])) - to_ms(parse(x.args["start"]))) // MINUTE) for x in asked
    )
    c.eq(
        "the primary is read once for the reference minute and the hour, in one window; the second oracle for the reference and the outcome minutes",
        spans,
        sorted(
            [(r.w.adapter.provider, i, 61) for i in ("BTCUSDT", "ETHUSDT")]
            + [(r.market.okx.provider, i, 1) for i in ("BTC-USDT", "ETH-USDT") for _ in range(2)]
        ),
    )
    r.w.close()
    # a bar missing before the touch: pending, then unsettleable once the hour of retrying is over
    m = Rig(root / "m", barrier=barrier_map())
    m.market.override[("binance-spot", "BTC", m.origin + 3 * MINUTE)] = None
    m.at(m.origin)
    m.run(mod)
    m.at(m.origin + HOUR)
    first = m.run(mod)
    ones = b_slots(m, 1)
    c.eq(
        "the missing bar leaves the barrier forecast of BTC pending, the other settles",
        (ones["BTC"] in first.pending, m.ends()[ones["ETH"]]),
        (True, "SETTLED"),
    )
    m.at(m.origin + 2 * HOUR + MINUTE)
    m.run(mod)
    reasons = {rec["body"]["forecast_id"]: (rec["body"]["state"], rec["body"]["reason"]) for rec in m.w.records("settlement")}
    c.eq("an hour later it is unsettleable for the missing bar", reasons[ones["BTC"]], ("UNSETTLEABLE", "UNSETTLEABLE_MISSING"))
    m.w.close()
    # a day: windows of at most 300 bars
    d = Rig(root / "d", barrier=barrier_map())
    d.at(d.origin + 24 * HOUR)
    mark = len(d.w.calls)
    d.run(mod)
    d.at(d.origin + 24 * HOUR)
    d.run(mod)
    asked = [x for x in d.w.calls[mark:] if x.operation == "candles" and x.provider == d.w.adapter.provider]
    sizes = sorted((to_ms(parse(x.args["end"])) - to_ms(parse(x.args["start"]))) // MINUTE for x in asked)
    c.eq("a day of one-minute bars is read in windows of at most 300, with the reference minute", sizes, sorted([300] * 8 + [241] * 2))
    d.w.close()
    return c.bad


def check_one_bad_slot_does_not_stop_the_others(mod: types.ModuleType, root: Path) -> list[str]:
    """A slot whose record or data cannot be settled (a malformed oracle pair, a damaged record) is named in ``failed`` and left where it
    is, and every other slot is settled in the same pass: one poisoned slot must not stop the settlement of the whole league."""
    c = Case(mod)
    r = Rig(root)
    r.at(r.origin)
    r.run(mod)
    r.at(r.origin + HOUR)
    fids = [x.forecast_id for x in r.out.slots if x.hours == 1]
    bad = sorted(fids)[0]
    real = mod._decide

    def poisoned(slot: Any, *args: Any, **kwargs: Any) -> Any:
        if slot.forecast.common.forecast_id == bad:
            raise mod.SettleJobError("the record of this slot is damaged")
        return real(slot, *args, **kwargs)

    mod._decide = poisoned
    try:
        out = r.run(mod)
    finally:
        mod._decide = real
    c.eq("the poisoned slot is named with its reason", tuple(out.failed), ((bad, "the record of this slot is damaged"),))
    ends = r.ends()
    c.eq("it stays settling", ends[bad], "SETTLING")
    c.eq("and every other slot of that horizon ended", sorted(ends[f] for f in fids if f != bad), sorted(["SETTLED"] * (len(fids) - 1)))
    again = r.run(mod)
    c.eq("with nothing poisoned the slot settles in the next pass", (again.failed, r.ends()[bad]), ((), "SETTLED"))
    r.w.close()
    r2 = Rig(root / "long")
    r2.at(r2.origin)
    r2.run(mod)
    r2.at(r2.origin + HOUR)
    long_slot = sorted(x.forecast_id for x in r2.out.slots if x.hours == 1)[0]

    def wordy(slot: Any, *args: Any, **kwargs: Any) -> Any:
        if slot.forecast.common.forecast_id == long_slot:
            raise mod.SettleJobError("x" * 300)
        return real(slot, *args, **kwargs)

    mod._decide = wordy
    try:
        out2 = r2.run(mod)
    finally:
        mod._decide = real
    c.eq("a long reason is cut to 200 characters", tuple(out2.failed), ((long_slot, "x" * 200),))
    r2.w.close()
    return c.bad


def check_basis(mod: types.ModuleType) -> list[str]:
    """The stable coin's distance from the dollar at the outcome minute: its dollar price less one, if that bar was read."""
    from hoshitori.settlement import Bar

    c = Case(mod)
    D = Decimal
    key = ("kraken", "USDTZUSD", 600_000)

    def read(close: str | None) -> Any:
        bars = {key: None if close is None else Bar(600_000, D(close), D(close), D(close), D(close), None)}
        return mod.Read({}, bars)

    need = mod._Need(None, 0, (), (), ("kraken", "USDTZUSD"), 600_000)
    c.eq("a dollar price under one", mod._basis(need, read("0.9970")), D("-0.0030"))
    c.eq("a dollar price over one", mod._basis(need, read("1.0045")), D("0.0045"))
    c.eq("exactly one dollar", mod._basis(need, read("1.0")), D(0))
    c.eq("a bar that was not read is not a basis of zero", mod._basis(need, read(None)), None)
    other_minute = mod._Need(None, 0, (), (), ("kraken", "USDTZUSD"), 660_000)
    c.eq("a bar of another minute is not read", mod._basis(other_minute, read("0.99")), None)
    c.eq("no instrument, no basis", mod._basis(mod._Need(None, 0, (), (), None, None), read("0.99")), None)
    c.eq("an instrument with no minute, no basis", mod._basis(mod._Need(None, 0, (), (), ("kraken", "USDTZUSD"), None), read("0.99")), None)
    return c.bad


def check_daily_closes(mod: types.ModuleType, root: Path) -> list[str]:
    """The finished daily closes of the last days, by symbol, each venue and instrument asked once; what cannot be read leaves its symbol out."""
    c = Case(mod)
    r = Rig(root)
    day = mod.DAY_MS
    now = 40 * day + 5 * 3_600_000  # five hours into a day
    today = 40 * day
    r.at(now)
    wanted = {("binance-spot", "BTCUSDT"): "BTC", ("binance-spot", "ETHUSDT"): "ETH"}
    r.market.override[("binance-spot", "ETH", today - day)] = Decimal("98.5")
    r.market.extra.add(("binance-spot", "BTC", today))  # the day that is not over: given, and never used
    r.market.calls.clear()
    got = mod.read_daily_closes(r.services(mod).env, wanted, now, 3, 30.0, "practice")
    days = [today - 3 * day, today - 2 * day, today - day]
    c.eq("the three finished days of each symbol, and not today", sorted(got), ["BTC", "ETH"])
    c.eq("BTC closes", got["BTC"], {days[0]: Decimal(101), days[1]: Decimal(101), days[2]: Decimal(101)})
    c.eq("ETH closes, as the exchange gave them", got["ETH"], {days[0]: Decimal(99), days[1]: Decimal(99), days[2]: Decimal("98.5")})
    c.eq("every call has the deadline of the wait", set(r.market.deadlines), {r.w.clock.now() + timedelta(seconds=30)})
    kinds = sorted((v, op) for v, op, _, _ in r.market.calls)
    c.eq("each venue's clock once, each instrument once", kinds, [("binance-spot", "candles")] * 2 + [("binance-spot", "time")])
    c.eq("on the first priority", {p for _, _, p, _ in r.market.calls}, {"P0"})
    asked = next(a for _, op, _, a in r.market.calls if op == "candles")
    c.eq(
        "daily bars over the days asked",
        (asked["interval_s"], asked["start"], asked["end"]),
        (86_400, "1970-02-07T00:00:00.000Z", "1970-02-10T00:00:00.000Z"),
    )
    # a venue that gives no clock, one with no candles, one the adapters do not know: its symbols are left out and nothing else is
    r.market.calls.clear()
    wanted2 = {("binance-spot", "BTCUSDT"): "BTC", ("okx", "ETH-USDT"): "ETH", ("nowhere", "XRP-USDT"): "XRP"}
    r.market.no_time.add("okx")
    got = mod.read_daily_closes(r.services(mod).env, wanted2, now, 3, 30.0, "practice")
    c.eq("no clock from a venue: its symbol is left out, an unknown venue is skipped", sorted(got), ["BTC"])
    r.market.no_time.clear()
    r.market.no_candles.add("okx")
    got = mod.read_daily_closes(r.services(mod).env, wanted2, now, 3, 30.0, "practice")
    c.eq("candles that cannot be had: left out", sorted(got), ["BTC"])
    r.market.no_candles.clear()
    r.market.garbage_candles.add("okx")
    got = mod.read_daily_closes(r.services(mod).env, wanted2, now, 3, 30.0, "practice")
    c.eq("candles that cannot be read: left out, the others kept", sorted(got), ["BTC"])
    r.market.garbage_candles.clear()
    r.market.garbage_time.add("okx")
    got = mod.read_daily_closes(r.services(mod).env, wanted2, now, 3, 30.0, "practice")
    c.eq("a clock that cannot be read: left out", sorted(got), ["BTC"])
    r.market.garbage_time.clear()
    c.eq("nothing wanted", mod.read_daily_closes(r.services(mod).env, {}, now, 3, 30.0, "practice"), {})
    r.w.close()
    return c.bad


def check_basis_read(mod: types.ModuleType, root: Path) -> list[str]:
    """The stable coin's bar is asked for the outcome minute of each slot, at the instant the slot ends, and only when both are known."""
    import dataclasses
    from types import SimpleNamespace

    from hoshitori import oracle as real_oracle

    c = Case(mod)
    r = Rig(root)
    r.at(r.origin)
    r.run(mod)
    seen: list[Any] = []

    def fake(registry: Any, venues: Any, at: Any) -> tuple[str, str]:
        seen.append(at)
        return ("kraken", "USDTZUSD")

    real = mod.oracle
    mod.oracle = SimpleNamespace(pick_sealed=real_oracle.pick_sealed, stable_basis_instrument=fake)
    try:
        sv = r.services(mod)
        waiting = r.waiting(mod)
        needs = mod._needs(sv, waiting, to_ms(r.w.clock.now()))
    finally:
        mod.oracle = real
    ends = sorted(r.origin + w.forecast.common.horizon_ms for w in waiting)
    c.eq("the registry is asked at the instant each slot ends", sorted(to_ms(a) for a in seen), ends)
    c.eq("every slot names the instrument", {n.basis for n in needs.values()}, {("kraken", "USDTZUSD")})
    c.eq(
        "and the outcome minute of its own horizon",
        sorted(n.basis_minute for n in needs.values()),
        sorted(mod.outcome_candle_open(r.origin, w.forecast.common.horizon_ms) for w in waiting),
    )
    captured: dict[str, Any] = {}
    original = mod.read_bars

    def capture(env: Any, wanted: Any, wait_s: float, earmark: Any, purpose: str) -> Any:
        captured["wanted"] = {k: list(v) for k, v in wanted.items()}
        return mod.Read({}, {})

    mod.read_bars = capture
    try:
        mod._read(sv, needs, 30.0, None)
        c.eq(
            "the minutes asked of the stable coin are those of the slots",
            sorted(captured["wanted"][("kraken", "USDTZUSD")]),
            sorted(n.basis_minute for n in needs.values()),
        )
        no_minute = {k: dataclasses.replace(n, basis_minute=None) for k, n in needs.items()}
        mod._read(sv, no_minute, 30.0, None)
        c.eq("a slot with no minute asks nothing of it", ("kraken", "USDTZUSD") in captured["wanted"], False)
        no_basis = {k: dataclasses.replace(n, basis=None) for k, n in needs.items()}
        mod._read(sv, no_basis, 30.0, None)
        c.eq("a slot with no instrument asks nothing of it", ("kraken", "USDTZUSD") in captured["wanted"], False)
    finally:
        mod.read_bars = original
    r.w.close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_progress,
        check_outcomes,
        check_missing,
        check_no_clock,
        check_unknown_pair,
        check_delisting_window,
        check_interrupted,
        check_edges,
        check_barrier,
        check_one_bad_slot_does_not_stop_the_others,
        check_basis,
        check_daily_closes,
        check_basis_read,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check in (check_constants, check_basis) else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1100:]}")
    return bad


_ = (canonical, timedelta, NOW)


# A change the sweep cannot tell from the code (listed with its reason).
EQUIVALENT_REASONS = {
    "|str+~|'DEFERRED'|0": "a marker compared only with itself, by the module's own constant, so its spelling cannot matter",
    "read_daily_closes|Lt->LtE|to_ms(c.open_time) < today|0": "the series holds the window asked for, which ends before today, so no bar of today reaches the filter",
}
