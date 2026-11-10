#!/usr/bin/env python3
"""Run the failure drills and write them as ``drill.json`` for the Drill page.

Each drill runs the real league code (``hoshitori.league``) on the scripted world of the tests (a registry, a scripted exchange, a real
ledger, a scripted model) with one thing broken, reads what the cycle did from its result and its ledger, and compares it with what the
league is meant to do. Nothing here touches a live service or a key. Usage: python scripts/make_drill.py OUT.json
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the drills use the scripted world of the tests, a package of this folder

import contextlib
import importlib
import json
import os
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any

from hoshitori import arm_l1, deployment, drill, labels, league, scheduler, view_models
from hoshitori.gateway import Outcome
from hoshitori.timeline import from_ms

# the scripted world of the tests, imported by name so that the type checker does not follow it into the tests
models_world = importlib.import_module("tests.league_models_checks")
league_checks = importlib.import_module("tests.league_checks")
commit_checks = importlib.import_module("tests.commit_checks")
Reply = importlib.import_module("tests.collect_checks").Reply
World = league_checks.World
NOW = league_checks.NOW
klines = league_checks.klines
time_reply = league_checks.time_reply
ManualClock = commit_checks.ManualClock
scheduler_checks = importlib.import_module("tests.scheduler_checks")
recovery_checks = importlib.import_module("tests.recovery_checks")
deployment_checks = importlib.import_module("tests.deployment_checks")
e2e_league = importlib.import_module("tests.e2e_league")
ryo_fake_server = importlib.import_module("tests.ryo_fake_server")

PAUSE_WORDS = "The exchange asked for a two-second pause; the league waited and asked again once it was over"
FATE_WORDS = {
    "FORECAST": "Open",
    "VOID_GAP": "Void",
    "NO_CALL": "Abstained",
    "FAILED_EVIDENCE": "Failed",
    "FAILED": "Failed",
    "FAILED_LATE": "Too late",
}


def observe(w: Any, out: Any) -> dict[str, Any]:
    """What a cycle did, in the words of the interface: its state, the commitment level, its slots by state, the reasons, the forecasts."""
    counts = Counter(FATE_WORDS[s.fate.value] for s in out.slots)
    reasons: set[str] = set()
    for s in out.slots:
        if s.reason:
            code = s.reason.split(": ")[-1]
            reasons.add(labels.REASON_WORDS.get(code, labels.OTHER_REASON))
    if out.reason:
        reasons.add(labels.REASON_WORDS.get(out.reason, labels.OTHER_REASON))
    for settled in w.records("settlement"):  # a slot voided at once says why in its settlement, not in the slot
        if settled["body"]["reason"]:
            reasons.add(labels.REASON_WORDS.get(settled["body"]["reason"], labels.OTHER_REASON))
    levels = sorted({s.level for s in out.slots if s.level})
    return {
        "cycle": out.state.value.title(),
        "level": ", ".join(levels) if levels else None,
        "slots": [[state, counts[state]] for state in labels.ROW_STATES if counts[state]],
        "reasons": sorted(reasons),
        "forecasts": len(w.records("forecast")),
    }


def exchange_fails_one_token(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    def script(call: Any, world: Any) -> Any:
        if call.operation == "candles" and call.args["instrument"] == "ETHUSDT":
            return Reply(outcome=Outcome.FAILED, reason="UPSTREAM")
        return world.default(call)

    w = World(root, script=script)
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 3], ["Failed", 3]],
            "reasons": ["No evidence could be built for the token"],
            "forecasts": 3,
        },
        "exchange-fails-one-token",
        "An exchange fails for one token",
        "The price data for one of two tokens could not be fetched, while the other came through.",
        "The token with no evidence is a failure, never a made-up call; "
        "the other token is forecast as usual; the cycle is still committed.",
    )


def exchange_clock_is_off(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    w = World(root, script=lambda call, world: Reply(time_reply(world.clock, 60) if call.operation == "time" else klines(call.args)))
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {"cycle": "Abandoned", "level": None, "slots": [["Failed", 6]], "reasons": ["The exchange's clock was off"], "forecasts": 0},
        "exchange-clock-off",
        "An exchange's clock is a minute ahead",
        "The exchange reported a time a minute later than ours.",
        "No forecast is made on prices whose time cannot be trusted: "
        "the cycle is abandoned and every slot is recorded as failed with the reason.",
    )


def collecting_is_slow(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    def slow(call: Any, world: Any) -> Any:
        if call.operation == "candles":
            world.clock.advance(timedelta(minutes=11))
        return world.default(call)

    w = World(root, script=slow)
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {
            "cycle": "Abandoned",
            "level": None,
            "slots": [["Failed", 6]],
            "reasons": ["Collecting the evidence took too long"],
            "forecasts": 0,
        },
        "collecting-is-slow",
        "Collecting the evidence takes eleven minutes",
        "Every price request took so long that the evidence was eleven minutes old before it was frozen.",
        "A cycle never runs on evidence that old: it is abandoned, and its slots are recorded as failed.",
    )


def evidence_is_too_old(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    w = World(
        root,
        clock=commit_checks.TickClock(NOW, 0.01),
        protocol={"snapshot_freshness_ms": 1},
        script=lambda call, world: Reply(time_reply(ManualClock(NOW)) if call.operation == "time" else klines(call.args)),
    )
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {
            "cycle": "Abandoned",
            "level": None,
            "slots": [["Failed", 6]],
            "reasons": ["The evidence was too old when it was frozen"],
            "forecasts": 0,
        },
        "evidence-too-old",
        "The evidence is older than the protocol allows",
        "The protocol allows a snapshot one millisecond old and the clock moved faster than that.",
        "The snapshot is refused and the cycle abandoned; nothing is committed on stale evidence.",
    )


def stamps_are_late(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    w = World(root, attested=[("a", "ta", 100)])
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {"cycle": "Committed", "level": "C1", "slots": [["Open", 6]], "reasons": [], "forecasts": 6},
        "stamps-late",
        "The time-stamp authorities answer too late",
        "The only time-stamp that arrived was stamped long after the forecasts were committed, so it cannot count.",
        "The forecasts are kept, recorded by us only: a late time-stamp never raises the level that a forecast is counted at.",
    )


def ledger_clock_runs_late(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    w = World(root, ledger_clock=ManualClock(NOW + timedelta(minutes=7)))
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 4], ["Void", 2]],
            "reasons": ["Too long between the evidence and the start"],
            "forecasts": 6,
        },
        "start-too-late",
        "The cycle commits seven minutes after its evidence",
        "The commitment came seven minutes after the snapshot, past the six-minute limit of the one-hour horizon.",
        "The one-hour calls are voided and say why; the four-hour and 24-hour calls, whose limit is longer, stand.",
    )


def model_answers_late(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    w = models_world.world(root, watchlist=("BTC",))
    model = models_world.Model(w)
    model.late[(arm_l1.STAGE, 1)] = 241
    got = observe(w, w.run(league, models=models_world.models(model)))
    w.close()
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 7], ["Too late", 1]],
            "reasons": ["The answer came after the deadline"],
            "forecasts": 7,
        },
        "model-late",
        "The model answers after its deadline",
        "The single-model agent's one-hour answer arrived 41 seconds after its 200-second limit.",
        "The late answer is thrown away and the slot is marked too late; "
        "the other agents and horizons are unaffected and the cycle is committed.",
    )


def model_is_down(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    w = models_world.world(root, watchlist=("BTC",))
    model = models_world.Model(w)
    model.gone.add((arm_l1.STAGE, 4))
    got = observe(w, w.run(league, models=models_world.models(model)))
    w.close()
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 6], ["Failed", 2]],
            "reasons": ["The model did not answer in time"],
            "forecasts": 6,
        },
        "model-down",
        "The model does not answer for one horizon",
        "Every request of the single-model agent for its four-hour judgment went unanswered; "
        "the matched control asks the same question, so it went unanswered too.",
        "The two slots that needed that answer are failed with the reason, not guessed; "
        "every other slot stands and the cycle is committed.",
    )


def process_killed_after_the_snapshot(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """Three hourly slots of one league. The first commits. The second is killed after its evidence is frozen and before its forecasts are
    submitted (nothing catches the kill, as with a lost process). A new process runs the tick again: it must close the lost cycle, leave the
    first cycle's forecasts alone, and run the third slot on time. What is reported is the whole ledger afterwards."""
    sc, lc = scheduler_checks, league_checks
    w = sc.world(root)
    cfg = sc.config(scheduler, root)
    services = w.services(league)
    w.clock.set(from_ms(lc.SLOT + 20_000))
    levels = {scheduler.tick(services, cfg).ran.commitment.level}  # type: ignore[union-attr]
    real = w.ledger.submit_many

    def killed_at_the_batch(records: Any) -> Any:
        if any(r.get("type") == "snapshot" for r in records):  # the batch is written as one group: nothing of it reaches the ledger
            raise recovery_checks.Died
        return real(records)

    w.ledger.submit_many = killed_at_the_batch
    w.clock.set(from_ms(lc.SLOT + lc.HOUR + 20_000))
    with contextlib.suppress(recovery_checks.Died):
        scheduler.tick(services, cfg)
    w.ledger.submit_many = real
    w.clock.set(from_ms(lc.SLOT + lc.HOUR + 15 * 60_000))  # the new process: it finds the lost cycle and closes it
    scheduler.tick(services, cfg)
    w.clock.set(from_ms(lc.SLOT + 2 * lc.HOUR + 20_000))  # and the next slot, on time
    levels.add(scheduler.tick(services, cfg).ran.commitment.level)  # type: ignore[union-attr]
    cycles, slots = lc.histories(w)
    last = max(cycles.values(), key=lambda h: h[0].step.at_ms)
    counts = Counter(view_models.row_state(h[-1].step.state) for h in slots.values())
    reasons = {labels.REASON_WORDS.get(h[-1].step.reason, labels.OTHER_REASON) for h in cycles.values() if h[-1].step.reason}
    got = {
        "cycle": last[-1].step.state.value.title(),
        "level": ", ".join(sorted(levels)),
        "slots": [[state, counts[state]] for state in labels.ROW_STATES if counts[state]],
        "reasons": sorted(reasons),
        "forecasts": len(w.records("forecast")),
    }
    w.close()
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 12], ["Failed", 6]],
            "reasons": ["The process was lost during the cycle"],
            "forecasts": 12,
        },
        "process-killed",
        "The process is killed after the evidence is frozen",
        "A cycle's process was killed after it froze its evidence and before it wrote any forecast; then a new process started.",
        "The lost cycle is closed and its six slots are recorded as failed, never as forecasts; "
        "the earlier cycle's forecasts are untouched; the next slot runs on time and commits.",
    )


