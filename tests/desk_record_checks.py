"""Checks of the Desk's decision-log records (``hoshitori.desk_record``), runnable against a mutant (contract section 57).

Every record kind has a valid body below. The checks: each round-trips byte for byte; each member is refused when it is missing, extra or of
a form it never takes (a generic poison loop over every member); the rules that tie members together (a reason exactly when the state needs
one, a forecast exactly when committed, a ceiling never above the target, a last gate event that stops exactly when nothing is committed) are
each broken once; and the ids are those of what the record says.
"""

from __future__ import annotations

import copy
import types
from typing import Any

from hoshitori import canonical

H1, H2, H3, H4 = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
EV1, EV2 = "ev" + "a" * 26, "ev" + "b" * 26
SRC = "urn:hoshitori:desk:run-1"
AT = 1793275510000


def run_state(state: str = "RUNNING", reason: str | None = None, forecast_id: str | None = None) -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "state": state,
        "reason": reason,
        "at_ms": AT,
        "regime": "OPERATIONAL",
        "label": "live",
        "asset_id": "asset:btc",
        "symbol": "BTC",
        "discovery_route": "EVENT",
        "trigger_id": "trg-1",
        "policy": "P-DEPENDENCY",
        "calibration": "UNCALIBRATED",
        "menu_sha256": H1,
        "budget": {"calls": {"binance-spot": 6, "ryo": 4}, "seconds": 600, "tokens": 20000},
        "spent": {"calls": {"ryo": 1}, "seconds": 12, "tokens": 900},
        "agent_version_hash": H2,
        "code_version": H3,
        "protocol_hash": H4,
        "forecast_id": forecast_id,
    }


def hypothesis() -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "hypothesis_id": "hyp-1",
        "template_id": "ANNOUNCEMENT_MOVE",
        "statement": "BTC rose after the announcement and the move began after it.",
        "invalidation": "The move began before the announcement.",
        "graph": {
            "nodes": [
                {"node_id": "obs", "type": "OBSERVATION", "span": [0, 14]},
                {"node_id": "cond", "type": "CONDITION", "span": [15, 40]},
            ],
            "edges": [{"from": "cond", "to": "obs", "kind": "DEPENDS_ON"}],
        },
        "stance": "LONG",
        "discovery_route": "EVENT",
        "triage_rule_version": 1,
    }


def desk_check() -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "seq": 0,
        "node_id": "obs",
        "node_type": "OBSERVATION",
        "check_id": "INDEPENDENT_PRICE_CHECK:binance-spot:candles",
        "check_type": "INDEPENDENT_PRICE_CHECK",
        "provider": "binance-spot",
        "operation": "candles",
        "origin_family": "EXCHANGE:binance",
        "args": [["symbol", "BTC"]],
        "chosen_by": "DEPENDENCY_ORDER",
        "why": {"template_id": "FIRST_OPEN_PART", "settled": []},
        "alternatives": [{"check_id": "INDEPENDENT_PRICE_CHECK:kraken:candles", "rule": "COSTS_MORE"}],
        "cost_expected": {"calls": 2, "seconds": "0.600"},
        "cost_spent": {"calls": 2, "attempts": 2, "seconds": "0.512"},
        "outcome": "OK",
        "error_class": None,
        "capture_ids": ["cap-1", "cap-2"],
        "evidence_ids": [EV1],
        "status_before": "UNCHECKED",
        "own_status": "SUPPORTED",
        "status_after": "SUPPORTED",
        "layer": "MOVE_CHECK",
    }


def falsification() -> dict[str, Any]:
    return {
        "run_id": "run-1",
        "hypothesis_id": "hyp-1",
        "target_assumption": "cond",
        "proposed_explanation": "ANNOUNCEMENT",
        "competing_explanations": [{"klass": "MARKET_WIDE_MOVE", "predicts": "The token moves with the market."}],
        "tests": [
            {
                "type": "T_ONSET",
                "method": "one-look",
                "version": "falsify/1",
                "parameters": {"h": "60", "ta": "700"},
                "statistics": {"z_post": "3.100000"},
                "result": "FAVOURS_ORIGINAL",
                "reason": None,
                "calibrated": True,
                "calibration_sha256": H1,
            },
            {
                "type": "T_PLACEBO_TIME",
                "method": "placebo-time",
                "version": "falsify/1",
                "parameters": {},
                "statistics": {},
                "result": "NOT_TESTABLE",
                "reason": "uncalibrated",
                "calibrated": False,
                "calibration_sha256": None,
            },
        ],
        "unresolved": ["T_PLACEBO_TIME:uncalibrated"],
        "completed": True,
        "label": None,
        "pre_test_target": {"stance": "LONG", "p": "0.65", "weight": "0.05"},
        "outcome": "PASSED",
        "ceiling": {"weight": "0.050000", "p_cap": "0.6500"},
    }


