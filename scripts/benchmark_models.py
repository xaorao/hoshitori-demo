"""Which model should the arms use? (the choice is recorded as ADR-009 in ``DECISIONS``.)

A measurement, not part of the product. Every candidate model is given the same frozen cases: the snapshot of a stored cycle (real
exchange data, read back from the ledger and checked against the digests it names), asked through the product's own gateway, client
and arms, so the numbers are what a cycle would see: structured-output reliability, how often the one repair is needed, refusals,
truncation, latency, billed tokens (reasoning included), how often the grounding gate stops a call, and how much repeated asking
changes the answer.

What it does not measure is whether a model is right: that needs settled outcomes, and a benchmark of a dozen cases says nothing
about skill. It is a gate for models that cannot do the job, not a ranking of forecasters. The key is read from the environment file
and used only as a header; it is never printed or written. The raw prompts and answers stay in a private directory outside the
repository; the summary written holds counts and times only.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
import tempfile
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import hoshitori.arm_l1 as arm_l1
import hoshitori.arm_l2 as arm_l2
import hoshitori.canonical as canonical
import hoshitori.model_client as model_client
import hoshitori.model_gateway as model_gateway
import hoshitori.segment as segment
import hoshitori.snapshot as snapshot
from hoshitori.breaker import BreakerConfig
from hoshitori.capture import CaptureStore
from hoshitori.clock import SystemClock
from hoshitori.gateway import DispatchJournal, Gateway
from hoshitori.retry import RetryPolicy
from hoshitori.runner import Runner, ThreadExecutor

HOUR_MS = 3_600_000
MODELS = (
    "nvidia/nemotron-3.5-super-vl-120b-a12b",
    "nvidia/nemotron-3-ultra-550b-a55b",
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
)


def read_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            name, value = line.split("=", 1)
            out[name.strip()] = value.strip()
    return out


def read_records(directory: Path) -> list[dict[str, Any]]:
    """Every record of a league's ledger, read from its segments without opening the ledger."""
    out: list[dict[str, Any]] = []
    previous: tuple[str, bytes, int] | None = None
    import hashlib

    for file in sorted((directory / "ledger" / "segments").glob("segment-*.jsonl")):
        data = file.read_bytes()
        scan = segment.scan_segment(data, segment.segment_first_seq(file.name), previous)
        if scan.problem is not segment.Problem.NONE:
            raise SystemExit(f"{file.name}: {scan.problem.name}: {scan.detail}")
        out.extend(canonical.parse_record(line.record) for line in scan.records)
        previous = (file.name, hashlib.sha256(data).digest(), len(scan.records))
    return out


def cases_of(directory: Path, cycle: int) -> tuple[snapshot.Reader, list[tuple[snapshot.Candidate, str]], int]:
    """The reader of the ``cycle``-th snapshot of a stored league (from the last, counting back) and its candidates with their symbols."""
    records = read_records(directory)
    snapshots = [r for r in records if r["type"] == snapshot.TYPE]
    manifests = {r["body"]["cycle_id"]: r for r in records if r["type"] == "manifest"}
    chosen = snapshots[-1 - cycle]
    evidence_by_id = {r["id"]: r for r in records if r["type"] == "evidence"}
    rebuilt = snapshot.rebuild(chosen, evidence_by_id)
    symbols = {
        e["candidate_id"]: e["symbol"] for e in manifests[chosen["body"]["cycle_id"]]["body"]["entries"] if e["inclusion"] == "INCLUDED"
    }
    candidates = [(snapshot.Candidate(slot.candidate_id, slot.asset_id), symbols[slot.candidate_id]) for slot in rebuilt.slots]
    return snapshot.Reader(rebuilt, snapshot.Access.INDEPENDENT), candidates, rebuilt.snapshot_at_ms