def process_killed_before_the_batch_is_written(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """Three hourly slots of one league. The first commits. The second is killed after its whole batch was acknowledged (journaled and
    queued) and before any of it was appended. A new process runs the tick again: the batch is whole in the queue, so it is appended and the
    cycle is finished from the ledger alone, at the level the records can show; each slot's forecast is recorded, and a slot whose start
    came too long after its evidence is void for that reason. The third slot runs on time. What is reported is the whole ledger
    afterwards."""
    sc, lc = scheduler_checks, league_checks
    w = sc.world(root)
    cfg = sc.config(scheduler, root)
    services = w.services(league)
    w.clock.set(from_ms(lc.SLOT + 20_000))
    scheduler.tick(services, cfg)
    real = w.ledger.append_pending
    sc.die_before_batch_append(w)
    w.clock.set(from_ms(lc.SLOT + lc.HOUR + 20_000))
    with contextlib.suppress(recovery_checks.Died):
        scheduler.tick(services, cfg)
    w.ledger.append_pending = real
    w.clock.set(from_ms(lc.SLOT + lc.HOUR + 15 * 60_000))  # the new process: it finds the queued batch and finishes the cycle
    scheduler.tick(services, cfg)
    w.clock.set(from_ms(lc.SLOT + 2 * lc.HOUR + 20_000))  # and the next slot, on time
    scheduler.tick(services, cfg)
    cycles, slots = lc.histories(w)
    last = max(cycles.values(), key=lambda h: h[0].step.at_ms)
    counts = Counter(view_models.row_state(h[-1].step.state) for h in slots.values())
    steps = [t.step for h in slots.values() for t in h]
    levels = {s.level.value for s in steps if s.state.value == "COMMITTED" and s.level is not None}
    reasons = {labels.REASON_WORDS.get(s.reason, labels.OTHER_REASON) for s in steps if s.reason}
    got = {
        "cycle": last[-1].step.state.value.title(),
        "level": ", ".join(sorted(levels)),
        "slots": [[state, counts[state]] for state in labels.ROW_STATES if counts[state]],
        "reasons": sorted(reasons),
        "forecasts": len(w.records("forecast")),
    }
    w.close()
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C1, C3",
            "slots": [["Open", 12], ["Void", 6]],
            "reasons": ["Too long between the evidence and the start"],
            "forecasts": 18,
        },
        "process-killed-after-the-batch",
        "The process is killed after the batch was acknowledged and before it was written",
        "A cycle's process was killed after its whole batch (the evidence, the forecasts and the commitment step) was acknowledged "
        "and queued, "
        "and before any of it reached the ledger; then a new process started.",
        "The new process appends the queued batch and finishes the cycle from the ledger alone, at the level the records show (not the "
        "independent time-stamps, which were never asked for because the kill came before the batch was written); the six forecasts "
        "are recorded and are void for the lateness of their start; the earlier cycle is untouched and the next slot runs on time and "
        "commits.",
    )


