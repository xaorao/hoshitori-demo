"""Scenarios for reading RYO in a cycle (``hoshitori.ryo_collect``), runnable against the real module or a mutant of it.

The gateway is replaced by a runner that answers from a script and writes real captures into a real capture store (the pattern of the
venue collector's checks); the answers are the synthetic ones of ``ryo_data``. What is checked is what the module asks, what it does with the
answers and what it returns. The expectations are worked out by hand from the rules in the module's docstring:

* the scan asks ``scan_market`` for the protocol's top N, and its answer becomes the manifest's rows (RYO's own rank and symbol) with the
  capture it came from; an answer that cannot be a scan is a closed failure reason, with the capture kept when one came;
* every candidate is asked ``analyze_token`` and ``deep_analysis`` by symbol, and the cycle is asked ``market_overview`` and
  ``monitor_market_sentiment_shift`` once; an observation item of each answer goes in the snapshot's list, an interpretation item only in
  the records (an independent arm never gets it);
* one answer that fails costs only itself: the candidate keeps what it did get, and the failure names the tool and the reason.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
import types
from collections.abc import Callable
from concurrent.futures import Future
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import snapshot
from hoshitori.capture import CaptureStore
from hoshitori.clock import ManualClock, SystemClock
from hoshitori.connector import ConnectorError, ErrorClass
from hoshitori.gateway import Call, Outcome
from hoshitori.lineage import EvidenceStore
from hoshitori.manifest import Failed, ScanRow
from tests import ryo_data as D
from tests.collect_checks import NOW, FakeRunner, Reply

SRC = "urn:hoshitori:ryo:test"


def reply_for(call: Call) -> Reply:
    args = dict(call.args)
    result = D.result(call.operation)
    if "symbol" in args:
        data = D.DATA[call.operation]()
        data["asset"]["symbol"] = args["symbol"]
        result = D.result(call.operation, data=data, request=args)
    return Reply(D.rest_body(call.operation, result))


class World:
    def __init__(self, root: Path, script: Callable[[Call], Reply] | None = None) -> None:
        self.clock = ManualClock(NOW)
        self.captures = CaptureStore(root / "captures")
        self.store = EvidenceStore()
        self.runner = FakeRunner(self.captures, self.clock, script or reply_for)

    def env(self, mod: types.ModuleType) -> Any:
        return mod.RyoEnvironment(self.runner, self.captures, self.store, self.clock)

    def operations(self) -> list[str]:
        return [call.operation for call in self.runner.calls]


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def scan_body(candidates: list[dict[str, Any]], **over: Any) -> Reply:
    data = D.scan_market_data()
    base = data["candidates"][0]
    data["candidates"] = [{**base, **c} for c in candidates]
    data["candidate_count"] = len(candidates)
    return Reply(D.rest_body("scan_market", D.result("scan_market", data=data, **over)))


def check_the_scan(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    rows = [{"rank": 2, "symbol": "BBB"}, {"rank": 1, "symbol": "AAA"}, {"rank": 3, "symbol": "CCC"}]
    w = World(root / "ok", lambda call: scan_body(rows))
    got = mod.scan(w.env(mod), top_n=5, wait_s=0.5)
    c.eq("the scan asks scan_market for the top N", [(x.operation, dict(x.args)) for x in w.runner.calls], [("scan_market", {"top_n": 5})])
    c.eq("the rows are RYO's own rank and symbol, in rank order", got.rows, (ScanRow(1, "AAA"), ScanRow(2, "BBB"), ScanRow(3, "CCC")))
    c.eq("and carry the capture they were read from, with its hash", got.capture is not None, True)
    if got.capture is not None:
        held = w.captures.get(got.capture.capture_id)
        c.eq("the hash is the stored response's", got.capture.sha256, held.body_sha256)
    c.eq("a scan that came has no failure text", got.detail, "")

    spread = "the candidates do not have distinct ranks from 1 and distinct symbols"
    cases: list[tuple[str, Callable[[Call], Reply], str, str]] = [
        (
            "the call failed",
            lambda call: Reply(outcome=Outcome.FAILED, reason="UPSTREAM", capture=False),
            "RYO_SCAN_CALL_FAILED",
            "FAILED: UPSTREAM",
        ),
        ("no answer in time", lambda call: Reply(hang=True), "RYO_SCAN_TIMED_OUT", "no result before the wait ended"),
        ("not an envelope", lambda call: Reply(b"<html>"), "RYO_SCAN_UNREADABLE", "RYO's response is not readable JSON (SYNTAX)"),
        (
            "the answer is for another tool",
            lambda call: Reply(D.rest_body("scan_market", D.result("analyze_token"))),
            "RYO_SCAN_UNREADABLE",
            "RYO's response answers for tool 'analyze_token', not 'scan_market'",
        ),
        ("unavailable", lambda call: scan_body(rows, status="unavailable"), "RYO_SCAN_UNAVAILABLE", "RYO says the scan is unavailable"),
        (
            "simulated",
            lambda call: scan_body(rows, data_mode="simulated"),
            "RYO_SCAN_NOT_LIVE",
            "RYO's data mode is 'simulated', not 'live'",
        ),
        ("mixed", lambda call: scan_body(rows, data_mode="mixed"), "RYO_SCAN_NOT_LIVE", "RYO's data mode is 'mixed', not 'live'"),
        ("no candidates", lambda call: scan_body([]), "RYO_SCAN_EMPTY", "the scan holds no candidate"),
        (
            "two candidates of one rank",
            lambda call: scan_body([{"rank": 1, "symbol": "A"}, {"rank": 1, "symbol": "B"}]),
            "RYO_SCAN_UNREADABLE",
            spread,
        ),
        ("a rank below one", lambda call: scan_body([{"rank": 0, "symbol": "A"}]), "RYO_SCAN_UNREADABLE", spread),
        (
            "a rank below one beside a good one",
            lambda call: scan_body([{"rank": 0, "symbol": "A"}, {"rank": 5, "symbol": "B"}]),
            "RYO_SCAN_UNREADABLE",
            spread,
        ),
        ("a symbol twice", lambda call: scan_body([{"rank": 1, "symbol": "A"}, {"rank": 2, "symbol": "A"}]), "RYO_SCAN_UNREADABLE", spread),
        (
            "a rank that is null",
            lambda call: scan_body([{"rank": None, "symbol": "A"}]),
            "RYO_SCAN_UNREADABLE",
            "a candidate has no whole-number rank and symbol",
        ),
    ]
    for label, script, reason, text in cases:
        w = World(root / f"fail-{reason}-{label.replace(' ', '_')}", script)
        got = mod.scan(w.env(mod), top_n=5, wait_s=0.05)
        c.eq(f"{label}: the scan is a failure with a closed reason, and says why in words", (got.rows, got.detail), (Failed(reason), text))
    dropped = scan_body(rows)
    body = json.loads(dropped.body)
    del body["result"]["data"]["candidates"][0]["rank"]  # a candidate with no rank at all
    w = World(root / "no-rank", lambda call: Reply(json.dumps(body).encode()))
    got = mod.scan(w.env(mod), top_n=5, wait_s=0.05)
    c.eq(
        "a candidate with no rank is unreadable, not a scan with fewer candidates",
        (got.rows, got.detail),
        (Failed("RYO_SCAN_UNREADABLE"), "a candidate has no whole-number rank and symbol"),
    )
    body = json.loads(scan_body(rows).body)
    for row in body["result"]["data"]["candidates"]:
        del row["rank"], row["symbol"]  # candidates that name neither
    w = World(root / "no-fields", lambda call: Reply(json.dumps(body).encode()))
    got = mod.scan(w.env(mod), top_n=5, wait_s=0.05)
    c.eq(
        "candidates that name no symbol are refused as the reader refuses them",
        (got.rows, got.detail),
        (Failed("RYO_SCAN_UNREADABLE"), "RYO's data.candidates[0].symbol is missing"),
    )
    w = World(root / "asked", lambda call: scan_body(rows))
    mod.scan(w.env(mod), top_n=5, wait_s=7.0, caller="mine", earmark=3)
    (asked,) = w.runner.calls
    c.eq(
        "the call is labelled by its caller, earmarked as asked, and due when the wait ends",
        (asked.provider, asked.caller, asked.window, asked.deadline),
        ("ryo", "mine", 3, NOW + timedelta(seconds=7)),
    )
    w = World(root / "defaults", lambda call: scan_body(rows))
    mod.scan(w.env(mod), top_n=5, wait_s=7.0)
    c.eq("by default it is the cycle's call, with no earmark", (w.runner.calls[0].caller, w.runner.calls[0].window), ("cycle", None))
    w = World(root / "partial", lambda call: scan_body(rows, status="partial"))
    c.eq("a partial scan still gives its rows", len(mod.scan(w.env(mod), top_n=5, wait_s=0.5).rows), 3)
    w = World(root / "kept", lambda call: scan_body(rows, data_mode="simulated"))
    got = mod.scan(w.env(mod), top_n=5, wait_s=0.5)
    c.eq("a scan refused after it came keeps the capture it came in", got.capture is not None, True)
    try:
        got.rows = ()  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("a scan can be changed after it is made")
    w = World(root / "none", lambda call: Reply(outcome=Outcome.FAILED, reason="X", capture=False))
    c.eq("a scan that never came has no capture", mod.scan(w.env(mod), top_n=5, wait_s=0.05).capture, None)
    return c.bad


def candidates() -> tuple[list[snapshot.Candidate], dict[str, str]]:
    cs = [snapshot.Candidate("cd-sol", "as-sol"), snapshot.Candidate("cd-avax", "as-avax")]
    return cs, {"cd-sol": "SOL", "cd-avax": "AVAX"}


def run(mod: types.ModuleType, w: World, **over: Any) -> Any:
    cs, symbols = candidates()
    options: dict[str, Any] = {"source_urn": SRC, "wait_s": 0.5}
    options.update(over)
    return mod.collect_ryo(w.env(mod), cs, symbols, **options)


def check_the_evidence_of_a_cycle(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    got = run(mod, w)
    asked = [(x.operation, tuple(sorted(dict(x.args).items()))) for x in w.runner.calls]
    c.eq(
        "the calls go in the order of what matters: the quick read of each candidate (in candidate order), the two market tools, then the deep reads",
        asked,
        [
            ("analyze_token", (("symbol", "AVAX"),)),
            ("analyze_token", (("symbol", "SOL"),)),
            ("market_overview", ()),
            ("monitor_market_sentiment_shift", ()),
            ("deep_analysis", (("include_perp", True), ("symbol", "AVAX"))),
            ("deep_analysis", (("include_perp", True), ("symbol", "SOL"))),
        ],
    )
    c.eq(
        "every call is the cycle's, to RYO, with no earmark, due when the wait ends",
        {(x.provider, x.caller, x.window, x.deadline) for x in w.runner.calls},
        {("ryo", "cycle", None, NOW + timedelta(seconds=0.5))},
    )
    c.eq("candidates are listed in candidate order", [cid for cid, _ in got.by_candidate], ["cd-avax", "cd-sol"])
    try:
        got.shared = ()  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("a collection can be changed after it is made")
    try:
        w.env(mod).store = None  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("an environment can be changed after it is made")
    w2 = World(root / "labelled")
    run(mod, w2, caller="mine", earmark=4)
    c.eq("a caller and an earmark are passed on", {(x.caller, x.window) for x in w2.runner.calls}, {("mine", 4)})
    c.eq("nothing failed", got.failures, ())
    ids = got.ids()
    c.eq(
        "both candidates have evidence, two observation items each",
        sorted((k, len(v)) for k, v in ids.items()),
        [("cd-avax", 2), ("cd-sol", 2)],
    )
    c.eq("the cycle has two shared observation items", len(got.shared), 2)
    items = [w.store.get(i) for i in (*ids["cd-sol"], *got.shared)]
    c.eq(
        "they are all observations of the RYO origin",
        {(i.kind.value, i.origin.text, i.source) for i in items},
        {("OBSERVATION", "RYO", "ryo")},
    )
    c.eq("tools of the candidate's items", sorted(str(w.store.get(i).tool) for i in ids["cd-sol"]), ["analyze_token", "deep_analysis"])
    sol = w.store.get(next(i for i in ids["cd-sol"] if w.store.get(i).tool == "analyze_token"))
    c.eq("a candidate's item is about the candidate's asset, resolved", sol.asset_refs, (("as-sol", "ryo:SOL", "RESOLVED"),))
    c.eq("a shared item is about no asset", {w.store.get(i).asset_refs for i in got.shared}, {()})
    kinds = sorted(str(r["body"]["kind"]) for r in got.records)  # type: ignore[index,call-overload]
    paths = sorted(str(r["body"]["path"]) for r in got.records)  # type: ignore[index,call-overload]
    c.eq(
        "the records hold what the items are: 6 observation items and 6 interpretation items (every one of the 4 tools has interpretation fields in the table)",
        (kinds.count("OBSERVATION"), kinds.count("INTERPRETATION")),
        (6, 6),
    )
    c.eq(
        "the interpretation items are of the tools that have interpretation fields, per candidate",
        [p for p in paths if p.endswith(".interpretation")],
        [
            "ryo.analyze_token.interpretation",
            "ryo.analyze_token.interpretation",
            "ryo.deep_analysis.interpretation",
            "ryo.deep_analysis.interpretation",
            "ryo.market_overview.interpretation",
            "ryo.monitor_market_sentiment_shift.interpretation",
        ],
    )
    listed = set(got.shared) | {i for v in ids.values() for i in v}
    c.eq("no interpretation item is listed for the snapshot", {w.store.get(i).kind.value for i in listed}, {"OBSERVATION"})
    c.eq("the interpretation items are in the evidence store, for lineage", len([1 for r in got.records if str(r["id"]) not in listed]), 6)
    return c.bad


def check_the_reads(mod: types.ModuleType, root: Path) -> list[str]:
    """Arm R: each candidate whose deep analysis answered has one read of RYO's verdict, pointing at that answer's interpretation item."""
    c = Case(mod)
    w = World(root / "reads")
    got = run(mod, w)
    c.eq(
        "one read per candidate, in candidate order, as RYO said it",
        [(r.candidate_id, r.symbol, r.call) for r in got.reads],
        [("cd-avax", "AVAX", "avoid"), ("cd-sol", "SOL", "avoid")],
    )
    c.eq(
        "with the plan of the answer",
        {(r.plan.entry, r.plan.stop, r.plan.target, r.plan_availability) for r in got.reads},
        {("100.5", "95.0", "110.0", "available")},
    )
    by_id = {str(r["id"]): r["body"] for r in got.records}
    for read in got.reads:
        item = by_id.get(read.evidence_id, {})
        c.eq(
            f"{read.symbol}: the read points at the deep analysis's interpretation item of its own candidate",
            (item.get("path"), item.get("kind"), [ref["asset_id"] for ref in item.get("asset_refs", [])]),
            ("ryo.deep_analysis.interpretation", "INTERPRETATION", [f"as-{read.symbol.lower()}"]),
        )
        c.eq(f"{read.symbol}: and at the capture of that answer", item.get("capture_id"), read.capture_id)

    def deep_fails_for_sol(call: Call) -> Reply:
        if call.operation == "deep_analysis" and dict(call.args)["symbol"] == "SOL":
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM", capture=False)
        return reply_for(call)

    w2 = World(root / "one-failed", deep_fails_for_sol)
    c.eq("a failed deep analysis gives no read", [r.symbol for r in run(mod, w2).reads], ["AVAX"])

    def no_verdict_word(call: Call) -> Reply:
        if call.operation != "deep_analysis":
            return reply_for(call)
        args = dict(call.args)
        data = D.DATA["deep_analysis"]()
        data["asset"]["symbol"] = args["symbol"]
        data["verdict"]["call"] = None
        return Reply(D.rest_body("deep_analysis", D.result("deep_analysis", data=data, request=args)))

    w3 = World(root / "no-word", no_verdict_word)
    third = run(mod, w3)
    c.eq("an answer whose verdict has no word gives no read, and is still evidence", (third.reads, len(third.ids()["cd-sol"])), ((), 2))
    return c.bad


def check_what_fails(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)

    def lose(tool: str, symbol: str | None) -> Callable[[Call], Reply]:
        def script(call: Call) -> Reply:
            if call.operation == tool and (symbol is None or dict(call.args).get("symbol") == symbol):
                return Reply(
                    outcome=Outcome.FAILED, reason="UPSTREAM", error=ConnectorError(ErrorClass.UPSTREAM, "RYO is down"), capture=False
                )
            return reply_for(call)

        return script

    w = World(root / "one", lose("deep_analysis", "SOL"))
    got = run(mod, w)
    ids = got.ids()
    c.eq(
        "a lost deep_analysis costs the candidate only that tool",
        sorted(str(w.store.get(i).tool) for i in ids["cd-sol"]),
        ["analyze_token"],
    )
    c.eq("the other candidate is whole", len(ids["cd-avax"]), 2)
    c.eq(
        "the failure names the candidate, the reason and the tool",
        [(f.candidate_id, f.reason.value, f.detail.startswith("deep_analysis: ")) for f in got.failures],
        [("cd-sol", "CALL_FAILED", True)],
    )
    c.eq("and RYO as the source that failed", {f.source for f in got.failures}, {"ryo"})

    w = World(root / "first-lost", lose("analyze_token", "AVAX"))
    got = run(mod, w)
    c.eq(
        "candidates are listed in candidate order even when a later read is what first gave one of them evidence",
        [cid for cid, _ in got.by_candidate],
        ["cd-avax", "cd-sol"],
    )

    w = World(root / "all", lose("analyze_token", "SOL"))
    got = run(mod, w)
    c.eq("a candidate keeps the evidence it did get", sorted(w.store.get(i).tool for i in got.ids()["cd-sol"]), ["deep_analysis"])

    def lose_both(call: Call) -> Reply:
        if dict(call.args).get("symbol") == "SOL":
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM", capture=False)
        return reply_for(call)

    w = World(root / "both", lose_both)
    got = run(mod, w)
    c.eq("a candidate with nothing is absent from the evidence, not empty", sorted(got.ids()), ["cd-avax"])
    c.eq(
        "and has a failure for each tool",
        sorted(f.detail.split(":")[0] for f in got.failures if f.candidate_id == "cd-sol"),
        ["analyze_token", "deep_analysis"],
    )

    w = World(root / "shared", lose("market_overview", None))
    got = run(mod, w)
    c.eq("a lost market tool costs the cycle only that tool", len(got.shared), 1)
    c.eq(
        "and is a failure of the cycle, not of a candidate",
        [(f.candidate_id, f.detail.split(":")[0]) for f in got.failures],
        [("cycle", "market_overview")],
    )

    def stale_wrong(call: Call) -> Reply:
        if call.operation == "analyze_token" and dict(call.args)["symbol"] == "SOL":
            data = D.analyze_token_data()
            data["asset"]["symbol"] = "AVAX"
            return Reply(D.rest_body("analyze_token", D.result("analyze_token", data=data)))
        return reply_for(call)

    w = World(root / "wrong", stale_wrong)
    got = run(mod, w)
    c.eq(
        "an answer about another token is refused and recorded as unreadable",
        [(f.candidate_id, f.reason.value, f.detail) for f in got.failures],
        [("cd-sol", "UNREADABLE", "analyze_token: RYO answered about AVAX, not SOL")],
    )

    for label, over, text in (
        (
            "simulated",
            {"data_mode": "simulated"},
            "analyze_token: RYO's data mode is 'simulated', not 'live': nothing that is not live enters a cycle",
        ),
        ("unavailable", {"status": "unavailable"}, "analyze_token: RYO says the answer is unavailable"),
    ):
        w = World(
            root / f"refuse-{label}",
            lambda call, over=over: (
                Reply(D.rest_body(call.operation, D.result(call.operation, **over)))
                if call.operation == "analyze_token"
                else reply_for(call)
            ),
        )  # type: ignore[misc]
        got = run(mod, w)
        c.eq(f"a {label} answer is refused", sorted({(f.reason.value, f.detail) for f in got.failures}), [("UNREADABLE", text)])
        c.eq(f"a {label} answer is a failure of RYO", {f.source for f in got.failures}, {"ryo"})

    w = World(root / "hang", lambda call: Reply(hang=True))
    got = run(mod, w, wait_s=0.05)
    c.eq(
        "nothing in time: every call is a timeout and nothing is listed",
        (got.ids(), got.shared, {f.reason.value for f in got.failures}),
        ({}, (), {"TIMED_OUT"}),
    )
    c.eq("six answers were awaited, six failures", len(got.failures), 6)
    return c.bad


def check_setup(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root)
    cs, symbols = candidates()
    for label, kwargs, text in (
        ("a wait that is not positive", {"wait_s": 0}, "the wait is a positive number of seconds"),
        ("a boolean wait", {"wait_s": True}, "the wait is a positive number of seconds"),
    ):
        try:
            mod.collect_ryo(w.env(mod), cs, symbols, source_urn=SRC, **kwargs)
        except mod.RyoCollectError as exc:
            c.eq(label, str(exc), text)
        else:
            c.bad.append(f"{label}: was accepted")
    try:
        mod.collect_ryo(w.env(mod), cs, {"cd-sol": "SOL"}, source_urn=SRC, wait_s=1)
    except mod.RyoCollectError as exc:
        c.eq("a candidate with no symbol", str(exc), "every candidate has the symbol RYO is asked about")
    else:
        c.bad.append("a candidate with no symbol was accepted")
    try:
        mod.scan(w.env(mod), top_n=5, wait_s=0)
    except mod.RyoCollectError as exc:
        c.eq("a scan with no time to wait", str(exc), "the wait is a positive number of seconds")
    else:
        c.bad.append("a scan with no time to wait was accepted")
    c.eq("nothing was asked of RYO by any refused collection", w.operations(), [])
    return c.bad


def check_the_budget(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root / "three")
    got = run(mod, w, budget=3)
    c.eq(
        "a budget of three asks both analyses, then the first cycle-wide tool",
        sorted(w.operations()),
        ["analyze_token", "analyze_token", "market_overview"],
    )
    c.eq(
        "what it does not cover is a failure that says so, by owner",
        [(f.candidate_id, f.reason.value, f.detail) for f in got.failures],
        [
            ("cd-avax", "CALL_FAILED", "deep_analysis: not asked: the RYO budget for this hour is spent"),
            ("cd-sol", "CALL_FAILED", "deep_analysis: not asked: the RYO budget for this hour is spent"),
            ("cycle", "CALL_FAILED", "monitor_market_sentiment_shift: not asked: the RYO budget for this hour is spent"),
        ],
    )
    c.eq("and what was asked is evidence", (sorted(got.ids()), len(got.shared)), (["cd-avax", "cd-sol"], 1))
    w = World(root / "none")
    got = run(mod, w, budget=0)
    c.eq("a budget of none asks nothing", (w.operations(), got.ids(), got.shared, len(got.failures)), ([], {}, (), 6))
    c.eq("and each of the six not asked is a failure of RYO", {f.source for f in got.failures}, {"ryo"})
    w = World(root / "enough")
    got = run(mod, w, budget=6)
    c.eq("a budget that covers everything asks everything", (len(w.operations()), got.failures), (6, ()))
    w = World(root / "unbounded")
    run(mod, w, budget=None)
    c.eq("no budget is no limit", len(w.operations()), 6)
    for label, value in (("a negative budget", -1), ("a boolean budget", True), ("a fractional budget", 1.5)):
        try:
            run(mod, World(root / f"bad-{label.split()[1]}"), budget=value)
        except mod.RyoCollectError as exc:
            c.eq(label, str(exc), "the budget is a whole number of calls, none or more")
        else:
            c.bad.append(f"{label}: was accepted")
    return c.bad


class Delayed(FakeRunner):
    """A runner whose answers come after a pause, as RYO's do."""

    def __init__(self, inner: FakeRunner, delay_s: float) -> None:
        self.inner, self.delay_s = inner, delay_s
        self.calls = inner.calls

    def submit(self, call: Call) -> Any:
        ready = self.inner.submit(call)
        late: Any = Future()
        threading.Timer(self.delay_s, lambda: late.set_result(ready.result())).start()
        return late


