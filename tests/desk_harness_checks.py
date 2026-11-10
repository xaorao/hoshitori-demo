"""Checks of the recorded harness (``hoshitori.desk_harness``), runnable against a mutant.

The harness runs the Desk on recordings: bodies loaded byte for byte, a manual clock set to the moment of a recording, every record labelled
recorded, written to a throwaway ledger and never attested or scored. The checks use small recordings built here in the format of the real
ones, so they run anywhere; ``check_real_recordings`` uses the private ones and reports nothing when they are absent.
"""

from __future__ import annotations

import json
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hoshitori import desk_record
from hoshitori.clock import ManualClock
from hoshitori.ledger import Ledger
from hoshitori.timeline import to_ms
from tests import ryo_data as D
from tests.root_support import log_signer

CAPTURED = "2026-10-29T12:50:07Z"
CAPTURED_AT = datetime(2026, 10, 29, 12, 50, 7, tzinfo=UTC)
REAL_DIR = Path(__file__).resolve().parents[2] / "research" / "architecture" / "fixtures" / "ryo_live"


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
        except self.mod.HarnessError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def dump(value: Any) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def write(directory: Path, name: str, tool: str, args: dict[str, Any], body: Any, status: int = 200, sha: str | None = None) -> None:
    import hashlib

    raw = dump(body)
    record = {
        "request": {"method": "POST", "path": f"/tools/{tool}/call", "body": args, "authenticated": True},
        "response": {
            "status": status,
            "headers": {"Content-Type": "application/json", "Date": "x"},
            "body_sha256": sha or hashlib.sha256(raw).hexdigest(),
            "body_bytes": len(raw),
            "body": body,
        },
        "seconds": 1.0,
        "captured_at": CAPTURED,
        "attempts": [{"attempt": 1, "status": status, "seconds": 1.0}],
    }
    (directory / f"{name}.json").write_text(json.dumps(record))


def token_body(symbol: str, change_1h: float) -> dict[str, Any]:
    data = D.DATA["analyze_token"]()
    data["asset"]["symbol"] = symbol
    data["performance"]["change_1h_pct"] = change_1h
    return json.loads(D.rest_body("analyze_token", D.result("analyze_token", data=data, request={"symbol": symbol})))


def recordings_dir(root: Path, symbol: str = "SOL", change_1h: float = 0.30) -> Path:
    directory = root / "ryo_live"
    directory.mkdir()
    write(directory, "call_analyze_token_valid.a", "analyze_token", {"symbol": symbol}, token_body(symbol, change_1h))
    write(
        directory,
        "call_market_overview_valid.b",
        "market_overview",
        {},
        json.loads(D.rest_body("market_overview", D.result("market_overview"))),
    )
    deep = D.DATA["deep_analysis"]()
    deep["asset"]["symbol"] = symbol
    deep_body = json.loads(D.rest_body("deep_analysis", D.result("deep_analysis", data=deep, request={"symbol": symbol})))
    write(directory, "call_deep_analysis_valid.e", "deep_analysis", {"symbol": symbol, "include_perp": True}, deep_body)
    shift = json.loads(D.rest_body("monitor_market_sentiment_shift", D.result("monitor_market_sentiment_shift")))
    write(directory, "call_monitor_market_sentiment_shift_valid.f", "monitor_market_sentiment_shift", {}, shift)
    write(directory, "invalid_unknown_symbol.c", "analyze_token", {"symbol": "NOPE"}, {"error": {"code": "BAD"}}, status=400)
    (directory / "health.d.json").write_text(
        json.dumps({"request": {"method": "GET", "path": "/health"}, "response": {"status": 200, "body": {"status": "ok"}}})
    )
    return directory


def throwaway_ledger(root: Path) -> Ledger:
    signer = log_signer(7)
    return Ledger.open(root / "ledger", signer=signer, origin=signer.name, clock=ManualClock(CAPTURED_AT))


