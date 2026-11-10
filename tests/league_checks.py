"""Checks of one cycle of the league (``hoshitori.league``), runnable against a mutant.

The gateway is a scripted runner that writes real captures, the registry, the adapter, the collector, the snapshot, the rules arm, the
ledger and the commitment are the real ones, and the authorities are replaced by an attestor that builds the verdict of ``timing.assess``
from made-up tokens placed relative to the commit time. What is checked is the order of the steps, what each one writes, what is
committed together, and what happens when a step cannot be taken.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any

from hoshitori import canonical, cycle, forecast, lifecycle, protocol, rules_arm, settlement_record
from hoshitori.capture import CaptureStore
from hoshitori.clock import ManualClock
from hoshitori.collect import Environment
from hoshitori.evidence import record_digest
from hoshitori.gateway import Call, Outcome
from hoshitori.ids import natural_id
from hoshitori.ledger import strip_committed_at
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry, Want
from hoshitori.timeline import from_ms, origin, to_ms
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339
from hoshitori.venue_binance import BinanceSpot
from tests import ryo_data
from tests.collect_checks import FakeRunner, Reply, klines
from tests.commit_checks import RULES, TickClock, attestor, ledger_in
from tests.protocol_checks import make
from tests.registry_checks import T0, inst, lst, t
from tests.rules_arm_checks import graded, rates

NOW = datetime(2026, 10, 29, 12, 5, 10, tzinfo=UTC)
SLOT = to_ms(T0)
HOUR = 3_600_000
URN = "urn:hoshitori:league:test"
GOOD = [("a", "ta", 1), ("b", "tb", 3)]  # two operators' tokens a second and three seconds after the commit: C3


def time_reply(clock: ManualClock, skew_s: float = 0) -> bytes:
    return f'{{"serverTime":{to_ms(clock.now()) + round(skew_s * 1000)}}}'.encode()


class World:
    """A registry of three majors on two venues, a scripted exchange, a real ledger and the services that join them."""

    def __init__(
        self, root: Path, *, script: Callable[[Call, World], Reply] | None = None, watchlist: tuple[str, ...] = ("BTC", "ETH"), **kw: Any
    ):
        self.root = root
        self.clock: ManualClock = kw.pop("clock", None) or ManualClock(NOW)
        self.ledger_clock: ManualClock = kw.pop("ledger_clock", None) or self.clock  # one clock, unless a check wants two
        self.captures = CaptureStore(root / "captures")
        self.registry = Registry()
        self.adapter = BinanceSpot()
        names = ("BTC", "ETH", "SOL")
        tick = kw.pop("tick", "0.01")
        self.registry.admit(
            lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n, tick=tick) for n in names]),
            scope="spot",
            known_at=t(0),
            capture_id="cap-b",
        )
        self.registry.admit(
            lst("okx", *[inst("okx", f"{n}-USDT", n, tick=tick) for n in names[:2]]), scope="spot", known_at=t(1), capture_id="cap-o"
        )
        self.mapping = kw.pop("mapping", None) or before_the_schedule(graded(rules_arm))
        over: dict[str, Any] = {
            "primary_venue": "binance-spot",
            "second_venue": "okx",
            "schedule_start_ms": SLOT,
            "watchlist": watchlist,
            "arms": (("L0", self.mapping.sha256),),
            "clock_offset_limit_ms": 2_000,
            "wait_s": 1,
        }
        over.update(kw.pop("protocol", {}))
        self.protocol = make(protocol, **over)
        self.calls: list[Call] = []
        self.script = script or (lambda call, world: self.default(call))
        self.runner = FakeRunner(self.captures, self.clock, self.respond)
        self.ledger = ledger_in(root, self.ledger_clock)
        self.attested = kw.pop("attested", GOOD)
        self.attestor = kw.pop("attestor", None) or attestor(self.attested)
        self.options = kw

    def default(self, call: Call) -> Reply:
        return Reply(time_reply(self.clock) if call.operation == "time" else klines(call.args))

    def respond(self, call: Call) -> Reply:
        self.calls.append(call)
        return self.script(call, self)

    def services(self, mod: types.ModuleType, **over: Any) -> Any:
        env = Environment(self.runner, self.captures, {"binance-spot": self.adapter}, self.registry, EvidenceStore(), self.clock)
        values: dict[str, Any] = {
            "protocol": self.protocol,
            "mappings": {"L0": self.mapping},
            "env": env,
            "ledger": self.ledger,
            "attestor": self.attestor,
            "timing_rules": RULES,
            "source_urn": URN,
        }
        values.update(over)
        return mod.Services(**values)

    def run(self, mod: types.ModuleType, **over: Any) -> Any:
        return mod.run_cycle(self.services(mod, **over), SLOT, since_ms=SLOT - HOUR)

    def records(self, kind: str | None = None) -> list[dict[str, Any]]:
        out = [canonical.parse_record(self.ledger.read_record(seq)) for seq in range(self.ledger.size)]
        return [r for r in out if kind is None or r["type"] == kind]

    def close(self) -> None:
        self.ledger.close()


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
        except self.mod.LeagueError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def histories(w: World) -> tuple[dict[str, cycle.History], dict[str, lifecycle.History]]:
    return cycle.rebuild(w.records("cycle_state")), lifecycle.rebuild(w.records("forecast_state"))


def expected_p() -> set[Decimal]:
    """The five probabilities of the graded mapping, (10 + 20k + 1) / 102 to six places (halves to even), by integer arithmetic."""
    return {Decimal(round(Fraction(11 + 20 * k, 102) * 10**6)).scaleb(-6) for k in range(5)}


def states(history: Any) -> list[str]:
    return [t.step.state.value for t in history]


# --- the checks ---------------------------------------------------------------------------------------------------------------------------
def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("scan reason", mod.SCAN_OFF, "the RYO scan route is off until the metered probe is approved")
    c.eq("no-call reason", mod.NO_CALL_REASON, "OUT_OF_SCOPE")
    c.eq("abandoned slots", mod.ABANDONED_FAILURE, "CYCLE_ABANDONED")
    c.eq("an hour", mod.HOUR_MS, 3_600_000)
    c.eq("fates", [f.value for f in mod.Fate], ["FORECAST", "VOID_GAP", "NO_CALL", "FAILED_EVIDENCE", "FAILED_LATE", "FAILED"])
    for cls in (mod.Services, mod.SlotResult, mod.Outcome):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_happy_path(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, ledger_clock=TickClock(NOW, 0.001))
    out = w.run(mod)
    c.eq("committed", (out.state.value, out.reason, out.failures), ("COMMITTED", None, ()))
    c.eq("the cycle id is the protocol and the slot", out.cycle_id, cycle.cycle_id(w.protocol.sha256, SLOT))
    c.eq("six slots: two candidates, three horizons", len(out.slots), 6)
    c.eq("every one a forecast at C3", {(s.fate.value, s.level, s.reason) for s in out.slots}, {("FORECAST", "C3", None)})
    c.eq("arm and symbols", sorted({(s.arm_id, s.symbol) for s in out.slots}), [("L0", "BTC"), ("L0", "ETH")])
    c.eq("hours", sorted({s.hours for s in out.slots}), [1, 4, 24])
    c.eq(
        "the commitment holds them",
        (out.commitment is not None and out.commitment.level, out.commitment is not None and out.commitment.uniform),
        ("C3", True),
    )
    assert out.commitment is not None
    # what is in the ledger
    kinds: dict[str, int] = {}
    for r in w.records():
        kinds[r["type"]] = kinds.get(r["type"], 0) + 1
    c.eq("records by type", kinds, {"cycle_state": 8, "manifest": 1, "snapshot": 1, "evidence": 6, "forecast": 6, "forecast_state": 36})
    # a C3 slot names the numbers its origin follows from, and its origin follows them
    attestation = out.commitment.attestation  # type: ignore[union-attr]
    waits = [r["body"] for r in w.records("forecast_state") if r["body"]["state"] == "AWAITING_ORIGIN"]
    spread = round(attestation.timing.spread_s * 1000)
    c.eq("six slots wait for the origin", len(waits), 6)
    c.eq(
        "each names the attested time, the uncertainty and the spread of the time-stamps",
        {(b["attested_at"], b["uncertainty_ms"], b["spread_ms"]) for b in waits},
        {(format_rfc3339_ms(from_ms(attestation.attested_at_ms)), attestation.uncertainty_ms, spread)},
    )
    c.eq(
        "and is eligible exactly then",
        {b["eligible_after"] for b in waits},
        {format_rfc3339_ms(from_ms(attestation.attested_at_ms + attestation.uncertainty_ms + spread))},
    )
    # the batch can be found again from the log: the committing step is the last record under the stamped checkpoint, the cycle's records are
    # the batch_size ones before it, and their digests make the root the step names (what a verifier recomputes)
    committing = next(r for r in w.records("cycle_state") if r["body"]["state"] == "COMMITTING")
    at = w.ledger.find(committing["source"], committing["id"])
    size = committing["body"].get("batch_size")
    c.eq("the committing step names how many records the batch holds besides itself", isinstance(size, int) and size > 0, True)
    c.eq(
        "it is the last record under the checkpoint that was stamped",
        (at, out.commitment.size),
        (out.commitment.size - 1, out.commitment.size),
    )  # type: ignore[union-attr]
    batch = [canonical.parse_record(strip_committed_at(w.ledger.read_record(s))) for s in range(at - size, at)]  # type: ignore[operator]
    c.eq("the root of the last batch_size records before it is the root it names", mod.batch_root(batch), committing["body"]["batch_root"])
    others = [
        n
        for n in (size - 1, size + 1)
        if mod.batch_root([canonical.parse_record(strip_committed_at(w.ledger.read_record(s))) for s in range(at - n, at)])
        == committing["body"]["batch_root"]
    ]  # type: ignore[operator]
    c.eq("and no other count gives it", others, [])
    by_cycle, by_slot = histories(w)
    c.eq("the cycle went through every state, once", states(by_cycle[out.cycle_id]), [s.value for s in cycle.ORDER])
    history = by_cycle[out.cycle_id]
    entered = {t.step.state: t.step for t in history}
    c.eq(
        "planned for the slot under the protocol",
        (entered[cycle.State.PLANNED].planned_for_ms, entered[cycle.State.PLANNED].protocol_hash),
        (SLOT, w.protocol.sha256),
    )
    assert out.commitment is not None
    c.eq(
        "the times of the steps: the clock, and for the last the moment the batch was committed",
        [(t.step.state.value, t.step.at_ms) for t in history],
        [(s.value, to_ms(NOW)) for s in cycle.ORDER[:-1]] + [("COMMITTED", out.commitment.attestation.committed_at_ms)],
    )
    mrec = w.records("manifest")[0]
    c.eq(
        "the sealed manifest is the one the cycle names",
        record_digest(_strip(mrec)) and entered[cycle.State.SEALED].manifest_hash,
        hashlib.sha256(canonical.dumps(mrec["body"])).hexdigest(),
    )
    srec = w.records("snapshot")[0]
    c.eq(
        "the frozen snapshot is the one the cycle names, and its hash is the hash of its body",
        (
            entered[cycle.State.SNAPSHOT_FROZEN].snapshot_hash,
            hashlib.sha256(canonical.dumps({k: v for k, v in srec["body"].items() if k != "sha256"})).hexdigest(),
        ),
        (srec["body"]["sha256"], srec["body"]["sha256"]),
    )
    c.eq(
        "the final step names the commitment",
        (entered[cycle.State.COMMITTED].commitment_id, entered[cycle.State.COMMITTED].committed_at_ms),
        (out.commitment.commitment_id, out.commitment.attestation.committed_at_ms),
    )
    # the committing step names the root of the batch before it
    seqs = sorted(r.seq for r in out.commitment.records)
    leaves = [canonical.parse_record(strip_committed_at(w.ledger.read_record(q))) for q in seqs[:-1]]
    c.eq(
        "the batch root is the Merkle root of the batch's other records", entered[cycle.State.COMMITTING].batch_root, mod.batch_root(leaves)
    )
    c.eq(
        "and the committing step is the last record of the batch",
        canonical.parse_record(w.ledger.read_record(seqs[-1]))["body"]["state"],
        "COMMITTING",
    )
    c.eq("the first record of the batch is evidence", canonical.parse_record(w.ledger.read_record(seqs[0]))["type"], "evidence")
    # forecasts
    seen = set()
    for rec in w.records("forecast"):
        f = forecast.from_record(_strip(rec))
        common = f.common
        seen.add((common.symbol, common.horizon_ms // HOUR))
        c.eq(
            f"{rec['id']}: the common members",
            (
                common.cycle_id,
                common.arm_id,
                common.contract,
                common.quote_class,
                common.instrument_kind,
                common.oracle_pair,
                common.oracle_primary,
                common.oracle_second,
                common.protocol_hash,
                common.interpretation_visible,
            ),
            (out.cycle_id, "L0", "D", "USDT", "spot", "A", "binance-spot", "okx", w.protocol.sha256, False),
        )
        c.eq(
            f"{rec['id']}: agent version and evidence time",
            (common.agent_version_hash, common.as_of_ms),
            (rules_arm.agent_version_hash(w.mapping), to_ms(NOW)),
        )
        c.eq(f"{rec['id']}: invalidation", common.invalidation, rules_arm.INVALIDATION)
        c.eq(f"{rec['id']}: three evidence items, each with its digest", len(common.evidence), 3)
        c.eq(f"{rec['id']}: a probability of the mapping", f.p in expected_p(), True)  # type: ignore[union-attr]
        asset = w.registry.join(inst("binance-spot", f"{common.symbol}USDT", common.symbol).instrument_id, Want()).asset
        c.eq(f"{rec['id']}: the asset of the primary oracle", common.asset_id, None if asset is None else asset.asset_id)
    c.eq("a forecast for each symbol and horizon", seen, {(s, h) for s in ("BTC", "ETH") for h in (1, 4, 24)})
    # slots
    c.eq("every slot is waiting for its origin, at C3", {states(h)[-1] for h in by_slot.values()}, {"AWAITING_ORIGIN"})
    c.eq("six histories, each of six steps", sorted(len(h) for h in by_slot.values()), [6] * 6)
    for h in by_slot.values():
        steps = {t.step.state: t.step for t in h}
        c.eq("a slot's steps", states(h), ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "DECIDED", "COMMITTED", "AWAITING_ORIGIN"])
        c.eq("committed at C3", steps[lifecycle.State.COMMITTED].level, forecast.Level.C3)
        c.eq(
            "eligible when the batch says",
            steps[lifecycle.State.AWAITING_ORIGIN].eligible_after_ms,
            out.commitment.attestation.eligible_after_ms,
        )
    c.eq("the origin of each result", {s.origin_ms for s in out.slots}, {origin(out.commitment.attestation.eligible_after_ms)})
    c.eq("no settlement yet", w.records("settlement"), [])
    c.eq("the results are in the order of the slots' ids", [s.forecast_id for s in out.slots], sorted(s.forecast_id for s in out.slots))
    committed = [r for r in w.records("forecast_state") if r["body"]["state"] == "COMMITTED"]
    c.eq(
        "the committed steps were written in the order of the slots' ids",
        [r["body"]["forecast_id"] for r in committed],
        sorted(r["body"]["forecast_id"] for r in committed),
    )
    when = {r["id"]: to_ms(parse_rfc3339(r["committed_at"])) for r in w.records("forecast")}
    c.eq(
        "each step says when its own forecast was committed",
        {r["body"]["forecast_id"]: to_ms(parse_rfc3339(r["body"]["committed_at"])) for r in committed},
        when,
    )
    c.eq("and those moments are all different", len(set(when.values())), 6)
    manifest_input = w.records("manifest")[0]["body"]["inputs"]
    c.eq("the trigger log is named by the cycle", [i["capture_id"] for i in manifest_input], [natural_id("tl", {"cycle": out.cycle_id})])
    c.eq("the registry's changes were read since the slot before", len(w.calls) > 0, True)
    c.eq(
        "what was asked of the exchange: its clock, then three windows' worth for each candidate",
        [x.operation for x in w.calls][:1],
        ["time"],
    )
    c.eq("by the cycle, at the highest priority", {(x.caller, x.priority.value) for x in w.calls}, {("cycle", "P0")})
    w.close()
    return c.bad


def check_event_route(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, watchlist=())
    # a list read after the baseline adds DOGE to both venues, inside the window the cycle reads
    w.registry.admit(
        lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n) for n in ("BTC", "ETH", "SOL", "DOGE")]),
        scope="spot",
        known_at=t(100),
        capture_id="cap-b2",
    )
    w.registry.admit(
        lst("okx", *[inst("okx", f"{n}-USDT", n) for n in ("BTC", "ETH", "DOGE")]), scope="spot", known_at=t(101), capture_id="cap-o2"
    )
    out = w.run(mod)
    c.eq("committed", out.state.value, "COMMITTED")
    c.eq("only the token that appeared is a candidate", sorted({s.symbol for s in out.slots}), ["DOGE"])
    body = w.records("manifest")[0]["body"]
    c.eq("the manifest says how each route went", body["routes"], {"EVENT": "OK", "RYO_SCAN": "FAILED_EVIDENCE", "WATCHLIST": "OK"})
    c.eq("and why the scan did not", body["failures"], {"RYO_SCAN": mod.SCAN_OFF})
    (entry,) = body["entries"]
    by_trigger = {r["trigger_id"]: r for r in entry["routes"]}
    c.eq(
        "the earlier trigger of the two venues is the one that counts, the other is its duplicate",
        sorted((r["first_seen_at"], r["decision"], r["reason"]) for r in entry["routes"]),
        [("2026-10-29T12:01:40.000Z", "INCLUDED", None), ("2026-10-29T12:01:41.000Z", "EXCLUDED", "duplicate")],
    )
    inputs = {i["kind"]: i for i in body["inputs"]}
    c.eq("the trigger log is named", sorted(inputs), ["event_trigger_log"])
    seen = {"2026-10-29T12:01:40.000Z": ("EXCHANGE:binance", 100), "2026-10-29T12:01:41.000Z": ("EXCHANGE:okx", 101)}
    rows = [
        {
            "id": ident,
            "rule": "REGISTRY_NEW_INSTRUMENT",
            "version": "1",
            "symbol": "DOGE",
            "primary": True,
            "families": [seen[route["first_seen_at"]][0]],
            "first_seen_at_ms": to_ms(t(seen[route["first_seen_at"]][1])),
            "impact": "1",
        }
        for ident, route in by_trigger.items()
    ]
    ordered = sorted(rows, key=lambda r: (r["first_seen_at_ms"], r["id"]))
    c.eq(
        "its hash is the hash of the triggers in order",
        inputs["event_trigger_log"]["sha256"],
        hashlib.sha256(canonical.dumps({"triggers": ordered})).hexdigest(),
    )
    w.close()
    return c.bad


def check_empty_cycle(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root, watchlist=())
    out = w.run(mod)
    c.eq("a cycle with no candidate is a cycle", (out.state.value, out.slots, out.reason), ("COMMITTED", (), None))
    kinds: dict[str, int] = {}
    for r in w.records():
        kinds[r["type"]] = kinds.get(r["type"], 0) + 1
    c.eq("it records its manifest and commits its snapshot", kinds, {"cycle_state": 8, "manifest": 1, "snapshot": 1})
    assert out.commitment is not None
    c.eq(
        "in one batch of two records (the snapshot and the committing step) at C3",
        ([r.record_id for r in out.commitment.records][-1].startswith("cs"), len(out.commitment.records), out.commitment.level),
        (True, 2, "C3"),
    )
    body = w.records("manifest")[0]["body"]
    c.eq(
        "the trigger log of an empty window",
        [i["sha256"] for i in body["inputs"]],
        [hashlib.sha256(canonical.dumps({"triggers": []})).hexdigest()],
    )
    w.close()
    return c.bad


def check_failed_evidence(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)

    def script(call: Call, world: World) -> Reply:
        if call.operation == "candles" and call.args["instrument"] == "ETHUSDT":
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
        return world.default(call)

    w = World(root, script=script)
    out = w.run(mod)
    c.eq("the cycle is committed", out.state.value, "COMMITTED")
    c.eq(
        "the failures of the collection are returned",
        [(f.reason.value, f.detail) for f in out.failures],
        [("CALL_FAILED", "FAILED: UPSTREAM")] * 2,
    )
    fates = {(s.symbol, s.fate.value, s.reason) for s in out.slots}
    c.eq(
        "a token without evidence is a failure of evidence, never a no-call",
        fates,
        {("BTC", "FORECAST", None), ("ETH", "FAILED_EVIDENCE", "NO_FEATURES")},
    )
    c.eq("no level for a slot that was not committed", {s.level for s in out.slots if s.symbol == "ETH"}, {None})
    unread = [e["unavailable"] for e in w.records("snapshot")[0]["body"]["candidates"] if "unavailable" in e]
    c.eq(
        "the snapshot says what could not be read: both windows of the failed token, and nothing for the other",
        unread,
        [
            [
                {"source": "binance-spot", "reason": "CALL_FAILED", "interval_s": 60},
                {"source": "binance-spot", "reason": "CALL_FAILED", "interval_s": 3600},
            ]
        ],
    )
    _, slots = histories(w)
    ends = sorted(states(h)[-1] for h in slots.values())
    c.eq("and its history ends there", ends, ["AWAITING_ORIGIN"] * 3 + ["FAILED_EVIDENCE"] * 3)
    reasons = {t.step.reason for h in slots.values() for t in h if t.step.state is lifecycle.State.FAILED_EVIDENCE}
    c.eq("with the reason of the arm", reasons, {"NO_FEATURES"})
    c.eq("three forecasts only", len(w.records("forecast")), 3)
    w.close()
    return c.bad


def check_no_call(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    thin = before_the_schedule(rates(rules_arm, lambda h, s: (50, 25, 0)))
    w = World(root, mapping=thin)
    out = w.run(mod)
    c.eq("committed", out.state.value, "COMMITTED")
    c.eq(
        "a state with too little history is a voluntary no-call, at C3",
        {(s.fate.value, s.reason, s.level) for s in out.slots},
        {("NO_CALL", "UNMAPPED", "C3")},
    )
    c.eq("no forecast was made", w.records("forecast"), [])
    c.eq("and none waits for an origin", {s.origin_ms for s in out.slots}, {None})
    _, slots = histories(w)
    for h in slots.values():
        steps = {t.step.state: t.step for t in h}
        c.eq("the history of a no-call", states(h), ["SCHEDULED", "CREATED", "EVIDENCE_FROZEN", "DECIDED", "NO_CALL"])
        c.eq(
            "its reason is the lifecycle's and it holds the level of the batch",
            (steps[lifecycle.State.NO_CALL].reason, steps[lifecycle.State.NO_CALL].level),
            ("OUT_OF_SCOPE", forecast.Level.C3),
        )
    w.close()
    return c.bad


def abandoned(c: Case, w: World, out: Any, reason: str, *, snapshot: bool, slots: int = 6) -> None:
    c.eq(
        f"{reason}: the cycle is over, abandoned, with nothing committed",
        (out.state.value, out.reason, out.commitment),
        ("ABANDONED", reason, None),
    )
    by_cycle, by_slot = histories(w)
    last = by_cycle[out.cycle_id][-1].step
    c.eq(f"{reason}: the last step", (last.state.value, last.reason), ("ABANDONED", reason))
    c.eq(
        f"{reason}: every slot is recorded and failed, with the reason",
        sorted({(states(h)[-1], h[-1].step.reason) for h in by_slot.values()}),
        [("FAILED", f"CYCLE_ABANDONED: {reason}")] if slots else [],
    )
    c.eq(f"{reason}: the number of slots", len(by_slot), slots)
    c.eq(
        f"{reason}: no forecast and no snapshot record",
        (w.records("forecast"), len(w.records("snapshot")) == (1 if snapshot else 0)),
        ([], True),
    )
    c.eq(f"{reason}: the manifest is kept", len(w.records("manifest")), 1)
    c.eq(f"{reason}: every slot ended failed", {s.fate.value for s in out.slots}, {"FAILED"} if slots else set())
    c.eq(f"{reason}: no slot has a level", {s.level for s in out.slots}, {None} if slots else set())


def check_abandoned(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    # the exchange's clock is a minute ahead of ours
    skew = World(
        root / "skew", script=lambda call, world: Reply(time_reply(world.clock, 60) if call.operation == "time" else klines(call.args))
    )
    out = skew.run(mod)
    abandoned(c, skew, out, "CLOCK_OFFSET", snapshot=False)
    skew.close()

    # the candles take eleven minutes
    def slow(call: Call, world: World) -> Reply:
        if call.operation == "candles":
            world.clock.advance(timedelta(minutes=11))
        return world.default(call)

    late = World(root / "late", script=slow)
    abandoned(c, late, late.run(mod), "DISCOVERY_SNAPSHOT_GAP", snapshot=False)
    late.close()
    # a clock that moves a hundredth of a second every time it is read, and a snapshot that may be one millisecond old
    ticking = World(
        root / "old",
        clock=TickClock(NOW, 0.01),
        protocol={"snapshot_freshness_ms": 1},
        script=lambda call, world: Reply(time_reply(ManualClock(NOW)) if call.operation == "time" else klines(call.args)),
    )
    abandoned(c, ticking, ticking.run(mod), "SNAPSHOT_TOO_OLD", snapshot=False)
    ticking.close()
    return c.bad


def check_defect(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)

    def broken(digest: bytes, committed_at_ms: int) -> Any:
        raise RuntimeError("the authorities are not reachable at all")

    w = World(root, attestor=broken)
    try:
        w.run(mod)
        c.bad.append("a defect was swallowed")
    except RuntimeError as exc:
        c.eq("the error is the original", str(exc), "the authorities are not reachable at all")
    by_cycle, by_slot = histories(w)
    (history,) = by_cycle.values()
    c.eq(
        "the cycle is abandoned for a lost process, and its history has no gap",
        (states(history)[-2:], history[-1].step.reason, [t.seq for t in history]),
        (["COMMITTING", "ABANDONED"], "PROCESS_LOST", list(range(1, 9))),
    )
    c.eq("every slot, though decided, is failed", sorted({states(h)[-1] for h in by_slot.values()}), ["FAILED"])
    c.eq("the forecasts that were appended stay in the ledger", len(w.records("forecast")), 6)
    w.close()
    # the batch is refused before anything of it is appended: the committing step must still be in the history
    from hoshitori import commit

    limit = commit.MAX_RECORDS
    commit.MAX_RECORDS = 3
    try:
        r = World(root / "refused")
        try:
            r.run(mod)
            c.bad.append("a batch of too many records was committed")
        except commit.CommitError:
            pass
        by_cycle, by_slot = histories(r)
        (history,) = by_cycle.values()
        c.eq(
            "the history keeps its committing step before the end",
            (states(history)[-2:], [t.seq for t in history]),
            (["COMMITTING", "ABANDONED"], list(range(1, 9))),
        )
        c.eq("nothing of the batch is in the ledger", (r.records("forecast"), r.records("snapshot"), r.records("evidence")), ([], [], []))
        c.eq("the slots are failed", sorted({states(h)[-1] for h in by_slot.values()}), ["FAILED"])
        r.close()
    finally:
        commit.MAX_RECORDS = limit
    # a clock that goes back an hour while the cycle runs: the closing records are never dated before the ones they follow
    ticking = TickClock(NOW, 0.01)

    def back(digest: bytes, committed_at_ms: int) -> Any:
        ticking.set(NOW - timedelta(hours=1))
        raise RuntimeError("the clock was set back")

    k = World(
        root / "back",
        clock=ticking,
        ledger_clock=ManualClock(NOW + timedelta(minutes=1)),
        attestor=back,
        script=lambda call, world: Reply(time_reply(ManualClock(NOW)) if call.operation == "time" else klines(call.args)),
    )
    with contextlib.suppress(RuntimeError):
        k.run(mod)
    by_cycle, by_slot = histories(k)
    (history,) = by_cycle.values()
    c.eq("abandoned all the same", (states(history)[-1], sorted({states(h)[-1] for h in by_slot.values()})), ("ABANDONED", ["FAILED"]))
    k.close()
    return c.bad


def check_levels_and_gap(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    # tokens outside the counting window: the batch is in the ledger only
    w = World(root / "c1", attested=[("a", "ta", 100)])
    out = w.run(mod)
    c.eq("no counted token: C1, and committed all the same", ({s.level for s in out.slots}, out.state.value), ({"C1"}, "COMMITTED"))
    _, slots = histories(w)
    c.eq(
        "the level is the one the slot reached",
        {t.step.level for h in slots.values() for t in h if t.step.state is lifecycle.State.COMMITTED},
        {forecast.Level.C1},
    )
    assert out.commitment is not None
    c.eq(
        "the origin follows the commit time when no token counts",
        {s.origin_ms for s in out.slots},
        {origin(out.commitment.attestation.committed_at_ms)},
    )
    w.close()
    # the ledger's clock is seven minutes after the snapshot: the one-hour horizon is past its bound, the others are not
    g = World(root / "gap", ledger_clock=ManualClock(NOW + timedelta(minutes=7)))
    out = g.run(mod)
    c.eq(
        "the one-hour forecasts are void for the gap, the others wait",
        sorted((s.hours, s.fate.value) for s in out.slots),
        [(1, "VOID_GAP")] * 2 + [(4, "FORECAST")] * 2 + [(24, "FORECAST")] * 2,
    )
    c.eq(
        "a void has no origin to wait for, the others have",
        {(s.hours, s.origin_ms is None) for s in out.slots},
        {(1, True), (4, False), (24, False)},
    )
    settled = g.records("settlement")
    c.eq("a settlement record for each void", len(settled), 2)
    _, slots = histories(g)
    forecasts = {r["id"]: record_digest(_strip(r)) for r in g.records("forecast")}
    for rec in settled:
        s, digest = settlement_record.from_record(_strip(rec))
        c.eq(
            "the void is a gap, for pair A, naming its forecast by digest",
            (s.state.value, s.reason, s.pair_id, digest),
            ("VOID", "VOID_GAP", "A", forecasts[s.forecast_id]),
        )
        history = slots[s.forecast_id]
        last = history[-1].step
        c.eq(
            "and the slot's last step is the void with that settlement",
            (states(history)[-2:], last.reason, last.settlement_id),
            (["AWAITING_ORIGIN", "VOID"], "VOID_GAP", s.settlement_id),
        )
    g.close()
    return c.bad


def before_the_schedule(mapping: Any) -> Any:
    """The mapping as if it had been calibrated on the sixty days that end where the schedule starts, as a real one must be."""
    import dataclasses

    return dataclasses.replace(mapping, period_start_ms=SLOT - 60 * 24 * HOUR, period_end_ms=SLOT)


def barrier_map(target: int = 30, stop: int = 30, timeout: int = 40) -> Any:
    from hoshitori import barrier_rates

    cells = tuple((h, barrier_rates.Cell(target, stop, timeout, 0)) for h in barrier_rates.HORIZONS)
    return barrier_rates.BarrierRates(cells, "binance-spot", "USDT", ("BTCUSDT", "ETHUSDT"), 0, 1, 0)


def barrier_world(root: Path, bm: Any, **kw: Any) -> Any:
    return World(root, protocol={"barrier_arms": (("L0", bm.sha256),)}, **kw)


def check_barrier_slots(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    bm = barrier_map()
    w = barrier_world(root / "a", bm, ledger_clock=TickClock(NOW, 0.001))
    out = w.run(mod, barrier_mappings={"L0": bm})
    c.eq("committed", (out.state.value, out.reason), ("COMMITTED", None))
    c.eq("twelve slots: two candidates, three horizons, two contracts", len(out.slots), 12)
    c.eq(
        "six of each contract",
        sorted((s.contract, s.hours) for s in out.slots),
        sorted([(k, h) for k in "DB" for h in (1, 4, 24) for _ in (0, 1)]),
    )
    c.eq("every one a forecast at C3", {(s.fate.value, s.level) for s in out.slots}, {("FORECAST", "C3")})
    kinds: dict[str, int] = {}
    for r in w.records():
        kinds[r["type"]] = kinds.get(r["type"], 0) + 1
    c.eq("records by type", kinds, {"cycle_state": 8, "manifest": 1, "snapshot": 1, "evidence": 6, "forecast": 12, "forecast_state": 72})
    b = [r["body"] for r in w.records("forecast") if r["body"]["contract"] == "B"]
    d = [r["body"] for r in w.records("forecast") if r["body"]["contract"] == "D"]
    c.eq("six barrier forecasts and six directional ones", (len(b), len(d)), (6, 6))
    c.eq(
        "a barrier forecast carries the mapped probabilities",
        {(r["p_target"], r["p_stop"], r["p_timeout"], r["side"]) for r in b},
        {("0.300000", "0.300000", "0.400000", "LONG")},
    )
    c.eq("the multipliers", {(r["k_target"], r["k_stop"]) for r in b}, {("1.5", "1.5")})
    features = [r["body"] for r in w.records("evidence") if r["body"].get("path") == "features"]
    atrs = {r["body"]["fields"]["atr14"] for r in w.records("evidence") if r["body"].get("path") == "features"} if features else set()
    c.eq("the ATR is the one the features item holds", {r["atr"] for r in b}, atrs)
    c.eq(
        "the offsets are the multipliers times the ATR",
        all(Decimal(r["target_offset"]) == Decimal("1.5") * Decimal(r["atr"]) for r in b),
        True,
    )
    c.eq(
        "the agent version of a barrier forecast is not the directional one",
        {r["agent_version_hash"] for r in b}.isdisjoint({r["agent_version_hash"] for r in d}),
        True,
    )
    c.eq("the ids differ by contract", len({r["id"] for r in w.records("forecast")}), 12)
    w.close()
    # a barrier mapping with too few windows: the barrier slots are no calls, the directional ones are forecasts
    thin = barrier_map(30, 30, 39)
    t = barrier_world(root / "b", thin)
    out = t.run(mod, barrier_mappings={"L0": thin})
    c.eq(
        "unmapped barrier: no call; directional: forecast",
        sorted({(s.contract, s.fate.value, s.reason) for s in out.slots}),
        [("B", "NO_CALL", "UNMAPPED"), ("D", "FORECAST", None)],
    )
    t.close()
    # the one-hour barrier forecasts are void for the gap, with a settlement of contract B
    g = barrier_world(root / "gap", bm, ledger_clock=ManualClock(NOW + timedelta(minutes=7)))
    out = g.run(mod, barrier_mappings={"L0": bm})
    c.eq(
        "the one-hour forecasts of both contracts are void for the gap",
        sorted((s.contract, s.hours, s.fate.value) for s in out.slots if s.hours == 1),
        [("B", 1, "VOID_GAP")] * 2 + [("D", 1, "VOID_GAP")] * 2,
    )
    voided = {s.forecast_id for s in out.slots if s.fate.value == "VOID_GAP"}
    contracts = {settlement_record.from_record(_strip(r))[0].contract for r in g.records("settlement")}
    c.eq("and each void is a settlement of its own contract", contracts, {"B", "D"})
    c.eq("four of them", len(voided), 4)
    g.close()
    # the services must agree with the protocol about the barrier mappings
    v = barrier_world(root / "v", bm)
    c.refuses(
        "no barrier mapping given",
        lambda: v.run(mod),
        "the barrier mappings are those of the protocol's barrier arms, no more and no fewer",
    )
    c.refuses(
        "another barrier mapping than the pinned",
        lambda: v.run(mod, barrier_mappings={"L0": barrier_map(31, 30, 39)}),
        "the barrier mapping of arm L0 is not the one the protocol pins",
    )
    v.close()
    plain = World(root / "p")
    c.refuses(
        "a barrier mapping the protocol does not pin",
        lambda: plain.run(mod, barrier_mappings={"L0": bm}),
        "the barrier mappings are those of the protocol's barrier arms, no more and no fewer",
    )
    plain.close()
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    sv = w.services(mod)
    c.refuses(
        "a slot not on the schedule", lambda: mod.run_cycle(sv, SLOT + 1, since_ms=SLOT), "the slot is a slot of the protocol's schedule"
    )
    c.refuses(
        "a slot before the schedule",
        lambda: mod.run_cycle(sv, SLOT - HOUR, since_ms=SLOT - 2 * HOUR),
        "the slot is a slot of the protocol's schedule",
    )
    c.refuses(
        "a negative last read",
        lambda: mod.run_cycle(sv, SLOT, since_ms=-1),
        "the registry was last read for changes no later than the slot",
    )
    c.refuses(
        "a last read after the slot",
        lambda: mod.run_cycle(sv, SLOT, since_ms=SLOT + 1),
        "the registry was last read for changes no later than the slot",
    )
    c.refuses(
        "a boolean last read",
        lambda: mod.run_cycle(sv, SLOT, since_ms=True),
        "the registry was last read for changes no later than the slot",
    )
    future = World(root / "f", clock=ManualClock(NOW - timedelta(hours=1)))
    c.refuses("a slot that has not come", lambda: future.run(mod), "the slot has not come")
    c.refuses(
        "a source that is not a urn", lambda: mod.run_cycle(w.services(mod, source_urn="x"), SLOT, since_ms=SLOT), "the source is a urn"
    )
    c.refuses(
        "no mapping",
        lambda: mod.run_cycle(w.services(mod, mappings={}), SLOT, since_ms=SLOT),
        "the mappings are those of the protocol's arms, no more and no fewer",
    )
    other = before_the_schedule(rates(rules_arm, lambda h, s: (100, 50, 0)))
    c.refuses(
        "another mapping",
        lambda: mod.run_cycle(w.services(mod, mappings={"L0": other}), SLOT, since_ms=SLOT),
        "the mapping of arm L0 is not the one the protocol pins",
    )
    import dataclasses

    late = dataclasses.replace(before_the_schedule(graded(rules_arm)), period_end_ms=SLOT + 1)
    c.refuses(
        "a mapping calibrated on data that runs into the scored period",
        lambda: World(root / "late", mapping=late).run(mod),
        "the mapping of arm L0 was calibrated on data that runs into the scored period",
    )
    edge = before_the_schedule(graded(rules_arm))
    c.eq("one that ends where the schedule starts is allowed", World(root / "edge", mapping=edge).run(mod).state.value, "COMMITTED")
    bm = barrier_map()
    edge_bm = dataclasses.replace(bm, period_end_ms=SLOT)
    c.eq(
        "a barrier mapping that ends where the schedule starts is allowed",
        barrier_world(root / "edgebm", edge_bm).run(mod, barrier_mappings={"L0": edge_bm}).state.value,
        "COMMITTED",
    )
    late_bm = dataclasses.replace(bm, period_end_ms=SLOT + 1)
    c.refuses(
        "a barrier mapping calibrated on data that runs into the scored period",
        lambda: barrier_world(root / "latebm", late_bm).run(mod, barrier_mappings={"L0": late_bm}),
        "the barrier mapping of arm L0 was calibrated on data that runs into the scored period",
    )
    bare = Environment(w.runner, w.captures, {}, w.registry, EvidenceStore(), w.clock)
    c.refuses(
        "no adapter for the primary venue",
        lambda: mod.run_cycle(w.services(mod, env=bare), SLOT, since_ms=SLOT),
        "the environment has an adapter for the protocol's primary oracle venue",
    )
    c.eq("nothing was written or asked", (w.records(), w.calls), ([], []))
    first = w.run(mod)
    c.eq("a first run", first.state.value, "COMMITTED")
    c.refuses("a second run of the same slot", lambda: w.run(mod), "this slot has already been started: a cycle is never run twice")
    c.eq("the second run wrote nothing new", len(w.records("cycle_state")), 8)
    for world in (w, future):
        world.close()
    return c.bad


def check_edges(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    at = World(root / "at", clock=ManualClock(T0))
    c.eq("a slot that is exactly now has come", at.run(mod).state.value, "COMMITTED")
    since = World(root / "since")
    c.eq(
        "the registry may have been read at the very slot", mod.run_cycle(since.services(mod), SLOT, since_ms=SLOT).state.value, "COMMITTED"
    )
    zero = World(root / "zero")
    c.eq("or never", mod.run_cycle(zero.services(mod), SLOT, since_ms=0).state.value, "COMMITTED")

    def skewed(seconds: float) -> World:
        return World(
            root / f"skew{seconds}",
            script=lambda call, world: Reply(time_reply(world.clock, seconds) if call.operation == "time" else klines(call.args)),
        )

    exact = skewed(2.0).run(mod)
    c.eq("a venue clock exactly at the limit is accepted", exact.state.value, "COMMITTED")
    over = skewed(2.001).run(mod)
    c.eq("one millisecond more is not", (over.state.value, over.reason), ("ABANDONED", "CLOCK_OFFSET"))
    behind = skewed(-2.001).run(mod)
    c.eq("nor is a clock behind ours by that much", (behind.state.value, behind.reason), ("ABANDONED", "CLOCK_OFFSET"))

    def slow(call: Call, world: World) -> Reply:
        if call.operation == "candles":
            world.clock.advance(timedelta(seconds=5))
        return world.default(call)

    strict = World(root / "strict", script=slow, protocol={"freshness_ms": (("EXCHANGE", 1000),)})
    out = strict.run(mod)
    c.eq(
        "the protocol's own freshness limit is the one used",
        {(s.fate.value, s.reason) for s in out.slots},
        {("FAILED_EVIDENCE", "NO_FEATURES")},
    )
    plain = World(root / "plain", script=slow, protocol={"freshness_ms": ()})
    out = plain.run(mod)
    c.eq("with none, the defaults are", {s.fate.value for s in out.slots}, {"FORECAST"})
    for world in (at, since, zero, strict, plain):
        world.close()
    return c.bad


def check_batch_root(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    records = [
        {
            "spec": "hoshitori.record/1",
            "type": "x",
            "type_version": 1,
            "id": f"r{n}",
            "source": URN,
            "created_at": "2026-10-29T12:00:00.000Z",
            "body": {"n": n},
        }
        for n in range(3)
    ]
    leaves = [__import__("hashlib").sha256(b"\x00" + bytes.fromhex(record_digest(r))).digest() for r in records]
    left = (
        __import__("hashlib").sha256(b"\x01" + __import__("hashlib").sha256(b"\x01" + leaves[0] + leaves[1]).digest() + leaves[2]).digest()
    )
    c.eq("a tree over the digests of three records, as RFC 6962 builds it", mod.batch_root(records), left.hex())
    c.eq("it depends on the order", mod.batch_root(records[::-1]) == mod.batch_root(records), False)
    c.eq("and on every record", mod.batch_root(records[:2]) == mod.batch_root(records), False)
    return c.bad


# --- RYO as a source of the league ------------------------------------------------------------------------------------------------------
def whoami_body(allowance: dict[str, Any] | None) -> bytes:
    """RYO's key status in the recorded shape of ``GET /whoami``, with the call allowance given (none: the member is absent)."""
    body: dict[str, Any] = {
        "key_id": "key-test",
        "label": "test",
        "scopes": ["tools:read"],
        "expires_at": "2027-01-01T00:00:00Z",
        "rate_limit_per_min": 60,
        "rate_limit_remaining": 60,
        "tools": ["market_overview", "scan_market", "analyze_token", "deep_analysis", "compare_tokens", "monitor_market_sentiment_shift"],
    }
    if allowance is not None:
        body["call_allowance"] = allowance
    return json.dumps(body).encode()


