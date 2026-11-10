"""Scenario checks of a Desk run (``hoshitori.desk``), runnable against a mutant.

Every port is scripted: the checks are about the order of the steps, what each step may do, what is recorded and how the run ends, on planted
data (the falsification series are the scenarios of experiment E7, labelled simulated) and a scripted model. No network, no key and no private
recording is needed. A scripted model is not evidence of any model's quality.
"""

from __future__ import annotations

import random
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hoshitori import desk_acquire, desk_gates, desk_menu, desk_record, evidence, falsify, falsify_calibration
from hoshitori.clock import ManualClock
from hoshitori.lineage import EvidenceStore
from tests.desk_interpret_checks import series as candle_series
from tests.evidence_checks import observation

D = Decimal
NOW = datetime(2026, 10, 29, 12, 5, 10, tzinfo=UTC)
TA, H = 700, 60
SPOT, KRAKEN = "INDEPENDENT_PRICE_CHECK:binance-spot:candles", "INDEPENDENT_PRICE_CHECK:kraken:candles"
FUNDING, TOKEN = "DERIVATIVES_CONTEXT:binance-funding:funding", "RYO_TOKEN_READ:ryo:analyze_token"
SCREEN = "ADDRESS_FLAG_CHECK:address-flag:check"
CONTRACT = ("ethereum", "0x000000000000000000000000000000000000dEaD")
CALIBRATION = falsify.Calibration({"T_ONSET": 0.05, "T_MARKET": 0.0}, 600, "a" * 64)
H1, H2, H3, H4 = "1" * 64, "2" * 64, "3" * 64, "4" * 64
PINNED_DECISION_ID = "dnbvyeyl4dur3apd472prck6diuf"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def scenario(name: str, seed: int = 1) -> tuple[list[float], list[float]]:
    return falsify_calibration.generate(name, random.Random(seed), TA, H)


def seed_surviving(name: str) -> int:
    """A seed whose scenario no calibrated test takes for an alternative explanation (so the step passes)."""
    for seed in range(200):
        token, market = scenario(name, seed)
        results = [falsify.t_onset(token, TA, H), falsify.t_market(token, market, TA, H)]
        if falsify.run(results, CALIBRATION).outcome == "SURVIVED":
            return seed
    raise AssertionError(f"no seed of scenario {name} survives")


def seed_where(name: str, want: str) -> int:
    for seed in range(200):
        token, _ = scenario(name, seed)
        if falsify.t_onset(token, TA, H).result == want:
            return seed
    raise AssertionError(f"no seed of scenario {name} gives {want}")