def trigger(mod: types.ModuleType, **over: Any) -> Any:
    from hoshitori import desk_plan

    values: dict[str, Any] = {
        "trigger_id": "trg-1",
        "route": "EVENT",
        "symbol": "SOL",
        "direction": "UP",
        "hours": 1,
        "event_time_ms": None,
    }
    values.update(over)
    return desk_plan.Trigger(**values)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the bindings label", mod.LABEL, "recorded")
    c.eq("the recorded asset prefix", mod.ASSET_PREFIX, "asset:recorded-")
    c.eq("the source of every record", mod.SOURCE_PREFIX, "urn:hoshitori:desk-recorded:")
    c.eq(
        "the label of invented answers and its source",
        (mod.SYNTHETIC_LABEL, mod.SYNTHETIC_SOURCE_PREFIX),
        ("synthetic", "urn:hoshitori:desk-synthetic:"),
    )
    return c.bad


def check_loading(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as raw:
        directory = recordings_dir(Path(raw))
        loaded = mod.load_ryo_recordings(directory)
        c.eq("four good recordings, the failed one and the health check left out", len(loaded.recordings), 4)
        recorded = loaded.recordings.lookup("analyze_token", {"symbol": "SOL"})
        c.eq("the body is the bytes the file's digest names", recorded.body, dump(token_body("SOL", 0.30)))
        c.eq("its status and the capture it replays", (recorded.status, recorded.capture_id), (200, "call_analyze_token_valid.a"))
        c.eq("a request with no recording has none", loaded.recordings.lookup("analyze_token", {"symbol": "ETH"}), None)
        c.eq("the moment is the recording's own", loaded.captured_at, CAPTURED_AT)
        c.eq("the recordings are frozen", mod.RyoRecordings.__dataclass_params__.frozen, True)
        c.eq("a header is kept with a lower-case name", recorded.headers, {"content-type": "application/json", "date": "x"})
        c.eq(
            "the tools it can answer, by symbol",
            (loaded.tools_for("SOL"), loaded.tools_for("ETH")),
            (
                frozenset({"analyze_token", "deep_analysis", "market_overview", "monitor_market_sentiment_shift"}),
                frozenset({"market_overview", "monitor_market_sentiment_shift"}),
            ),
        )
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw) / "ryo_live"
        directory.mkdir()
        write(directory, "call_a_token.1", "analyze_token", {"symbol": "SOL"}, token_body("SOL", 0.10))
        write(directory, "call_b_token.2", "analyze_token", {"symbol": "SOL"}, token_body("SOL", 0.20))
        write(directory, "call_c_failed.3", "analyze_token", {"symbol": "ETH"}, token_body("ETH", 0.1), status=503)
        base = json.loads((directory / "call_a_token.1.json").read_text())

        def variant(name: str, **changes: Any) -> None:
            record = json.loads(json.dumps(base))
            for key, value in changes.items():
                if "__" in key:
                    target, _, member = key.partition("__")
                    record[target][member] = value
                else:
                    record[key] = value
            (directory / f"{name}.json").write_text(json.dumps(record))

        variant("call_d_path.4", request__path="/health")
        variant("call_e_body.5", request__body=["not", "an", "object"])
        variant("call_f_later.6", captured_at="2026-10-29T13:00:00Z", request__body={"symbol": "BTC"})
        variant("call_g_earlier.7", captured_at="2026-10-29T11:00:00Z", request__body={"symbol": "AVAX"})
        many = mod.load_ryo_recordings(directory)
        c.eq(
            "only the good calls load: not the failed one, the other path, the body that is not an object (three requests, one recorded twice)",
            len(many.recordings),
            3,
        )
        c.eq(
            "of two recordings of one request the later file name wins",
            many.recordings.lookup("analyze_token", {"symbol": "SOL"}).capture_id,
            "call_b_token.2",
        )
        c.eq("and the moment is the latest of all of them", many.captured_at, datetime(2026, 10, 29, 13, 0, 0, tzinfo=UTC))
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw) / "ryo_live"
        directory.mkdir()
        write(directory, "call_analyze_token_valid.a", "analyze_token", {"symbol": "SOL"}, token_body("SOL", 0.3), sha="0" * 64)
        c.refuses("a body that no longer reproduces its digest", lambda: mod.load_ryo_recordings(directory), "no longer reproduces")
    with tempfile.TemporaryDirectory() as raw:
        c.refuses("a folder that is not there", lambda: mod.load_ryo_recordings(Path(raw) / "none"), "no recordings")
        empty = Path(raw) / "empty"
        empty.mkdir()
        c.refuses("a folder with nothing in it", lambda: mod.load_ryo_recordings(empty), "no recordings")
    return c.bad