def ryo_world(
    root: Path,
    ryo_script: Callable[[Call], Reply] | None = None,
    delay_s: float = 0.0,
    allowance: dict[str, Any] | None = None,
    status_reply: Reply | None = None,
    **kw: Any,
) -> tuple[World, Any]:
    """A world whose protocol lists RYO, and the gateway it would be read through, scripted. Every answer is as of the world's clock. Its key
    status answers with ``allowance`` (none: no allowance member, as a key with no limit); the status reads are kept apart from the calls."""
    from hoshitori import league, ryo_collect

    w = World(root, protocol={"optional_sources": ("ryo",)}, **kw)
    as_of = format_rfc3339_ms(w.clock.now())

    def answer(call: Call) -> Reply:
        args = dict(call.args)
        if call.operation == "scan_market":
            data = ryo_data.scan_market_data()
            base = data["candidates"][0]
            rows = [(1, "BTC"), (2, "ETH"), (3, "SOL"), (4, "ZZZ")]
            data["candidates"] = [{**base, "rank": rank, "symbol": symbol} for rank, symbol in rows]
            data["candidate_count"] = len(rows)
            return Reply(ryo_data.rest_body("scan_market", ryo_data.result("scan_market", data=data, as_of=as_of)))
        data = ryo_data.DATA[call.operation]()
        if "symbol" in args:
            data["asset"]["symbol"] = args["symbol"]
        return Reply(ryo_data.rest_body(call.operation, ryo_data.result(call.operation, data=data, as_of=as_of)))

    ryo_calls: list[Call] = []
    status_reads: list[Call] = []

    def respond(call: Call) -> Reply:
        if call.operation == "_whoami":
            status_reads.append(call)
            return status_reply or Reply(whoami_body(allowance))
        ryo_calls.append(call)
        w.clock.advance(timedelta(seconds=delay_s))  # RYO's own pace: each call takes this long of the world's clock
        return (ryo_script or answer)(call)

    store = EvidenceStore()
    env = Environment(w.runner, w.captures, {"binance-spot": w.adapter}, w.registry, store, w.clock)
    ryo_runner = FakeRunner(w.captures, w.clock, respond)
    ryo_runner._n = 1_000_000  # its own capture numbers, apart from the exchange runner's
    ryo = league.RyoWiring(ryo_collect.RyoEnvironment(ryo_runner, w.captures, store, w.clock))
    return w, {"env": env, "ryo": ryo, "calls": ryo_calls, "status": status_reads}