def observe_tick(running: Any, ran: Any, reasons: set[str]) -> dict[str, Any]:
    """What a cycle of an assembled league did, in the words of the interface; the caller adds the reasons it measured."""
    counts = Counter(FATE_WORDS[s.fate.value] for s in ran.slots)
    levels = sorted({s.level for s in ran.slots if s.level})
    records = scheduler.read_ledger(running.ledger)
    return {
        "cycle": ran.state.value.title(),
        "level": ", ".join(levels) if levels else None,
        "slots": [[state, counts[state]] for state in labels.ROW_STATES if counts[state]],
        "reasons": sorted(reasons),
        "forecasts": sum(1 for r in records if r.get("type") == "forecast"),
    }


def exchange_asks_for_a_pause(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """The real gateway, runner and HTTP transport against a loopback exchange that answers 429 with ``Retry-After: 2`` to its first price
    request. The exchange's clock of arrival is recorded by the server itself; a reason is reported only if what happened shows it."""
    clock = e2e_league.OffsetClock()
    wait_s = 2
    arrivals: list[tuple[float, str, int]] = []  # (monotonic time, path, status served)
    served = {"429": 0}

    with e2e_league.outside(clock) as world:

        def script(seen: Any) -> Any:
            path = seen.path.split("?")[0]
            if path == "/api/v3/klines" and served["429"] == 0:
                served["429"] += 1
                arrivals.append((time.monotonic(), path, 429))
                return ryo_fake_server.Behaviour(
                    429, b'{"code":-1003,"msg":"Too many requests"}', {"content-type": "application/json", "retry-after": str(wait_s)}
                )
            arrivals.append((time.monotonic(), path, 200))
            return None

        world.exchange.script = script
        settings = deployment.load_settings(deployment_checks.world_files(root, world))
        with deployment.assemble(settings, clock=clock) as running:
            ran = scheduler.tick(running.services, running.config).ran
            assert ran is not None
            first = next(at for at, _, status in arrivals if status == 429)
            in_flight = 0.25  # a request sent in the same instant as the refused one cannot know of the refusal
            quiet_until = first + wait_s - 0.05  # a tolerance for the thread that logged the arrival
            after = [(at, path) for at, path, _ in arrivals if path.startswith("/api/v3/") and at > first + in_flight]
            hammered = [path for at, path in after if at < quiet_until]
            asked_again = [at for at, path in after if path == "/api/v3/klines" and at >= quiet_until]
            refused_once = sum(1 for _, _, status in arrivals if status == 429) == 1
            reasons = {PAUSE_WORDS} if refused_once and not hammered and asked_again else set()
            got = observe_tick(running, ran, reasons)
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 6]],
            "reasons": [PAUSE_WORDS],
            "forecasts": 6,
        },
        "exchange-asks-for-a-pause",
        "An exchange answers 429 and asks for two seconds",
        "The first price request was refused with HTTP 429 and a Retry-After of two seconds, through the real gateway and HTTP client.",
        "The league sends the exchange nothing until the pause is over, asks again afterwards, and the cycle still commits.",
    )