class Stack:
    """The product's own way to ask a model: gateway, runner, transport and client, with a private capture store."""

    def __init__(self, base: str, key: str, workdir: Path, rate_per_min: float, concurrency: int) -> None:
        self.clock = SystemClock()
        self.captures = CaptureStore(workdir / "captures")
        gateway = Gateway(
            self.captures,
            DispatchJournal(workdir / "dispatch.jsonl"),
            retry_policy=RetryPolicy(),
            rng=random.Random(),  # noqa: S311 - jitter for retries, not a secret
        )
        self.endpoint = model_gateway.Endpoint("bench", base.rstrip("/"))
        attached = model_gateway.attach_model(
            gateway,
            self.endpoint,
            lambda: key,
            self.clock,
            rate_limit=rate_per_min,
            concurrency=concurrency,
            breaker=BreakerConfig(consecutive_failures=1000),
            attempt_cap_s=180.0,
            min_attempt_s=5.0,
        )
        self.pool = ThreadExecutor(concurrency + 2)
        self.runner = Runner(gateway, {attached.adapter.provider: attached.transport}, self.clock, self.pool)
        self.loop = threading.Thread(target=self.runner.run_forever, daemon=True)
        self.loop.start()
        self.client = model_client.ModelClient(
            model_client.GatewayFetcher(self.runner, self.captures, self.clock, attached.adapter.provider)
        )

    def close(self) -> None:
        self.runner.stop()
        self.loop.join(timeout=5)
        self.pool.shutdown()
        self.captures.close()


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, round(q * (len(ordered) - 1)))]


def summarise(decisions: list[Any], calls: list[Any]) -> dict[str, Any]:
    first = [c for c in calls if c.attempt == 1]
    latencies = [c.latency_ms for c in calls if c.latency_ms is not None]
    billed = [c.usage.billed for c in calls if c.usage is not None]
    reasoning = [c.usage.reasoning_tokens for c in calls if c.usage is not None and c.usage.reasoning_tokens is not None]
    outcomes = Counter(c.outcome.value for c in first)
    repaired = sum(1 for c in calls if c.attempt == 2 and c.outcome is model_client.Outcome.VALID)
    return {
        "decisions": len(decisions),
        "verdicts": dict(Counter(d.verdict.value for d in decisions)),
        "reasons": dict(Counter(d.reason.value for d in decisions if d.reason is not None)),
        "grounding_stopped": sum(1 for d in decisions if d.gate.value == "GROUNDING"),
        "calls": len(calls),
        "first_attempt_outcomes": dict(outcomes),
        "valid_first_attempt": round(outcomes.get("VALID", 0) / len(first), 3) if first else None,
        "repaired": repaired,
        "latency_ms": {
            "p50": percentile([float(x) for x in latencies], 0.5),
            "p95": percentile([float(x) for x in latencies], 0.95),
            "max": max(latencies) if latencies else None,
        },
        "billed_tokens": {"mean": round(statistics.fmean(billed), 1) if billed else None, "max": max(billed) if billed else None},
        "reasoning_tokens_mean": round(statistics.fmean(reasoning), 1) if reasoning else None,
    }


def stability(by_case: dict[Any, list[Any]]) -> dict[str, Any]:
    """How much asking the same question again changes the answer (temperature zero, no seed)."""
    agree, spreads = [], []
    for answers in by_case.values():
        if len(answers) < 2:
            continue
        verdicts = {a.verdict.value for a in answers}
        agree.append(len(verdicts) == 1)
        ps = [float(a.p) for a in answers if a.p is not None]
        if len(ps) >= 2:
            spreads.append(max(ps) - min(ps))
    return {
        "cases_asked_more_than_once": len(agree),
        "same_verdict_every_time": round(sum(agree) / len(agree), 3) if agree else None,
        "mean_probability_range": round(statistics.fmean(spreads), 4) if spreads else None,
        "largest_probability_range": round(max(spreads), 4) if spreads else None,
    }


