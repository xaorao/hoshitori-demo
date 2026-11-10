"""Checks of restart and replay of Desk runs (``hoshitori.desk_recovery``), runnable against a mutant.

A process that dies leaves a run open. On restart every open run is closed as failed (never resumed, so no check, call or record is
repeated), unless its forecast is already in the ledger, in which case it is closed as committed. A replay runs the same steps again on the
stored answers and must give the same records; a different version of the code is refused, because the same answers would not mean the same.
"""

from __future__ import annotations

import copy
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import desk, desk_record
from tests.desk_checks import H1, H2, H4, World

NOW_MS = 1793275999000


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
        except self.mod.RecoveryError as exc:
            if says not in str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to contain {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def finished_run() -> tuple[World, Any]:
    w = World(desk)
    out = w.run()
    return w, out


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("terminal states", mod.TERMINAL, ("COMMITTED", "ABSTAINED", "FAILED", "FAILED_FINAL"))
    c.eq("the closing word", (mod.CLOSING_STATE, mod.CLOSING_REASON), ("FAILED_FINAL", "RUN_INTERRUPTED"))
    c.eq("frozen", mod.Replayed.__dataclass_params__.frozen, True)
    return c.bad


def check_open_runs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, _ = finished_run()
    c.eq("a finished run is not open", mod.open_runs(w.records), {})
    cut = w.records[:2]  # RUNNING and the hypothesis
    c.eq(
        "a run killed after its hypothesis is open, at the state it was last in",
        {k: v["state"] for k, v in mod.open_runs(cut).items()},
        {"run-1": "RUNNING"},
    )
    stopped = World(desk)
    stopped.run(budget=desk.Budget({}, 600, 20000))
    states = [r for r in stopped.records if r[0] == "desk_run_state"]
    c.eq(
        "a run cut after it stopped is open at STOPPED", {k: v["state"] for k, v in mod.open_runs(states[:2]).items()}, {"run-1": "STOPPED"}
    )
    for kind_state in ("ABSTAINED", "FAILED", "FAILED_FINAL", "COMMITTED"):
        body = copy.deepcopy(w.records[0][1])
        body["state"] = kind_state
        body["reason"] = {"ABSTAINED": "OUT_OF_SCOPE", "FAILED": "FAILED", "FAILED_FINAL": "RUN_INTERRUPTED", "COMMITTED": None}[kind_state]
        body["forecast_id"] = "fc-1" if kind_state == "COMMITTED" else None
        c.eq(f"a run in {kind_state} is closed", mod.open_runs([("desk_run_state", w.records[0][1]), ("desk_run_state", body)]), {})
    other = copy.deepcopy(w.records[0][1])
    other["run_id"] = "run-2"
    c.eq("two runs, one open", sorted(mod.open_runs([*cut, ("desk_run_state", other), *w.records])), ["run-2"])
    c.eq("records of other kinds alone open nothing", mod.open_runs([r for r in w.records if r[0] != "desk_run_state"]), {})
    c.eq("no records", mod.open_runs([]), {})
    return c.bad


def check_closing(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, _ = finished_run()
    cut = w.records[:3]
    closed = mod.close_lost_runs(cut, NOW_MS)
    c.eq(
        "one closing record, a run state",
        [(k, b["state"], b["reason"]) for k, b in closed],
        [("desk_run_state", "FAILED_FINAL", "RUN_INTERRUPTED")],
    )
    body = closed[0][1]
    first = w.records[0][1]
    c.eq(
        "it says what the run was",
        {
            k: body[k]
            for k in (
                "run_id",
                "label",
                "asset_id",
                "symbol",
                "discovery_route",
                "trigger_id",
                "menu_sha256",
                "agent_version_hash",
                "code_version",
                "protocol_hash",
                "budget",
            )
        },
        {
            k: first[k]
            for k in (
                "run_id",
                "label",
                "asset_id",
                "symbol",
                "discovery_route",
                "trigger_id",
                "menu_sha256",
                "agent_version_hash",
                "code_version",
                "protocol_hash",
                "budget",
            )
        },
    )
    c.eq("at the time of the closing, with no forecast", (body["at_ms"], body["forecast_id"]), (NOW_MS, None))
    try:
        desk_record.check_body("desk_run_state", body)
    except desk_record.DeskRecordError as exc:
        c.bad.append(f"the closing record is refused: {exc}")
    c.eq("closing twice is closing once", mod.close_lost_runs([*cut, *closed], NOW_MS + 1), [])
    c.eq("the same closing whenever it is made", mod.close_lost_runs(cut, NOW_MS + 5000)[0][1]["state"], "FAILED_FINAL")
    c.eq(
        "the same id whenever it is made",
        desk_record.record_id("desk_run_state", mod.close_lost_runs(cut, NOW_MS + 5000)[0][1]),
        desk_record.record_id("desk_run_state", body),
    )
    # spent so far is what the last state said
    spent = copy.deepcopy(cut)
    spent[0][1]["spent"] = {"calls": {"ryo": 2}, "seconds": 7, "tokens": 9}
    c.eq(
        "what the run had spent is kept",
        mod.close_lost_runs(spent, NOW_MS)[0][1]["spent"],
        {"calls": {"ryo": 2}, "seconds": 7, "tokens": 9},
    )
    # a run whose forecast was already committed to the ledger before the process died
    done = mod.close_lost_runs(cut, NOW_MS, {"run-1": "fc-run-1"})
    c.eq(
        "closed as committed with its forecast",
        [(b["state"], b["reason"], b["forecast_id"]) for _, b in done],
        [("COMMITTED", None, "fc-run-1")],
    )
    c.eq("a forecast of another run changes nothing", mod.close_lost_runs(cut, NOW_MS, {"run-9": "fc-9"})[0][1]["state"], "FAILED_FINAL")
    # many runs, in order
    runs = []
    for name in ("run-b", "run-a"):
        b = copy.deepcopy(w.records[0][1])
        b["run_id"] = name
        runs.append(("desk_run_state", b))
    c.eq(
        "several open runs are closed in the order of their ids",
        [b["run_id"] for _, b in mod.close_lost_runs(runs, NOW_MS)],
        ["run-a", "run-b"],
    )
    c.eq("a restart that finds nothing open does nothing", mod.close_lost_runs(w.records, NOW_MS), [])
    c.eq("the first instant is a time", mod.close_lost_runs(cut, 0)[0][1]["at_ms"], 0)
    broken = copy.deepcopy(cut)
    broken[0][1]["spent"] = {"calls": {"ryo": -1}, "seconds": 0, "tokens": 0}
    try:
        mod.close_lost_runs(broken, NOW_MS)
    except desk_record.DeskRecordError:
        pass
    else:
        c.bad.append("a closing record that the schema refuses was made")
    return c.bad


def check_malformed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, _ = finished_run()
    bad = copy.deepcopy(w.records[:1])
    bad[0][1]["state"] = "SLEEPING"
    c.refuses("a state that does not exist", lambda: mod.open_runs(bad), "state")
    bad = copy.deepcopy(w.records[:1])
    del bad[0][1]["run_id"]
    c.refuses("a state record with no run id", lambda: mod.open_runs(bad), "run_id")
    bad = copy.deepcopy(w.records[:1])
    bad[0][1]["run_id"] = 5
    c.refuses("a run id that is not text", lambda: mod.open_runs(bad), "run_id")
    c.refuses("a time that is not a number", lambda: mod.close_lost_runs(w.records[:1], "now"), "now_ms")
    c.refuses("a negative time", lambda: mod.close_lost_runs(w.records[:1], -1), "now_ms")
    c.refuses("a boolean time", lambda: mod.close_lost_runs(w.records[:1], True), "now_ms")
    return c.bad


def recorded(world: World) -> dict[str, list[Any]]:
    return {"acquire": [], "history": [], "propose": []}


def tape_ports(world: World, tape: dict[str, list[Any]], record: bool) -> Any:
    """The world's ports, either recording every answer on a tape or serving the tape back."""
    real = world.ports()
    cursor = {"acquire": 0, "history": 0, "propose": 0}

    def serve(name: str, real_call: Callable[..., Any]) -> Callable[..., Any]:
        def call(*args: Any) -> Any:
            if record:
                answer = real_call(*args)
                tape[name].append(answer)
                return answer
            answer = tape[name][cursor[name]]
            cursor[name] += 1
            return answer

        return call

    from dataclasses import replace

    if record:
        tape["store"] = [world.store]
    return replace(
        real,
        store=tape["store"][0],
        acquire=serve("acquire", real.acquire),
        history=serve("history", real.history),
        propose=serve("propose", real.propose),
    )


def check_replay(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    tape: dict[str, list[Any]] = {"acquire": [], "history": [], "propose": [], "store": []}
    w = World(desk)
    first = desk.run(
        tape_ports(w, tape, True),
        w.trigger(),
        w.menu(),
        desk.Budget({"binance-spot": 4, "kraken": 4, "binance-funding": 4, "ryo": 4}, 600, 20000),
        "run-1",
        desk.Binding("live"),
        desk.Versions(H1, H2, H4),
    )
    stored = list(first.records)
    code = stored[0][1]["code_version"]

    def replay(
        records: Any = None,
        trigger: Any = None,
        menu: Any = None,
        version: str | None = None,
        mutate: Callable[[World], None] | None = None,
    ) -> Any:
        again = World(desk)
        if mutate is not None:
            mutate(again)
        return mod.replay_run(
            records if records is not None else stored,
            trigger or again.trigger(),
            menu or again.menu(),
            tape_ports(again, tape, False),
            version or code,
        )

    r = replay()
    c.eq("the same answers give the same records", (r.equal, r.differences), (True, ()))
    c.eq("and the produced records are returned", len(r.produced), len(stored))
    c.eq("a replay at another time is the same replay", replay().equal, True)
    c.eq("another version of the code is refused", _refused(mod, lambda: replay(version="f" * 64)), "version")
    c.eq(
        "another menu is refused",
        _refused(mod, lambda: replay(menu=World(desk).menu("BTC").__class__(**{**World(desk).menu().__dict__, "sha256": "e" * 64}))),
        "menu",
    )
    c.eq("a trigger of another symbol is refused", _refused(mod, lambda: replay(trigger=World(desk).trigger(symbol="ETH"))), "trigger")
    c.eq("a trigger of another route is refused", _refused(mod, lambda: replay(trigger=World(desk).trigger(route="WATCHLIST"))), "trigger")
    c.eq(
        "a trigger whose hypothesis differs is refused",
        _refused(mod, lambda: replay(trigger=World(desk).trigger(direction="DOWN"))),
        "trigger",
    )
    c.eq("a trigger of another id is refused", _refused(mod, lambda: replay(trigger=World(desk).trigger(trigger_id="trg-9"))), "trigger")
    c.eq("records with no first state are refused", _refused(mod, lambda: replay(records=stored[1:])), "first")
    c.eq("no records are refused", _refused(mod, lambda: replay(records=[])), "first")
    # a stored record that differs from what the run now produces is named
    changed = copy.deepcopy(stored)
    changed[2][1]["status_after"] = "CONTRADICTED"
    r = replay(records=changed)
    c.eq(
        "a stored record that differs is named by its place and member", (r.equal, r.differences), (False, ("desk_check#2: status_after",))
    )
    short = stored[:-1]
    r = replay(records=short)
    c.eq(
        "a stored run that ends early is a difference",
        (r.equal, [d for d in r.differences if d.startswith("records")]),
        (False, ["records: 7 produced, 6 stored"]),
    )
    longer = [*stored, stored[-1]]
    r = replay(records=longer)
    c.eq(
        "and so is one that goes on",
        (r.equal, [d for d in r.differences if d.startswith("records")]),
        (False, ["records: 7 produced, 8 stored"]),
    )
    many = copy.deepcopy(stored)
    many[2][1].update(
        {
            "node_id": "x",
            "check_id": "A:b:c",
            "layer": "NONE",
            "status_after": "UNCHECKED",
            "status_before": "SUPPORTED",
            "provider": "other",
        }
    )
    r = replay(records=many)
    c.eq(
        "members that differ are listed in order",
        r.differences,
        tuple(f"desk_check#2: {m}" for m in ("check_id", "layer", "node_id", "provider", "status_after", "status_before")),
    )
    swapped = copy.deepcopy(stored)
    swapped[3], swapped[4] = swapped[4], swapped[3]
    r = replay(records=swapped)
    c.eq("a stored record of another kind is named by the kind it has", r.differences, ("falsification#3: kind", "desk_check#4: kind"))
    timed = copy.deepcopy(stored)
    timed[0][1]["at_ms"] += 5000
    timed[-1][1]["at_ms"] += 9000
    c.eq("times are not compared", replay(records=timed).equal, True)
    # a different model answer
    other = {
        "store": tape["store"],
        "acquire": list(tape["acquire"]),
        "history": list(tape["history"]),
        "propose": [
            desk.Proposed(
                desk.desk_gates.Proposal("LONG", Decimal("0.55"), Decimal("0.01")),
                tape["propose"][0].cites,
                tape["propose"][0].model_outputs_digest,
                500,
            )
        ],
    }
    again = World(desk)
    r = mod.replay_run(stored, again.trigger(), again.menu(), tape_ports(again, other, False), code)
    c.eq(
        "a different model answer is a difference, and the decision record is where it shows",
        (r.equal, any(d.startswith("decision#5") for d in r.differences)),
        (False, True),
    )
    return c.bad


def _refused(mod: types.ModuleType, call: Callable[[], object]) -> str | None:
    try:
        call()
    except mod.RecoveryError as exc:
        text = str(exc)
        for word in ("version", "menu", "trigger", "first"):
            if word in text:
                return word
        return text
    return None


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_open_runs, check_closing, check_malformed, check_replay):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
