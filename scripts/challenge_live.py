#!/usr/bin/env python3
"""Challenge one committed forecast of the single-judgment model arm (L1): ask the same model again, in pairs, with and without a source;
or replay a stored challenge offline, from the answers the league stored, and say whether it comes out the same.

    python scripts/challenge_live.py SETTINGS CYCLE SYMBOL HOURS SOURCE --pairs 20 --budget N --out RESULT.json
    python scripts/challenge_live.py SETTINGS --replay STORED.json [--out RESULT.json]

For one committed cycle it rebuilds the frozen snapshot from the ledger, takes SOURCE (for example ``binance-funding``) and everything
derived from it out of that candidate's evidence, and asks the model ``pairs`` times on the full evidence, ``pairs`` times on the evidence
without the source and ``pairs`` times again on the full evidence (the model against itself, the control). Every run is its own request
(``model_challenge.seeds``): pair ``i`` asks its full run and its run without the source with the seed index ``i`` and its control with
the index ``pairs + i``, each seed being the L1 settings' own seed, or 0 when they have none, plus the index. The actions go to
``hoshitori.paired_stats``, a run that failed staying in its pair as ``RUN_FAILED``. The result is written to RESULT.json through
``hoshitori.model_challenge_file.record``, with every run's decision, seed and calls by their digests (the tape), so the result can be read,
checked against its runs and shown again without asking again. The original action is the one the cycle committed. Nothing is written to
the ledger.

It makes model calls: three times ``pairs``, and one more for each answer that needs its repair; it does not start unless that worst case
fits ``--budget``. The model key is read from the environment variable the model settings name; it is never printed or stored. Set the
environment as ``hoshitori-league run`` needs it.

With ``--replay`` it makes no model call. It rebuilds the challenge STORED.json describes, with the same cycle, candidate, source, pairs and
seeds, and answers every request from the league's capture store by the digest of its arguments (``model_client.ReplayFetcher``). It
reports, run by run, whether the replayed action is the stored one and, where the file holds the tape, whether each call's request and
capture are the stored ones. A request with no stored answer is ``REPLAY_MISSING``: it is reported as such, never asked. The exit status is
0 when every run comes out as stored, 1 when one does not, 2 when the file or the settings cannot be used. With ``--out`` a replay that
came out as stored is written through ``record``; one that did not is not written. The stored file is a file of ``model_challenge_file``
(read strictly) or one this script wrote before it wrote through ``record`` (the C1 results: the same members, each run with its seed and
action, and its calls by digest once the script stored them). The model settings must name the variable of the key, which a replay never
sends: set it to any value that is not the key.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from hoshitori import (
    arm_l1,
    deployment,
    intervention,
    model_challenge,
    model_challenge_file,
    model_client,
    scheduler,
    snapshot,
)
from hoshitori.model_locate import ATTEMPTS, HOUR_MS, Located, Refused, locate, the_arm
from hoshitori.snapshot import Reader

# the members of a result this script wrote before it wrote through model_challenge_file.record, and of each of its runs
OLD_KEYS = frozenset(
    {
        "cycle",
        "symbol",
        "hours",
        "source",
        "removed",
        "original",
        "pairs",
        "outcome",
        "mechanism",
        "failed",
        "treated",
        "control",
        "verdict",
        "runs",
    }
)
OLD_RUN_KEYS = frozenset({"arm", "i", "seed", "action", "verdict", "reason", "p", "proposal_p", "gate", "cites_removed", "detail", "calls"})
TAPE_KEYS = ("request_digest", "args_digest", "capture_id", "response_digest")


def asker(
    client: model_client.ModelClient, at: Located, l1: arm_l1.Config, seeds: Mapping[tuple[str, int], int], symbol: str, hours: int
) -> Callable[[str, int], arm_l1.Decision]:
    """One run of the challenge: the arm on the full evidence or without the source, with the run's own seed."""
    without = intervention.withhold(at.frozen, at.store, at.removed).snapshot

    def ask(arm: str, i: int) -> arm_l1.Decision:
        what_if = at.frozen if arm != model_challenge.WITHHELD else without
        assert what_if is not None  # the module checks the removal for a leak before it asks any run
        settings = replace(l1, seed=seeds[(arm, i)])
        reader = Reader(what_if, snapshot.Access.INDEPENDENT)
        return arm_l1.decide(client, reader, at.candidate, symbol, hours * HOUR_MS, at.frozen.snapshot_at_ms, settings)

    return ask


