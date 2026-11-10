"""``hoshitori-league``: run the league from a settings file, and look at what it wrote.

    hoshitori-league init-key FILE ORIGIN             make a log signing key; print its public key for the trusted root
    hoshitori-league make-protocol OUT --root FILE --mapping ARM=FILE ... --start-ms N --every-minutes M --watchlist BTC,ETH ...
        [--models FILE --cutoff ARM=HOURS:MS,...] [--optional-source NAME ...]
                                                      write the protocol file of the first league, pinned to a root and its mappings
    hoshitori-league run SETTINGS [--once]            tick until stopped (or once); one JSON line per tick on standard output
    hoshitori-league status SETTINGS                  counts of what the ledger holds, and what the heartbeat says
    hoshitori-league export SETTINGS CYCLE OUTDIR     the bundle of a committed cycle, for ``hoshitori-verify``
    hoshitori-league site SETTINGS OUTDIR [--scored] [--status FILE] [--drill FILE] [--claims FILE] [--model-challenge FILE ...]
        [--forgery DIR] [--desk-runs FILE] [--lab LOG [--lab-candles FILE ...] [--lab-runs FILE]]
                                                      the static documents of the interface and a bundle for each committed cycle
    hoshitori-league forge-demo SETTINGS CYCLE OUTDIR a forged copy of a cycle's bundle, signed by a stand-in key, for the Verify page
    hoshitori-league replay SETTINGS CYCLE            rebuild the cycle's decisions from the ledger and the stored answers and compare

The exit status is 0 on success, 1 for a failure of the run or the command, 2 for settings that cannot run. ``run`` stops cleanly on an
interrupt or a terminate signal, after the tick it is in.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from pathlib import Path
from typing import Any

from hoshitori import deployment, league, scheduler
from hoshitori.ledger import LedgerError
from hoshitori.ryo_gateway import RyoAttached, RyoGatewayError, refresh_catalogue


def _tick_line(item: scheduler.Tick | Exception) -> dict[str, Any]:
    if isinstance(item, Exception):
        return {"error": type(item).__name__, "message": str(item)[:500]}
    ran = item.ran
    return {
        "now_ms": item.now_ms,
        "closed": [{"cycle": c.cycle_id, "was": c.state_was.value, "slots_failed": c.slots_failed} for c in item.closed],
        "refreshed": None
        if item.refreshed is None
        else {
            "records": len(item.refreshed.records),
            "failures": len(item.refreshed.failures),
            # which list could not be read and why, so that a cycle with no candidates because a list failed says so
            "failed": [{"venue": f.venue, "scope": f.scope, "reason": f.reason, "detail": f.detail[:200]} for f in item.refreshed.failures],
        },
        "ran": None
        if ran is None
        else {
            "cycle": ran.cycle_id,
            "state": ran.state.value,
            "reason": ran.reason,
            "level": None if ran.commitment is None else ran.commitment.level,
            "slots": deployment.tally(s.fate.value for s in ran.slots),
        },
        "settled": None
        if item.passed is None
        else {
            "records": len(item.passed.records),
            "pending": len(item.passed.pending),
            "deferred": len(item.passed.deferred),
            "failed": len(item.passed.failed),
            # which slot could not be moved and why: a refused lifecycle step is named, not only counted
            "failures": [{"slot": slot, "reason": reason[:200]} for slot, reason in item.passed.failed],
        },
        "on_demand": None
        if item.on_demand is None and item.on_demand_failed is None
        else {
            "records": 0 if item.on_demand is None else len(item.on_demand.records),
            "pending": 0 if item.on_demand is None else len(item.on_demand.pending),
            "deferred": 0 if item.on_demand is None else len(item.on_demand.deferred),
            "failed": None if item.on_demand_failed is None else item.on_demand_failed,
        },
        "planned": None
        if item.planned is None
        else {
            "records": len(item.planned.records),
            "pending": len(item.planned.pending),
            # an arm R plan that could not be settled: which read and why
            "failed": [{"read": read, "reason": reason[:200]} for read, reason in item.planned.failed],
        },
        "desk": None
        if item.desked is None and item.desk_failed is None
        else {
            "runs": [] if item.desked is None else [{"trigger": t, "state": s, "forecast": f} for t, s, f in item.desked.outcomes],
            "raised": [] if item.desked is None else [{"trigger": t, "reason": r} for t, r in item.desked.failed],
            "failed": item.desk_failed,
        },
        "practiced": None
        if item.practiced is None
        else {
            each.arm: {
                "records": len(each.records),
                "waiting": len(each.plan.waiting),
                "missed": len(each.plan.missed),
                "refused": len(each.plan.refused),
                "unread": list(each.unread),
            }
            for each in item.practiced
        },
        "findings": [{"slot": b.planned_for_ms, "finding": b.finding.value} for b in item.findings],
    }


def _emit(value: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(value, sort_keys=True) + "\n")
    sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hoshitori-league", description="Run the Hoshitori league and look at what it wrote.")
    commands = parser.add_subparsers(dest="command", required=True)
    key = commands.add_parser("init-key", help="make a log signing key")
    key.add_argument("file", type=Path)
    key.add_argument("origin")
    make = commands.add_parser("make-protocol", help="write a protocol file")
    make.add_argument("out", type=Path)
    make.add_argument("--root", type=Path, required=True)
    make.add_argument("--mapping", action="append", default=[], metavar="ARM=FILE", required=True)
    make.add_argument("--barrier-mapping", action="append", default=[], metavar="ARM=FILE", help="also forecast contract B for this arm")
    make.add_argument(
        "--models", type=Path, default=None, help="the model arms' settings file; the protocol pins the versions it describes"
    )
    make.add_argument("--cutoff", action="append", default=[], metavar="ARM=HOURS:MS,...", help="the time each model arm has, per horizon")
    make.add_argument(
        "--optional-source",
        action="append",
        default=[],
        metavar="NAME",
        help="a source read when it can be and never required (binance-funding)",
    )
    make.add_argument("--name", required=True)
    make.add_argument("--start-ms", type=int, required=True)
    make.add_argument("--every-minutes", type=int, required=True)
    make.add_argument("--watchlist", required=True, help="comma-separated symbols")
    make.add_argument("--primary", default="binance-spot")
    make.add_argument("--second", default="okx")
    run = commands.add_parser("run", help="tick until stopped")
    run.add_argument("settings", type=Path)
    run.add_argument("--once", action="store_true", help="one tick, then exit")
    rebase = commands.add_parser(
        "rebase-clock",
        help="confirm that the host clock's present reading is the true time after the writer refused a stepped clock",
    )
    rebase.add_argument("settings", type=Path)
    rebase.add_argument("--reason", required=True, help="what was checked (for example two independent time references)")
    show = commands.add_parser("status", help="what the ledger holds")
    show.add_argument("settings", type=Path)
    again = commands.add_parser("replay", help="rebuild a committed cycle's decisions from the ledger and the stored answers, and compare")
    again.add_argument("settings", type=Path)
    again.add_argument("cycle")
    sig = commands.add_parser(
        "signals", help="read the public voices and wallets named in a file once and write the document the Voices page shows"
    )
    sig.add_argument("config", type=Path)
    sig.add_argument("out", type=Path)
    serving = commands.add_parser("serve", help="serve the site, and read the claims that are pasted into its Check a claim page")
    serving.add_argument("site", type=Path, help="the folder `site` wrote, with the built page beside its documents")
    serving.add_argument("--host", default="127.0.0.1")
    serving.add_argument("--port", type=int, default=8080)
    serving.add_argument(
        "--models",
        type=Path,
        default=None,
        help="the model settings: the single judgment's model reads the claims; without them none is read",
    )
    serving.add_argument(
        "--challenges",
        type=Path,
        default=None,
        help="the file that keeps the evidence challenges people file on receipts (made if it is not there); without it none are taken",
    )
    site = commands.add_parser("site", help="the static documents of the interface")
    site.add_argument("settings", type=Path)
    site.add_argument("out", type=Path)
    site.add_argument("--scored", action="store_true", help="say the period is a scored one (the default says it is exploratory)")
    site.add_argument("--status", type=Path, default=None, help="the status table (docs/STATUS.md) for the Evidence of quality page")
    site.add_argument("--sweeps", type=Path, default=None, help="the mutation sweep page (docs/SWEEP.md) for the Evidence of quality page")
    site.add_argument(
        "--coverage", type=Path, default=None, help="the folder of the rubric and contract matrices (docs/specs) for the same page"
    )
    site.add_argument("--drill", type=Path, default=None, help="the recorded failure drills (scripts/make_drill.py) for the Drill page")
    site.add_argument("--claims", type=Path, default=None, help="the recorded claim readings (scripts/make_claims.py) for the Claims page")
    site.add_argument(
        "--model-sources",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="recorded runs of the model arm with and without a source (scripts/model_source_value.py); repeatable",
    )
    site.add_argument(
        "--model-challenge",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="a recorded challenge of the model arm (scripts/challenge_live.py) for the What if page; repeatable",
    )
    site.add_argument(
        "--signals", type=Path, default=None, help="the collected voices and wallets (the signals command) for the Voices page"
    )
    site.add_argument("--lab", type=Path, default=None, metavar="LOG", help="a Laboratory command log, for the Laboratory page")
    site.add_argument(
        "--lab-candles",
        type=Path,
        action="append",
        default=[],
        metavar="FILE",
        help="stored real hourly candles of one token (scripts/make_lab_preview.py), for the Laboratory previews; repeatable, needs --lab",
    )
    site.add_argument("--lab-runs", type=Path, default=None, metavar="FILE", help="stored runs of Laboratory versions, needs --lab")
    site.add_argument(
        "--desk-runs",
        type=Path,
        default=None,
        metavar="FILE",
        help="a file of recorded Desk runs (hoshitori.desk_view.file_bytes) for the Desk page",
    )
    site.add_argument(
        "--forgery",
        type=Path,
        default=None,
        metavar="DIR",
        help="the forged copy made by forge-demo, shown on the Verify page as a forgery",
    )
    forge = commands.add_parser("forge-demo", help="a forged copy of a committed cycle's bundle, to show what the time-stamps are for")
    forge.add_argument("settings", type=Path)
    forge.add_argument("cycle")
    forge.add_argument("out", type=Path)
    export = commands.add_parser("export", help="the bundle of a committed cycle")
    export.add_argument("settings", type=Path)
    export.add_argument("cycle")
    export.add_argument("out", type=Path)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    try:
        if args.command == "init-key":
            _emit({"file": str(args.file), "public_key": deployment.new_key(args.file, args.origin)})
            return 0
        if args.command == "make-protocol":
            _emit(deployment.make_protocol(args))
            return 0
        if args.command == "serve":
            return _serve(args)
        if args.command == "signals":
            return _signals(args)
        settings = deployment.load_settings(args.settings)
        if args.command == "run":
            with deployment.assemble(settings) as running:
                return _run(running, once=args.once)
        if args.command == "rebase-clock":
            with deployment.assemble(settings) as running:
                try:
                    running.ledger.rebase_clock(args.reason)
                except LedgerError as exc:
                    raise deployment.DeploymentError(str(exc)) from exc
                _emit({"rebased": True, "events": running.ledger.clock_events()[-1]})
            return 0
        # the rest only look: they open the ledger and the captures to read, beside a running league, with no key, and write nothing
        with deployment.assemble_reading(settings) as reading:
            if args.command == "status":
                _emit(deployment.status(reading))
            elif args.command == "site":
                _emit(
                    deployment.export_site(
                        reading,
                        args.out,
                        scored=args.scored,
                        status=args.status,
                        drills=args.drill,
                        claims=args.claims,
                        model_challenges=args.model_challenge,
                        model_sources=args.model_sources,
                        forged=args.forgery,
                        desk_runs=args.desk_runs,
                        sweeps=args.sweeps,
                        coverage=args.coverage,
                        signals=args.signals,
                        lab_log=args.lab,
                        lab_candles=args.lab_candles,
                        lab_runs=args.lab_runs,
                    )
                )
            elif args.command == "forge-demo":
                _emit(deployment.forge_cycle(reading, args.cycle, args.out))
            elif args.command == "export":
                _emit(deployment.export_cycle(reading, args.cycle, args.out))
            else:
                found = deployment.replay_cycle(reading, args.cycle)
                _emit(found)
                return 0 if found["clean"] else 1
    except deployment.DeploymentError as exc:
        sys.stderr.write(f"hoshitori-league: {exc}\n")
        return 2
    except Exception as exc:
        sys.stderr.write(f"hoshitori-league: {type(exc).__name__}: {exc}\n")
        return 1
    return 0


def _signals(args: argparse.Namespace) -> int:
    from hoshitori import signals

    try:
        settings = signals.read_settings(args.config.read_bytes(), args.config.parent)
    except (OSError, signals.SignalsError) as exc:
        _emit({"error": str(exc)})
        return 2
    document = signals.collect(settings)
    args.out.write_bytes(signals.encode(document))
    _emit(
        {
            "out": str(args.out),
            "accounts_read": sum(a["state"] == "READ" for a in document["accounts"]),
            "wallets_read": sum(w["state"] == "READ" for w in document["wallets"]),
        }
    )
    return 0


def _serve(args: argparse.Namespace) -> int:
    import contextlib
    import os

    from hoshitori import challenge_store, model_settings, serve

    if not args.site.is_dir():
        raise deployment.DeploymentError(f"{args.site} is not a folder")
    root = Path(__file__).resolve().parents[2]
    with contextlib.ExitStack() as stack:
        reader = None
        if args.models is not None:
            models = model_settings.load(args.models)
            if models.l1 is None:
                raise deployment.DeploymentError("the model settings have no single judgment to take a model from")
            key = os.environ.get(models.key_env, "")
            if not key:
                raise deployment.DeploymentError(f"the model key is not set: the environment variable {models.key_env} is empty")
            reader = stack.enter_context(serve.open_reader(models.base_url, lambda: key, models.l1.model))
        kept = challenge_store.ChallengeStore(args.challenges) if args.challenges is not None else None
        _emit(
            {
                "serving": str(args.site),
                "address": f"http://{args.host}:{args.port}/",
                "reads_claims": reader is not None,
                "takes_challenges": kept is not None,
            }
        )
        serve.serve(args.site, reader, args.host, args.port, root, kept)
    return 0


def _run(running: deployment.Running, *, once: bool) -> int:
    stop = threading.Event()
    if threading.current_thread() is threading.main_thread():
        for name in (signal.SIGINT, signal.SIGTERM):
            signal.signal(name, lambda *_: stop.set())
    failed: list[Exception] = []

    def report(item: scheduler.Tick | Exception) -> None:
        _emit(_tick_line(item))
        if isinstance(item, Exception):
            failed.append(item)
        if once:
            stop.set()

    if running.ryo is not None and running.services.ryo is not None:
        _emit(_catalogue_line(running, running.ryo, running.services.ryo))
    scheduler.run_forever(running.services, running.config, stop, every_s=running.settings.tick_every_s, report=report)
    return 1 if once and failed else 0


def _catalogue_line(running: deployment.Running, attached: RyoAttached, wiring: league.RyoWiring) -> dict[str, Any]:
    """Read RYO's tool list once, before the first cycle, and say what it showed: the structure as pinned, drift (a tool whose structure
    changed is unavailable until a person has looked), or that the list could not be read (the league goes on; its cycles will say what
    RYO did)."""
    try:
        drift = refresh_catalogue(wiring.env.runner, attached.adapter, running.captures, wiring.env.clock, wiring.scan_wait_s)
    except RyoGatewayError as exc:
        return {"ryo_catalogue": "not read", "message": str(exc)[:300]}
    return {
        "drift": [f"{d.tool}: {d.kind}" + (" (unavailable until looked at)" if d.blocks else "") for d in drift],
        "ryo_catalogue": "drift" if drift else "as pinned",
    }


if __name__ == "__main__":
    raise SystemExit(main())