def ryo_is_down_and_partial(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """The real gateway, runner, RYO adapter and transport against the synthetic loopback RYO (labelled synthetic: its values are not
    RYO's). Its deep analysis answers 503 as the recorded service did; its quick read answers ``partial`` with a null and a zero."""
    key_env = "HOSHITORI_DRILL_RYO_KEY"
    previous = os.environ.get(key_env)
    os.environ[key_env] = ryo_fake_server.KEY
    clock = e2e_league.OffsetClock()
    ryo_data = importlib.import_module("tests.ryo_data")
    base = deployment_checks.ryo_script(clock)

    def script(seen: Any) -> Any:
        prefix, suffix = "/api/mcp/tools/", "/call"
        if seen.method != "POST" or not (seen.path.startswith(prefix) and seen.path.endswith(suffix)):
            return None
        name = seen.path[len(prefix) : -len(suffix)]
        if name == "deep_analysis":
            args = json.loads(seen.body or b"{}")
            result = ryo_data.result(name, status="unavailable", request=args, as_of=clock.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            return ryo_fake_server.Behaviour(200, ryo_data.rest_body(name, result), {"content-type": "application/json"})
        if name == "analyze_token":
            args = json.loads(seen.body or b"{}")
            data = ryo_data.analyze_token_data()
            data["asset"]["symbol"] = args["symbol"]
            data["technical_analysis"]["rsi_14"] = None
            data["market"]["volume_24h_usd"] = 0
            availability = dict(ryo_data.AVAILABILITY[name], market_data="partial")
            result = ryo_data.result(
                name,
                data=data,
                request=args,
                status="partial",
                availability=availability,
                as_of=clock.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            )
            return ryo_fake_server.Behaviour(200, ryo_data.rest_body(name, result), {"content-type": "application/json"})
        return base(seen)

    fake = ryo_fake_server.FakeRyo(script=script).start()
    try:
        with e2e_league.outside(clock) as world:
            ryo = {**deployment_checks.good_ryo(fake.url), "key_env": key_env, "scan_wait_s": 60, "collect_wait_s": 60}
            settings = deployment.load_settings(deployment_checks.world_files(root, world, optional_sources=("ryo",), ryo=ryo))
            with deployment.assemble(settings, clock=clock) as running:
                ran = scheduler.tick(running.services, running.config).ran
                assert ran is not None
                records = scheduler.read_ledger(running.ledger)
                items = [r["body"] for r in records if r.get("type") == "evidence" and r["body"].get("source") == "ryo"]
                partial = [
                    b for b in items if b.get("tool") == "analyze_token" and b.get("status") == "partial" and b.get("unknown_fields")
                ]
                gaps = {u["reason"] for b in partial for u in b["unknown_fields"]}
                reasons = set()
                if any(f.detail.startswith("deep_analysis: ") for f in ran.failures):
                    reasons.add("RYO said its deep analysis was unavailable")
                if partial and {"NULL", "DEGRADED_ZERO"} <= gaps:
                    reasons.add("A partial RYO answer kept its null and its zero as unknown, not as numbers")
                got = observe_tick(running, ran, reasons)
    finally:
        fake.stop()
        if previous is None:
            os.environ.pop(key_env, None)
        else:
            os.environ[key_env] = previous
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 6]],
            "reasons": [
                "A partial RYO answer kept its null and its zero as unknown, not as numbers",
                "RYO said its deep analysis was unavailable",
            ],
            "forecasts": 6,
        },
        "ryo-down-and-partial",
        "RYO's deep analysis is unavailable and its quick read is partial",
        "On a synthetic RYO (its values are made up), every deep analysis answered that it was unavailable and every quick read answered "
        "partial, with one value null and one value zero. A real 503 is retried for minutes, "
        "so that path is tested elsewhere, not drilled here.",
        "The cycle commits on the other sources; the partial answer is kept, its gaps unknown and never a zero; "
        "the failed reads are named, not guessed.",
    )