def asked_of(at: Located, l1: arm_l1.Config, cycle: str, symbol: str, hours: int, source: str, pairs: int) -> model_challenge_file.Asked:
    return model_challenge_file.Asked(
        cycle, at.forecast_id, symbol, hours, source, at.original, pairs, l1.model, arm_l1.PROMPT_VERSION, l1.temperature, l1.seed,
        arm_l1.agent_version_hash(l1),
    )  # fmt: skip


def summary(challenge: model_challenge_file.Challenge) -> dict[str, Any]:
    v = challenge.verdict
    a = challenge.asked
    return {
        "symbol": a.symbol,
        "hours": a.hours,
        "source": a.source,
        "original": a.original,
        "pairs": a.pairs,
        "outcome": challenge.outcome,
        "mechanism": challenge.mechanism,
        "failed": challenge.failed,
        "attempted": challenge.attempted,
        "treated": None if challenge.treated is None else vars(challenge.treated),
        "control": None if challenge.control is None else vars(challenge.control),
        "verdict": None if v is None else {"n": v.n, "shift": v.shift, "low": v.low, "high": v.high, "p": v.p, "note": v.note},
    }


# --- a live challenge ----------------------------------------------------------------------------------------------------------------
def live(args: argparse.Namespace) -> int:
    settings = deployment.load_settings(args.settings)
    l1 = the_arm(settings)
    try:
        seeds = model_challenge.seeds(l1.seed, args.pairs)
    except model_challenge.ChallengeError as exc:
        raise Refused(str(exc)) from exc
    worst = ATTEMPTS * len(seeds)  # every run its answer and its one repair
    if worst > args.budget:
        raise Refused(f"a challenge of {args.pairs} pairs may make {worst} model calls, more than the budget of {args.budget}")
    with deployment.assemble(settings) as running:
        at = locate(scheduler.read_all(running.services), args.cycle, args.symbol, args.hours, args.source)
        if at.agent_version != arm_l1.agent_version_hash(l1):
            raise Refused("the settings' L1 arm is not the one that committed the forecast")
        fetcher = model_client.GatewayFetcher(
            running.services.env.runner, running.captures, running.services.env.clock, running.model_provider or ""
        )
        ask = asker(model_client.ModelClient(fetcher), at, l1, seeds, args.symbol, args.hours)

        # The module decides the order: it checks the removal for a leak and asks the probe (run 0 without the evidence) before anything
        # else, and stops there when the evidence is required. So the probe is asked alone, and the other runs are asked together, all at
        # once, only when the module goes on to its first pair; a leak or a required source costs no other call.
        done: dict[tuple[str, int], arm_l1.Decision] = {}

        def lookup(_reader: Reader, i: int, arm: str) -> arm_l1.Decision:
            if (arm, i) not in done:
                if (arm, i) == (model_challenge.WITHHELD, 0):
                    done[(arm, i)] = ask(arm, i)
                else:
                    rest = [job for job in seeds if job not in done]
                    with ThreadPoolExecutor(max_workers=args.workers) as pool:
                        done.update(zip(rest, pool.map(lambda job: ask(*job), rest), strict=True))
            return done[(arm, i)]

        result = model_challenge.challenge(at.frozen, at.store, at.removed, lookup, at.original, args.pairs)
        data = model_challenge_file.record(asked_of(at, l1, args.cycle, args.symbol, args.hours, args.source, args.pairs), result, done)
    args.out.write_bytes(data)
    print(json.dumps(summary(model_challenge_file.read(data)), indent=1))
    return 0


# --- a stored challenge, replayed offline --------------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Stored:
    kind: str  # "model_challenge_file", or "script before record" for a result this script wrote before it wrote through record
    cycle: str
    symbol: str
    hours: int
    source: str
    removed: tuple[str, ...]
    original: str
    pairs: int
    outcome: str
    treated: dict[str, int] | None
    control: dict[str, int] | None
    runs: dict[tuple[str, int], tuple[int, str, tuple[tuple[Any, ...], ...] | None]]  # seed, action, the tape when it was stored
    asked: model_challenge_file.Asked | None


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in pairs:
        if key in out:
            raise Refused(f"the stored file gives the key {key!r} twice in one object")
        out[key] = value
    return out