def run_model(
    stack: Stack,
    reader: snapshot.Reader,
    candidates: list[tuple[snapshot.Candidate, str]],
    taken: int,
    model: str,
    repeats: int,
    debates: int,
    thinking: bool | None,
    max_tokens: int,
) -> dict[str, Any]:
    single = arm_l1.Config(model, max_tokens=max_tokens, timeout_s=170.0, thinking=thinking)
    debate = arm_l2.Config(single, single, single, 200_000)
    started = time.monotonic()
    jobs: list[tuple[str, snapshot.Candidate, str, int, int]] = []
    for candidate, symbol in candidates:
        for hours in (1, 4):
            jobs.extend(("L1", candidate, symbol, hours, k) for k in range(repeats))
    for candidate, symbol in candidates[:debates]:
        jobs.append(("L2", candidate, symbol, 4, 0))

    def work(job: tuple[str, snapshot.Candidate, str, int, int]) -> tuple[tuple[str, str, int], Any]:
        arm, candidate, symbol, hours, _ = job
        if arm == "L1":
            return (arm, candidate.candidate_id, hours), arm_l1.decide(
                stack.client, reader, candidate, symbol, hours * HOUR_MS, taken, single
            )
        return (arm, candidate.candidate_id, hours), arm_l2.decide(stack.client, reader, candidate, symbol, hours * HOUR_MS, taken, debate)

    with ThreadPoolExecutor(max_workers=4) as pool:
        done = list(pool.map(work, jobs))
    elapsed = time.monotonic() - started
    singles: dict[Any, list[Any]] = {}
    debates_done: list[Any] = []
    for key, got in done:
        if key[0] == "L1":
            singles.setdefault(key, []).append(got)
        else:
            debates_done.append(got)
    all_singles = [d for ds in singles.values() for d in ds]
    out: dict[str, Any] = {
        "single_judgment": summarise(all_singles, [c for d in all_singles for c in d.calls]),
        "stability": stability(singles),
        "seconds_for_everything": round(elapsed, 1),
    }
    if debates_done:
        out["debate"] = {
            **summarise([d.decision for d in debates_done], [c for d in debates_done for c in d.calls]),
            "collapsed": sum(1 for d in debates_done if d.collapsed),
            "failed_role": dict(Counter(d.failed_role for d in debates_done if d.failed_role)),
            "tokens_mean": round(statistics.fmean(d.tokens for d in debates_done), 1),
            "calls_per_debate_mean": round(statistics.fmean(len(d.calls) for d in debates_done), 2),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True, help="the environment file with LLM_BASE_URL and LLM_API_KEY")
    parser.add_argument("--league", type=Path, required=True, help="the directory of a stored league (it holds ledger/)")
    parser.add_argument("--cycle", type=int, default=0, help="which snapshot, counting back from the last (0 is the last)")
    parser.add_argument("--out", type=Path, required=True, help="where to write the summary (outside the repository)")
    parser.add_argument("--work", type=Path, default=None, help="a private directory for the raw captures (default: a temporary one)")
    parser.add_argument("--models", nargs="*", default=list(MODELS))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--debates", type=int, default=4, help="how many candidates are also given to the debate")
    parser.add_argument("--thinking", choices=("default", "on", "off"), nargs="+", default=["default"], help="the modes to measure")
    parser.add_argument("--max-tokens", type=int, default=12000, help="the token ceiling of a call (reasoning counts inside it)")
    parser.add_argument("--rate", type=float, default=60.0, help="requests a minute the stack may send")
    parser.add_argument("--concurrency", type=int, default=4)
    args = parser.parse_args(argv)
    env = read_env(args.env)
    base, key = env.get("LLM_BASE_URL", ""), env.get("LLM_API_KEY", "")
    if not base or not key:
        print("the environment file has no LLM_BASE_URL or LLM_API_KEY", file=sys.stderr)
        return 2
    reader, candidates, taken = cases_of(args.league, args.cycle)
    summary: dict[str, Any] = {
        "cases": {"candidates": [s for _, s in candidates], "horizons_hours": [1, 4], "repeats": args.repeats},
        "models": {},
    }
    with tempfile.TemporaryDirectory(prefix="bench-") as scratch:
        modes = {"default": None, "on": True, "off": False}
        for model in args.models:
            for mode in args.thinking:
                work = args.work or Path(scratch)
                directory = work / f"{model.replace('/', '_')}-{mode}"
                directory.mkdir(parents=True, exist_ok=True)
                stack = Stack(base, key, directory, args.rate, args.concurrency)
                try:
                    summary["models"][f"{model} [thinking {mode}]"] = run_model(
                        stack, reader, candidates, taken, model, args.repeats, args.debates, modes[mode], args.max_tokens
                    )
                finally:
                    stack.close()
                print(f"{model} [thinking {mode}]: done", flush=True)
    args.out.write_text(json.dumps(summary, indent=1, sort_keys=True), encoding="utf-8")
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