class World:
    """A scripted Desk: what each check brings, what the history is, what the model says."""

    def __init__(self, mod: types.ModuleType, **over: Any) -> None:
        self.mod = mod
        self.store = EvidenceStore()
        self.clock = ManualClock(NOW)
        self.n = 0
        self.acquired: list[str] = []
        self.proposed: list[Any] = []
        self.committed: list[Any] = []
        self.records: list[tuple[str, dict[str, Any]]] = []
        self.moves = {SPOT: ("1000", "1005"), KRAKEN: ("1000", "1005")}  # +0.5 percent
        self.failures: dict[str, str] = {}
        self.history: Any = scenario("A", seed_surviving("A"))
        self.proposal = desk_gates.Proposal("LONG", D("0.62"), D("0.04"))
        self.model_failure: str | None = None
        self.grounded = True
        self.stale = False
        self.room = D("0.05")
        self.calibration: Any = CALIBRATION
        self.ryo_change: str | None = "0.30"
        self.items = 1
        self.cites_reversed = False
        self.cites_count = 1
        self.commit_error: Exception | None = None
        self.ctxs: list[Any] = []
        self.contract: tuple[str, str] | None = None  # the token's contract: with one, the menu offers a screen of it
        self.screen_reading: str | None = "NO_FLAG_FOUND"  # what the screen says; None: the call fails
        self.__dict__.update(over)

    def menu(self, symbol: str = "BTC") -> Any:
        providers = {"binance-spot": frozenset({"candles"}), "kraken": frozenset({"candles"}), "binance-funding": frozenset({"funding"})}
        if self.contract is not None:
            providers["address-flag"] = frozenset({"check"})
        return desk_menu.build_menu(
            "asset:btc", symbol, providers, frozenset({"analyze_token"}), desk_menu.CostTable.from_latencies({}), contract=self.contract
        )

    def acquire(self, check: Any, ctx: Any) -> Any:
        self.acquired.append(check.check_id)
        self.ctxs.append(ctx)
        self.n += 1
        if check.check_id in self.failures:
            return desk_acquire.Acquired("FAILED", (), (), self.failures[check.check_id], "scripted", 1)
        if check.check_type == "ADDRESS_FLAG_CHECK":
            if self.screen_reading is None:
                return desk_acquire.Acquired("FAILED", (f"cap-{self.n}",), (), "CALL_FAILED", "no source of the screen answered", 1)
            ids = []
            for source, family in tuple((f"source_{k}", "PRIMARY_DOC:ofac" if k == 0 else "CHAIN:ethereum") for k in range(6)):
                state = "listed" if self.screen_reading == "FLAGGED" else "not_listed"
                item = observation(
                    evidence,
                    fields=(("kind", "sanctions_list"), ("stale", False), ("state", state)),
                    unknown_fields=(),
                    capture_id=f"cap-{self.n}",
                    path=f"address_flag/{source}",
                    origin=evidence.parse_family(family),
                    asset_refs=(),
                )
                self.store.add(item, f"{self.n * 7 + len(ids):064x}")
                ids.append(item.evidence_id)
            return desk_acquire.Acquired("OK", (f"cap-{self.n}",), tuple(sorted(ids, reverse=True)), None, "", 1, None, self.screen_reading)
        fields = (("market.price_usd", "1"),) + ((("performance.change_1h_pct", self.ryo_change),) if self.ryo_change else ())
        item = observation(evidence, fields=fields, unknown_fields=(), capture_id=f"cap-{self.n}")
        self.store.add(item, f"{self.n:064x}")
        ids = (item.evidence_id,)
        for extra in range(self.items - 1):
            more = observation(evidence, fields=fields, unknown_fields=(), capture_id=f"cap-{self.n}-{extra}")
            self.store.add(more, f"{self.n * 1000 + extra + 100:064x}")
            ids = (*ids, more.evidence_id)
        ids = tuple(sorted(ids, reverse=True))
        move = self.moves.get(check.check_id)
        series = candle_series(*move) if move is not None and check.check_type == "INDEPENDENT_PRICE_CHECK" else None
        return desk_acquire.Acquired("OK", (f"cap-{self.n}",), ids, None, "", check.cost.calls, series)

    def ports(self) -> Any:
        mod = self.mod

        def propose(request: Any) -> Any:
            self.proposed.append(request)
            if self.model_failure is not None:
                return mod.ModelFailure(self.model_failure)
            cites = tuple(sorted(request.evidence_ids, reverse=self.cites_reversed))[: self.cites_count]
            return mod.Proposed(self.proposal, cites, H3, 500)

        def commit(request: Any) -> str:
            self.committed.append(request)
            if self.commit_error is not None:
                raise self.commit_error
            return f"fc-{request.run_id}"

        return mod.Ports(
            acquire=self.acquire,
            store=self.store,
            history=lambda ctx: self.history if self.history is None else mod.History(self.history[0], self.history[1], TA, H),
            propose=propose,
            grounded=lambda proposed, ids: self.grounded and set(proposed.cites) <= set(ids),
            stale=lambda ctx: self.stale,
            room=lambda ctx: self.room,
            commit=commit,
            sink=lambda kind, body: self.records.append((kind, body)),
            calibration=self.calibration,
            clock=self.clock,
        )

    def trigger(self, **over: Any) -> Any:
        from hoshitori import desk_plan

        values: dict[str, Any] = {
            "trigger_id": "trg-1",
            "route": "EVENT",
            "symbol": "BTC",
            "direction": "UP",
            "hours": 1,
            "event_time_ms": 1793275000000,
        }
        values.update(over)
        return desk_plan.Trigger(**values)

    def run(self, trigger: Any = None, menu: Any = None, budget: Any = None, binding: str = "live") -> Any:
        mod = self.mod
        budget = budget or mod.Budget({"binance-spot": 4, "kraken": 4, "binance-funding": 4, "ryo": 4}, 600, 20000)
        versions = mod.Versions(H1, H2, H4)
        return mod.run(self.ports(), trigger or self.trigger(), menu or self.menu(), budget, "run-1", mod.Binding(binding), versions)

    def kinds(self) -> list[str]:
        return [k for k, _ in self.records]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [b for k, b in self.records if k == kind]

    def states(self) -> list[tuple[str, str | None]]:
        return [(b["state"], b["reason"]) for b in self.of("desk_run_state")]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "the request carries no ceiling or exposure for a model to see",
        sorted(f for f in mod.ProposeRequest.__dataclass_fields__),
        ["evidence_ids", "hypothesis", "statuses", "tests"],
    )
    c.eq("a proposed answer", sorted(mod.Proposed.__dataclass_fields__), ["cites", "model_outputs_digest", "proposal", "tokens"])
    c.eq("model failures", mod.MODEL_FAILURES, ("MODEL_UNAVAILABLE", "MODEL_OUTPUT_INVALID"))
    c.eq("explanations", mod.EXPLANATION_CLASS, "ANNOUNCEMENT")
    kinds = (
        mod.Budget,
        mod.Binding,
        mod.Versions,
        mod.History,
        mod.Proposed,
        mod.ModelFailure,
        mod.ProposeRequest,
        mod.CommitRequest,
        mod.RunOutcome,
        mod.Ports,
    )
    c.eq("frozen", [k.__dataclass_params__.frozen for k in kinds], [True] * len(kinds))
    return c.bad