def load_stored(path: Path) -> Stored:
    data = path.read_bytes()
    try:
        got = model_challenge_file.read(data)
    except model_challenge_file.ChallengeFileError as strict:
        try:
            doc = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Refused(f"the stored file is not a challenge file: {strict}") from None
        if not isinstance(doc, dict) or set(doc) != OLD_KEYS or not isinstance(doc["runs"], list):
            raise Refused(f"the stored file is not a challenge file: {strict}") from None
        return _old(doc)
    a = got.asked
    runs: dict[tuple[str, int], tuple[int, str, tuple[tuple[Any, ...], ...] | None]] = {
        (r.arm, r.i): (r.seed, r.action, tuple((c.request_digest, c.args_digest, c.capture_id, c.response_digest) for c in r.calls))
        for r in got.runs
    }
    return Stored(
        "model_challenge_file", a.cycle, a.symbol, a.hours, a.source, got.removed, a.original, a.pairs, got.outcome,
        None if got.treated is None else vars(got.treated), None if got.control is None else vars(got.control), runs, a,
    )  # fmt: skip


def _old(doc: dict[str, Any]) -> Stored:
    """A result this script wrote before it wrote through ``record``: the runs' seeds and actions, and their calls once it stored them
    (before that, ``calls`` is how many there were)."""
    runs: dict[tuple[str, int], tuple[int, str, tuple[tuple[Any, ...], ...] | None]] = {}
    for run in doc["runs"]:
        if not isinstance(run, dict) or set(run) != OLD_RUN_KEYS:
            raise Refused(f"a stored run has {', '.join(sorted(OLD_RUN_KEYS))}")
        calls = run["calls"]
        tape = None
        if isinstance(calls, list):
            if not all(isinstance(c, dict) and set(TAPE_KEYS) <= set(c) for c in calls):
                raise Refused(f"a stored call has {', '.join(TAPE_KEYS)}")
            tape = tuple(tuple(c[k] for k in TAPE_KEYS) for c in calls)
        runs[(run["arm"], run["i"])] = (run["seed"], run["action"], tape)
    return Stored(
        "script before record", doc["cycle"], doc["symbol"], doc["hours"], doc["source"], tuple(sorted(doc["removed"])), doc["original"],
        doc["pairs"], doc["outcome"], doc["treated"], doc["control"], runs, None,
    )  # fmt: skip


def replay(args: argparse.Namespace) -> int:
    stored = load_stored(args.replay)
    settings = deployment.load_settings(args.settings)
    l1 = the_arm(settings)
    seeds = model_challenge.seeds(l1.seed, stored.pairs)
    if any(seeds.get(key) != seed for key, (seed, _, _) in stored.runs.items()):
        raise Refused("the stored seeds are not the ones these settings give the challenge")
    a = stored.asked
    if a is not None and (a.model, a.prompt_version, a.temperature, a.seed, a.agent_version) != (
        l1.model,
        arm_l1.PROMPT_VERSION,
        l1.temperature,
        l1.seed,
        arm_l1.agent_version_hash(l1),
    ):
        raise Refused("the stored challenge asked another arm than these settings describe")
    with deployment.assemble(settings) as running:
        at = locate(scheduler.read_all(running.services), stored.cycle, stored.symbol, stored.hours, stored.source)
        if tuple(sorted(at.removed)) != stored.removed or at.original != stored.original:
            raise Refused("the ledger's snapshot and forecast are not the ones the stored challenge was made of")
        if a is not None and a.forecast != at.forecast_id:
            raise Refused("the stored challenge names another forecast than the ledger's")
        answers = model_client.ReplayFetcher.from_store(running.captures, running.model_provider or "")
        served = {"from_store": 0, "missing": 0}

        def fetch(call_args: Mapping[str, Any], stage: str, timeout_s: float) -> Any:
            got = answers(call_args, stage, timeout_s)
            served["missing" if isinstance(got, model_client.Gone) else "from_store"] += 1
            return got

        ask = asker(model_client.ModelClient(fetch), at, l1, seeds, stored.symbol, stored.hours)
        done: dict[tuple[str, int], arm_l1.Decision] = {}

        def lookup(_reader: Reader, i: int, arm: str) -> arm_l1.Decision:
            done[(arm, i)] = ask(arm, i)
            return done[(arm, i)]

        result = model_challenge.challenge(at.frozen, at.store, at.removed, lookup, at.original, stored.pairs)
        report = compare(stored, result, done)
        report |= {"stored": str(args.replay), "stored_kind": stored.kind, "answers": served, "stored_answers": len(answers)}
        if args.out is not None and report["clean"]:
            data = model_challenge_file.record(
                asked_of(at, l1, stored.cycle, stored.symbol, stored.hours, stored.source, stored.pairs), result, done
            )
            args.out.write_bytes(data)
            report["written"] = str(args.out)
        else:
            report["written"] = None
    print(json.dumps(report, indent=1))
    return 0 if report["clean"] else 1