def ryo_limits_the_rate(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """The real gateway and RYO adapter against a loopback RYO that answers every deep analysis as the real service did when its fan-out
    limit was passed: HTTP 429, the code ``RATE_LIMITED``, the message ``Rate limit exceeded (6/min for mcp_fanout).`` and
    ``retry-after: 60``. The shape is the real one, recorded in the metered probe; its values are made up."""
    key_env = "HOSHITORI_DRILL_RYO_KEY"
    previous = os.environ.get(key_env)
    os.environ[key_env] = ryo_fake_server.KEY
    clock = e2e_league.OffsetClock()
    base = deployment_checks.ryo_script(clock)
    limited = json.dumps(
        {
            "code": "RATE_LIMITED",
            "message": "Rate limit exceeded (6/min for mcp_fanout).",
            "details": {"limit": 60, "reset_at": 1793760405},
            "trace_id": "0123456789abcdef0123456789abcdef",
        }
    ).encode()

    def script(seen: Any) -> Any:
        prefix, suffix = "/api/mcp/tools/", "/call"
        asked = seen.path[len(prefix) : -len(suffix)] if seen.path.startswith(prefix) and seen.path.endswith(suffix) else ""
        if seen.method == "POST" and asked == "deep_analysis":
            return ryo_fake_server.Behaviour(429, limited, {"content-type": "application/json", "retry-after": "60"})
        return base(seen)

    fake = ryo_fake_server.FakeRyo(script=script).start()
    try:
        with e2e_league.outside(clock) as world:
            ryo = {
                **deployment_checks.good_ryo(fake.url),
                "key_env": key_env,
                "scan_wait_s": 60,
                "collect_wait_s": 60,
                "calls_per_minute": 600,
            }
            settings = deployment.load_settings(deployment_checks.world_files(root, world, optional_sources=("ryo",), ryo=ryo))
            with deployment.assemble(settings, clock=clock) as running:
                ran = scheduler.tick(running.services, running.config).ran
                assert ran is not None
                records = scheduler.read_ledger(running.ledger)
                kept = {r["body"].get("tool") for r in records if r.get("type") == "evidence" and r["body"].get("source") == "ryo"}
                reasons = set()
                limited_calls = [f for f in ran.failures if f.detail.startswith("deep_analysis: ")]
                if limited_calls:
                    reasons.add("RYO refused the deep analyses as rate limited and the cycle named each one")
                if {"analyze_token", "market_overview", "monitor_market_sentiment_shift"} <= kept:
                    reasons.add("The reads RYO did answer were kept")
                if "deep_analysis" not in kept:
                    reasons.add("No deep analysis was invented")
                got = observe_tick(running, ran, reasons)
    finally:
        fake.stop()
        if previous is None:
            os.environ.pop(key_env, None)
        else:
            os.environ[key_env] = previous
    return (
        got,
        {
            "cycle": "Committed",
            "level": "C3",
            "slots": [["Open", 6]],
            "reasons": [
                "No deep analysis was invented",
                "RYO refused the deep analyses as rate limited and the cycle named each one",
                "The reads RYO did answer were kept",
            ],
            "forecasts": 6,
        },
        "ryo-limits-the-rate",
        "RYO refuses the deep analyses as rate limited",
        "On a synthetic RYO (its values are made up) every deep analysis answered HTTP 429 in the real service's shape: "
        "the code RATE_LIMITED, "
        "the message about six calls a minute for the fan-out, and a retry-after of 60 seconds. The wait was shorter than that, so the "
        "cycle could not ask again.",
        "The cycle commits on the other sources; the reads RYO answered are kept; "
        "each refused read is named and nothing is made up in its place.",
    )


def healthy_plain(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """The baseline of the drills that follow on the plain test exchange: the same world with nothing broken."""
    w = World(root)
    got = observe(w, w.run(league))
    w.close()
    return (
        got,
        {"cycle": "Committed", "level": "C3", "slots": [["Open", 6]], "reasons": [], "forecasts": 6},
        "healthy-exchange",
        "Nothing is broken: the test exchange, as it is",
        "Nothing. The same test exchange, registry, ledger and time-stamp authorities as the drills that follow, with no fault.",
        "Every slot becomes a forecast and the cycle is committed with no reason listed: the baseline the failures are read against.",
    )


def healthy_model(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """The baseline of the drills of the model agents: the same scripted model, answering every request in time."""
    w = models_world.world(root, watchlist=("BTC",))
    model = models_world.Model(w)
    got = observe(w, w.run(league, models=models_world.models(model)))
    w.close()
    return (
        got,
        {"cycle": "Committed", "level": "C3", "slots": [["Open", 8]], "reasons": [], "forecasts": 8},
        "healthy-model",
        "Nothing is broken: the test model answers every request in time",
        "Nothing. The same scripted model as the two model drills, answering every request before its deadline.",
        "Every slot of every agent becomes a forecast and the cycle is committed with no reason listed: "
        "the baseline the two model failures are read against.",
    )


def healthy_ryo(root: Path) -> tuple[dict[str, Any], dict[str, Any], str, str, str, str]:
    """The baseline of the RYO drills: the same synthetic loopback RYO (its values are made up), answering every tool normally."""
    key_env = "HOSHITORI_DRILL_RYO_KEY"
    previous = os.environ.get(key_env)
    os.environ[key_env] = ryo_fake_server.KEY
    clock = e2e_league.OffsetClock()
    fake = ryo_fake_server.FakeRyo(script=deployment_checks.ryo_script(clock)).start()
    try:
        with e2e_league.outside(clock) as world:
            ryo = {**deployment_checks.good_ryo(fake.url), "key_env": key_env, "scan_wait_s": 60, "collect_wait_s": 60}
            settings = deployment.load_settings(deployment_checks.world_files(root, world, optional_sources=("ryo",), ryo=ryo))
            with deployment.assemble(settings, clock=clock) as running:
                ran = scheduler.tick(running.services, running.config).ran
                assert ran is not None
                got = observe_tick(running, ran, set())
    finally:
        fake.stop()
        if previous is None:
            os.environ.pop(key_env, None)
        else:
            os.environ[key_env] = previous
    return (
        got,
        {"cycle": "Committed", "level": "C3", "slots": [["Open", 6]], "reasons": [], "forecasts": 6},
        "healthy-ryo",
        "Nothing is broken: the synthetic RYO answers every tool normally",
        "Nothing. The same loopback RYO (its values are made up) and settings as the two RYO drills, with every tool answering normally.",
        "Every slot becomes a forecast and the cycle is committed with no reason listed: the baseline of the two RYO failures.",
    )


SCENARIOS: tuple[Callable[[Path], tuple[dict[str, Any], dict[str, Any], str, str, str, str]], ...] = (
    healthy_plain,
    exchange_fails_one_token,
    exchange_clock_is_off,
    collecting_is_slow,
    evidence_is_too_old,
    stamps_are_late,
    ledger_clock_runs_late,
    healthy_model,
    model_answers_late,
    model_is_down,
    process_killed_after_the_snapshot,
    process_killed_before_the_batch_is_written,
    exchange_asks_for_a_pause,
    healthy_ryo,
    ryo_is_down_and_partial,
    ryo_limits_the_rate,
)


def run_all() -> list[dict[str, Any]]:
    out = []
    for scenario in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix="drill-") as tmp:
            observed, wanted, id_, title, injected, expected = scenario(Path(tmp))
        out.append(
            {
                "id": id_,
                "title": title,
                "injected": injected,
                "expected": expected,
                "observed": observed,
                "expected_observed": wanted,
                "held": observed == wanted,
            }
        )
    return out


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: make_drill.py OUT.json", file=sys.stderr)
        return 2
    drills = run_all()
    data = (json.dumps(drills, indent=1, sort_keys=False) + "\n").encode()
    drill.read_drills(data)  # what is written is what the page can read
    Path(sys.argv[1]).write_bytes(data)
    print(json.dumps([{"id": d["id"], "held": d["held"]} for d in drills]))
    return 0 if all(d["held"] for d in drills) else 1


if __name__ == "__main__":
    raise SystemExit(main())