def check_happy_path(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    out = w.run()
    c.eq("the run commits", (out.state, out.reason, out.forecast_id), ("COMMITTED", None, "fc-run-1"))
    c.eq(
        "records in the order the steps happen",
        w.kinds(),
        ["desk_run_state", "hypothesis", "desk_check", "desk_check", "falsification", "decision", "desk_run_state"],
    )
    c.eq("run states", w.states(), [("RUNNING", None), ("COMMITTED", None)])
    c.eq("the first price source was asked and then a second origin, once each", w.acquired, [SPOT, KRAKEN])
    check = w.of("desk_check")[0]
    c.eq(
        "the check record",
        (
            check["seq"],
            check["node_id"],
            check["check_id"],
            check["status_before"],
            check["status_after"],
            check["layer"],
            check["outcome"],
        ),
        (0, "obs", SPOT, "UNCHECKED", "SUPPORTED", "DETERMINISTIC", "OK"),
    )
    c.eq(
        "why this check, and what else was possible",
        (check["why"], [a["check_id"] for a in check["alternatives"]]),
        ({"template_id": "FIRST_OPEN_PART", "settled": []}, [KRAKEN, FUNDING, TOKEN]),
    )
    fal = w.of("falsification")[0]
    c.eq(
        "a passed step with the pre-test target as its ceiling",
        (fal["outcome"], fal["completed"], fal["label"], fal["ceiling"], fal["pre_test_target"]),
        ("PASSED", True, None, {"weight": "0.030000", "p_cap": "0.6000"}, {"stance": "LONG", "p": "0.60", "weight": "0.03"}),
    )
    c.eq("the assumption it tried", (fal["target_assumption"], fal["proposed_explanation"]), ("cond", "ANNOUNCEMENT"))
    c.eq(
        "the four tests, the placebo test left out",
        [(t["type"], t["result"]) for t in fal["tests"]][2:],
        [("T_SOURCE", "NOT_TESTABLE"), ("T_PLACEBO_TIME", "NOT_TESTABLE")],
    )
    dec = w.of("decision")[0]
    c.eq("the decision record", (dec["arm_id"], dec["final_action"]["kind"], dec["final_action"]["stance"]), ("DESK", "COMMIT", "LONG"))
    c.eq(
        "it was cut to the pre-test target and recorded",
        (dec["final_action"]["p"], dec["final_action"]["weight"], dec["proposal"]["weight"]),
        ("0.6000", "0.030000", "0.04"),
    )
    c.eq(
        "one commit request, with the falsification record beside the decision",
        (len(w.committed), w.committed[0].falsification["outcome"], w.committed[0].decision["final_action"]["kind"]),
        (1, "PASSED", "COMMIT"),
    )
    c.eq("and the final action", (w.committed[0].final.stance, w.committed[0].final.weight), ("LONG", D("0.030000")))
    # every record is a valid record
    for kind, body in w.records:
        try:
            desk_record.to_record(kind, body, "urn:hoshitori:desk:run-1", 0)
        except desk_record.DeskRecordError as exc:
            c.bad.append(f"a {kind} record is refused: {exc}")
    committed = w.of("desk_run_state")[-1]
    c.eq(
        "the committed state names the forecast, the binding, the hashes and what was spent",
        (
            committed["forecast_id"],
            committed["label"],
            committed["agent_version_hash"],
            committed["code_version"],
            committed["protocol_hash"],
            committed["menu_sha256"] == w.menu().sha256,
            committed["spent"],
        ),
        ("fc-run-1", "live", H1, H2, H4, True, {"calls": {"binance-spot": 2, "kraken": 2}, "seconds": 0, "tokens": 500}),
    )
    c.eq(
        "the first state records the whole budget and nothing spent",
        (w.of("desk_run_state")[0]["budget"], w.of("desk_run_state")[0]["spent"]),
        (
            {"calls": {"binance-spot": 4, "kraken": 4, "binance-funding": 4, "ryo": 4}, "seconds": 600, "tokens": 20000},
            {"calls": {}, "seconds": 0, "tokens": 0},
        ),
    )
    c.eq(
        "the model saw the statuses and no ceiling",
        (w.proposed[0].statuses, "ceiling" in str(w.proposed[0])),
        ({"obs": "SUPPORTED", "cond": "UNCHECKED"}, False),
    )
    again = World(mod)
    again.run()
    c.eq("the same inputs give the same records", again.records, w.records)
    return c.bad


def check_falsification(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod, history=scenario("B", seed_where("B", "FAVOURS_ALTERNATIVE")))
    out = w.run()
    fal = w.of("falsification")[0]
    c.eq(
        "an earlier onset fails the step",
        (fal["outcome"], fal["completed"], fal["ceiling"]),
        ("FAILED", True, {"weight": "0.015000", "p_cap": "0.5500"}),
    )
    dec = w.of("decision")[0]
    c.eq(
        "the commitment is within half the target",
        (out.state, dec["final_action"]["weight"], dec["final_action"]["p"]),
        ("COMMITTED", "0.015000", "0.5500"),
    )
    c.eq("the cut is among the gate events", ("FALSIFICATION", "CUT") in [(e["gate"], e["status"]) for e in dec["gate_events"]], True)
    w = World(mod, history=None)
    out = w.run()
    fal = w.of("falsification")[0]
    c.eq(
        "no history: both time tests cannot be run, the step is not completed",
        (fal["completed"], fal["label"], fal["outcome"]),
        (False, "NOT_FALSIFIED_ATTEMPTED", "FAILED"),
    )
    c.eq(
        "with the reason for each",
        [(t["type"], t["reason"]) for t in fal["tests"][:2]],
        [("T_ONSET", "series_too_short"), ("T_MARKET", "series_too_short")],
    )
    c.eq(
        "and the run still ends within half the target",
        (out.state, w.of("decision")[0]["final_action"]["weight"]),
        ("COMMITTED", "0.015000"),
    )
    w = World(mod)
    w.run(w.trigger(event_time_ms=None, route="RYO_SCAN", trigger_id=None))
    fal = w.of("falsification")[0]
    c.eq(
        "a candidate with no event time: the time tests are untestable for that reason",
        [(t["type"], t["result"], t["reason"]) for t in fal["tests"][:2]],
        [("T_ONSET", "NOT_TESTABLE", "no_event_time"), ("T_MARKET", "NOT_TESTABLE", "no_event_time")],
    )
    c.eq("and the step did not complete", (fal["completed"], fal["outcome"]), (False, "FAILED"))
    c.eq("the route is recorded", w.of("hypothesis")[0]["discovery_route"], "RYO_SCAN")
    w = World(mod, calibration=None)
    w.run()
    fal = w.of("falsification")[0]
    c.eq("with no calibration nothing counts: the step is not completed", (fal["completed"], fal["outcome"]), (False, "FAILED"))
    c.eq("and no test says it was calibrated", [t["calibrated"] for t in fal["tests"]], [False, False, True, False])
    poor = World(
        mod,
        calibration=falsify.Calibration({"T_ONSET": 0.5, "T_MARKET": 0.0}, 600, "b" * 64),
        history=scenario("B", seed_where("B", "FAVOURS_ALTERNATIVE")),
    )
    poor.run()
    t0 = poor.of("falsification")[0]["tests"][0]
    c.eq(
        "a test whose false-alternative rate is too high cannot cut, and says so",
        (t0["result"], t0["reason"], t0["calibrated"], t0["calibration_sha256"]),
        ("INCONCLUSIVE", "uncalibrated", False, None),
    )
    c.eq("the calibrated test names its calibration", w.of("falsification")[0]["tests"][0]["calibration_sha256"], None)
    good = World(mod)
    good.run()
    c.eq(
        "a calibrated one does",
        [(t["calibrated"], t["calibration_sha256"]) for t in good.of("falsification")[0]["tests"][:2]],
        [(True, "a" * 64), (True, "a" * 64)],
    )
    return c.bad


def check_checks(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod, moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1005")})
    out = w.run()
    c.eq(
        "a source that does not show the move gives way to the next, once each, and the one that does is put to a second origin",
        (w.acquired, out.state),
        ([SPOT, KRAKEN, TOKEN], "COMMITTED"),
    )
    c.eq(
        "the check records number the checks and name the statuses",
        [(r["seq"], r["status_before"], r["status_after"]) for r in w.of("desk_check")],
        [(0, "UNCHECKED", "NOT_FOUND_IN_SOURCES_CHECKED"), (1, "NOT_FOUND_IN_SOURCES_CHECKED", "SUPPORTED"), (2, "SUPPORTED", "SUPPORTED")],
    )
    c.eq("the second check says what was settled", w.of("desk_check")[1]["why"]["template_id"], "NEXT_UNTRIED_CHECK")
    flat = {SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1000.5")}
    w = World(mod, moves=flat, ryo_change="0.10")
    out = w.run()
    c.eq(
        "every check on the menu that can resolve the part is tried once and the premise stays unproven",
        (w.acquired, out.state, out.reason),
        ([SPOT, KRAKEN, FUNDING, TOKEN], "ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"),
    )
    c.eq(
        "with a decision record that stopped at the first gate and no model call",
        ([(e["gate"], e["status"]) for e in w.of("decision")[0]["gate_events"]], w.proposed),
        ([("GROUNDING", "STOP")], []),
    )
    c.eq("and no forecast", (w.committed, w.states()[-1]), ([], ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN")))
    w = World(mod, moves={SPOT: ("1000", "995"), KRAKEN: ("1000", "995")})
    out = w.run()
    c.eq(
        "a premise the data contradicts ends it as a conflict, with no model call",
        (out.state, out.reason, w.acquired, w.proposed),
        ("ABSTAINED", "EVIDENCE_CONFLICT_UNRESOLVED", [SPOT], []),
    )
    w = World(mod, moves={SPOT: ("1000", "1005"), KRAKEN: ("1000", "995")})
    out = w.run()
    c.eq(
        "a part one origin supports is put to a second origin, and two that disagree are found out and not averaged away",
        (out.state, out.reason, w.acquired[:2], [r["status_after"] for r in w.of("desk_check")][:2]),
        ("ABSTAINED", "EVIDENCE_CONFLICT_UNRESOLVED", [SPOT, KRAKEN], ["SUPPORTED", "MIXED"]),
    )
    w = World(mod)
    out = w.run()
    c.eq(
        "two origins that agree settle it, and a third is not sought; the funding read cannot corroborate a move and is not asked",
        (out.state, w.acquired, [(r["origin_family"], r["why"]["template_id"]) for r in w.of("desk_check")]),
        (
            "COMMITTED",
            [SPOT, KRAKEN],
            [("EXCHANGE:binance", "FIRST_OPEN_PART"), ("EXCHANGE:kraken", "CORROBORATE")],
        ),
    )
    w = World(mod, failures={KRAKEN: "CALL_FAILED"})
    out = w.run()
    c.eq(
        "a second origin that is down leaves the next origin to corroborate: RYO counts as one, whatever tool it is read through",
        (out.state, w.acquired, [r["origin_family"] for r in w.of("desk_check")]),
        ("COMMITTED", [SPOT, KRAKEN, TOKEN], ["EXCHANGE:binance", "EXCHANGE:kraken", "RYO"]),
    )
    w = World(mod, failures={KRAKEN: "CALL_FAILED", TOKEN: "CALL_FAILED"})
    out = w.run()
    c.eq(
        "corroboration is asked for and not required: a part one origin supports still stands when no other can be read",
        (out.state, w.acquired, [r["status_after"] for r in w.of("desk_check")]),
        ("COMMITTED", [SPOT, KRAKEN, TOKEN], ["SUPPORTED", "SUPPORTED", "SUPPORTED"]),
    )
    w = World(mod)
    out = w.run(budget=mod.Budget({"binance-spot": 4}, 600, 20000))
    c.eq("a budget that pays for no second origin does not stop the run", (out.state, w.acquired), ("COMMITTED", [SPOT]))
    providers = {"binance-spot": frozenset({"candles"}), "binance-usdm": frozenset({"candles"}), "kraken": frozenset({"candles"})}
    two_products = desk_menu.build_menu("asset:btc", "BTC", providers, frozenset(), desk_menu.CostTable.from_latencies({}))
    w = World(
        mod, moves={SPOT: ("1000", "1005"), "INDEPENDENT_PRICE_CHECK:binance-usdm:candles": ("1000", "1005"), KRAKEN: ("1000", "1005")}
    )
    out = w.run(menu=two_products)
    c.eq(
        "two products of one exchange are not two origins: the second product is never asked, the other exchange is",
        (out.state, w.acquired, [r["origin_family"] for r in w.of("desk_check")]),
        ("COMMITTED", [SPOT, KRAKEN], ["EXCHANGE:binance", "EXCHANGE:kraken"]),
    )
    w = World(mod, failures={SPOT: "CALL_FAILED", KRAKEN: "TIMED_OUT", TOKEN: "CALL_FAILED", FUNDING: "CALL_FAILED"})
    out = w.run()
    c.eq(
        "a required source down everywhere is a failure of the evidence, not an abstention",
        (out.state, out.reason, w.proposed, w.committed),
        ("FAILED", "FAILED_EVIDENCE", [], []),
    )
    dec = w.of("decision")[0]
    c.eq(
        "recorded as a decision that stopped at the required evidence",
        ([(e["gate"], e["status"]) for e in dec["gate_events"]], dec["final_action"]["kind"], dec["final_action"]["reason"]),
        ([("GROUNDING", "PASS"), ("REQUIRED_EVIDENCE", "STOP")], "FAILED", "FAILED_EVIDENCE"),
    )
    w = World(mod, failures={SPOT: "CALL_FAILED"})
    out = w.run()
    c.eq("one source down and the next up: the run goes on", (w.acquired, out.state), ([SPOT, KRAKEN, TOKEN], "COMMITTED"))
    w = World(mod, failures={SPOT: "NO_INSTRUMENT", KRAKEN: "NO_INSTRUMENT", FUNDING: "NO_INSTRUMENT", TOKEN: "NO_INSTRUMENT"})
    out = w.run()
    c.eq(
        "sources that cannot serve the asset leave the premise unproven, not a failed source",
        (out.state, out.reason),
        ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"),
    )
    w = World(mod)
    out = w.run(budget=mod.Budget({"binance-spot": 0, "kraken": 4}, 600, 20000))
    c.eq("a provider with no calls left is not asked; the next is", (w.acquired, out.state), ([KRAKEN], "COMMITTED"))
    w = World(mod)
    out = w.run(budget=mod.Budget({}, 600, 20000))
    c.eq(
        "a budget of no calls stops the run before any check",
        (w.acquired, w.states()),
        ([], [("RUNNING", None), ("STOPPED", "BUDGET_EXHAUSTED"), ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN")]),
    )
    c.eq("and then it abstains: nothing is proven", (out.state, out.reason), ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"))
    w = World(mod)
    out = w.run(budget=mod.Budget({"binance-spot": 4}, 0, 20000))
    c.eq("no seconds left is the same", (w.acquired, out.reason), ([], "ESSENTIAL_PREMISE_UNPROVEN"))
    w = World(mod)
    w.run()
    c.eq("a run that finished its checks writes no stop", [s for s in w.states() if s[0] == "STOPPED"], [])
    return c.bad


def check_screen_step(mod: types.ModuleType) -> list[str]:
    """A token whose contract is known is screened before anything about its price is asked: a listing ends the run, anything else changes
    nothing about the call, and the screen never counts as an origin that read the claim."""
    c = Case(mod)
    budget = mod.Budget({"binance-spot": 4, "kraken": 4, "binance-funding": 4, "ryo": 4, "address-flag": 2}, 600, 20000)
    w = World(mod, contract=CONTRACT)
    out = w.run(budget=budget)
    c.eq(
        "a clean screen is asked first, and the run goes on as it would have",
        (out.state, w.acquired),
        ("COMMITTED", [SCREEN, SPOT, KRAKEN]),
    )
    first = w.of("desk_check")[0]
    c.eq(
        "its record: the sanctions list's family, what it found, no claim part",
        (first["seq"], first["node_id"], first["node_type"], first["check_id"], first["origin_family"], first["chosen_by"], first["why"]),
        (0, "screen", "OBSERVATION", SCREEN, "PRIMARY_DOC:ofac", "ADDRESS_SCREEN", {"template_id": "ADDRESS_SCREEN", "settled": []}),
    )
    c.eq(
        "what it found, by itself and as it stands, and what it cost",
        (
            first["status_before"],
            first["own_status"],
            first["status_after"],
            first["layer"],
            first["outcome"],
            first["cost_spent"]["calls"],
        ),
        ("UNCHECKED", "NO_FLAG_FOUND", "NO_FLAG_FOUND", "DETERMINISTIC", "OK", 1),
    )
    c.eq("the checks of the claim are numbered after it", [r["seq"] for r in w.of("desk_check")], [0, 1, 2])
    dec = w.of("decision")[0]
    c.eq(
        "the decision begins with the screen's gate, which passed and says it is not a rating",
        dec["gate_events"][0],
        {"gate": "HAZARD_SCREEN", "status": "PASS", "detail": "no source lists the address; this is not a safety rating"},
    )
    c.eq(
        "and lists the readings it brought among its evidence, sorted and once each", dec["evidence_ids"], sorted(set(dec["evidence_ids"]))
    )
    c.eq("six readings of the screen and the checks of the claim", len(dec["evidence_ids"]) >= 6 + 2, True)
    c.eq("its call is counted in what the run spent", w.of("desk_run_state")[-1]["spent"]["calls"]["address-flag"], 1)
    # a flag stops the run, before the claim is looked at
    w = World(mod, contract=CONTRACT, screen_reading="FLAGGED")
    out = w.run(budget=budget)
    c.eq(
        "a listing ends the run as out of scope",
        (out.state, out.reason, w.acquired, w.proposed, w.committed),
        ("ABSTAINED", "OUT_OF_SCOPE", [SCREEN], [], []),
    )
    dec = w.of("decision")[0]
    c.eq(
        "with a decision that stopped at the screen and cites what it read",
        (
            [(e["gate"], e["status"]) for e in dec["gate_events"]],
            dec["final_action"]["kind"],
            dec["final_action"]["reason"],
            len(dec["evidence_ids"]),
        ),
        ([("HAZARD_SCREEN", "STOP")], "ABSTAIN", "OUT_OF_SCOPE", 6),
    )
    c.eq("and no falsification was run for a call that was not made", w.of("falsification"), [])
    c.eq("the run states say how it ended", w.states()[-1], ("ABSTAINED", "OUT_OF_SCOPE"))
    # some sources did not answer
    w = World(mod, contract=CONTRACT, screen_reading="INCOMPLETE")
    out = w.run(budget=budget)
    c.eq("an incomplete screen goes on, and says it was not clean", (out.state, w.acquired[0]), ("COMMITTED", SCREEN))
    c.eq(
        "its gate is not a clean pass",
        w.of("decision")[0]["gate_events"][0],
        {"gate": "HAZARD_SCREEN", "status": "PASS", "detail": "some sources did not answer: not a clean screen"},
    )
    c.eq("and the record says incomplete", w.of("desk_check")[0]["own_status"], "INCOMPLETE")
    # the screen cannot be made
    w = World(mod, contract=CONTRACT, screen_reading=None)
    out = w.run(budget=budget)
    c.eq("a screen that could not be made leaves the run as it was", (out.state, w.acquired), ("COMMITTED", [SCREEN, SPOT, KRAKEN]))
    first = w.of("desk_check")[0]
    c.eq(
        "the record says it failed and found nothing",
        (first["outcome"], first["error_class"], first["own_status"], first["layer"], first["evidence_ids"]),
        ("FAILED", "CALL_FAILED", "NOT_SCREENED", "NONE", []),
    )
    c.eq(
        "and the decision has no screen gate, which would claim a pass",
        [e["gate"] for e in w.of("decision")[0]["gate_events"]][:1],
        ["GROUNDING"],
    )
    # no calls left for the screen
    w = World(mod, contract=CONTRACT)
    out = w.run(budget=mod.Budget({"binance-spot": 4, "kraken": 4, "binance-funding": 4, "ryo": 4}, 600, 20000))
    c.eq(
        "a budget with no calls for the screen skips it without a trace",
        (out.state, w.acquired, [r["check_id"] for r in w.of("desk_check")]),
        ("COMMITTED", [SPOT, KRAKEN], [SPOT, KRAKEN]),
    )
    # no contract, no screen
    w = World(mod)
    w.run(budget=budget)
    c.eq("a menu without a contract has no screen: the run is the run it was", w.acquired, [SPOT, KRAKEN])
    c.eq("and no screen gate", w.of("decision")[0]["gate_events"][0]["gate"], "GROUNDING")
    return c.bad


def check_decision(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for failure in ("MODEL_UNAVAILABLE", "MODEL_OUTPUT_INVALID"):
        w = World(mod, model_failure=failure)
        out = w.run()
        c.eq(
            f"{failure}: the run fails with that word and commits nothing",
            (out.state, out.reason, w.committed, w.states()[-1]),
            ("FAILED", failure, [], ("FAILED", failure)),
        )
    w = World(mod, proposal=desk_gates.Proposal("LONG", D("0.51"), D("0.04")))
    out = w.run()
    c.eq("a thin edge is an abstention", (out.state, out.reason, w.committed), ("ABSTAINED", "BELOW_MINIMUM_EDGE", []))
    c.eq(
        "with the gate that stopped it",
        [(e["gate"], e["status"]) for e in w.of("decision")[0]["gate_events"]][-1],
        ("MINIMUM_EDGE", "STOP"),
    )
    w = World(mod, grounded=False)
    out = w.run()
    c.eq(
        "an ungrounded proposal is an abstention on an unproven premise",
        (out.state, out.reason),
        ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"),
    )
    w = World(mod, stale=True)
    out = w.run()
    c.eq("stale evidence at the decision is a failure", (out.state, out.reason), ("FAILED", "FAILED_STALE"))
    w = World(mod, room=D("0.01"))
    out = w.run()
    c.eq("the kernel's room cuts the weight", (out.state, w.of("decision")[0]["final_action"]["weight"]), ("COMMITTED", "0.010000"))
    w = World(mod, room=D("0"))
    out = w.run()
    c.eq(
        "no room is a flat commitment",
        (out.state, w.of("decision")[0]["final_action"]["stance"], w.of("decision")[0]["final_action"]["weight"]),
        ("COMMITTED", "FLAT", "0.000000"),
    )
    w = World(mod, proposal=desk_gates.Proposal("SHORT", D("0.30"), D("0.04")))
    out = w.run()
    c.eq("a short is out of scope", (out.state, out.reason), ("ABSTAINED", "OUT_OF_SCOPE"))
    w = World(mod, proposal=desk_gates.Proposal("LONG", D("0.9"), D("0.9")))
    out = w.run()
    c.eq(
        "a proposal above everything is cut to the target",
        (w.of("decision")[0]["final_action"]["weight"], w.of("decision")[0]["final_action"]["p"]),
        ("0.030000", "0.6000"),
    )
    w = World(mod)
    w.moves = {SPOT: ("1000", "995"), KRAKEN: ("1000", "995")}
    out = w.run(w.trigger(direction="DOWN"))
    c.eq(
        "a fall is a flat hypothesis: whatever the model says, the commitment is flat",
        (
            out.state,
            w.of("hypothesis")[0]["stance"],
            w.of("decision")[0]["final_action"]["stance"],
            w.of("decision")[0]["final_action"]["weight"],
        ),
        ("COMMITTED", "FLAT", "FLAT", "0.000000"),
    )
    c.eq(
        "the fall is read as a fall",
        w.of("desk_check")[0]["status_after"],
        "SUPPORTED",
    )
    return c.bad


def check_budget_and_edges(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # the arguments a port is given
    w = World(mod)
    w.run()
    ctx = w.ctxs[0]
    c.eq(
        "the check is run for this run, this asset and this symbol, with the deadline the budget gives",
        (ctx.run_id, ctx.asset_id, ctx.symbol, ctx.deadline),
        ("run-1", "asset:btc", "BTC", NOW + timedelta(seconds=600)),
    )

    # can a check be afforded
    def affordable(w: World, budget: Any, check_id: str) -> bool:
        run = mod._Run(w.ports(), w.trigger(), w.menu(), budget, "run-1", mod.Binding("live"), mod.Versions(H1, H2, H4))
        run.calls = dict(getattr(w, "spent_calls", {}))
        by_id = {x.check_id: x for x in w.menu().checks}
        return bool(run.affordable(by_id[check_id]))

    w = World(mod)
    c.eq("a price check costs two calls: two are enough", affordable(w, mod.Budget({"binance-spot": 2}, 600, 1), SPOT), True)
    c.eq("one is not", affordable(w, mod.Budget({"binance-spot": 1}, 600, 1), SPOT), False)
    c.eq("a provider that has no calls in the budget has none", affordable(w, mod.Budget({}, 600, 1), FUNDING), False)
    c.eq("one call is enough for a funding read", affordable(w, mod.Budget({"binance-funding": 1}, 600, 1), FUNDING), True)
    w.spent_calls = {"binance-funding": 1}
    c.eq("calls already made are taken off", affordable(w, mod.Budget({"binance-funding": 1}, 600, 1), FUNDING), False)
    c.eq("and what is left is enough", affordable(w, mod.Budget({"binance-funding": 2}, 600, 1), FUNDING), True)
    w.spent_calls = {}
    c.eq(
        "one second left is enough for a check whose time is not known",
        affordable(w, mod.Budget({"binance-funding": 1}, 1, 1), FUNDING),
        True,
    )
    c.eq("none is not", affordable(w, mod.Budget({"binance-funding": 1}, 0, 1), FUNDING), False)
    slow = World(mod)
    slow_menu = desk_menu.build_menu(
        "asset:btc",
        "BTC",
        {"binance-spot": frozenset({"candles"})},
        frozenset(),
        desk_menu.CostTable.from_latencies({("binance-spot", "candles"): [3.0]}),
    )
    slow.menu = lambda symbol="BTC": slow_menu  # type: ignore[method-assign]
    c.eq(
        "a check whose worst time fits the seconds left is affordable", affordable(slow, mod.Budget({"binance-spot": 2}, 3, 1), SPOT), True
    )
    c.eq("one that does not fit is not", affordable(slow, mod.Budget({"binance-spot": 2}, 2, 1), SPOT), False)
    run = mod._Run(
        slow.ports(),
        slow.trigger(),
        slow_menu,
        mod.Budget({"binance-spot": 2}, 4, 1),
        "run-1",
        mod.Binding("live"),
        mod.Versions(H1, H2, H4),
    )
    slow.clock.advance(timedelta(seconds=2))
    c.eq("time already spent is taken off the seconds", run.affordable(slow_menu.checks[0]), False)
    # the model's budget
    w = World(mod)
    out = w.run(budget=mod.Budget({"binance-spot": 4}, 600, 0))
    c.eq("no tokens left: the run fails before it asks a model", (out.state, out.reason, w.proposed), ("FAILED", "MODEL_UNAVAILABLE", []))
    w = World(mod)
    out = w.run(budget=mod.Budget({"binance-spot": 4}, 600, 1))
    c.eq("one token is enough to ask", (out.state, len(w.proposed)), ("COMMITTED", 1))
    # evidence and ids
    w = World(mod, moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1005")}, items=8)
    out = w.run()
    dec = w.of("decision")[0]
    c.eq("the decision lists the evidence of every check, sorted and once each", dec["evidence_ids"], sorted(set(dec["evidence_ids"])))
    c.eq("twenty-four items from three checks", len(dec["evidence_ids"]), 24)
    c.eq(
        "a check record lists its own, sorted",
        [r["evidence_ids"] == sorted(r["evidence_ids"]) for r in w.of("desk_check")],
        [True, True, True],
    )
    w2 = World(mod, moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1005")}, items=8, cites_reversed=True, cites_count=8)
    w2.run()
    cites = w2.of("decision")[0]["proposal"]["cites"]
    c.eq("a model that cites out of order, four times over: the record sorts them", (len(cites), cites == sorted(cites)), (8, True))
    c.eq("and what the model was shown is sorted", w2.proposed[0].evidence_ids, tuple(sorted(w2.proposed[0].evidence_ids)))
    c.eq(
        "the cites are sorted and among the evidence",
        (dec["proposal"]["cites"] == sorted(dec["proposal"]["cites"]), set(dec["proposal"]["cites"]) <= set(dec["evidence_ids"])),
        (True, True),
    )
    c.eq("the decision's id is natural and pinned", dec["decision_id"], PINNED_DECISION_ID)
    c.eq("the candidate is the trigger", dec["candidate_id"], "trg-1")
    w = World(mod)
    w.run(w.trigger(trigger_id=None))
    c.eq("with no trigger id it is the asset", w.of("decision")[0]["candidate_id"], "asset:btc")
    # why a run stopped early is written into its decision
    w = World(mod, moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1000.5")}, ryo_change="0.10")
    w.run()
    dec = w.of("decision")[0]
    c.eq(
        "an unproven premise, in words",
        (dec["gate_events"][0]["detail"], dec["proposal"]["no_call_reason"], dec["proposal"]["weight"]),
        ("a required part is unproven", "ESSENTIAL_PREMISE_UNPROVEN", "0"),
    )
    w = World(mod, moves={SPOT: ("1000", "995"), KRAKEN: ("1000", "995")})
    w.run()
    dec = w.of("decision")[0]
    c.eq(
        "a contradicted premise, in words",
        (dec["gate_events"][0]["detail"], dec["proposal"]["no_call_reason"]),
        ("a required part is contradicted or disputed", "EVIDENCE_CONFLICT_UNRESOLVED"),
    )
    w = World(mod, failures={SPOT: "CALL_FAILED", KRAKEN: "CALL_FAILED", TOKEN: "CALL_FAILED", FUNDING: "CALL_FAILED"})
    w.run()
    dec = w.of("decision")[0]
    c.eq(
        "a source that failed: no reason of abstention, and what is missing",
        (dec["proposal"]["no_call_reason"], dec["gate_events"][1]["detail"]),
        (None, "missing: obs"),
    )
    # the history of the falsification when there is none
    w = World(mod, history=None)
    w.run()
    params = w.of("falsification")[0]["tests"][0]["parameters"]
    c.eq("no history: the test asked about no time and one hour", (params["ta"], params["h"]), ("0", "60"))
    w = World(mod, history=None)
    w.run(w.trigger(hours=1))
    good = World(mod)
    good.run()
    c.eq(
        "a source-label test names no calibration though it is calibrated by nature",
        [t["calibration_sha256"] for t in good.of("falsification")[0]["tests"]],
        ["a" * 64, "a" * 64, None, None],
    )
    # a record the schema refuses is never sunk
    w = World(mod)
    try:
        w.run(budget=mod.Budget({"bad name!": 1}, 600, 20000))
    except desk_record.DeskRecordError:
        pass
    else:
        c.bad.append("a run with a budget a record cannot hold was accepted")
    c.eq("nothing was written", w.records, [])
    # the guard on the commitment
    w = World(mod)
    real = mod.desk_gates.gate

    def too_much(*args: Any) -> Any:
        result = real(*args)
        return mod.desk_gates.GateResult(
            "COMMIT", None, mod.desk_gates.FinalAction("LONG", D("0.6"), D("0.9")), result.events, result.ceiling, result.p_cap, result.step
        )

    mod.desk_gates.gate = too_much
    try:
        try:
            w.run()
        except mod.DeskError:
            pass
        else:
            c.bad.append("a weight above the ceiling was committed")
    finally:
        mod.desk_gates.gate = real
    c.eq("and nothing was committed", w.committed, [])
    # ports that raise
    w = World(mod, commit_error=mod.DeskError("refused by the ledger"))
    try:
        w.run()
    except mod.DeskError:
        pass
    else:
        c.bad.append("a refusal of the commitment was swallowed")
    w = World(mod, commit_error=RuntimeError("the ledger is down"))
    out = w.run()
    c.eq("a commit port that breaks ends the run as interrupted", (out.state, out.reason), ("FAILED_FINAL", "RUN_INTERRUPTED"))
    try:
        mod.ModelFailure("WHATEVER")
    except mod.DeskError:
        pass
    else:
        c.bad.append("a model failure that is not one of the closed words was accepted")
    for label, call in (
        ("negative tokens", lambda: mod.Budget({}, 0, -1)),
        ("a digest that is not hexadecimal", lambda: mod.Versions("g" * 64, H2, H4)),
        ("a code version of the wrong length", lambda: mod.Versions(H1, "a", H4)),
    ):
        try:
            call()
        except mod.DeskError:
            continue
        c.bad.append(f"{label}: was accepted")
    return c.bad


def check_binding(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    out = w.run(binding="recorded")
    c.eq(
        "a recorded run says so on every state and is not committed as a forecast",
        (out.state, [b["label"] for b in w.of("desk_run_state")]),
        ("COMMITTED", ["recorded", "recorded"]),
    )
    c.eq("and the commit request carries the label for the port to refuse to attest it", w.committed[0].label, "recorded")
    live = World(mod)
    live.run()
    c.eq("a live one says live", live.committed[0].label, "live")
    try:
        mod.Binding("simulated")
    except mod.DeskError:
        pass
    else:
        c.bad.append("a binding that is neither live nor recorded was accepted")
    return c.bad


def check_guards(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    for label, call in (
        ("a menu for another symbol", lambda: w.run(menu=w.menu("ETH"))),
        (
            "a run id that is not an id",
            lambda: mod.run(
                w.ports(), w.trigger(), w.menu(), mod.Budget({}, 1, 1), "bad id!", mod.Binding("live"), mod.Versions(H1, H2, H4)
            ),
        ),
        ("a budget of negative seconds", lambda: mod.Budget({}, -1, 0)),
        ("a budget with a negative call count", lambda: mod.Budget({"ryo": -1}, 0, 0)),
        ("a budget with a bool", lambda: mod.Budget({}, True, 0)),
        ("a version that is not a digest", lambda: mod.Versions("x", H2, H4)),
    ):
        try:
            call()
        except mod.DeskError:
            continue
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            continue
        c.bad.append(f"{label}: was accepted")
    c.eq("a refused run leaves nothing behind", w.records, [])
    # the commit guard
    request_ok = mod.guard_commit(D("0.03"), D("0.03"))
    c.eq("a weight at the ceiling passes the guard", request_ok, None)
    try:
        mod.guard_commit(D("0.031"), D("0.03"))
    except mod.DeskError:
        pass
    else:
        c.bad.append("a weight above the falsification ceiling was let through")
    return c.bad


def check_ports_misbehaving(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = World(mod)
    ports = w.ports()

    def bad_acquire(check: Any, ctx: Any) -> Any:
        raise RuntimeError("the port broke")

    broken = mod.Ports(**{**ports.__dict__, "acquire": bad_acquire}) if hasattr(ports, "__dict__") else ports
    out = mod.run(
        broken,
        w.trigger(),
        w.menu(),
        mod.Budget({"binance-spot": 4, "kraken": 4}, 600, 20000),
        "run-1",
        mod.Binding("live"),
        mod.Versions(H1, H2, H4),
    )
    c.eq(
        "a port that raises ends the run as interrupted, with the reason recorded",
        (out.state, out.reason, w.states()[-1]),
        ("FAILED_FINAL", "RUN_INTERRUPTED", ("FAILED_FINAL", "RUN_INTERRUPTED")),
    )
    c.eq("nothing is committed", w.committed, [])
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_happy_path,
        check_falsification,
        check_checks,
        check_screen_step,
        check_decision,
        check_budget_and_edges,
        check_binding,
        check_guards,
        check_ports_misbehaving,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
