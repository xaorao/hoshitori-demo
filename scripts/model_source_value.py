#!/usr/bin/env python3
"""Ask the single-model agent again on settled calls, with and without one source, and record the answers for "Does a source make the
calls better?" (``hoshitori.source_value``).

    python scripts/model_source_value.py SETTINGS SOURCE --budget N --out RUNS.json [--max-slots 40] [--hours 1,4] [--workers 4]

For each of the latest settled up-or-down calls of arm L1 (at most ``--max-slots``, at the horizons given, made by the L1 version the
settings describe, in cycles whose candidate holds something from SOURCE), the arm is asked twice on the cycle's frozen snapshot: once
on all of it and once with SOURCE and everything derived from it taken out, both with the same seed, so the two answers differ in the
evidence alone. The committed call is not used: a model answers differently each time, so the fair pair is two fresh answers. What was
asked and answered goes to RUNS.json (``source_value.read_model_runs`` reads it), and ``hoshitori-league site --model-sources RUNS.json``
turns it into the lines of the page. Nothing is written to the ledger.

The league's ledger takes one writer at a time, so while the league runs, run this on a copy of its directory (the settings, keys,
roots and ``run``); the answers it records name calls of the real ledger, and the export checks them against it.

It makes model calls: two runs per slot, each with at most one repair, so at most four calls a slot; it does not start unless that worst
case fits ``--budget``. The model key is read from the environment variable the model settings name; it is never printed or stored.
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from hoshitori import arm_l1, deployment, forecast, intervention, model_client, scheduler, snapshot, source_value  # noqa: E402
from hoshitori.model_locate import ATTEMPTS, HOUR_MS, Refused, locate, the_arm  # noqa: E402
from hoshitori.snapshot import Reader  # noqa: E402
from hoshitori.timeline import from_ms, to_ms  # noqa: E402
from hoshitori.timeutil import parse_rfc3339  # noqa: E402

RUNS_PER_SLOT = 2  # the full snapshot and the snapshot without the source


def slots(records: list[dict[str, Any]], version: str, hours: set[int], source: str, most: int) -> list[dict[str, Any]]:
    """The latest settled up-or-down calls of the L1 version ``version`` at the given horizons whose candidate holds the source."""
    outcomes = {
        r["body"]["forecast_id"]: int(r["body"]["outcome"] == "1")
        for r in records
        if r.get("type") == "settlement" and r["body"]["state"] == "SETTLED" and r["body"].get("outcome") in ("0", "1")
    }
    origins = {
        r["body"]["forecast_id"]: to_ms(parse_rfc3339(r["body"]["origin"]))
        for r in records
        if r.get("type") == "forecast_state" and r["body"]["state"] == "OPEN" and r["body"].get("origin") is not None
    }
    planned = {
        r["body"]["cycle_id"]: from_ms(to_ms(parse_rfc3339(r["body"]["planned_for"]))).hour
        for r in records
        if r.get("type") == "cycle_state" and r["body"]["state"] == "PLANNED"
    }
    found = []
    for r in records:
        b = r["body"] if r.get("type") == forecast.TYPE else None
        if b is None or b["arm_id"] != arm_l1.ARM or b["contract"] != "D" or b["agent_version_hash"] != version:
            continue
        h = b["horizon_ms"] // HOUR_MS
        if h not in hours or r["id"] not in outcomes or r["id"] not in origins:
            continue
        found.append(
            {
                "forecast_id": r["id"],
                "cycle_id": b["cycle_id"],
                "symbol": b["symbol"],
                "hours": h,
                "planned_hour": planned[b["cycle_id"]],
                "close_ms": origins[r["id"]] + b["horizon_ms"],
                "outcome": outcomes[r["id"]],
            }
        )
    found.sort(key=lambda s: (s["close_ms"], s["forecast_id"]), reverse=True)
    usable = []
    for s in found:
        try:
            s["at"] = locate(records, s["cycle_id"], s["symbol"], s["hours"], source)
        except Refused:
            continue  # the candidate holds nothing from the source in that cycle
        usable.append(s)
        if len(usable) == most:
            break
    return usable


def _p(decision: arm_l1.Decision) -> str | None:
    return None if decision.verdict is not arm_l1.Verdict.CALL or decision.p is None else str(decision.p)


def run(
    settings_path: Path,
    source: str,
    *,
    budget: int,
    out: Path,
    max_slots: int = 40,
    hours: set[int] | None = None,
    workers: int = 4,
    clock: Any = None,
) -> dict[str, Any]:
    """Ask, record the answers in ``out``, and say how many slots and model calls it took and what the lines read."""
    settings = deployment.load_settings(settings_path)
    l1 = the_arm(settings)
    worst = ATTEMPTS * RUNS_PER_SLOT * max_slots
    if worst > budget:
        raise Refused(f"{max_slots} slots may make {worst} model calls, more than the budget of {budget}")
    wanted = hours or {1, 4}
    version = arm_l1.agent_version_hash(l1)
    with deployment.assemble(settings, **({} if clock is None else {"clock": clock})) as running:
        chosen = slots(scheduler.read_all(running.services), version, wanted, source, max_slots)
        if not chosen:
            raise Refused(f"no settled call of this L1 version at {sorted(wanted)} hours has anything from {source}")
        fetcher = model_client.GatewayFetcher(
            running.services.env.runner, running.captures, running.services.env.clock, running.model_provider or ""
        )
        client = model_client.ModelClient(fetcher)
        base = l1.seed or 0

        def ask(job: tuple[int, bool]) -> arm_l1.Decision:
            i, withheld = job
            s = chosen[i]
            at = s["at"]
            what = intervention.withhold(at.frozen, at.store, at.removed).snapshot if withheld else at.frozen
            if what is None:
                raise Refused(f"taking {source} out of {s['cycle_id']} leaves evidence that stands on it")
            reader = Reader(what, snapshot.Access.INDEPENDENT)
            cfg = replace(l1, seed=base + i)
            return arm_l1.decide(client, reader, at.candidate, s["symbol"], s["hours"] * HOUR_MS, at.frozen.snapshot_at_ms, cfg)

        jobs = [(i, w) for i in range(len(chosen)) for w in (False, True)]
        with ThreadPoolExecutor(max_workers=workers) as pool:
            answers = dict(zip(jobs, pool.map(ask, jobs), strict=True))
    pairs = []
    for i, s in enumerate(chosen):
        full, without = answers[(i, False)], answers[(i, True)]
        pairs.append(
            {
                "cycle_id": s["cycle_id"],
                "asset_id": s["at"].candidate.asset_id,
                "symbol": s["symbol"],
                "hours": s["hours"],
                "planned_hour": s["planned_hour"],
                "close_ms": s["close_ms"],
                "outcome": s["outcome"],
                "seed": base + i,
                "with_p": _p(full),
                "without_p": _p(without),
                "failed": arm_l1.Verdict.FAILED in (full.verdict, without.verdict),
            }
        )
    doc = {"format": source_value.RUNS_FORMAT, "arm": arm_l1.ARM, "model": l1.model, "agent_version": version, "source": source}
    data = (json.dumps({**doc, "pairs": pairs}, indent=1, sort_keys=True) + "\n").encode()
    runs = source_value.read_model_runs(data)  # what is written is what the export will read
    out.write_bytes(data)
    lines = [
        {"hours": x.hours, "scored": x.shared, "state": x.state, "mean": x.mean, "required": x.required}
        for x in source_value.model_lines(runs)
    ]
    return {"slots": len(chosen), "model_calls": sum(len(a.calls) for a in answers.values()), "lines": lines}


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("settings", type=Path)
    ap.add_argument("source")
    ap.add_argument("--budget", type=int, required=True, help="the most model calls this run may make")
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--max-slots", type=int, default=40)
    ap.add_argument("--hours", default="1,4", help="comma-separated horizons, in hours")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args(argv)
    try:
        report = run(
            args.settings,
            args.source,
            budget=args.budget,
            out=args.out,
            max_slots=args.max_slots,
            hours={int(h) for h in args.hours.split(",")},
            workers=args.workers,
        )
    except Refused as exc:
        sys.stderr.write(f"model_source_value: {exc}\n")
        return 2
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