def decision() -> dict[str, Any]:
    return {
        "decision_id": "dec-1",
        "run_id": "run-1",
        "arm_id": "DESK",
        "candidate_id": "cd-btc",
        "proposal": {"stance": "LONG", "p": "0.62", "weight": "0.04", "cites": [EV1], "no_call_reason": None},
        "gate_events": [
            {"gate": "GROUNDING", "status": "PASS", "detail": ""},
            {"gate": "REQUIRED_EVIDENCE", "status": "PASS", "detail": ""},
            {"gate": "STALENESS", "status": "PASS", "detail": ""},
            {"gate": "MINIMUM_EDGE", "status": "PASS", "detail": ""},
            {"gate": "FALSIFICATION", "status": "PASS", "detail": "ceiling 0.050000, probability cap 0.6500"},
            {"gate": "RISK_KERNEL", "status": "PASS", "detail": "room 0.05"},
        ],
        "final_action": {"kind": "COMMIT", "reason": None, "stance": "LONG", "p": "0.62", "weight": "0.04"},
        "evidence_ids": [EV1, EV2],
        "observation_set_hash": H2,
        "model_outputs_digest": H3,
    }


def abstained() -> dict[str, Any]:
    body = decision()
    body["proposal"] = {"stance": "LONG", "p": "0.51", "weight": "0.04", "cites": [], "no_call_reason": None}
    body["gate_events"] = [
        {"gate": "GROUNDING", "status": "PASS", "detail": ""},
        {"gate": "REQUIRED_EVIDENCE", "status": "PASS", "detail": ""},
        {"gate": "STALENESS", "status": "PASS", "detail": ""},
        {"gate": "MINIMUM_EDGE", "status": "STOP", "detail": "p 0.51 is under 0.52"},
    ]
    body["final_action"] = {"kind": "ABSTAIN", "reason": "BELOW_MINIMUM_EDGE", "stance": None, "p": None, "weight": None}
    return body


FIXTURES = {
    "desk_run_state": run_state,
    "hypothesis": hypothesis,
    "desk_check": desk_check,
    "falsification": falsification,
    "decision": decision,
}


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, kind: str, body: Any, says: str | None = None) -> None:
        try:
            self.mod.check_body(kind, body)
        except self.mod.DeskRecordError as exc:
            if says is not None and says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")

    def accepts(self, label: str, kind: str, body: Any) -> None:
        try:
            self.mod.check_body(kind, body)
        except Exception as exc:
            self.bad.append(f"{label}: was refused: {exc}")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("types", mod.TYPES, ("desk_run_state", "hypothesis", "desk_check", "falsification", "decision"))
    c.eq("run states", mod.RUN_STATES, ("RUNNING", "STOPPED", "COMMITTED", "ABSTAINED", "FAILED", "FAILED_FINAL"))
    c.eq("stop reasons", mod.STOP_REASONS, ("UTILITY_BELOW_THRESHOLD", "CANNOT_FLIP_DECISION", "BUDGET_EXHAUSTED", "ALL_MENU_OPENED"))
    c.eq(
        "abstention reasons",
        mod.ABSTAIN_REASONS,
        ("BELOW_MINIMUM_EDGE", "ESSENTIAL_PREMISE_UNPROVEN", "EVIDENCE_CONFLICT_UNRESOLVED", "OUT_OF_SCOPE"),
    )
    c.eq(
        "failure reasons",
        mod.FAIL_REASONS,
        ("FAILED", "FAILED_EVIDENCE", "FAILED_STALE", "MODEL_OUTPUT_INVALID", "MODEL_UNAVAILABLE", "RUN_INTERRUPTED"),
    )
    c.eq("routes and labels", (mod.ROUTES, mod.LABELS), (("RYO_SCAN", "EVENT", "WATCHLIST"), ("live", "recorded", "synthetic")))
    c.eq(
        "id prefixes",
        mod.PREFIXES,
        {"desk_run_state": "dr", "hypothesis": "dh", "desk_check": "dc", "falsification": "df", "decision": "dd"},
    )
    c.eq("type version", mod.TYPE_VERSION, 1)
    c.eq("layers", mod.LAYERS, ("NONE", "DETERMINISTIC", "MOVE_CHECK", "PERSON"))
    c.eq(
        "limits",
        (mod.MAX_COUNT, mod.MAX_SEQ, mod.MAX_SPAN, mod.MAX_NODES, mod.MAX_INSTANT_MS),
        (10**12, 10**6, 300, 8, 253_402_300_799_999),
    )
    c.eq("reasons of a failed gate", mod.FAILED_GATE_REASONS, ("FAILED_EVIDENCE", "FAILED_STALE"))
    return c.bad