def check_run(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        loaded = mod.load_ryo_recordings(recordings_dir(root, change_1h=0.30))
        ledger = throwaway_ledger(root)
        try:
            out = mod.run_recorded(loaded, ledger, trigger(mod), "run-rec-1")
            c.eq("a rise the recording shows is supported and the run ends", (out.state, out.reason), ("COMMITTED", None))
            written = [json.loads(ledger.read_record(seq)) for seq in range(ledger.size)]
            c.eq(
                "every record is in the throwaway ledger, in order",
                [r["type"] for r in written],
                ["desk_run_state", "hypothesis", "desk_check", "falsification", "decision", "desk_run_state"],
            )
            c.eq("the ledger holds as many as the run made", ledger.size, len(out.records))
            c.eq(
                "each reads back as the Desk's own record",
                [desk_record.from_record({k: v for k, v in r.items() if k != "committed_at"})[0] for r in written],
                [r["type"] for r in written],
            )
            states = [r["body"] for r in written if r["type"] == "desk_run_state"]
            c.eq(
                "every state says recorded, at the recording's own moment",
                ({s["label"] for s in states}, {s["at_ms"] for s in states}),
                ({"recorded"}, {to_ms(CAPTURED_AT)}),
            )
            c.eq("the source of the records", {r["source"] for r in written}, {"urn:hoshitori:desk-recorded:run-rec-1"})
            check = next(r["body"] for r in written if r["type"] == "desk_check")
            c.eq(
                "the check was RYO's own hour from the recording",
                (check["check_type"], check["status_after"], check["outcome"]),
                ("RYO_TOKEN_READ", "SUPPORTED", "OK"),
            )
            decision = next(r["body"] for r in written if r["type"] == "decision")
            c.eq(
                "the harness proposes nothing: a flat commitment",
                (decision["final_action"]["stance"], decision["final_action"]["weight"]),
                ("FLAT", "0.000000"),
            )
            c.eq(
                "its proposal was flat at one half, with no weight and no cites",
                decision["proposal"],
                {"stance": "FLAT", "p": "0.5", "weight": "0", "cites": [], "no_call_reason": None},
            )
            c.eq(
                "it spent no model tokens and one call to RYO, from a budget of ten calls, an hour and a token",
                (states[-1]["spent"], states[0]["budget"]),
                ({"calls": {"ryo": 1}, "seconds": 0, "tokens": 0}, {"calls": {"ryo": 10}, "seconds": 3600, "tokens": 1}),
            )
            c.eq("and names the id a recorded run gives its commitment", states[-1]["forecast_id"], "recorded-run-rec-1")
            c.eq(
                "no attestation: nothing in the ledger is anything but the Desk's records",
                {r["type"] for r in written} <= set(desk_record.TYPES),
                True,
            )
        finally:
            ledger.close() if hasattr(ledger, "close") else None
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        loaded = mod.load_ryo_recordings(recordings_dir(root, change_1h=0.30))
        ledger = throwaway_ledger(root)
        out = mod.run_recorded(loaded, ledger, trigger(mod), "run-syn-1", "synthetic")
        written = [json.loads(ledger.read_record(seq)) for seq in range(ledger.size)]
        states = [r["body"] for r in written if r["type"] == "desk_run_state"]
        c.eq("a run labelled synthetic ends as the same run does", (out.state, out.reason), ("COMMITTED", None))
        c.eq("every state says synthetic", {s["label"] for s in states}, {"synthetic"})
        c.eq("and the source says so", {r["source"] for r in written}, {"urn:hoshitori:desk-synthetic:run-syn-1"})
        c.eq("and the commitment it names", states[-1]["forecast_id"], "synthetic-run-syn-1")
        c.refuses(
            "a label other than the two",
            lambda: mod.run_recorded(loaded, ledger, trigger(mod), "run-x", "live"),
            "labelled recorded or synthetic",
        )
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        loaded = mod.load_ryo_recordings(recordings_dir(root, change_1h=0.10))
        ledger = throwaway_ledger(root)
        out = mod.run_recorded(loaded, ledger, trigger(mod), "run-rec-2")
        c.eq("a rise the recording does not show is an abstention", (out.state, out.reason), ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"))
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        loaded = mod.load_ryo_recordings(recordings_dir(root, symbol="SOL"))
        ledger = throwaway_ledger(root)
        out = mod.run_recorded(loaded, ledger, trigger(mod, symbol="ETH"), "run-rec-3")
        c.eq(
            "a token with no recording of its own: nothing can check the part, so it is unproven",
            (out.state, out.reason),
            ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"),
        )
        c.eq("and the end is written", [json.loads(ledger.read_record(i))["type"] for i in range(ledger.size)][-1], "desk_run_state")
    return c.bad


def check_runner(mod: types.ModuleType) -> list[str]:
    from hoshitori.capture import CaptureStore
    from hoshitori.gateway import Call, Outcome
    from hoshitori.governor import Priority
    from hoshitori.ryo_transport import RecordedTransport

    c = Case(mod)
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        loaded = mod.load_ryo_recordings(recordings_dir(root))
        clock = ManualClock(CAPTURED_AT)
        captures = CaptureStore(root / "caps")
        runner = mod._RecordedRunner(RecordedTransport(loaded.recordings, clock.now), captures, clock)

        def call(tool: str, **args: Any) -> Any:
            return Call("ryo", tool, args, Priority.P2, CAPTURED_AT, "desk:test")

        first = runner.submit(call("analyze_token", symbol="SOL")).result()
        second = runner.submit(call("market_overview")).result()
        c.eq(
            "a call is answered from the recording",
            (first.outcome, second.outcome, first.attempts, first.error, first.reason),
            (Outcome.OK, Outcome.OK, 1, None, ""),
        )
        c.eq(
            "calls and captures are numbered from one",
            (first.call_id, second.call_id, first.capture_id, second.capture_id),
            (1, 2, f"cap-{1:032x}", f"cap-{2:032x}"),
        )
        replayed = captures.get(first.capture_id).request
        c.eq(
            "the capture is a replay of the file it came from",
            (replayed["mode"], replayed["recorded_from"]),
            ("recorded", "call_analyze_token_valid.a"),
        )
        missing = runner.submit(call("analyze_token", symbol="ETH")).result()
        c.eq(
            "a request with no recording fails and is not worth trying again",
            (missing.outcome, missing.capture_id, missing.attempts, missing.reason, missing.error.error_class.value),
            (Outcome.FAILED, None, 1, "NOT_RETRYABLE", "NOT_FOUND"),
        )
        c.eq("a failed call is still numbered", missing.call_id, 3)
        c.eq("and the time of every result is the clock's", {first.finished_at, second.finished_at, missing.finished_at}, {CAPTURED_AT})
    return c.bad


def check_menu(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as raw:
        loaded = mod.load_ryo_recordings(recordings_dir(Path(raw)))
        menu = mod.menu_for("SOL", loaded)
        c.eq(
            "the menu has the RYO checks the recordings can answer and no exchange",
            sorted(x.check_id for x in menu.checks),
            [
                "MARKET_CONTEXT_OVERVIEW:ryo:market_overview",
                "MARKET_SENTIMENT_SHIFT:ryo:monitor_market_sentiment_shift",
                "RYO_DEEP_ANALYSIS:ryo:deep_analysis",
                "RYO_TOKEN_READ:ryo:analyze_token",
            ],
        )
        c.eq("the asset is a recorded one", (menu.asset_id, menu.symbol), ("asset:recorded-sol", "SOL"))
    return c.bad


def check_real_recordings(mod: types.ModuleType) -> list[str]:
    """Nothing is checked, and nothing is claimed, when the private recordings are not on this machine."""
    c = Case(mod)
    if not any(REAL_DIR.glob("call_analyze_token_valid*.json")):
        return c.bad
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        loaded = mod.load_ryo_recordings(REAL_DIR)
        c.eq("the real recordings load", len(loaded.recordings) >= 4, True)
        ledger = throwaway_ledger(root)
        out = mod.run_recorded(loaded, ledger, trigger(mod), "run-real-1")
        c.eq(
            "on the real SOL recording (+0.10 percent in the hour) a rise is not shown",
            (out.state, out.reason),
            ("ABSTAINED", "ESSENTIAL_PREMISE_UNPROVEN"),
        )
        states = [b for k, b in out.records if k == "desk_run_state"]
        c.eq("labelled recorded", {s["label"] for s in states}, {"recorded"})
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_loading, check_runner, check_menu, check_run, check_real_recordings):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