def check_the_wait(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    w = World(root / "late")
    w.runner = Delayed(w.runner, 0.3)
    got = run(mod, w, wait_s=5.0)
    c.eq(
        "an answer that comes after a pause is waited for",
        (got.failures, sorted(got.ids()), len(got.shared)),
        ((), ["cd-avax", "cd-sol"], 2),
    )
    w = World(root / "late-scan", lambda call: scan_body([{"rank": 1, "symbol": "AAA"}]))
    w.runner = Delayed(w.runner, 0.3)
    c.eq("and so is the scan's", mod.scan(w.env(mod), top_n=5, wait_s=5.0).rows, (ScanRow(1, "AAA"),))
    w = World(root / "bound", lambda call: Reply(hang=True))
    w.clock = SystemClock()
    started = time.monotonic()
    got = run(mod, w, wait_s=0.4)
    spent = time.monotonic() - started
    c.eq("the whole collection shares one deadline: six answers that never come end it together, not one after another", spent < 1.2, True)
    c.eq("and each is a timeout", {f.reason.value for f in got.failures}, {"TIMED_OUT"})
    return c.bad


def check_the_key_status(mod: types.ModuleType, root: Path) -> list[str]:
    """RYO's key status from ``GET /whoami``: allowed and the calls left as RYO says them; a status that cannot be read says nothing."""
    from tests.league_checks import whoami_body

    c = Case(mod)
    w = World(root / "off", lambda call: Reply(whoami_body({"call_limit": 200, "used": 0, "remaining": 200, "allowed": False})))
    got = mod.key_status(w.env(mod), wait_s=0.5)
    c.eq("one read of the key status, with no arguments", [(x.operation, dict(x.args)) for x in w.runner.calls], [("_whoami", {})])
    c.eq("on the cycle's own priority, as the cycle", (w.runner.calls[0].priority.name, w.runner.calls[0].caller), ("P0", "cycle"))
    c.eq("with a deadline the wait from now", w.runner.calls[0].deadline, w.clock.now() + timedelta(seconds=0.5))
    try:
        got.allowed = True  # type: ignore[misc]
        c.bad.append("a key status can be changed after it is read")
    except AttributeError:
        pass
    c.eq("not allowed, two hundred left, read without a failure", got, mod.KeyStatus(False, 200, ""))
    w = World(root / "free", lambda call: Reply(whoami_body(None)))
    c.eq("a key with no allowance member says nothing", mod.key_status(w.env(mod), wait_s=0.5), mod.KeyStatus(None, None, ""))
    for label, reply, says in (
        ("a failed call", Reply(outcome=Outcome.FAILED, reason="UPSTREAM", capture=False), "whoami: FAILED: UPSTREAM"),
        ("no answer in time", Reply(hang=True), "whoami: no result before the wait ended"),
        ("a page that is not JSON", Reply(b"<html>"), "whoami: RYO's response is not readable JSON (SYNTAX)"),
    ):
        w = World(root / label.replace(" ", "-"), lambda call, r=reply: r)
        c.eq(f"{label}: the status says nothing, and why", mod.key_status(w.env(mod), wait_s=0.3), mod.KeyStatus(None, None, says))
    w = World(root / "called", lambda call: Reply(whoami_body({"allowed": True})))
    mod.key_status(w.env(mod), wait_s=0.5, caller="desk")
    c.eq("the caller is the one named", w.runner.calls[0].caller, "desk")
    for bad in (0, -1, True):
        try:
            mod.key_status(w.env(mod), wait_s=bad)
            c.bad.append(f"a wait of {bad!r} was accepted")
        except mod.RyoCollectError:
            pass
    c.eq("the closed reasons", (mod.KEY_NOT_ALLOWED, mod.KEY_SPENT), ("RYO_KEY_NOT_ALLOWED", "RYO_ALLOWANCE_SPENT"))
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType, Path], list[str]]] = {
    "the_scan": check_the_scan,
    "the_evidence_of_a_cycle": check_the_evidence_of_a_cycle,
    "the_reads": check_the_reads,
    "what_fails": check_what_fails,
    "setup": check_setup,
    "the_budget": check_the_budget,
    "the_wait": check_the_wait,
    "the_key_status": check_the_key_status,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        root = Path(tempfile.mkdtemp(prefix=f"hoshitori-ryo-collect-{name}-"))
        try:
            bad += [f"{name}: {message}" for message in scenario(mod, root)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