def check_round_trip(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    fixtures = {**{k: f() for k, f in FIXTURES.items()}, "abstained": abstained()}
    for name, body in fixtures.items():
        kind = "decision" if name == "abstained" else name
        record = mod.to_record(kind, body, SRC, AT)
        raw = canonical.dumps(record)
        again = canonical.parse_record(raw)
        c.eq(f"{name}: the record reads back to its body", mod.from_record(again), (kind, body))
        c.eq(f"{name}: and writes the same bytes", canonical.dumps(mod.to_record(kind, copy.deepcopy(body), SRC, AT)), raw)
        c.eq(
            f"{name}: public, version 1, the type's own name",
            (record["privacy"], record["type_version"], record["type"]),
            ("public", 1, kind),
        )
        c.eq(f"{name}: the id is natural and carries the type's prefix", str(record["id"]).startswith(mod.PREFIXES[kind]), True)
        c.eq(f"{name}: created at the instant given", record["created_at"], "2026-10-29T12:05:10.000Z")
    # ids
    ids = {
        mod.record_id("desk_run_state", run_state(s, r, f))
        for s, r, f in (("RUNNING", None, None), ("STOPPED", "ALL_MENU_OPENED", None), ("ABSTAINED", "OUT_OF_SCOPE", None))
    }
    c.eq("one id for each state of a run", len(ids), 3)
    other = run_state()
    other["at_ms"] = AT + 5
    c.eq(
        "a state is one record whenever it is written", mod.record_id("desk_run_state", other), mod.record_id("desk_run_state", run_state())
    )
    h2 = hypothesis()
    h2["hypothesis_id"] = "hyp-2"
    c.eq("another hypothesis another id", mod.record_id("hypothesis", h2) != mod.record_id("hypothesis", hypothesis()), True)
    k2 = desk_check()
    k2["seq"] = 1
    c.eq("another check number another id", mod.record_id("desk_check", k2) != mod.record_id("desk_check", desk_check()), True)
    d2 = decision()
    d2["decision_id"] = "dec-2"
    c.eq("another decision another id", mod.record_id("decision", d2) != mod.record_id("decision", decision()), True)
    f2 = falsification()
    f2["hypothesis_id"] = "hyp-2"
    c.eq(
        "another falsified hypothesis another id",
        mod.record_id("falsification", f2) != mod.record_id("falsification", falsification()),
        True,
    )
    c.eq(
        "two kinds with the same key do not collide",
        mod.record_id("hypothesis", hypothesis()) != mod.record_id("falsification", falsification()),
        True,
    )
    # the reader holds to the id and the envelope
    record = mod.to_record("decision", decision(), SRC, AT)
    for label, change in (
        ("a changed id", lambda r: r.__setitem__("id", mod.record_id("decision", {**decision(), "decision_id": "dec-9"}))),
        ("an unknown type", lambda r: r.__setitem__("type", "arm_run")),
        ("another type version", lambda r: r.__setitem__("type_version", 2)),
        ("an extra envelope member", lambda r: r.__setitem__("extra", 1)),
        ("a body changed after the id was made", lambda r: r["body"].__setitem__("decision_id", "dec-9")),
        ("a private record", lambda r: r.__setitem__("privacy", "private_digest_only")),
    ):
        broken = copy.deepcopy(record)
        change(broken)
        try:
            mod.from_record(broken)
        except mod.DeskRecordError:
            continue
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}")
            continue
        c.bad.append(f"{label}: was accepted")
    committed = copy.deepcopy(record)
    committed["committed_at"] = "2026-10-29T12:05:11.000Z"
    c.eq("a committed record is read as the producer wrote it", mod.from_record(committed), ("decision", decision()))
    for label, kwargs in (("a time before zero", {"created_at_ms": -1}), ("a boolean time", {"created_at_ms": True})):
        try:
            mod.to_record("decision", decision(), SRC, **kwargs)
        except mod.DeskRecordError:
            continue
        c.bad.append(f"{label}: was accepted")
    return c.bad


POISON: tuple[object, ...] = (True, -1, 0.5, 10**20, [], {}, "bad id!\n", "é", "x" * 2000, None)


OPEN_MAPS: frozenset[tuple[str, ...]] = frozenset(
    {("budget", "calls"), ("spent", "calls"), ("tests", "parameters"), ("tests", "statistics")}
)


OPTIONAL_PATHS: frozenset[tuple[str, ...]] = frozenset(
    {("reason",), ("forecast_id",), ("trigger_id",), ("error_class",), ("label",), ("cost_expected", "seconds"), ("cost_spent", "seconds")}
)