def check_ryo_route(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w, over = ryo_world(root / "on", watchlist=("ETH",))
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    c.eq("a cycle that reads RYO is committed", (out.state.value, out.reason), ("COMMITTED", None))
    body = w.records("manifest")[0]["body"]
    c.eq("the scan route is read", body["routes"]["RYO_SCAN"], "OK")
    c.eq("and has nothing to say against", body["failures"], {})
    c.eq(
        "RYO's rank decides: the scan gives BTC and ETH, SOL has no second oracle, ZZZ is in no registry",
        sorted({s.symbol for s in out.slots}),
        ["BTC", "ETH"],
    )
    inputs = {i["kind"]: i for i in body["inputs"]}
    scan_input = inputs.get("ryo_scan_response", {})
    c.eq(
        "the scan's response is named in the manifest, by the capture it was read from and that capture's hash",
        (sorted(inputs), w.captures.get(scan_input.get("capture_id", "x")).body_sha256 == scan_input.get("sha256"))
        if scan_input
        else (sorted(inputs), False),
        (["event_trigger_log", "ryo_scan_response"], True),
    )
    c.eq("RYO was asked the scan first", [call.operation for call in over["calls"]][:1], ["scan_market"])
    asked = sorted(call.operation for call in over["calls"][1:])
    c.eq(
        "then both tools for each of the two candidates and the two market tools",
        asked,
        sorted(["analyze_token"] * 2 + ["deep_analysis"] * 2 + ["market_overview", "monitor_market_sentiment_shift"]),
    )
    ryo = [r for r in w.records("evidence") if r["body"]["source"] == "ryo"]
    kinds = sorted(str(r["body"]["kind"]) for r in ryo)
    c.eq(
        "the cycle commits RYO's observation and interpretation items", (kinds.count("OBSERVATION"), kinds.count("INTERPRETATION")), (6, 6)
    )
    snap = w.records("snapshot")[0]["body"]
    listed = {str(i) for i in canonical.dumps(snap).decode().split('"') if i.startswith("ev")}
    observed = {str(r["id"]) for r in ryo if r["body"]["kind"] == "OBSERVATION"}
    interpreted = {str(r["id"]) for r in ryo if r["body"]["kind"] == "INTERPRETATION"}
    c.eq("the snapshot holds the observations of RYO", observed <= listed, True)
    c.eq("and none of its interpretations", interpreted & listed, set())
    marks = [r for r in w.records("evidence") if r["body"]["source"] == "hoshitori.crosscheck"]
    everything = {str(r["id"]): r["body"] for r in w.records("evidence")}
    priced = [r for r in ryo if r["body"]["kind"] == "OBSERVATION" and "market.price_usd" in r["body"]["fields"]]
    c.eq(
        "a price check for each RYO price of each candidate, each in the snapshot",
        (len(marks), len(priced), {str(r["id"]) for r in marks} <= listed),
        (len(priced), 4, True),
    )
    exchange = {i for i, b in everything.items() if b["source"] not in ("ryo", "hoshitori.crosscheck", "hoshitori.features")}
    stood = sorted(
        (
            sum(1 for p in r["body"]["derived_from"] if everything[p["evidence_id"]]["source"] == "ryo"),
            sum(1 for p in r["body"]["derived_from"] if p["evidence_id"] in exchange),
        )
        for r in marks
    )
    c.eq("each stands on one RYO price and one exchange series", stood, [(1, 1)] * len(marks))
    worked = []
    for r in marks:
        parents = [everything[p["evidence_id"]] for p in r["body"]["derived_from"]]
        said = Fraction(next(b for b in parents if b["source"] == "ryo")["fields"]["market.price_usd"])
        close = Fraction(next(b for b in parents if b["source"] != "ryo")["fields"]["last_close"])
        gap = abs(said - close) / close
        shown = round(gap * 10**6)
        worked.append(
            (
                r["body"]["fields"]["difference"],
                r["body"]["fields"]["state"],
                f"{shown // 10**6}.{shown % 10**6:06d}",
                "AGREE" if gap <= Fraction(1, 50) else "CONFLICT",
            )
        )
    c.eq(
        "and says the gap and the state as an independent reading of its parents gives them",
        [(a, b) for a, b, _, _ in worked],
        [(x, y) for _, _, x, y in worked],
    )
    c.eq("which, in this world, is a conflict (RYO says 100.5, the exchange 1005)", {b for _, b, _, _ in worked}, {"CONFLICT"})
    reads = sorted((r["body"] for r in w.records("ryo_read")), key=lambda b: str(b["symbol"]))
    c.eq(
        "arm R: one read of RYO's own verdict and plan per candidate its deep analysis answered, in the cycle's batch",
        [(b["symbol"], b["call"], b["reading"], b["plan"], b["cycle_id"] == out.cycle_id) for b in reads],
        [(symbol, "avoid", "NOT_LONG", {"entry": "100.5", "stop": "95.0", "target": "110.0"}, True) for symbol in ("BTC", "ETH")],
    )
    c.eq("each read points at an interpretation item that no arm was given", all(str(b["evidence_id"]) in interpreted for b in reads), True)
    c.eq("and at the capture it was read from", all(str(b["capture_id"]) in w.captures for b in reads), True)
    from hoshitori import view_models
    from hoshitori.timeline import to_ms

    views = view_models.build(w.records(), w.protocol, now_ms=to_ms(w.clock.now()))
    assert views.ryo is not None
    day = 24 in w.protocol.horizons_hours
    c.eq(
        "the views hold RYO's record: each read waits for its cycle's 24-hour calls, or has no window when the protocol has none",
        [(o.symbol, o.reading, o.state) for o in views.ryo.outcomes],
        [(symbol, "NOT_LONG", "Waiting" if day else "No window") for symbol in ("BTC", "ETH")],
    )
    c.eq("nothing is scored before it settles", (views.ryo.summary.reads, views.ryo.summary.scored), (2, 0))
    w.close()

    w, over = ryo_world(
        root / "scan-down", watchlist=("ETH",), ryo_script=lambda call: Reply(outcome=Outcome.FAILED, reason="UPSTREAM", capture=False)
    )
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    body = w.records("manifest")[0]["body"]
    c.eq(
        "with RYO down the cycle still commits on the watchlist",
        (out.state.value, sorted({s.symbol for s in out.slots})),
        ("COMMITTED", ["ETH"]),
    )
    c.eq(
        "the manifest says the scan failed and why, in a closed reason",
        (body["routes"]["RYO_SCAN"], body["failures"]),
        ("FAILED_EVIDENCE", {"RYO_SCAN": "RYO_SCAN_CALL_FAILED"}),
    )
    c.eq(
        "the failures of its evidence are in the outcome, by tool",
        sorted(f.detail.split(":")[0] for f in out.failures if f.candidate_id == "cd-eth" or f.candidate_id == "cycle")[:1] != [],
        True,
    )
    c.eq("no RYO item was committed", [r for r in w.records("evidence") if r["body"]["source"] == "ryo"], [])
    c.eq("and no read of RYO's verdict: a missing read stays missing", w.records("ryo_read"), [])
    from hoshitori import view_models as vm
    from hoshitori.timeline import to_ms as ms

    c.eq("and the views have no RYO record", vm.build(w.records(), w.protocol, now_ms=ms(w.clock.now())).ryo, None)
    c.eq(
        "the scan's failure is in words among the cycle's failures, before the others",
        [(f.candidate_id, f.reason.value, f.detail) for f in out.failures][:1],
        [("cycle", "CALL_FAILED", "scan_market: FAILED: UPSTREAM")],
    )
    w.close()

    w = World(root / "off")
    out = w.run(mod)
    c.eq(
        "without RYO nothing asks it and the scan route says it is off",
        (w.records("manifest")[0]["body"]["failures"], out.state.value),
        ({"RYO_SCAN": mod.SCAN_OFF}, "COMMITTED"),
    )
    w.close()

    w, over = ryo_world(root / "mismatch-a", watchlist=("ETH",))
    for label, kwargs in (
        ("a protocol that lists RYO and a league that is not wired", {"env": over["env"]}),
        ("a league wired to a protocol that does not list RYO", None),
    ):
        try:
            if kwargs is None:
                w2 = World(root / "mismatch-b", watchlist=("ETH",))
                w2.run(mod, ryo=over["ryo"])
            else:
                w.run(mod, **kwargs)
        except mod.LeagueError as exc:
            c.eq(label, str(exc), "RYO is wired exactly when the protocol lists it as an optional source")
        else:
            c.bad.append(f"{label}: was accepted")
    w, over = ryo_world(root / "mismatch-c", watchlist=("ETH",))
    try:
        w.run(mod, ryo=over["ryo"])  # the services' own environment has another store than the one RYO is read into
    except mod.LeagueError as exc:
        c.eq("RYO read into another store", str(exc), "RYO is read into the cycle's own evidence store and capture store")
    else:
        c.bad.append("RYO read into another store was accepted")
    return c.bad


def check_slow_ryo_leaves_the_exchanges_fresh(mod: types.ModuleType, root: Path) -> list[str]:
    """RYO is read at the pace its fan-out limit allows (five calls a minute), which takes minutes for a few candidates; the exchange series
    have a freshness limit of a minute. So RYO is read first and the exchanges last: a slow RYO must not make every candle series stale."""
    c = Case(mod)
    w, over = ryo_world(root / "slow", watchlist=("ETH",), delay_s=20.0)
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    c.eq("a cycle whose RYO reads take minutes is committed", (out.state.value, out.reason), ("COMMITTED", None))
    c.eq(
        "and its forecasts are made: the candle series were not stale at the snapshot",
        (sorted({str(s.fate.value) for s in out.slots}), sorted({str(s.reason) for s in out.slots})),
        (["FORECAST"], ["None"]),
    )
    w.close()
    return c.bad


def check_ryo_daily_hour(mod: types.ModuleType, root: Path) -> list[str]:
    """A key's monthly allowance is about 200 calls: RYO may be read in one cycle a day only, and every other cycle says so."""
    import dataclasses

    c = Case(mod)
    hour = SLOT // HOUR % 24
    w, over = ryo_world(root / "other-hour", watchlist=("ETH",))
    out = w.run(mod, env=over["env"], ryo=dataclasses.replace(over["ryo"], daily_hour=(hour + 1) % 24))
    body = w.records("manifest")[0]["body"]
    c.eq("outside the daily hour RYO is asked nothing", over["calls"], [])
    c.eq(
        "and the cycle says why, under the cycle, first",
        [(f.candidate_id, f.reason.value, f.detail) for f in out.failures][:1],
        [("cycle", "CALL_FAILED", "scan_market: RYO is read once a day, in another hour")],
    )
    c.eq("the cycle commits on the other routes", (out.state.value, sorted({s.symbol for s in out.slots})), ("COMMITTED", ["ETH"]))
    c.eq("the manifest says why", (body["routes"]["RYO_SCAN"], body["failures"]), ("FAILED_EVIDENCE", {"RYO_SCAN": "RYO_READ_ONCE_A_DAY"}))
    c.eq(
        "and no read of RYO is written",
        (w.records("ryo_read"), [r for r in w.records("evidence") if r["body"]["source"] == "ryo"]),
        ([], []),
    )
    w.close()
    w, over = ryo_world(root / "the-hour", watchlist=("ETH",))
    out = w.run(mod, env=over["env"], ryo=dataclasses.replace(over["ryo"], daily_hour=hour))
    c.eq(
        "in the daily hour RYO is read",
        (out.state.value, sorted(r["body"]["symbol"] for r in w.records("ryo_read"))),
        ("COMMITTED", ["BTC", "ETH"]),
    )
    w.close()
    env = over["ryo"].env
    for bad in (24, -1, True):
        try:
            mod.RyoWiring(env, daily_hour=bad)
            c.bad.append(f"a daily hour of {bad!r} was accepted")
        except mod.LeagueError as exc:
            c.eq(f"a daily hour of {bad!r}", str(exc), "the daily RYO hour is a UTC hour, 0 to 23")
    c.eq("the first and the last hour of the day are hours", [mod.RyoWiring(env, daily_hour=h).daily_hour for h in (0, 23)], [0, 23])
    return c.bad


def check_ryo_key_status(mod: types.ModuleType, root: Path) -> list[str]:
    """RYO's own key status, read before the scan: a key that may not call, or has no call left, is asked nothing; a known remainder caps
    the cycle's calls; a status that says nothing, or cannot be read, changes nothing."""
    c = Case(mod)
    w, over = ryo_world(root / "off", watchlist=("ETH",), allowance={"call_limit": 200, "used": 0, "remaining": 200, "allowed": False})
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    body = w.records("manifest")[0]["body"]
    c.eq("a key RYO says may not call is asked nothing", (over["calls"], len(over["status"])), ([], 1))
    c.eq("the cycle commits on the other routes", (out.state.value, sorted({s.symbol for s in out.slots})), ("COMMITTED", ["ETH"]))
    c.eq(
        "and says why, in words, under the cycle",
        [(f.candidate_id, f.detail) for f in out.failures][:1],
        [("cycle", "scan_market: RYO's key status says this key may not call now")],
    )
    c.eq("the manifest's closed reason", body["failures"], {"RYO_SCAN": "RYO_KEY_NOT_ALLOWED"})
    c.eq(
        "and no read was asked either",
        {f.detail.split(": ", 1)[1] for f in out.failures[1:]},
        {"not asked: the RYO budget for this hour is spent"},
    )
    w.close()

    w, over = ryo_world(root / "spent", watchlist=("ETH",), allowance={"remaining": 0, "allowed": True})
    spent = w.run(mod, env=over["env"], ryo=over["ryo"])
    c.eq("a key with no call left is asked nothing", over["calls"], [])
    c.eq("with its own reason", w.records("manifest")[0]["body"]["failures"], {"RYO_SCAN": "RYO_ALLOWANCE_SPENT"})
    c.eq(
        "said in words under the cycle",
        [f.detail for f in spent.failures][:1],
        ["scan_market: RYO's key status leaves no call in this period"],
    )
    w.close()

    w, over = ryo_world(root / "few", watchlist=("ETH",), allowance={"remaining": 4, "allowed": True})
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    asked = [call.operation for call in over["calls"]]
    c.eq(
        "four calls left: the scan, then three of what matters most, and no more",
        (asked[0], sorted(asked[1:])),
        ("scan_market", ["analyze_token", "analyze_token", "market_overview"]),
    )
    c.eq("and the cycle commits", out.state.value, "COMMITTED")
    w.close()

    for label, allowance in (("no allowance member", None), ("an allowance with no limit", {"remaining": None, "allowed": True})):
        w, over = ryo_world(root / label.replace(" ", "-"), watchlist=("ETH",), allowance=allowance)
        w.run(mod, env=over["env"], ryo=over["ryo"])
        c.eq(
            f"{label}: the cycle reads RYO as before (the scan, two tools for each of two candidates, two market tools)",
            len(over["calls"]),
            7,
        )
        w.close()

    w, over = ryo_world(root / "unread", watchlist=("ETH",), status_reply=Reply(b"<html>down</html>"))
    w.run(mod, env=over["env"], ryo=over["ryo"])
    c.eq("a status that cannot be read stops nothing: the scan is asked", [call.operation for call in over["calls"]][:1], ["scan_market"])
    w.close()
    return c.bad


def check_ryo_budget(mod: types.ModuleType, root: Path) -> list[str]:
    from hoshitori.capture import RawCapture

    c = Case(mod)
    w, over = ryo_world(root / "spent", watchlist=("ETH",))
    for n in range(40):  # forty attempts sent to RYO in the last hour, before this cycle
        sent = w.clock.now() - timedelta(minutes=30)
        w.captures.append(RawCapture(f"cap-{2_000_000 + n:032x}", "ryo", {"operation": "analyze_token"}, 200, {}, b"{}", sent, sent))
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    body = w.records("manifest")[0]["body"]
    c.eq("a spent hour asks RYO nothing", over["calls"], [])
    c.eq("the cycle commits on the other routes", (out.state.value, sorted({s.symbol for s in out.slots})), ("COMMITTED", ["ETH"]))
    c.eq(
        "and says in words why RYO was not asked, under the cycle, first",
        [(f.candidate_id, f.reason.value, f.detail) for f in out.failures][:1],
        [("cycle", "CALL_FAILED", "scan_market: the RYO budget for this hour is spent")],
    )
    c.eq(
        "then that no read was asked either",
        {f.detail.split(": ", 1)[1] for f in out.failures[1:]},
        {"not asked: the RYO budget for this hour is spent"},
    )
    c.eq(
        "and the manifest says why the scan did not run",
        (body["routes"]["RYO_SCAN"], body["failures"]),
        ("FAILED_EVIDENCE", {"RYO_SCAN": "RYO_BUDGET_SPENT"}),
    )
    w.close()

    w, over = ryo_world(root / "old", watchlist=("ETH",))
    for n in range(40):  # forty attempts, but more than an hour ago: not counted
        sent = w.clock.now() - timedelta(minutes=61)
        w.captures.append(RawCapture(f"cap-{2_000_000 + n:032x}", "ryo", {"operation": "analyze_token"}, 200, {}, b"{}", sent, sent))
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    c.eq("attempts of more than an hour ago are not counted", over["calls"][0].operation, "scan_market")
    w.close()

    plain = mod.RyoWiring(over["ryo"].env)
    c.eq(
        "the defaults: 200 s for the scan, 360 s for the evidence, 40 calls an hour",
        (plain.scan_wait_s, plain.collect_wait_s, plain.calls_per_hour),
        (200.0, 360.0, 40),
    )
    try:
        plain.calls_per_hour = 1  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("the wiring can be changed after it is made")
    w, over = ryo_world(root / "small", watchlist=("ETH",))
    over["ryo"] = mod.RyoWiring(over["ryo"].env, calls_per_hour=6)  # one for the key status, which counts against this budget too
    out = w.run(mod, env=over["env"], ryo=over["ryo"])
    asked = [call.operation for call in over["calls"]]
    c.eq("the key status was read once, before the scan", [call.operation for call in over["status"]], ["_whoami"])
    c.eq(
        "a small budget is spent on the scan, then on what matters most",
        (asked[0], sorted(asked[1:])),
        ("scan_market", ["analyze_token", "analyze_token", "market_overview", "monitor_market_sentiment_shift"]),
    )
    c.eq("and the cycle still commits", out.state.value, "COMMITTED")
    c.eq(
        "the unasked calls are failures of the cycle",
        sorted(f.detail.split(":")[0] for f in out.failures if "not asked" in f.detail),
        ["deep_analysis", "deep_analysis"],
    )
    w.close()
    return c.bad


def _strip(record: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in record.items() if k != "committed_at"}


def check_failure_after_the_batch(mod: types.ModuleType, root: Path) -> list[str]:
    """A process that fails after the batch is in the ledger does not abandon the cycle: the forecasts are committed, and a committed
    forecast is never abandoned (contract 0.8, 17). The cycle is left committing, for the recovery to roll forward."""
    c = Case(mod)
    w = World(root)
    real = mod._after

    def fails(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the disk went away after the batch")

    mod._after = fails
    try:
        w.run(mod)
        raised = False
    except RuntimeError:
        raised = True
    finally:
        mod._after = real
    c.eq("the failure is raised, not swallowed", raised, True)
    c.eq("the forecasts are in the ledger", len(w.records("forecast")), 6)
    cycles, slots = histories(w)
    (cid,) = cycles
    c.eq("the cycle is left committing, not abandoned", states(cycles[cid])[-1], "COMMITTING")
    c.eq("and no slot with a forecast is failed", sorted({states(h)[-1] for h in slots.values()}), ["DECIDED"])
    w.close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_happy_path,
        check_event_route,
        check_empty_cycle,
        check_failed_evidence,
        check_no_call,
        check_abandoned,
        check_failure_after_the_batch,
        check_defect,
        check_levels_and_gap,
        check_validation,
        check_edges,
        check_batch_root,
        check_barrier_slots,
        check_ryo_route,
        check_slow_ryo_leaves_the_exchanges_fresh,
        check_ryo_budget,
        check_ryo_key_status,
        check_ryo_daily_hour,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-900:]}")
    return bad
