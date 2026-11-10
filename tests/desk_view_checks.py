"""Checks of the view of Desk runs (``hoshitori.desk_view``), runnable against a mutant.

The view is what the Desk page shows: for each run, what it was asked, what it checked and why, what it tried to disprove, how the gates
treated the proposal and how it ended. It is built only from the Desk's own validated records, so nothing in it can come from a source.
"""

from __future__ import annotations

import copy
import json
import types
from typing import Any

from hoshitori import view_models
from tests.desk_checks import FUNDING, KRAKEN, SPOT, TOKEN, World


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, records: Any, says: str) -> None:
        try:
            self.mod.runs(records)
        except self.mod.DeskViewError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def desk_budget() -> Any:
    from hoshitori import desk

    return desk.Budget({}, 600, 20000)


def run_of(**over: Any) -> Any:
    from hoshitori import desk

    budget = over.pop("budget", None)
    w = World(desk, **over)
    w.run(budget=budget)
    return w


def as_json(value: Any) -> Any:
    return json.loads(json.dumps(view_models.to_json(value)))


def check_committed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = run_of()
    (run,) = mod.runs(w.records)
    j = as_json(run)
    c.eq(
        "who and how it ended",
        (j["run_id"], j["label"], j["symbol"], j["route"], j["state"], j["reason"], j["open"]),
        ("run-1", "live", "BTC", "EVENT", "COMMITTED", None, False),
    )
    c.eq("the forecast it names", j["forecast_id"], "fc-run-1")
    c.eq(
        "the hypothesis",
        (j["hypothesis"]["statement"], j["hypothesis"]["stance"], j["hypothesis"]["template_id"]),
        ("BTC rose over the last hour. The move began after the announcement.", "LONG", "MOVE_UP"),
    )
    c.eq(
        "two checks, in words the page can use, each with the origin family it read and what it found by itself",
        [
            (
                k["seq"],
                k["node_id"],
                k["check_type"],
                k["provider"],
                k["origin_family"],
                k["outcome"],
                k["error_class"],
                k["status_before"],
                k["status_after"],
                k["own_status"],
                k["layer"],
            )
            for k in j["checks"]
        ],
        [
            (
                0,
                "obs",
                "INDEPENDENT_PRICE_CHECK",
                "binance-spot",
                "EXCHANGE:binance",
                "OK",
                None,
                "UNCHECKED",
                "SUPPORTED",
                "SUPPORTED",
                "DETERMINISTIC",
            ),
            (
                1,
                "obs",
                "INDEPENDENT_PRICE_CHECK",
                "kraken",
                "EXCHANGE:kraken",
                "OK",
                None,
                "SUPPORTED",
                "SUPPORTED",
                "SUPPORTED",
                "DETERMINISTIC",
            ),
        ],
    )
    c.eq(
        "the origins that read the part: how many, which support it and which contradict it, and the calls they cost",
        j["origins"],
        {
            "read": ["EXCHANGE:binance", "EXCHANGE:kraken"],
            "supporting": ["EXCHANGE:binance", "EXCHANGE:kraken"],
            "contradicting": [],
            "calls": 4,
        },
    )
    c.eq(
        "how many others could have been chosen, and what it cost",
        (j["checks"][0]["alternatives"], j["checks"][0]["calls"], j["checks"][0]["evidence"]),
        (3, 2, 1),
    )
    f = j["falsification"]
    c.eq(
        "what was tried to disprove it", (f["outcome"], f["completed"], f["label"], f["target_assumption"]), ("PASSED", True, None, "cond")
    )
    c.eq(
        "each test and what it said",
        [(t["type"], t["result"], t["reason"], t["calibrated"]) for t in f["tests"]],
        [
            ("T_ONSET", t_onset(w), None, True),
            ("T_MARKET", t_market(w), None, True),
            ("T_SOURCE", "NOT_TESTABLE", "no_source", True),
            ("T_PLACEBO_TIME", "NOT_TESTABLE", "uncalibrated", False),
        ],
    )
    c.eq(
        "the target and the ceiling",
        (f["pre_test"], f["ceiling"]),
        ({"stance": "LONG", "p": "0.60", "weight": "0.03"}, {"weight": "0.030000", "p_cap": "0.6000"}),
    )
    d = j["decision"]
    c.eq(
        "the proposal as the model made it",
        (d["proposal"]["stance"], d["proposal"]["p"], d["proposal"]["weight"], d["proposal"]["cites"]),
        ("LONG", "0.62", "0.04", 1),
    )
    c.eq(
        "the gates in order, with their status",
        [(g["gate"], g["status"]) for g in d["gates"]],
        [
            ("GROUNDING", "PASS"),
            ("REQUIRED_EVIDENCE", "PASS"),
            ("STALENESS", "PASS"),
            ("MINIMUM_EDGE", "PASS"),
            ("FALSIFICATION", "CUT"),
            ("MINIMUM_EDGE_AFTER_CUT", "PASS"),
            ("RISK_KERNEL", "PASS"),
        ],
    )
    c.eq("the final action", d["final"], {"kind": "COMMIT", "reason": None, "stance": "LONG", "p": "0.6000", "weight": "0.030000"})
    c.eq(
        "what was spent against the budget",
        (j["spent"], j["budget"]),
        ({"calls": 4, "seconds": 0, "tokens": 500}, {"calls": 16, "seconds": 600, "tokens": 20000}),
    )
    c.eq(
        "the view is frozen",
        [
            k.__dataclass_params__.frozen
            for k in (
                mod.DeskRun,
                mod.CheckView,
                mod.FalsificationView,
                mod.DecisionView,
                mod.HypothesisView,
                mod.Counts,
                mod.GateView,
                mod.ProposalView,
                mod.TestView,
                mod.Origins,
            )
        ],
        [True] * 10,
    )
    return c.bad