def compare(stored: Stored, result: model_challenge.ModelChallenge, done: Mapping[tuple[str, int], arm_l1.Decision]) -> dict[str, Any]:
    """Run by run: the stored action against the replayed one, and the stored tape against the replayed calls where it was stored."""
    different, missing, tape_different = [], [], []
    with_tape = tape_matched = 0
    for key in sorted(set(stored.runs) | set(done), key=lambda k: (k[1], ("full", "withheld", "control").index(k[0]))):
        d = done.get(key)
        replayed = None if d is None else (model_challenge.action_class(d) or model_challenge.RUN_FAILED)
        mine = stored.runs.get(key)
        if mine is None or replayed != mine[1]:
            different.append({"arm": key[0], "i": key[1], "stored": None if mine is None else mine[1], "replayed": replayed})
        if d is not None and any(c.outcome is model_client.Outcome.REPLAY_MISSING for c in d.calls):
            missing.append({"arm": key[0], "i": key[1]})
        if mine is not None and mine[2] is not None:
            with_tape += 1
            calls = () if d is None else tuple((c.request_digest, c.args_digest, c.capture_id, c.response_digest) for c in d.calls)
            if calls == mine[2]:
                tape_matched += 1
            else:
                tape_different.append({"arm": key[0], "i": key[1]})
    tables = (
        None if result.treated is None else vars(result.treated),
        None if result.control is None else vars(result.control),
    ) == (stored.treated, stored.control)
    clean = not different and not missing and not tape_different and tables and result.outcome == stored.outcome
    return {
        "cycle": stored.cycle,
        "symbol": stored.symbol,
        "hours": stored.hours,
        "source": stored.source,
        "pairs": stored.pairs,
        "runs": {"stored": len(stored.runs), "replayed": len(done), "actions_matched": len(set(stored.runs) | set(done)) - len(different)},
        "actions_different": different,
        "replay_missing": missing,
        "tape": {"runs_with_stored_tape": with_tape, "matched": tape_matched, "different": tape_different},
        "outcome": {"stored": stored.outcome, "replayed": result.outcome},
        "tables_match": tables,
        "model_calls": 0,  # the client's only fetcher answers from the capture store (model_client.ReplayFetcher); nothing is asked
        "clean": clean,
    }


def main() -> int:
    parser = argparse.ArgumentParser(prog="challenge_live", description=__doc__.split("\n\n")[0])
    parser.add_argument("settings", type=Path)
    parser.add_argument("cycle", nargs="?")
    parser.add_argument("symbol", nargs="?")
    parser.add_argument("hours", nargs="?", type=int)
    parser.add_argument("source", nargs="?")
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument(
        "--budget", type=int, default=None, help="the most model calls this challenge may make; it starts only if its worst case fits"
    )
    parser.add_argument(
        "--replay", type=Path, default=None, help="a stored challenge to rebuild offline from the capture store, with no model call"
    )
    args = parser.parse_args()
    given = [x for x in (args.cycle, args.symbol, args.hours, args.source) if x is not None]
    try:
        if args.replay is not None:
            if given or args.budget is not None:
                raise Refused("a replay takes the settings and the stored file only: the stored file says what was challenged")
            return replay(args)
        if len(given) != 4 or args.budget is None or args.out is None:
            raise Refused("a live challenge needs CYCLE, SYMBOL, HOURS and SOURCE, --budget and --out")
        return live(args)
    except (Refused, OSError, deployment.DeploymentError, model_challenge.ChallengeError, model_challenge_file.ChallengeFileError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