def walk(value: Any, path: tuple[Any, ...] = ()) -> Any:
    """Every position of a body: its path (keys and list indexes) and the value there."""
    yield path, value
    if isinstance(value, dict):
        for key, item in value.items():
            yield from walk(item, (*path, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from walk(item, (*path, index))


def replaced(body: Any, path: tuple[Any, ...], new: Any) -> Any:
    out = copy.deepcopy(body)
    if not path:
        return new
    holder = out
    for step in path[:-1]:
        holder = holder[step]
    holder[path[-1]] = new
    return out


def without(body: Any, path: tuple[Any, ...]) -> Any:
    out = copy.deepcopy(body)
    holder = out
    for step in path[:-1]:
        holder = holder[step]
    del holder[path[-1]]
    return out


def poison_members(c: Case, kind: str, body: dict[str, Any]) -> None:
    """Every member, at every depth: refused when missing, when an unknown one is added beside it and when given a form it never takes."""
    for path, original in walk(body):
        if not path:
            c.refuses(f"{kind} with an extra member", kind, {**body, "extra": 1})
            c.refuses(f"{kind} that is not an object", kind, [])
            continue
        label = f"{kind}.{'.'.join(str(p) for p in path)}"
        names = tuple(p for p in path if isinstance(p, str))
        if isinstance(path[-1], str) and names[:-1] not in OPEN_MAPS:
            c.refuses(f"{label} missing", kind, without(body, path))
        if isinstance(original, dict) and original and names not in OPEN_MAPS:
            c.refuses(f"{label} with an extra member", kind, replaced(body, path, {**original, "extra": 1}))
        for poison in POISON:
            if poison is None and (original is None or names in OPTIONAL_PATHS):
                continue
            if type(poison) is type(original) and not isinstance(poison, str):
                continue
            changed = replaced(body, path, poison)
            try:
                c.mod.check_body(kind, changed)
            except c.mod.DeskRecordError:
                continue
            except Exception as exc:
                c.bad.append(f"{label} = {poison!r:.30}: raised {type(exc).__name__}: {exc}")
                continue
            c.bad.append(f"{label} = {poison!r:.30}: was accepted")


def check_members(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for kind, make in FIXTURES.items():
        c.accepts(f"{kind}: the valid body", kind, make())
        poison_members(c, kind, make())
    c.accepts("an abstained decision", "decision", abstained())
    poison_members(c, "decision", abstained())
    c.refuses("an unknown kind", "arm_run", {}, "kind is one of")
    return c.bad


def check_rules(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # run state
    for state, reason, forecast in (
        ("RUNNING", None, None),
        ("STOPPED", "BUDGET_EXHAUSTED", None),
        ("COMMITTED", None, "fc-1"),
        ("ABSTAINED", "BELOW_MINIMUM_EDGE", None),
        ("FAILED", "FAILED_EVIDENCE", None),
        ("FAILED_FINAL", "RUN_INTERRUPTED", None),
    ):
        c.accepts(f"state {state}", "desk_run_state", run_state(state, reason, forecast))
    c.refuses("running with a reason", "desk_run_state", run_state("RUNNING", "BUDGET_EXHAUSTED"), "reason")
    c.refuses("committed with a reason", "desk_run_state", run_state("COMMITTED", "OUT_OF_SCOPE", "fc-1"), "reason")
    c.refuses("stopped with no reason", "desk_run_state", run_state("STOPPED", None), "reason")
    c.refuses("stopped for an abstention reason", "desk_run_state", run_state("STOPPED", "OUT_OF_SCOPE"), "reason")
    c.refuses("abstained for a stop reason", "desk_run_state", run_state("ABSTAINED", "ALL_MENU_OPENED"), "reason")
    c.refuses("failed for an abstention reason", "desk_run_state", run_state("FAILED", "OUT_OF_SCOPE"), "reason")
    c.refuses("committed with no forecast", "desk_run_state", run_state("COMMITTED", None, None), "forecast")
    c.refuses("abstained with a forecast", "desk_run_state", run_state("ABSTAINED", "OUT_OF_SCOPE", "fc-1"), "forecast")
    for name, bad in (
        ("regime", "LEAGUE"),
        ("label", "simulated"),
        ("policy", "P-FIXED"),
        ("calibration", "CALIBRATED"),
        ("discovery_route", "USER_CLAIM"),
        ("state", "DONE"),
    ):
        c.refuses(f"a run state with {name} {bad}", "desk_run_state", {**run_state(), name: bad})
    b = run_state()
    b["spent"] = {"calls": {"": 1}, "seconds": 1, "tokens": 1}
    c.refuses("a provider with no name", "desk_run_state", b)
    b["spent"] = {"calls": {"ryo": -1}, "seconds": 1, "tokens": 1}
    c.refuses("a negative call count", "desk_run_state", b)
    b["spent"] = {"calls": {}, "seconds": 1}
    c.refuses("spent with a member missing", "desk_run_state", b)
    # hypothesis
    h = hypothesis()
    h["graph"]["nodes"][0]["span"] = [14, 0]
    c.refuses("a span that runs backwards", "hypothesis", h, "span")
    h = hypothesis()
    h["graph"]["nodes"][1]["span"] = [15, 400]
    c.refuses("a span beyond the statement", "hypothesis", h, "span")
    h = hypothesis()
    h["graph"]["edges"][0]["to"] = "nowhere"
    c.refuses("an edge to a node that is not there", "hypothesis", h, "edge")
    h = hypothesis()
    h["graph"]["nodes"].append(dict(h["graph"]["nodes"][0]))
    c.refuses("a node twice", "hypothesis", h, "node")
    h = hypothesis()
    h["graph"]["nodes"] = [{"node_id": f"n{i}", "type": "OBSERVATION", "span": [0, 5]} for i in range(9)]
    h["graph"]["edges"] = []
    c.refuses("more than eight nodes", "hypothesis", h, "nodes")
    h = hypothesis()
    h["graph"]["nodes"][0]["type"] = "ANYTHING"
    c.refuses("a node type that does not exist", "hypothesis", h, "type")
    h = hypothesis()
    h["graph"]["edges"][0]["kind"] = "MAGIC"
    c.refuses("an edge kind that does not exist", "hypothesis", h, "kind")
    h = hypothesis()
    h["stance"] = "SHORT"
    c.refuses("a short hypothesis", "hypothesis", h, "stance")
    # check
    k = desk_check()
    k["outcome"] = "FAILED"
    c.refuses("a failed check with no error class", "desk_check", k, "error")
    k["error_class"] = "CALL_FAILED"
    c.refuses("a failed check with evidence", "desk_check", k, "evidence")
    k["evidence_ids"] = []
    c.accepts("a failed check with its class and no evidence", "desk_check", k)
    k = desk_check()
    k["error_class"] = "CALL_FAILED"
    c.refuses("a check that worked with an error class", "desk_check", k, "error")
    k = desk_check()
    k["chosen_by"] = "VALUE"
    c.refuses("a check chosen by a rule this product does not have yet", "desk_check", k, "chosen")
    k = desk_check()
    k["error_class"] = "SURPRISE"
    k["outcome"] = "FAILED"
    k["evidence_ids"] = []
    c.refuses("an error class that does not exist", "desk_check", k, "error")
    k = desk_check()
    k["evidence_ids"] = [EV2, EV1]
    c.refuses("evidence ids out of order", "desk_check", k, "sorted")
    k = desk_check()
    k["check_type"] = "ASK_THE_WHALES"
    c.refuses("a check type that does not exist", "desk_check", k, "check_type")
    k = desk_check()
    del k["own_status"]
    c.refuses("a check that does not say what it found by itself", "desk_check", k, "own_status")
    for family in ("DERIVED", "NEWS:wire", "EXCHANGE:", "exchange:binance", "RYO:analyze_token", 5, None):
        k = desk_check()
        k["origin_family"] = family
        c.refuses(f"an origin family a check cannot have ({family!r})", "desk_check", k, "origin_family")
    k = desk_check()
    k["chosen_by"] = "ADDRESS_SCREEN"
    c.accepts("a screen that was made first and not chosen", "desk_check", k)
    k = desk_check()
    k["chosen_by"] = "GUESS"
    c.refuses("a way of choosing that does not exist", "desk_check", k, "chosen_by")
    k = desk_check()
    k["origin_family"] = "RYO"
    c.accepts("an RYO check names the one family RYO", "desk_check", k)
    for family in ("PRIMARY_DOC:ofac", "CHAIN:ethereum", "EXCHANGE:binance"):
        k = desk_check()
        k["origin_family"] = family
        c.accepts(f"the family {family} that a check can read", "desk_check", k)
    k = desk_check()
    k["cost_spent"] = {"calls": 2, "attempts": 2, "seconds": "slow"}
    c.refuses("seconds that are not a decimal", "desk_check", k, "seconds")
    k = desk_check()
    k["cost_expected"] = {"calls": 2, "seconds": None}
    c.accepts("an expected cost that is unknown", "desk_check", k)
    k = desk_check()
    k["args"] = [["symbol", "BTC"], ["include_perp", "true"]]
    c.refuses("arguments out of order", "desk_check", k, "args")
    # falsification
    f = falsification()
    f["outcome"] = "FAILED"
    f["ceiling"] = {"weight": "0.025000", "p_cap": "0.5750"}
    c.accepts("a failed step with its lower ceiling", "falsification", f)
    f["ceiling"] = {"weight": "0.050000", "p_cap": "0.6500"}
    c.refuses("a failed step whose ceiling did not fall", "falsification", f, "ceiling")
    f = falsification()
    f["ceiling"] = {"weight": "0.030000", "p_cap": "0.6500"}
    c.refuses("a passed step whose ceiling is not the target", "falsification", f, "ceiling")
    f = falsification()
    f["ceiling"] = {"weight": "0.060000", "p_cap": "0.6500"}
    f["outcome"] = "FAILED"
    c.refuses("a ceiling above the target", "falsification", f, "ceiling")
    f = falsification()
    f["completed"] = False
    c.refuses("a step not completed that passed", "falsification", f, "completed")
    f["outcome"] = "FAILED"
    f["ceiling"] = {"weight": "0.025000", "p_cap": "0.5750"}
    c.refuses("a step not completed with no label", "falsification", f, "label")
    f["label"] = "NOT_FALSIFIED_ATTEMPTED"
    c.accepts("a step not completed, labelled", "falsification", f)
    f = falsification()
    f["label"] = "NOT_FALSIFIED_ATTEMPTED"
    c.refuses("a completed step with the label", "falsification", f, "label")
    f = falsification()
    f["tests"][0]["result"] = "MAYBE"
    c.refuses("a test result that does not exist", "falsification", f, "result")
    f = falsification()
    f["tests"][0]["calibrated"] = False
    c.refuses("a test not calibrated that names a calibration", "falsification", f, "calibration")
    f = falsification()
    f["tests"][1]["calibrated"] = True
    c.refuses("a test calibrated with no calibration named", "falsification", f, "calibration")
    f = falsification()
    f["tests"][1]["reason"] = None
    c.refuses("a test that cannot be run with no reason", "falsification", f, "reason")
    f = falsification()
    f["tests"][0]["reason"] = "uncalibrated"
    c.refuses("a test that ran with a reason", "falsification", f, "reason")
    f = falsification()
    f["pre_test_target"] = {"stance": "FLAT", "p": "0.5", "weight": "0.05"}
    c.refuses("a flat target with weight", "falsification", f, "target")
    # a screen that lists the address stops the run before anything else is asked
    d = abstained()
    d["gate_events"] = [{"gate": "HAZARD_SCREEN", "status": "STOP", "detail": "the screen of the token contract lists it"}]
    d["final_action"] = {"kind": "ABSTAIN", "reason": "OUT_OF_SCOPE", "stance": None, "p": None, "weight": None}
    c.accepts("a decision that the screen stopped", "decision", d)
    d["gate_events"] = [{"gate": "GROUNDING", "status": "PASS", "detail": ""}, *d["gate_events"]]
    c.refuses("the screen after the grounding gate", "decision", d, "fixed order")
    d = abstained()
    d["gate_events"] = [{"gate": "HAZARD_SCREEN", "status": "PASS", "detail": "no source lists it"}, *d["gate_events"]]
    c.accepts("a screen that passed, before the others", "decision", d)
    # decision
    d = decision()
    d["final_action"] = {"kind": "ABSTAIN", "reason": "BELOW_MINIMUM_EDGE", "stance": None, "p": None, "weight": None}
    c.refuses("an abstention whose last gate did not stop", "decision", d, "last gate")
    d = abstained()
    d["final_action"] = {"kind": "COMMIT", "reason": None, "stance": "LONG", "p": "0.51", "weight": "0.04"}
    c.refuses("a commitment whose last gate stopped", "decision", d, "last gate")
    d = decision()
    d["final_action"]["reason"] = "OUT_OF_SCOPE"
    c.refuses("a commitment with a reason", "decision", d, "reason")
    d = abstained()
    d["final_action"]["reason"] = None
    c.refuses("an abstention with no reason", "decision", d, "reason")
    d = abstained()
    d["final_action"]["weight"] = "0.01"
    c.refuses("an abstention with a weight", "decision", d, "final")
    d = decision()
    d["final_action"]["weight"] = None
    c.refuses("a commitment with no weight", "decision", d, "weight")
    d = decision()
    d["proposal"]["cites"] = [EV2, EV1]
    c.refuses("cites out of order", "decision", d, "sorted")
    d = decision()
    d["proposal"]["cites"] = ["ev" + "c" * 26]
    c.refuses("a cite that is not among the evidence", "decision", d, "cites")
    d = decision()
    d["gate_events"][0]["gate"] = "VIBES"
    c.refuses("a gate that does not exist", "decision", d, "gate")
    d = decision()
    d["gate_events"][0]["status"] = "MAYBE"
    c.refuses("a gate status that does not exist", "decision", d, "status")
    d = decision()
    d["gate_events"] = []
    c.refuses("a decision with no gate events", "decision", d, "gate")
    d = decision()
    d["gate_events"][1], d["gate_events"][0] = d["gate_events"][0], d["gate_events"][1]
    c.refuses("gates out of order", "decision", d, "order")
    d = decision()
    d["gate_events"][1]["status"] = "STOP"
    c.refuses("a stop that is not last", "decision", d, "stop")
    d = decision()
    d["arm_id"] = "L1"
    c.refuses("another arm", "decision", d, "arm")
    d = decision()
    d["final_action"]["kind"] = "WIN"
    c.refuses("a kind that does not exist", "decision", d, "kind")
    d = decision()
    d["proposal"]["stance"] = "BUY"
    c.refuses("a proposal stance that does not exist", "decision", d, "stance")
    d = decision()
    d["proposal"]["no_call_reason"] = "OUT_OF_SCOPE"
    c.accepts("a proposal that gave a no-call reason", "decision", d)
    d["proposal"]["no_call_reason"] = "CHANGED_MY_MIND"
    c.refuses("a no-call reason that is not a closed word", "decision", d, "reason")

    # numbers and their edges
    def with_(body: dict[str, Any], *path: Any, value: Any) -> dict[str, Any]:
        return replaced(body, tuple(path), value)

    c.accepts("a run that has spent nothing", "desk_run_state", with_(run_state(), "spent", value={"calls": {}, "seconds": 0, "tokens": 0}))
    c.accepts("a provider with no calls", "desk_run_state", with_(run_state(), "budget", "calls", value={"ryo": 0}))
    c.accepts("the first instant", "desk_run_state", with_(run_state(), "at_ms", value=0))
    c.accepts("the last instant", "desk_run_state", with_(run_state(), "at_ms", value=mod.MAX_INSTANT_MS))
    c.refuses("one past the last instant", "desk_run_state", with_(run_state(), "at_ms", value=mod.MAX_INSTANT_MS + 1))
    c.refuses("a provider name that is not a name", "desk_run_state", with_(run_state(), "budget", "calls", value={"bad name!": 1}))
    c.accepts("the largest count", "desk_run_state", with_(run_state(), "spent", "seconds", value=mod.MAX_COUNT))
    c.refuses("one past the largest count", "desk_run_state", with_(run_state(), "spent", "seconds", value=mod.MAX_COUNT + 1))
    c.accepts("a committed run, forecast named", "desk_run_state", run_state("COMMITTED", None, "fc-1"))
    poison_members(c, "desk_run_state", run_state("COMMITTED", None, "fc-1"))
    k0 = with_(
        with_(desk_check(), "cost_expected", value={"calls": 0, "seconds": "0.000"}),
        "cost_spent",
        value={"calls": 0, "attempts": 0, "seconds": None},
    )
    c.accepts("a check that cost nothing and whose time is unknown", "desk_check", k0)
    c.accepts("the largest count of calls", "desk_check", with_(desk_check(), "cost_expected", "calls", value=mod.MAX_COUNT))
    c.refuses("one past it", "desk_check", with_(desk_check(), "cost_expected", "calls", value=mod.MAX_COUNT + 1))
    c.accepts("the largest number of a check", "desk_check", with_(desk_check(), "seq", value=mod.MAX_SEQ))
    c.refuses("one past it (the number of a check)", "desk_check", with_(desk_check(), "seq", value=mod.MAX_SEQ + 1))
    for layer in mod.LAYERS:
        c.accepts(f"layer {layer}", "desk_check", with_(desk_check(), "layer", value=layer))
    c.refuses("a layer that does not exist", "desk_check", with_(desk_check(), "layer", value="MAGIC"))
    c.refuses("an argument name that is not a name", "desk_check", with_(desk_check(), "args", value=[["Bad Name", "x"]]))
    c.refuses("an argument value that is not a value", "desk_check", with_(desk_check(), "args", value=[["symbol", "a b!"]]))
    c.refuses("an argument that is not a pair", "desk_check", with_(desk_check(), "args", value=[["symbol"]]))
    c.refuses("an argument that is three things", "desk_check", with_(desk_check(), "args", value=[["symbol", "a", "b"]]))
    # decimals
    for text in ("0", "1", "1.0", "0.123456", "0.5"):
        c.accepts(f"the decimal {text}", "decision", with_(decision(), "proposal", "p", value=text))
    for text in ("0.1234567", "1.1", "2", "0.", ".5", "-0.1", "1e-1", " 0.5", "0.5 "):
        c.refuses(f"the decimal {text!r}", "decision", with_(decision(), "proposal", "p", value=text))
    # the gates of a decision
    d = decision()
    d["gate_events"] = [d["gate_events"][0], d["gate_events"][0]]
    c.refuses("the same gate twice", "decision", d, "order")
    d = decision()
    d["proposal"]["cites"] = [EV1, EV2]
    c.accepts("cites that are all the evidence", "decision", d)
    d = abstained()
    d["gate_events"] = [{"gate": "GROUNDING", "status": "STOP", "detail": "a figure or statement is not grounded"}]
    d["final_action"] = {"kind": "ABSTAIN", "reason": "ESSENTIAL_PREMISE_UNPROVEN", "stance": None, "p": None, "weight": None}
    c.accepts("an abstention that stopped at the first gate", "decision", d)
    for reason in ("FAILED_EVIDENCE", "FAILED_STALE"):
        d = abstained()
        d["gate_events"] = [
            {"gate": "GROUNDING", "status": "PASS", "detail": ""},
            {"gate": "REQUIRED_EVIDENCE" if reason == "FAILED_EVIDENCE" else "STALENESS", "status": "STOP", "detail": "missing: x"},
        ]
        d["final_action"] = {"kind": "FAILED", "reason": reason, "stance": None, "p": None, "weight": None}
        c.accepts(f"a failure: {reason}", "decision", d)
        d["final_action"]["reason"] = "BELOW_MINIMUM_EDGE"
        c.refuses(f"a failure for an abstention reason: {reason}", "decision", d, "reason")

    # falsification rules, one member at a time
    def fals(outcome: str, completed: bool, label: str | None, pre: tuple[str, str, str], cap: tuple[str, str]) -> dict[str, Any]:
        body = falsification()
        body["outcome"], body["completed"], body["label"] = outcome, completed, label
        body["pre_test_target"] = {"stance": pre[0], "p": pre[1], "weight": pre[2]}
        body["ceiling"] = {"weight": cap[0], "p_cap": cap[1]}
        return body

    c.accepts("a failed step from a flat target", "falsification", fals("FAILED", True, None, ("FLAT", "0.5", "0"), ("0", "0.5")))
    c.accepts(
        "a failed step with only the weight lowered at one half",
        "falsification",
        fals("FAILED", True, None, ("LONG", "0.5", "0.05"), ("0.025", "0.5")),
    )
    c.refuses(
        "a failed step with only the probability lowered",
        "falsification",
        fals("FAILED", True, None, ("LONG", "0.65", "0.05"), ("0.05", "0.575")),
        "ceiling",
    )
    c.refuses(
        "a failed step with only the weight lowered",
        "falsification",
        fals("FAILED", True, None, ("LONG", "0.65", "0.05"), ("0.025", "0.65")),
        "ceiling",
    )
    c.refuses(
        "a weight above the target, the probability not",
        "falsification",
        fals("FAILED", True, None, ("LONG", "0.65", "0.05"), ("0.06", "0.6")),
        "ceiling",
    )
    c.refuses(
        "a probability above the target, the weight not",
        "falsification",
        fals("FAILED", True, None, ("LONG", "0.65", "0.05"), ("0.02", "0.7")),
        "ceiling",
    )
    c.refuses(
        "a flat target with weight, ceiling consistent",
        "falsification",
        fals("PASSED", True, None, ("FLAT", "0.5", "0.05"), ("0.05", "0.5")),
        "flat",
    )
    c.accepts(
        "a flat target with weight written as zero places",
        "falsification",
        fals("PASSED", True, None, ("FLAT", "0.5", "0.000000"), ("0.000000", "0.5000")),
    )
    c.refuses(
        "a step not completed that passed, with its label",
        "falsification",
        fals("PASSED", False, "NOT_FALSIFIED_ATTEMPTED", ("LONG", "0.65", "0.05"), ("0.05", "0.65")),
        "not completed",
    )
    c.refuses(
        "a label that is not the label",
        "falsification",
        fals("FAILED", False, "OTHER_LABEL", ("LONG", "0.65", "0.05"), ("0.025", "0.575")),
        "label",
    )
    c.refuses(
        "a failed step from a target at one half, probability above it",
        "falsification",
        fals("FAILED", True, None, ("LONG", "0.5", "0.05"), ("0.025", "0.6")),
        "ceiling",
    )
    c.refuses(
        "a failed step from a flat target, weight above it",
        "falsification",
        fals("FAILED", True, None, ("FLAT", "0.5", "0"), ("0.01", "0.5")),
        "ceiling",
    )
    one = hypothesis()
    one["graph"] = {"nodes": [one["graph"]["nodes"][0]], "edges": []}
    c.accepts("a graph of one node", "hypothesis", one)
    # tests
    for result, reason, ok in (
        ("INCONCLUSIVE", "uncalibrated", True),
        ("INCONCLUSIVE", "no_source", False),
        ("INCONCLUSIVE", None, True),
        ("FAVOURS_ALTERNATIVE", "uncalibrated", False),
    ):
        f = falsification()
        f["tests"][0].update({"result": result, "reason": reason})
        (c.accepts if ok else c.refuses)(f"a test {result} with reason {reason}", "falsification", f)
    f = falsification()
    f["tests"][0].update({"type": "T_SOURCE", "calibrated": True, "calibration_sha256": None})
    c.accepts("a source test needs no calibration", "falsification", f)
    f["tests"][0]["type"] = "T_MARKET"
    c.refuses("a market test does", "falsification", f, "calibration")
    f = falsification()
    f["tests"][0]["parameters"] = {"Bad Key": "1"}
    c.refuses("a parameter name that is not a name", "falsification", f, "parameters")
    f = falsification()
    f["tests"][0]["statistics"] = {"z": "a b!"}
    c.refuses("a statistic that is not text of that form", "falsification", f, "statistics")
    f = falsification()
    f["tests"][0]["parameters"] = []
    c.refuses("parameters that are not a map", "falsification", f, "parameters")
    # hypothesis edges
    h = hypothesis()
    h["graph"]["nodes"][0]["span"] = [5, 5]
    c.refuses("an empty span", "hypothesis", h, "span")
    h = hypothesis()
    h["graph"]["nodes"][0]["span"] = [0, 1]
    c.accepts("the shortest span", "hypothesis", h)
    h = hypothesis()
    h["graph"]["nodes"][0]["span"] = [0, len(h["statement"])]
    c.accepts("a span to the end of the statement", "hypothesis", h)
    h["graph"]["nodes"][0]["span"] = [0, len(h["statement"]) + 1]
    c.refuses("a span one past the statement", "hypothesis", h, "span")
    h = hypothesis()
    h["graph"]["nodes"][0]["span"] = [0, 1, 2]
    c.refuses("a span of three", "hypothesis", h, "span")
    h = hypothesis()
    h["graph"]["nodes"] = [{"node_id": f"n{i}", "type": "OBSERVATION", "span": [0, 5]} for i in range(8)]
    h["graph"]["edges"] = []
    c.accepts("eight nodes", "hypothesis", h)
    h["graph"]["nodes"] = []
    c.refuses("no nodes", "hypothesis", h, "nodes")
    for version, ok in ((0, False), (1, True), (1000, True), (1001, False)):
        h = with_(hypothesis(), "triage_rule_version", value=version)
        (c.accepts if ok else c.refuses)(f"triage rule version {version}", "hypothesis", h)
    h = hypothesis()
    h["graph"]["edges"][0]["from"] = ["x"]
    c.refuses("an edge from something that is not an id", "hypothesis", h, "from")
    # the writer and the reader
    try:
        mod.record_id("arm_run", {})
    except mod.DeskRecordError:
        pass
    else:
        c.bad.append("an id for a kind that does not exist was made")
    for label, kwargs in (
        ("a body that is not valid", {"body": with_(decision(), "arm_id", value="L1")}),
        ("a source that is not a urn", {"source": "not-a-urn"}),
        ("a time one past the last", {"created_at_ms": mod.MAX_INSTANT_MS + 1}),
    ):
        arguments: dict[str, Any] = {"kind": "decision", "body": decision(), "source": SRC, "created_at_ms": AT}
        arguments.update(kwargs)
        try:
            mod.to_record(**arguments)
        except mod.DeskRecordError:
            continue
        c.bad.append(f"{label}: was written")
    for at in (0, mod.MAX_INSTANT_MS):
        try:
            mod.to_record("decision", decision(), SRC, at)
        except mod.DeskRecordError as exc:
            c.bad.append(f"the instant {at} was refused: {exc}")
    bad_body = with_(decision(), "arm_id", value="L1")
    forged = {**mod.to_record("decision", decision(), SRC, AT), "body": bad_body}
    forged["id"] = mod.record_id("decision", bad_body)
    try:
        mod.from_record(forged)
    except mod.DeskRecordError:
        pass
    else:
        c.bad.append("a record with an invalid body and its own id was read")
    # free text is the product's template text and nothing wider
    for member, text in (("statement", "BTC rose <script>"), ("statement", "Line one\nline two"), ("invalidation", "price = $5")):
        h = hypothesis()
        h[member] = text
        c.refuses(f"{member} outside the template alphabet: {text!r}", "hypothesis", h, member)
    h = hypothesis()
    h["statement"] = "x" * 301
    c.refuses("a statement over the length limit", "hypothesis", h, "statement")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_round_trip, check_members, check_rules):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