def t_onset(w: Any) -> str:
    return next(t["result"] for t in w.of("falsification")[0]["tests"] if t["type"] == "T_ONSET")


def t_market(w: Any) -> str:
    return next(t["result"] for t in w.of("falsification")[0]["tests"] if t["type"] == "T_MARKET")


def check_other_endings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = run_of(moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1000.5")}, ryo_change="0.10")
    (run,) = mod.runs(w.records)
    j = as_json(run)
    c.eq(
        "a run that abstained before any test: no falsification, a decision that stopped at the first gate",
        (j["state"], j["reason"], j["falsification"], [g["gate"] for g in j["decision"]["gates"]], j["decision"]["final"]["kind"]),
        ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN", None, ["GROUNDING"], "ABSTAIN"),
    )
    c.eq(
        "every check it tried is listed in order",
        [(k["seq"], k["check_type"], k["status_after"]) for k in j["checks"]],
        [
            (0, "INDEPENDENT_PRICE_CHECK", "NOT_FOUND_IN_SOURCES_CHECKED"),
            (1, "INDEPENDENT_PRICE_CHECK", "NOT_FOUND_IN_SOURCES_CHECKED"),
            (2, "DERIVATIVES_CONTEXT", "NOT_FOUND_IN_SOURCES_CHECKED"),
            (3, "RYO_TOKEN_READ", "NOT_FOUND_IN_SOURCES_CHECKED"),
        ],
    )
    w = run_of(failures={SPOT: "CALL_FAILED", KRAKEN: "CALL_FAILED", TOKEN: "CALL_FAILED", FUNDING: "CALL_FAILED"})
    j = as_json(mod.runs(w.records)[0])
    c.eq(
        "a failure keeps the class of each failed check",
        ([k["error_class"] for k in j["checks"]], j["state"], j["reason"]),
        (["CALL_FAILED"] * 4, "FAILED", "FAILED_EVIDENCE"),
    )
    killed = run_of()
    cut = killed.records[:2]
    j = as_json(mod.runs(cut)[0])
    c.eq(
        "a run cut after its hypothesis is open, at the state it reached",
        (j["open"], j["state"], j["checks"], j["falsification"], j["decision"], j["hypothesis"]["stance"]),
        (True, "RUNNING", [], None, None, "LONG"),
    )
    j = as_json(mod.runs(killed.records[:1])[0])
    c.eq("a run cut at once has no hypothesis", (j["open"], j["hypothesis"]), (True, None))
    c.eq(
        "a stopped run is open until it ends",
        as_json(
            mod.runs([killed.records[0], ("desk_run_state", {**killed.records[0][1], "state": "STOPPED", "reason": "BUDGET_EXHAUSTED"})])[0]
        )["open"],
        True,
    )
    c.eq("records of no runs give no runs", mod.runs([]), [])
    stopped = run_of(budget=desk_budget())
    j = as_json(mod.runs(stopped.records)[0])
    c.eq(
        "a run that stopped and then ended is not open and ends in its last state",
        (j["open"], j["state"], j["reason"]),
        (False, "ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"),
    )
    many = run_of(moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1000.5")}, ryo_change="0.10")
    checks = [r for r in many.records if r[0] == "desk_check"]
    rest = [r for r in many.records if r[0] != "desk_check"]
    reordered = [*rest[:2], *reversed(checks), *rest[2:]]
    c.eq(
        "checks are shown in the order they were numbered, whatever order they came in",
        [k["seq"] for k in as_json(mod.runs(reordered)[0])["checks"]],
        [0, 1, 2, 3],
    )
    return c.bad


def check_several(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a, b = run_of(), run_of()
    second = copy.deepcopy(b.records)
    ids = {"run-1": "run-2"}
    for _, body in second:
        body["run_id"] = ids.get(body["run_id"], body["run_id"])
        if "decision_id" in body:
            body["decision_id"] = "dec-2"
    both = mod.runs([*a.records, *second])
    c.eq("runs keep the order they began in", [r.run_id for r in both], ["run-1", "run-2"])
    interleaved = [a.records[0], second[0], *a.records[1:], *second[1:]]
    c.eq(
        "and interleaved records go to their own run",
        [(r.run_id, len(r.checks), r.state) for r in mod.runs(interleaved)],
        [("run-1", 2, "COMMITTED"), ("run-2", 2, "COMMITTED")],
    )
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = run_of()
    bad = copy.deepcopy(w.records)
    bad[0][1]["state"] = "SLEEPING"
    c.refuses("a record the schema refuses", bad, "record")
    bad = copy.deepcopy(w.records)
    bad[1][1]["statement"] = "BTC <rose>"
    c.refuses("a hypothesis with text outside the template alphabet", bad, "record")
    c.refuses("a kind of record that is not the Desk's", [("arm_run", {})], "the kind of a record is one of")
    first = {**w.records[0][1], "state": "STOPPED", "reason": "BUDGET_EXHAUSTED"}
    c.refuses("a run that begins with another state", [("desk_run_state", first)], "begins with a RUNNING")
    c.refuses("a record that is not a pair", ["nothing"], "record")
    c.refuses("a hypothesis before the run began", w.records[1:], "RUNNING")
    c.refuses("a hypothesis twice", [*w.records, w.records[1]], "once")
    c.refuses("a falsification twice", [*w.records, w.records[3]], "once")
    c.refuses("a decision twice", [*w.records, w.records[4]], "once")
    c.refuses("two terminal states", [*w.records, w.records[-1]], "once")
    c.refuses("a check numbered twice", [*w.records, w.records[2]], "numbered")
    other = copy.deepcopy(w.records[2])
    other[1]["run_id"] = "run-9"
    c.refuses("a check of a run that never began", [*w.records, other], "RUNNING")
    c.refuses("a run that began twice", [w.records[0], w.records[0], *w.records[1:]], "RUNNING")
    return c.bad


def check_json(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = run_of()
    runs = mod.runs(w.records)
    text = json.dumps(view_models.to_json(runs), sort_keys=True)
    c.eq("it is plain JSON", json.loads(text)[0]["run_id"], "run-1")
    again = json.dumps(view_models.to_json(mod.runs(copy.deepcopy(w.records))), sort_keys=True)
    c.eq("the same records give the same bytes", again, text)
    return c.bad


def check_file(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = run_of()
    raw = mod.file_bytes(w.records)
    c.eq(
        "a file of records is canonical JSON with one newline",
        (raw.endswith(b"}\n"), raw.count(b"\n"), json.loads(raw)["format"]),
        (True, 1, 1),
    )
    c.eq("it reads back to the same runs", mod.read_file(raw), mod.runs(w.records))
    c.eq("and to the same bytes", mod.file_bytes([(k["kind"], k["body"]) for k in json.loads(raw)["records"]]), raw)
    c.eq("the format number", mod.FILE_FORMAT, 1)
    c.eq(
        "with its keys sorted at every depth and nothing extra",
        raw.decode() == json.dumps(json.loads(raw), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n",
        True,
    )

    def refused(label: str, data: bytes, says: str) -> None:
        try:
            mod.read_file(data)
        except mod.DeskViewError as exc:
            if says not in str(exc):
                c.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except Exception as exc:
            c.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        c.bad.append(f"{label}: was accepted")

    good = json.loads(raw)
    refused("bytes that are not JSON", b"not json", "readable")
    refused("bytes that are not UTF-8", b"\xff\xfe", "readable")
    refused("JSON that is not an object", b"[]", "object")
    refused("another format", json.dumps({**good, "format": 2}).encode(), "format")
    refused("a member that is not known", json.dumps({**good, "extra": 1}).encode(), "members")
    refused("no records member", json.dumps({"format": 1}).encode(), "members")
    refused("records that are not a list", json.dumps({"format": 1, "records": {}}).encode(), "list")
    refused("a record that is not an object", json.dumps({"format": 1, "records": [[1, 2]]}).encode(), "kind and a body")
    refused(
        "a record with another member",
        json.dumps({"format": 1, "records": [{"kind": "decision", "body": {}, "x": 1}]}).encode(),
        "kind and a body",
    )
    refused(
        "a record the schema refuses", json.dumps({"format": 1, "records": [{"kind": "desk_run_state", "body": {}}]}).encode(), "refused"
    )
    refused("a number that is not whole", b'{"format":1.5,"records":[]}', "format")
    c.eq("an empty file has no runs", mod.read_file(mod.file_bytes([])), [])
    return c.bad


def check_origins(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w = run_of(moves={SPOT: ("1000", "1005"), KRAKEN: ("1000", "995")})
    (run,) = mod.runs(w.records)
    j = as_json(run)
    c.eq(
        "origins that disagree are told apart, not averaged",
        (j["state"], j["reason"], j["origins"]["supporting"], j["origins"]["contradicting"], j["origins"]["read"]),
        (
            "ABSTAINED",
            "EVIDENCE_CONFLICT_UNRESOLVED",
            ["EXCHANGE:binance", "RYO"],
            ["EXCHANGE:kraken"],
            ["EXCHANGE:binance", "EXCHANGE:kraken", "RYO"],
        ),
    )
    w = run_of(moves={SPOT: ("1000", "1000.5"), KRAKEN: ("1000", "1005")})
    (run,) = mod.runs(w.records)
    j = as_json(run)
    c.eq(
        "a source that was read and showed no move read the part, and does not support it",
        (j["origins"]["read"], j["origins"]["supporting"], j["origins"]["contradicting"]),
        (["EXCHANGE:binance", "EXCHANGE:kraken", "RYO"], ["EXCHANGE:kraken", "RYO"], []),
    )
    w = run_of(failures={KRAKEN: "CALL_FAILED", TOKEN: "CALL_FAILED"})
    (run,) = mod.runs(w.records)
    j = as_json(run)
    c.eq(
        "an origin that could not be read did not read the part and is not counted, though the call is",
        (j["origins"]["read"], j["origins"]["supporting"], j["origins"]["calls"], [k["own_status"] for k in j["checks"]]),
        (["EXCHANGE:binance"], ["EXCHANGE:binance"], 4, ["SUPPORTED", "SOURCE_UNAVAILABLE", "SOURCE_UNAVAILABLE"]),
    )
    return c.bad


def check_a_screened_run(mod: types.ModuleType) -> list[str]:
    from hoshitori import desk
    from tests.desk_checks import CONTRACT

    c = Case(mod)
    budget = desk.Budget({"binance-spot": 4, "kraken": 4, "binance-funding": 4, "ryo": 4, "address-flag": 2}, 600, 20000)
    w = run_of(contract=CONTRACT, budget=budget)
    (run,) = mod.runs(w.records)
    j = as_json(run)
    c.eq(
        "the screen is the first check, of the sanctions list's family, and says what it found",
        [(k["seq"], k["check_type"], k["provider"], k["origin_family"], k["own_status"]) for k in j["checks"][:2]],
        [
            (0, "ADDRESS_FLAG_CHECK", "address-flag", "PRIMARY_DOC:ofac", "NO_FLAG_FOUND"),
            (1, "INDEPENDENT_PRICE_CHECK", "binance-spot", "EXCHANGE:binance", "SUPPORTED"),
        ],
    )
    c.eq(
        "it is no origin that read the claim, though its call is counted",
        (j["origins"]["read"], j["origins"]["supporting"], j["origins"]["calls"]),
        (["EXCHANGE:binance", "EXCHANGE:kraken"], ["EXCHANGE:binance", "EXCHANGE:kraken"], 5),
    )
    c.eq("and its gate leads the decision's", [g["gate"] for g in j["decision"]["gates"]][:2], ["HAZARD_SCREEN", "GROUNDING"])
    flagged = run_of(contract=CONTRACT, screen_reading="FLAGGED", budget=budget)
    (stopped,) = mod.runs(flagged.records)
    j = as_json(stopped)
    c.eq(
        "a run that a listing stopped has read nothing of the claim",
        (
            j["state"],
            j["reason"],
            j["origins"],
            [k["check_type"] for k in j["checks"]],
            [(g["gate"], g["status"]) for g in j["decision"]["gates"]],
        ),
        (
            "ABSTAINED",
            "OUT_OF_SCOPE",
            {"read": [], "supporting": [], "contradicting": [], "calls": 1},
            ["ADDRESS_FLAG_CHECK"],
            [("HAZARD_SCREEN", "STOP")],
        ),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_committed,
        check_origins,
        check_a_screened_run,
        check_other_endings,
        check_several,
        check_refusals,
        check_json,
        check_file,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
