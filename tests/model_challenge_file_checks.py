"""Checks of the file of a model arm's challenge (``hoshitori.model_challenge_file``), runnable against a mutant.

The challenges here are made by the real ``model_challenge.challenge`` with scripted stand-ins for the arm, each run with a tape of its
own (the digests of its calls, as ``model_client`` records them). What is checked: that ``read`` gives back what ``record`` wrote for
every outcome, that it refuses every file ``record`` would not write with an exact message, that it re-derives the result from the runs
alone, that ``bind`` holds a file to the ledger of a real league with a model arm, and the rows the What if page reads.

The three files under ``tests/recorded_model_challenges/`` are recorded, not made up: the C1 live challenge of the single-judgment arm
(20 pairs, Binance's funding reading taken out; BTC, ETH and SOL at one hour; cycle ``cymsmyj5ihfp3dzsfqr7hdbustpx``), written again
through ``record`` from their offline replay against the league's capture store, in which every run's action, and every request digest
and capture the live file had stored, equalled the live file's (``scripts/challenge_live.py --replay``). They hold ids, digests, seeds
and the arm's own codes only.
"""

from __future__ import annotations

import hashlib
import random
import re
import types
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from functools import cache
from pathlib import Path
from typing import Any

from hoshitori import arm_l1, canonical, forecast, lineage, model_challenge, model_client, paired_stats, snapshot
from tests.intervention_checks import Liar, world

D = Decimal
V, R, G = arm_l1.Verdict, arm_l1.Reason, arm_l1.Gate
OC = model_client.Outcome
HOUR_MS = 3_600_000
HERE = Path(__file__).resolve().parent
RECORDED = HERE / "recorded_model_challenges"
FORMAT_TS = HERE.parent / "web" / "src" / "format.ts"
CYCLE = "cy" + "a" * 26
FORECAST = "fc" + "b" * 26
VERSION = "0" * 64
FORM = "the challenge file is not written in canonical form with one newline at its end"


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
        except self.mod.ChallengeFileError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")

    def reads(self, label: str, data: bytes, says: str) -> None:
        self.refuses(label, lambda: self.mod.read(data), says)


# --- runs with a tape of their own -------------------------------------------------------------------------------------------------
def hexed(*parts: object) -> str:
    return hashlib.sha256(repr(parts).encode()).hexdigest()


def evidence_id(k: int) -> str:
    """A well-formed evidence id that no item has."""
    return "ev" + "a" * 21 + format(k, "05b").translate(str.maketrans("01", "bc"))


class Tape:
    """Calls as the client records them: each run its own request, each answer its own capture; no capture when no answer came."""

    def __init__(self) -> None:
        self.n = 0

    def calls(self, key: tuple[str, int], seed: int, outcomes: tuple[Any, ...]) -> tuple[model_client.ModelCall, ...]:
        first = hexed(key, 1)
        out = []
        for attempt, outcome in enumerate(outcomes, 1):
            self.n += 1
            came = outcome not in (OC.UNAVAILABLE, OC.REPLAY_MISSING, OC.BUDGET)
            out.append(
                model_client.ModelCall(
                    stage=arm_l1.STAGE,
                    prompt_version=arm_l1.PROMPT_VERSION,
                    request_digest=first,
                    args_digest=hexed(key, attempt),
                    capture_id=f"cap-{self.n:032x}" if came else None,
                    response_digest=hexed("answer", self.n) if came else None,
                    model="fake/model",
                    reported_model=None,
                    max_tokens=1000,
                    temperature="0.7",
                    seed=seed,
                    response_format="json_schema",
                    attempt=attempt,
                    usage=None,
                    latency_ms=None,
                    finish_reason=None,
                    reasoning_chars=0,
                    outcome=outcome,
                    detail=None,
                )
            )
        return tuple(out)


# what each kind of run decided, as arm_l1.propose decides: (verdict, reason, p, the proposal's decision and p, gate, its calls' outcomes)
KINDS: dict[str, tuple[Any, Any, str | None, tuple[str, str | None] | None, Any, tuple[Any, ...]]] = {
    "up": (V.CALL, None, "0.6", ("CALL", "0.6"), G.NONE, (OC.VALID,)),
    "down": (V.CALL, None, "0.4", ("CALL", "0.4"), G.NONE, (OC.VALID,)),
    "abstain": (V.NO_CALL, R.OUT_OF_SCOPE, None, ("NO_CALL", None), G.NONE, (OC.VALID,)),
    "gated": (V.NO_CALL, R.ESSENTIAL_PREMISE_UNPROVEN, None, ("CALL", "0.6"), G.GROUNDING, (OC.VALID,)),
    "gated_down": (V.NO_CALL, R.ESSENTIAL_PREMISE_UNPROVEN, None, ("CALL", "0.4"), G.GROUNDING, (OC.VALID,)),
    "repaired": (V.CALL, None, "0.6", ("CALL", "0.6"), G.NONE, (OC.INVALID, OC.VALID)),
    "failed": (V.FAILED, R.MODEL_UNAVAILABLE, None, None, G.NONE, (OC.UNAVAILABLE,)),
    "invalid": (V.FAILED, R.MODEL_OUTPUT_INVALID, None, None, G.NONE, (OC.INVALID, OC.INVALID)),
    "unread": (V.FAILED, R.MODEL_UNAVAILABLE, None, None, G.NONE, (OC.UNREADABLE,)),
    "missing_answer": (V.FAILED, R.MODEL_UNAVAILABLE, None, None, G.NONE, (OC.REPLAY_MISSING,)),
    "missing": (V.FAILED_EVIDENCE, R.MISSING_EVIDENCE, None, None, G.NONE, ()),
}


def decided(kind: str, key: tuple[str, int], seed: int, tape: Tape, cite: str | None) -> arm_l1.Decision:
    verdict, reason, p, prop, gate, outcomes = KINDS[kind]
    cites = (cite,) if prop is not None and cite is not None else ()
    made = None if prop is None else arm_l1.Proposal(prop[0], None if prop[1] is None else D(prop[1]), None, (), cites, (), None)
    return arm_l1.Decision(
        "cd-btc", 1, verdict, reason, None if p is None else D(p), cites, "h", (), "v", made, gate, None, tape.calls(key, seed, outcomes)
    )


Script = Callable[[str, int], str]


def challenged(
    script: Script, pairs: int = 20, original: str = "UP", seed: int | None = None, liar: bool = False
) -> tuple[Any, dict[tuple[str, int], arm_l1.Decision]]:
    """The real challenge with a scripted arm, withholding BTC's RYO item (or, with a lineage that lies, its bars, which leak); every run
    the arm was asked, by arm and pair. Every run cites the interpretation item, which stays."""
    w, snap, *_ = world()
    store: Any = w.store
    if liar:
        store = Liar()
        for ident in sorted(w.store.ids(), key=lambda i: len(w.store.ancestors(i))):
            store.add(w.store.get(ident), w.store.digest(ident))
    seeds = model_challenge.seeds(seed, pairs)
    tape = Tape()
    done: dict[tuple[str, int], arm_l1.Decision] = {}

    def lookup(_reader: Any, i: int, arm: str) -> arm_l1.Decision:
        done[(arm, i)] = decided(script(arm, i), (arm, i), seeds[(arm, i)], tape, w.btc_int)
        return done[(arm, i)]

    result = model_challenge.challenge(snap, store, (w.btc_bars if liar else w.btc_ryo,), lookup, original, pairs)
    return result, done


def asked(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "cycle": CYCLE,
        "forecast": FORECAST,
        "symbol": "BTC",
        "hours": 1,
        "source": "ryo",
        "original": "UP",
        "pairs": 20,
        "model": "fake/model",
        "prompt_version": arm_l1.PROMPT_VERSION,
        "temperature": "0.7",
        "seed": None,
        "agent_version": VERSION,
    }
    return mod.Asked(**{**values, **over})


def made(mod: types.ModuleType, script: Script, pairs: int = 20, original: str = "UP", seed: int | None = None, **over: Any) -> bytes:
    result, done = challenged(script, pairs, original, seed)
    return mod.record(asked(mod, pairs=pairs, original=original, seed=seed, **over), result, done)  # type: ignore[no-any-return]


def edited(data: bytes, change: Callable[[dict[str, Any]], object]) -> bytes:
    doc: dict[str, Any] = canonical.parse_record(data)
    change(doc)
    return canonical.dumps(doc) + b"\n"


def put(path: tuple[Any, ...], value: Any) -> Callable[[dict[str, Any]], None]:
    def change(doc: dict[str, Any]) -> None:
        target: Any = doc
        for step in path[:-1]:
            target = target[step]
        target[path[-1]] = value

    return change


def uncertain(arm: str, i: int) -> str:
    return "down" if arm == "withheld" and i < 3 else "up"


def changed(arm: str, i: int) -> str:
    return "down" if arm == "withheld" else "up"


def required(arm: str, i: int) -> str:
    return "missing" if arm == "withheld" else "up"


def variation(arm: str, i: int) -> str:
    """Without the evidence five runs are stopped by the gate and five propose another action: the action moved, and neither layer alone
    moved beyond what five pairs can show (the exact test of five against none is 0.0625)."""
    if arm != "withheld" or i >= 10:
        return "up"
    return "gated" if i < 5 else "down"


# --- the checks ----------------------------------------------------------------------------------------------------------------------
def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the format", mod.FORMAT, 1)
    c.eq(
        "the members of a file, in their canonical order",
        mod.KEYS,
        (
            "agent_version",
            "arm",
            "control",
            "cycle",
            "failed",
            "forecast",
            "format",
            "hours",
            "leaks",
            "mechanism",
            "model",
            "original",
            "outcome",
            "pairs",
            "prompt_version",
            "removed",
            "runs",
            "seed",
            "source",
            "symbol",
            "temperature",
            "treated",
            "verdict",
            "withheld",
        ),
    )
    c.eq("of a run", mod.RUN_KEYS, ("action", "arm", "calls", "cites", "gate", "i", "p", "proposal_p", "reason", "seed", "verdict"))
    c.eq("of a call", mod.CALL_KEYS, ("args_digest", "attempt", "capture_id", "outcome", "request_digest", "response_digest"))
    c.eq("of a table", mod.TABLE_KEYS, ("changed_changed", "changed_same", "same_changed", "same_same"))
    c.eq("of a verdict", mod.VERDICT_KEYS, ("high", "low", "n", "note", "p", "shift"))
    c.eq("the committed actions", mod.ORIGINALS, ("UP", "DOWN", "FLAT"))
    c.eq(
        "the outcomes",
        mod.OUTCOMES,
        (
            "DECISION_CHANGED",
            "DECISION_STABLE",
            "EFFECT_UNCERTAIN",
            "NOT_EVALUATED(unstable_baseline)",
            "NOT_EVALUATED(structural_failure)",
            "EVIDENCE_REQUIRED",
            "NOT_EVALUATED",
        ),
    )
    c.eq("the mechanisms", mod.MECHANISMS, ("MODEL_REVISED", "GATE_BLOCKED", "BOTH", "VARIATION_ONLY"))
    c.eq("the outcomes of a call with no answer", sorted(mod.NO_ANSWER), ["BUDGET", "REPLAY_MISSING", "UNAVAILABLE"])
    for name in ("Asked", "Call", "Run", "Challenge", "Row"):
        c.eq(f"{name} is frozen", getattr(mod, name).__dataclass_params__.frozen, True)
    return c.bad


SCENARIOS: tuple[tuple[str, Script, int], ...] = (
    ("the model changes its mind", changed, 20),
    ("a gate stops it", lambda arm, i: "gated" if arm == "withheld" else "up", 20),
    ("the proposal and the gate both move", lambda arm, i: "gated_down" if arm == "withheld" else "up", 20),
    ("the action moves and neither layer alone does", variation, 20),
    ("nothing moves in sixty pairs", lambda arm, i: "up", 60),
    ("too few runs to say", uncertain, 20),
    ("asked again it already shifts", lambda arm, i: "down" if arm == "control" else "up", 20),
    ("too many runs failed", lambda arm, i: "failed" if i < 8 else "up", 20),
    ("repairs, failures and abstentions", lambda arm, i: ("repaired", "invalid", "abstain", "up", "up", "unread", "up")[i % 7], 21),
    ("one pair, the least a file holds", lambda arm, i: "up", 1),
    ("a stored answer missing from a replay", lambda arm, i: "missing_answer" if arm == "control" and i == 3 else "up", 20),
)


def as_read(d: arm_l1.Decision) -> tuple[Any, ...]:
    """A decision as a file holds it."""
    return (
        d.verdict.value,
        None if d.reason is None else d.reason.value,
        None if d.p is None else str(d.p),
        None if d.proposal is None or d.proposal.p is None else str(d.proposal.p),
        d.gate.value,
        model_challenge.action_class(d) or "RUN_FAILED",
        d.evidence_ids,
        tuple((x.attempt, x.outcome.value, x.request_digest, x.args_digest, x.capture_id, x.response_digest) for x in d.calls),
    )


def check_round_trip(mod: types.ModuleType) -> list[str]:
    """What ``read`` gives back is what the challenge said, for every outcome, and the bytes are the canonical record of it."""
    c = Case(mod)
    seen: set[str] = set()
    for label, script, pairs in SCENARIOS:
        result, done = challenged(script, pairs)
        data = mod.record(asked(mod, pairs=pairs), result, done)
        got = mod.read(data)
        seen.add(got.outcome)
        c.eq(f"{label}: one newline after the canonical record", data, canonical.dumps(canonical.parse_record(data)) + b"\n")
        c.eq(
            f"{label}: the result",
            (got.outcome, got.mechanism, got.failed, got.treated, got.control, got.verdict),
            (result.outcome, result.mechanism, result.failed, result.treated, result.control, result.verdict),
        )
        c.eq(
            f"{label}: the intervention",
            (got.removed, got.withheld, got.leaks),
            (result.intervention.removed, result.intervention.withheld, result.intervention.leaks),
        )
        c.eq(
            f"{label}: every run, pair by pair",
            [(r.arm, r.i, r.seed) for r in got.runs],
            [(arm, i, s) for (arm, i), s in model_challenge.seeds(None, pairs).items()],
        )
        c.eq(f"{label}: attempted", got.attempted, 3 * pairs)
        c.eq(f"{label}: what was asked", got.asked, asked(mod, pairs=pairs))
        c.eq(
            f"{label}: each run as it was decided, with its tape",
            [(r.verdict, r.reason, r.p, r.proposal_p, r.gate, r.action, r.cites, tuple(
                (x.attempt, x.outcome, x.request_digest, x.args_digest, x.capture_id, x.response_digest) for x in r.calls
            )) for r in got.runs],
            [as_read(done[(r.arm, r.i)]) for r in got.runs],
        )  # fmt: skip
    c.eq(
        "every label of a challenge with runs was made",
        sorted(seen),
        sorted(
            [
                "DECISION_CHANGED",
                "DECISION_STABLE",
                "EFFECT_UNCERTAIN",
                "NOT_EVALUATED(unstable_baseline)",
                "NOT_EVALUATED(structural_failure)",
            ]
        ),
    )
    mechanisms = [mod.read(made(mod, script, pairs)).mechanism for _, script, pairs in SCENARIOS[:4]]
    c.eq("and every mechanism", mechanisms, ["MODEL_REVISED", "GATE_BLOCKED", "BOTH", "VARIATION_ONLY"])
    data = made(mod, changed, 2, seed=40, temperature=None)
    c.eq("the arm's own seed is the base of every run's", [r.seed for r in mod.read(data).runs], [40, 40, 42, 41, 41, 43])
    c.eq("a temperature left to the provider", mod.read(data).asked.temperature, None)
    for original in ("DOWN", "FLAT"):
        c.eq(f"the committed action {original}", mod.read(made(mod, changed, 20, original)).asked.original, original)
    rng = random.Random(7)
    kinds = ("up",) * 6 + ("down",) * 3 + ("abstain", "gated", "gated_down", "repaired", "failed")
    for k in range(25):
        table = {(arm, i): rng.choice(kinds) for i in range(20) for arm in ("full", "withheld", "control")}
        original = rng.choice(("UP", "DOWN", "FLAT"))
        result, done = challenged(lambda arm, i, t=table: t[(arm, i)], 20, original)  # type: ignore[misc]
        got = mod.read(mod.record(asked(mod, original=original), result, done))
        c.eq(
            f"random challenge {k}: read derives what the challenge gave",
            (got.outcome, got.mechanism, got.failed, got.treated, got.control, got.verdict),
            (result.outcome, result.mechanism, result.failed, result.treated, result.control, result.verdict),
        )
    return c.bad


def check_without_runs(mod: types.ModuleType) -> list[str]:
    """A required source (known from the first run without it) and a leaking intervention (known before any run)."""
    c = Case(mod)
    result, done = challenged(required)
    got = mod.read(mod.record(asked(mod), result, done))
    c.eq(
        "a required source: one run, and nothing to count",
        (got.outcome, got.mechanism, got.failed, got.treated, got.control, got.verdict, got.attempted, [(r.arm, r.i) for r in got.runs]),
        ("EVIDENCE_REQUIRED", None, 0, None, None, None, 1, [("withheld", 0)]),
    )
    result, done = challenged(lambda arm, i: "up", liar=True)
    got = mod.read(mod.record(asked(mod), result, done))
    c.eq(
        "a leak: no run, and the items that still stand on what was removed",
        (got.outcome, got.runs, got.attempted, got.leaks, got.withheld, len(got.leaks)),
        ("NOT_EVALUATED", (), 0, result.intervention.leaks, result.intervention.withheld, 2),
    )
    return c.bad


def check_record(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    result, done = challenged(changed)
    c.refuses(
        "a decision of a run the challenge does not have",
        lambda: mod.record(asked(mod), result, {**done, ("full", 20): done[("full", 0)]}),
        "a decision is of no run of this challenge",
    )
    wrong = replace(done[("control", 2)], calls=tuple(replace(x, seed=5) for x in done[("control", 2)].calls))
    c.refuses(
        "a run asked with another seed than its own",
        lambda: mod.record(asked(mod), result, {**done, ("control", 2): wrong}),
        "run control 2: a call was not asked with the run's seed 22",
    )
    c.refuses(
        "other pairs said than were run: the control runs had other seeds",
        lambda: mod.record(asked(mod, pairs=21), result, done),
        "run control 0: a call was not asked with the run's seed 21",
    )
    c.refuses(
        "a run left out: the reader refuses it, so nothing is returned",
        lambda: mod.record(asked(mod), result, {k: d for k, d in done.items() if k != ("control", 19)}),
        "the runs are every pair's full, withheld and control run, pair by pair",
    )
    c.refuses(
        "a result that is not the runs'",
        lambda: mod.record(asked(mod), replace(result, failed=1), done),
        "the failed the file states is not the one its runs give",
    )
    c.refuses(
        "seeds a call cannot take",
        lambda: mod.record(asked(mod, seed=2**31 - 10), result, done),
        "the seeds of 20 pairs from 2147483638 are 2147483638 to 2147483677, and a call takes a seed from 0 to 2147483647",
    )
    return c.bad


def check_bytes_and_members(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = made(mod, uncertain)
    c.eq("it reads", mod.read(data).outcome, "EFFECT_UNCERTAIN")
    c.reads("not UTF-8", b"\xff", "the challenge file is not a canonical record: invalid UTF-8: invalid start byte")
    c.reads("not JSON", b"{", "the challenge file is not a canonical record: object key must be a string at offset 1")
    c.reads("a list", b"[]\n", "the challenge file is not a canonical record: a record must be a JSON object")
    fraction = data[:-2] + b',"x":0.5}\n'
    c.reads(
        "a number with a fraction",
        fraction,
        f"the challenge file is not a canonical record: fractional or exponent number; quantities are decimal strings at offset {fraction.rindex(b'.')}",
    )
    c.reads("no newline", data[:-1], FORM)
    c.reads("two newlines", data + b"\n", FORM)
    c.reads("a space", data.replace(b'":', b'": ', 1), FORM)
    doc = canonical.parse_record(data)
    turned = b"{" + b",".join(canonical.dumps({k: doc[k]})[1:-1] for k in sorted(doc, reverse=True)) + b"}\n"
    c.reads("members out of order", turned, FORM)
    keys = ", ".join(mod.KEYS)
    c.reads("a member missing", edited(data, lambda d: d.pop("seed")), f"a challenge file has {keys}")
    c.reads("a member more", edited(data, put(("extra",), 1)), f"a challenge file has {keys}")
    for bad in (2, True, "1"):
        c.reads(f"format {bad!r}", edited(data, put(("format",), bad)), "the challenge file is of format 1")
    c.reads("another arm", edited(data, put(("arm",), "L2")), "the arm is L1, the single judgment")
    for name, bad, says in (
        ("cycle", "cy" + "a" * 25, "the cycle is a cycle id"),
        ("cycle", "fc" + "a" * 26, "the cycle is a cycle id"),
        ("cycle", 7, "the cycle is a cycle id"),
        ("forecast", "fc" + "A" * 26, "the forecast is a forecast id"),
        ("forecast", "fc" + "a" * 27, "the forecast is a forecast id"),
        ("symbol", "btc", "the symbol is an upper-case symbol of at most 40 characters"),
        ("symbol", "", "the symbol is an upper-case symbol of at most 40 characters"),
        ("symbol", "B" * 41, "the symbol is an upper-case symbol of at most 40 characters"),
        ("symbol", ["BTC"], "the symbol is an upper-case symbol of at most 40 characters"),
        ("hours", 2, "the horizon is 1, 4 or 24 hours"),
        ("hours", True, "the horizon is 1, 4 or 24 hours"),
        ("hours", "1", "the horizon is 1, 4 or 24 hours"),
        ("source", "Binance Funding", "the source is a source name"),
        ("source", "", "the source is a source name"),
        ("source", None, "the source is a source name"),
        ("original", "NO_CALL", "the committed action is UP, DOWN or FLAT"),
        ("original", ["UP"], "the committed action is UP, DOWN or FLAT"),
        ("pairs", 0, "the pairs are a whole number of at least 1"),
        ("pairs", True, "the pairs are a whole number of at least 1"),
        ("model", "-model", "the model is a model id"),
        ("model", "m" * 101, "the model is a model id"),
        ("prompt_version", "L1-v5", "the prompt version is a version name"),
        ("temperature", "0.7500", "the temperature is null or a decimal from 0 to 2 with at most three places"),
        ("temperature", "3", "the temperature is null or a decimal from 0 to 2 with at most three places"),
        ("temperature", 1, "the temperature is null or a decimal from 0 to 2 with at most three places"),
        ("seed", -1, "the seed is null or a whole number of at least 0"),
        ("seed", True, "the seed is null or a whole number of at least 0"),
        ("seed", "0", "the seed is null or a whole number of at least 0"),
        ("agent_version", "0" * 63, "the agent version is a SHA-256 digest in lower-case hexadecimal"),
        ("agent_version", "A" * 64, "the agent version is a SHA-256 digest in lower-case hexadecimal"),
    ):
        c.reads(f"{name} {bad!r}", edited(data, put((name,), bad)), says)
    c.eq("a symbol of forty", mod.read(made(mod, uncertain, symbol="B" * 40)).asked.symbol, "B" * 40)
    c.eq("digits in a symbol", mod.read(made(mod, uncertain, symbol="1000PEPE")).asked.symbol, "1000PEPE")
    c.eq("the other horizons", [mod.read(made(mod, uncertain, hours=h)).asked.hours for h in (4, 24)], [4, 24])
    c.eq("a seed of 0", mod.read(made(mod, uncertain, seed=0)).asked.seed, 0)
    c.reads(
        "a seed too large for the last control run",
        edited(data, put(("seed",), 2**31 - 39)),
        "the seeds of 20 pairs from 2147483609 are 2147483609 to 2147483648, and a call takes a seed from 0 to 2147483647",
    )
    removed = doc["removed"][0]
    other = evidence_id(1)
    for name, bad in (
        ("removed", []),
        ("removed", ["x"]),
        ("removed", [removed, removed]),
        ("removed", removed),
        ("withheld", []),
        ("withheld", [removed, other] if removed > other else [other, removed]),
        ("leaks", "none"),
        ("leaks", [7]),
    ):
        what = {"removed": "removed", "withheld": "withheld", "leaks": "leaking"}[name]
        least = "" if name == "leaks" else "at least one "
        c.reads(f"{name} {bad!r}", edited(data, put((name,), bad)), f"the {what} items are {least}evidence ids, sorted and once each")
    rule = "what was removed was withheld, and nothing that still stands on it was"
    c.reads("a removed item that was not withheld", edited(data, put(("withheld",), [other])), rule)
    c.reads("a leak that was withheld", edited(data, put(("leaks",), [removed])), rule)
    c.reads("runs that are not a list", edited(data, put(("runs",), {})), "the runs are a list")
    return c.bad


def check_runs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = made(mod, lambda arm, i: ("up", "abstain", "gated", "repaired", "invalid", "failed")[i % 6])
    c.eq("it reads", mod.read(data).outcome in mod.OUTCOMES, True)
    doc = canonical.parse_record(data)
    at = {(r["arm"], r["i"]): k for k, r in enumerate(doc["runs"])}
    keys = ", ".join(mod.RUN_KEYS)

    def run(arm: str, i: int, name: str, value: Any) -> bytes:
        return edited(data, put(("runs", at[(arm, i)], name), value))

    def outcome(arm: str, i: int, k: int, value: str) -> bytes:
        return edited(data, put(("runs", at[(arm, i)], "calls", k, "outcome"), value))

    c.reads("a run that is not an object", edited(data, put(("runs", 0), [])), f"a run has {keys}")
    c.reads("a run without its seed", edited(data, lambda d: d["runs"][0].pop("seed")), f"a run has {keys}")
    which = "a run is the full, withheld or control run of one of the challenge's pairs"
    c.reads("an arm that is not one", run("full", 0, "arm", "baseline"), which)
    c.reads("an arm that is a list", run("full", 0, "arm", ["full"]), which)
    c.reads("a pair index that is a list", run("full", 0, "i", [0]), which)
    c.reads("a pair index that is true", run("full", 1, "i", True), which)
    c.reads("a pair past the last", run("full", 0, "i", 20), which)
    c.reads("a seed of another run", run("control", 3, "seed", 3), "run control 3: its seed is not 23, the one the challenge gives it")
    c.reads("a seed that is true", run("full", 1, "seed", True), "run full 1: its seed is not 1, the one the challenge gives it")
    words = "the verdict, the reason and the gate are the arm's own words"
    c.reads("a verdict", run("full", 0, "verdict", "MAYBE"), f"run full 0: {words}")
    c.reads("a gate", run("full", 0, "gate", "RISK"), f"run full 0: {words}")
    c.reads("a reason", run("full", 1, "reason", "TIRED"), f"run full 1: {words}")
    p_rule = "a probability is null or a decimal from 0 to 1 with at most six places"
    for bad in ("1.5", "0.1234567", ".5", "0,5", "-0", "01", 1):
        c.reads(f"p {bad!r}", run("full", 0, "p", bad), f"run full 0: {p_rule}")
    c.reads("a proposal's p", run("full", 0, "proposal_p", "2"), f"run full 0: {p_rule}")
    edges = ("1", "1.000000", "0", "0.000001", "0.5")  # as the proposal of a run the gate stopped, which moves no count here
    edge = [mod.read(edited(data, lambda d, x=x: d["runs"][6].update(proposal_p=x))).runs[6].proposal_p for x in edges]  # type: ignore[misc]
    c.eq("the edges of a probability", edge, list(edges))
    cite_rule = "the cites are at most 16 evidence ids, once each"
    cited = doc["runs"][at[("full", 0)]]["cites"][0]
    many = [evidence_id(k) for k in range(17)]
    c.reads("a cite that is not an id", run("full", 0, "cites", ["x"]), f"run full 0: {cite_rule}")
    c.reads("a cite twice", run("full", 0, "cites", [cited, cited]), f"run full 0: {cite_rule}")
    c.reads("cites that are not a list", run("full", 0, "cites", cited), f"run full 0: {cite_rule}")
    c.reads("seventeen cites", run("full", 0, "cites", many), f"run full 0: {cite_rule}")
    c.eq(
        "sixteen are allowed, in the order given", mod.read(run("full", 0, "cites", many[:16][::-1])).runs[0].cites, tuple(many[:16][::-1])
    )
    withheld = doc["withheld"][0]
    c.reads(
        "a run without the source citing what was taken out",
        run("withheld", 1, "cites", [withheld]),
        "run withheld 1: it cites an item that was taken out",
    )
    c.eq("a full run may cite it", mod.read(run("full", 0, "cites", [withheld])).runs[0].cites, (withheld,))
    call = "a call comes from a valid answer, with its proposal's probability, at least one cite, no reason and no gate"
    c.reads("a call with a reason", run("full", 0, "reason", "OUT_OF_SCOPE"), f"run full 0: {call}")
    c.reads("a call with a gate", run("full", 0, "gate", "GROUNDING"), f"run full 0: {call}")
    c.reads("a call with no probability", run("full", 0, "p", None), f"run full 0: {call}")
    c.reads("a call whose proposal said another", run("full", 0, "proposal_p", "0.7"), f"run full 0: {call}")
    c.reads("a call whose proposal was no call", run("full", 0, "proposal_p", None), f"run full 0: {call}")
    c.reads("a call without a cite", run("full", 0, "cites", []), f"run full 0: {call}")
    c.reads("a call from an answer that did not come", outcome("full", 0, 0, "TRUNCATED"), f"run full 0: {call}")
    own = "the model's own no-call comes from a valid answer, with one of its reasons and no probability"
    c.reads("an abstention with a probability", run("full", 1, "p", "0.6"), f"run full 1: {own}")
    c.reads("an abstention whose proposal was a call", run("full", 1, "proposal_p", "0.6"), f"run full 1: {own}")
    c.reads("an abstention for a reason the model has not", run("full", 1, "reason", "MISSING_EVIDENCE"), f"run full 1: {own}")
    c.reads("an abstention with no reason", run("full", 1, "reason", None), f"run full 1: {own}")
    c.reads("an abstention from an answer that did not come", outcome("full", 1, 0, "EMPTY"), f"run full 1: {own}")
    c.eq(
        "each of the model's reasons",
        [mod.read(run("full", 1, "reason", x)).runs[3].reason for x in arm_l1.ABSTENTIONS],
        list(arm_l1.ABSTENTIONS),
    )
    c.eq("an abstention with no cite", mod.read(run("full", 1, "cites", [])).runs[3].cites, ())
    gate = "a no-call of the grounding gate comes from a valid answer, with the reason ESSENTIAL_PREMISE_UNPROVEN and no probability"
    c.reads("a gated no-call for another reason", run("full", 2, "reason", "OUT_OF_SCOPE"), f"run full 2: {gate}")
    c.reads("a gated no-call with a probability", run("full", 2, "p", "0.6"), f"run full 2: {gate}")
    c.reads("a gated no-call from an answer that did not come", outcome("full", 2, 0, "REFUSED"), f"run full 2: {gate}")
    c.eq("a gate may stop a proposal of no call", mod.read(run("full", 2, "proposal_p", None)).runs[6].proposal_p, None)
    failed = "a failed run has the reason of its last call's outcome, no probability, no proposal, no gate and no cite"
    c.reads("a failed run for another reason", run("full", 4, "reason", "MODEL_UNAVAILABLE"), f"run full 4: {failed}")
    c.reads("a failed run with a probability", run("full", 4, "p", "0.6"), f"run full 4: {failed}")
    c.reads("a failed run with a proposal", run("full", 4, "proposal_p", "0.6"), f"run full 4: {failed}")
    c.reads("a failed run with a gate", run("full", 4, "gate", "GROUNDING"), f"run full 4: {failed}")
    c.reads("a failed run that cites", run("full", 4, "cites", [cited]), f"run full 4: {failed}")
    c.reads("a failed run with no call", run("full", 5, "calls", []), f"run full 5: {failed}")
    c.reads("a failed run whose last answer was valid", outcome("full", 4, 1, "VALID"), f"run full 4: {failed}")
    for code, reason in (
        ("TRUNCATED", "MODEL_TRUNCATED"),
        ("EMPTY", "MODEL_EMPTY"),
        ("REFUSED", "MODEL_REFUSED"),
        ("BUDGET", "BUDGET_SPENT"),
    ):

        def last(d: dict[str, Any], o: str = code, r: str = reason) -> None:
            k = d["runs"][at[("full", 4)]]
            k["calls"][1]["outcome"] = o
            k["reason"] = r
            if o == "BUDGET":
                k["calls"][1]["capture_id"] = k["calls"][1]["response_digest"] = None

        c.eq(f"a failed run whose last call was {code}", mod.read(edited(data, last)).runs[12].reason, reason)
    c.reads(
        "an action that is not the decision's", run("full", 0, "action", "DOWN"), "run full 0: the action is not the one its decision gives"
    )
    c.reads("a failed run's action", run("full", 4, "action", "FAILED"), "run full 4: the action is not the one its decision gives")
    lacks = made(mod, required)
    missing = "a run that lacks evidence asked no model and has the reason MISSING_EVIDENCE and nothing else"

    def lacking(**over: Any) -> bytes:
        return edited(lacks, lambda d: d["runs"][0].update(over))

    c.reads("lacking evidence for another reason", lacking(reason="MODEL_UNAVAILABLE"), f"run withheld 0: {missing}")
    c.reads("lacking evidence with a probability", lacking(p="0.5"), f"run withheld 0: {missing}")
    c.reads("lacking evidence with a proposal", lacking(proposal_p="0.5"), f"run withheld 0: {missing}")
    c.reads("lacking evidence with a gate", lacking(gate="GROUNDING"), f"run withheld 0: {missing}")
    c.reads("lacking evidence with a cite", lacking(cites=[cited]), f"run withheld 0: {missing}")
    c.reads("lacking evidence after asking a model", lacking(calls=doc["runs"][at[("full", 0)]]["calls"]), f"run withheld 0: {missing}")
    return c.bad


def check_run_set(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = made(mod, uncertain)
    every = "the runs are every pair's full, withheld and control run, pair by pair"
    c.reads("a run missing", edited(data, lambda d: d["runs"].pop(5)), every)
    c.reads("a run twice", edited(data, lambda d: d["runs"].append(d["runs"][4])), every)
    c.reads("two runs of a pair swapped", edited(data, lambda d: d["runs"].insert(0, d["runs"].pop(1))), every)
    c.reads("no run", edited(data, put(("runs",), [])), every)
    lacks = made(mod, required)
    probe = canonical.parse_record(lacks)["runs"][0]
    c.reads(
        "a challenge that stops at its first run without the source, a source that was not required",
        edited(data, lambda d: d.update(runs=[r for r in d["runs"] if (r["arm"], r["i"]) == ("withheld", 0)])),
        "a challenge stops at its first run without the source only when that source is required",
    )
    c.reads("a required source and more runs", edited(lacks, lambda d: d["runs"].append(canonical.parse_record(data)["runs"][2])), every)
    lacking = "a run lacks evidence only when the source is required, and then it is the challenge's only run"
    c.reads("a run without the source lacking evidence in a challenge that went on", edited(data, put(("runs", 1), probe)), lacking)
    c.reads("a full run lacking evidence", edited(data, put(("runs", 0), {**probe, "arm": "full"})), lacking)
    result, done = challenged(lambda arm, i: "up", liar=True)
    leaked = mod.record(asked(mod), result, done)
    c.reads(
        "a leak with runs",
        edited(leaked, put(("runs",), canonical.parse_record(data)["runs"])),
        "nothing is evaluated when something left still stands on what was removed, so no run is asked",
    )
    return c.bad


def check_tape(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = made(mod, lambda arm, i: ("up", "repaired", "failed", "invalid")[i % 4])
    doc = canonical.parse_record(data)
    at = {(r["arm"], r["i"]): k for k, r in enumerate(doc["runs"])}
    keys = ", ".join(mod.CALL_KEYS)

    def call(arm: str, i: int, k: int, name: str, value: Any) -> bytes:
        return edited(data, put(("runs", at[(arm, i)], "calls", k, name), value))

    def calls(arm: str, i: int, change: Callable[[list[Any]], object]) -> bytes:
        return edited(data, lambda d: change(d["runs"][at[(arm, i)]]["calls"]))

    two = "a run has at most two calls: the answer and its one repair"
    c.reads("three calls", calls("full", 1, lambda x: x.append(x[1])), f"run full 1: {two}")
    c.reads("calls that are not a list", edited(data, put(("runs", 0, "calls"), {})), f"run full 0: {two}")
    c.reads("a call that is not an object", calls("full", 0, lambda x: x.__setitem__(0, [])), f"run full 0: a call has {keys}")
    c.reads("a call without its capture", calls("full", 0, lambda x: x[0].pop("capture_id")), f"run full 0: a call has {keys}")
    order = "the calls are attempt 1 and then attempt 2"
    c.reads("attempts out of order", calls("full", 1, lambda x: x.reverse()), f"run full 1: {order}")
    c.reads("a first attempt numbered 2", call("full", 0, 0, "attempt", 2), f"run full 0: {order}")
    c.reads("an attempt that is true", call("full", 0, 0, "attempt", True), f"run full 0: {order}")
    c.reads("an outcome", call("full", 0, 0, "outcome", "LOST"), "run full 0: a call's outcome is one of the client's")
    form = "a digest is 64 hexadecimal digits in lower case"
    c.reads("a short request digest", call("full", 0, 0, "request_digest", "ab"), f"run full 0: {form}")
    c.reads("an upper-case arguments digest", call("full", 0, 0, "args_digest", "A" * 64), f"run full 0: {form}")
    c.reads("an arguments digest that is a number", call("full", 0, 0, "args_digest", 7), f"run full 0: {form}")
    came = "a call has a capture and the digest of its answer exactly when an answer came"
    c.reads("an answer without its capture", call("full", 0, 0, "capture_id", None), f"run full 0: {came}")
    c.reads("an answer without its digest", call("full", 0, 0, "response_digest", None), f"run full 0: {came}")
    c.reads("no answer but a capture", call("full", 2, 0, "capture_id", "cap-" + "1" * 32), f"run full 2: {came}")
    c.reads("no answer but a digest", call("full", 2, 0, "response_digest", "1" * 64), f"run full 2: {came}")
    named = "a capture is named by its id and an answer by its digest"
    c.reads("a capture id", call("full", 0, 0, "capture_id", "cap-1"), f"run full 0: {named}")
    c.reads("a capture id that is a number", call("full", 0, 0, "capture_id", 1), f"run full 0: {named}")
    c.reads("an answer's digest", call("full", 0, 0, "response_digest", "x" * 64), f"run full 0: {named}")
    c.reads("an answer's digest that is a number", call("full", 0, 0, "response_digest", 1), f"run full 0: {named}")
    first = "every call names the first attempt's request"
    c.reads("a request that is not the first attempt's", call("full", 0, 0, "request_digest", "1" * 64), f"run full 0: {first}")
    repair_args = doc["runs"][at[("full", 1)]]["calls"][1]["args_digest"]
    c.reads("a repair naming its own request", call("full", 1, 1, "request_digest", repair_args), f"run full 1: {first}")
    repair = "an invalid answer is repaired once, with a request of its own, and no other answer is"
    c.reads("an invalid answer left without its repair", calls("full", 3, lambda x: x.pop()), f"run full 3: {repair}")
    c.reads("a repair of a valid answer", call("full", 1, 0, "outcome", "VALID"), f"run full 1: {repair}")
    first_args = doc["runs"][at[("full", 1)]]["calls"][0]["args_digest"]
    c.reads("a repair that asks the same request again", call("full", 1, 1, "args_digest", first_args), f"run full 1: {repair}")
    full0, control0 = doc["runs"][at[("full", 0)]]["calls"][0], at[("control", 0)]

    def same_request(d: dict[str, Any]) -> None:
        mine = d["runs"][control0]["calls"][0]
        mine["request_digest"] = mine["args_digest"] = full0["args_digest"]

    c.reads("two runs that asked one request", edited(data, same_request), "two runs share one request, so one answer would stand for both")
    c.reads("two calls that name one capture", call("control", 0, 0, "capture_id", full0["capture_id"]), "two calls name one capture")
    c.eq("two answers may be alike", len(mod.read(call("control", 0, 0, "response_digest", full0["response_digest"])).runs), 60)
    return c.bad


def check_derived(mod: types.ModuleType) -> list[str]:
    """The result a file states is the one its runs give, member by member; a count written as true is not the count 1."""
    c = Case(mod)
    data = made(mod, lambda arm, i: "failed" if (arm, i) == ("control", 4) else uncertain(arm, i))
    doc = canonical.parse_record(data)
    c.eq("the control has one changed pair (a failed control run)", doc["control"]["same_changed"], 1)
    t, v = doc["treated"], doc["verdict"]
    for name, value in (
        ("outcome", "DECISION_STABLE"),
        ("outcome", "EVIDENCE_REQUIRED"),
        ("mechanism", "MODEL_REVISED"),
        ("failed", 0),
        ("failed", True),
        ("treated", {**t, "same_same": t["same_same"] - 1, "same_changed": t["same_changed"] + 1}),
        ("treated", doc["control"]),
        ("treated", None),
        ("control", {**doc["control"], "same_changed": True}),
        ("control", None),
        ("verdict", {**v, "note": "any shift is inside the stated width"}),
        ("verdict", {**v, "shift": "0.2"}),
        ("verdict", {**v, "low": repr(float(v["low"]) + 1e-12)}),
        ("verdict", {**v, "n": 19}),
        ("verdict", {**v, "p": "0.25000000000000001"}),
        ("verdict", None),
    ):
        c.reads(
            f"{name} stated as {value!r}", edited(data, put((name,), value)), f"the {name} the file states is not the one its runs give"
        )

    def flipped(d: dict[str, Any]) -> None:
        d["runs"][0].update(p="0.4", proposal_p="0.4", action="DOWN")

    c.reads(
        "a full run changed with its action, the stated tables left as they were",
        edited(data, flipped),
        "the treated the file states is not the one its runs give",
    )
    lacks = made(mod, required)
    for name, value in (("outcome", "NOT_EVALUATED"), ("failed", 1), ("treated", t), ("control", t), ("verdict", v), ("mechanism", "BOTH")):
        c.reads(
            f"a required source with {name} {value!r}",
            edited(lacks, put((name,), value)),
            f"the {name} the file states is not the one its runs give",
        )
    return c.bad


def check_rows(mod: types.ModuleType) -> list[str]:
    """The row the What if page reads: what was challenged, the result's codes and counts, its numbers as text."""
    c = Case(mod)
    got = mod.read(made(mod, lambda arm, i: "abstain" if (arm, i) == ("control", 7) else uncertain(arm, i)))
    row = mod.row(got)
    c.eq(
        "what was challenged",
        (row.cycle_id, row.forecast_id, row.arm_name, row.symbol, row.hours, row.source, row.items, row.original),
        (CYCLE, FORECAST, "Single model", "BTC", 1, "ryo", 1, "UP"),
    )
    c.eq(
        "the result",
        (row.outcome, row.mechanism, row.pairs, row.attempted, row.failed, row.treated, row.control),
        (got.outcome, None, 20, 60, 0, got.treated, got.control),
    )
    v = got.verdict
    c.eq(
        "its numbers as text",
        (row.shift, row.low, row.high, row.p),
        (mod.points(v.shift), mod.points(v.low), mod.points(v.high), mod.p_text(v.p)),
    )
    c.eq("and the model", (row.model, row.temperature), ("fake/model", "0.7"))
    for x, want in (
        (0.1, "+10"),
        (-0.124118317347926, "-12"),
        (0.326427879077665, "+33"),
        (0.0, "0"),
        (-0.004, "0"),
        (0.004, "0"),
        (1.0, "+100"),
        (-1.0, "-100"),
        (0.125, "+12"),
        (0.135, "+14"),
        (-0.135, "-14"),
    ):
        c.eq(f"points of {x}", mod.points(x), want)
    for x, want in (
        (0.625, "0.62"),
        (1.0, "1.00"),
        (0.0412, "0.04"),
        (0.004, "<0.01"),
        (1.9073486328125e-06, "<0.01"),
        (0.005, "<0.01"),
        (0.015, "0.02"),
        (0.0101, "0.01"),
    ):
        c.eq(f"p of {x}", mod.p_text(x), want)
    lacks = mod.row(mod.read(made(mod, required)))
    c.eq(
        "a required source has no numbers",
        (lacks.outcome, lacks.treated, lacks.control, lacks.shift, lacks.low, lacks.high, lacks.p, lacks.attempted, lacks.failed),
        ("EVIDENCE_REQUIRED", None, None, None, None, None, None, 1, 0),
    )
    result, done = challenged(lambda arm, i: "up", liar=True)
    leak = mod.row(mod.read(mod.record(asked(mod), result, done)))
    c.eq(
        "a leak has no runs and its items",
        (leak.outcome, leak.attempted, leak.items, leak.shift),
        ("NOT_EVALUATED", 0, len(result.intervention.withheld), None),
    )
    return c.bad


# --- a real league with a model arm --------------------------------------------------------------------------------------------------
@cache
def league() -> tuple[tuple[dict[str, Any], ...], dict[str, Any]]:
    """The records of a real cycle with the single-judgment arm (``view_models_checks.model_ledger``), and the arm's committed forecast."""
    from tests.view_models_checks import model_ledger

    records, _, _ = model_ledger()
    committed = next(r for r in records if r.get("type") == forecast.TYPE and r["body"]["arm_id"] == "L1")
    return tuple(records), committed


Decide = Callable[[Any, Any, int, tuple[str, int], int, Tape, str | None], arm_l1.Decision]


def real_challenge(mod: types.ModuleType, source: str, decide: Decide) -> bytes:
    """A challenge of the league's committed forecast of the single-judgment arm, withholding ``source``, asked as
    ``scripts/challenge_live.py`` asks it (20 pairs): full and control runs cite the first item taken out, runs without it cite nothing."""
    records, committed = league()
    body = committed["body"]
    shot = next(r for r in records if r.get("type") == snapshot.TYPE)
    evidence = [r for r in records if r.get("type") == "evidence"]
    frozen = snapshot.rebuild(shot, {r["id"]: r for r in evidence})
    store = lineage.rebuild([{k: v for k, v in r.items() if k != "committed_at"} for r in evidence])
    slot = frozen.slots[0]
    candidate = snapshot.Candidate(slot.candidate_id, slot.asset_id)
    removed = tuple(item.evidence_id for item, _ in frozen.items if item.source == source and item.evidence_id in slot.item_ids)
    seeds = model_challenge.seeds(None, 20)
    done: dict[tuple[str, int], arm_l1.Decision] = {}
    tape = Tape()

    def lookup(reader: Any, i: int, arm: str) -> arm_l1.Decision:
        cite = None if arm == "withheld" else removed[0]
        done[(arm, i)] = decide(reader, candidate, frozen.snapshot_at_ms, (arm, i), seeds[(arm, i)], tape, cite)
        return done[(arm, i)]

    result = model_challenge.challenge(frozen, store, removed, lookup, "UP", 20)
    about = asked(
        mod,
        cycle=body["cycle_id"],
        forecast=committed["id"],
        symbol=body["symbol"],
        hours=body["horizon_ms"] // HOUR_MS,
        source=source,
        agent_version=body["agent_version_hash"],
    )
    return mod.record(about, result, done)  # type: ignore[no-any-return]


def scripted(reader: Any, candidate: Any, at_ms: int, key: tuple[str, int], seed: int, tape: Tape, cite: str | None) -> arm_l1.Decision:
    return decided("abstain" if key[0] == "withheld" else "up", key, seed, tape, cite)


def check_bind(mod: types.ModuleType) -> list[str]:
    """A file is held to the ledger a site is made from: its forecast, and the items its source leaves with, from the ledger's snapshot."""
    c = Case(mod)
    records, committed = league()
    fid = committed["id"]
    got = mod.read(real_challenge(mod, "binance-spot", scripted))
    c.eq("a challenge of the league's own forecast is held", mod.bind(got, records), None)
    c.eq("and it is the arm's committed one", (got.asked.forecast, got.outcome, got.mechanism), (fid, "DECISION_CHANGED", "MODEL_REVISED"))

    class NoModel:
        def generate(self, *args: Any, **kwargs: Any) -> Any:
            raise AssertionError("the model was asked although a required source was missing")

    def real(reader: Any, candidate: Any, at_ms: int, key: tuple[str, int], seed: int, tape: Tape, cite: str | None) -> arm_l1.Decision:
        config = arm_l1.Config("fake/model", temperature="0.7", seed=seed)
        return arm_l1.decide(NoModel(), reader, candidate, "BTC", committed["body"]["horizon_ms"], at_ms, config)  # type: ignore[arg-type]

    lacks = mod.read(real_challenge(mod, "hoshitori.features", real))
    c.eq(
        "the real arm without a source it requires, held to the ledger",
        (lacks.outcome, mod.bind(lacks, records)),
        ("EVIDENCE_REQUIRED", None),
    )
    c.refuses(
        "a forecast the ledger does not hold",
        lambda: mod.bind(replace(got, asked=replace(got.asked, forecast=FORECAST)), records),
        f"{FORECAST}: this ledger holds no such forecast",
    )
    other = "the ledger's forecast is not the one the file challenges"
    for label, change in (
        ("another cycle", {"cycle": CYCLE}),
        ("another symbol", {"symbol": "ETH"}),
        ("another horizon", {"hours": 4}),
        ("another agent version", {"agent_version": VERSION}),
        ("another committed action", {"original": "DOWN"}),
        ("a flat one", {"original": "FLAT"}),
    ):
        c.refuses(label, lambda ch=change: mod.bind(replace(got, asked=replace(got.asked, **ch)), records), f"{fid}: {other}")  # type: ignore[misc]
    rules = next(
        r for r in records if r.get("type") == forecast.TYPE and r["body"]["arm_id"] == "L0" and r["body"]["horizon_ms"] == 86_400_000
    )
    c.refuses(
        "the rules arm's forecast of the same slot",
        lambda: mod.bind(replace(got, asked=replace(got.asked, forecast=rules["id"])), records),
        f"{rules['id']}: {other}",
    )

    def swapped(new: dict[str, Any]) -> list[dict[str, Any]]:
        return [new if r is committed else r for r in records]

    barrier = {**committed, "body": {k: v for k, v in committed["body"].items() if k != "p"} | {"contract": "B"}}
    c.refuses("a barrier forecast", lambda: mod.bind(got, swapped(barrier)), f"{fid}: {other}")
    note = {**committed, "type": "forecast_state"}
    c.refuses(
        "a record of another type with the forecast's id",
        lambda: mod.bind(got, swapped(note)),
        f"{fid}: this ledger holds no such forecast",
    )
    for p, action in (("0.5", "FLAT"), ("0.49", "DOWN"), ("0.500001", "UP")):
        moved = {**committed, "body": {**committed["body"], "p": p}}
        c.eq(f"a committed p of {p} is {action}", mod.bind(replace(got, asked=replace(got.asked, original=action)), swapped(moved)), None)
    c.refuses(
        "a source the snapshot holds nothing of",
        lambda: mod.bind(replace(got, asked=replace(got.asked, source="okx")), records),
        f"{fid}: the snapshot holds nothing from okx for BTC",
    )
    taken = "the items taken out are not the ones this ledger's snapshot gives without binance-spot"
    c.refuses("fewer items withheld", lambda: mod.bind(replace(got, withheld=got.removed), records), f"{fid}: {taken}")
    c.refuses("another item removed", lambda: mod.bind(replace(got, removed=got.withheld[-1:]), records), f"{fid}: {taken}")
    c.refuses("a leak the ledger does not have", lambda: mod.bind(replace(got, leaks=(evidence_id(3),)), records), f"{fid}: {taken}")
    none = "this ledger holds no snapshot and manifest of its cycle"
    c.refuses(
        "a ledger without the cycle's snapshot",
        lambda: mod.bind(got, [r for r in records if r.get("type") != snapshot.TYPE]),
        f"{fid}: {none}",
    )
    c.refuses(
        "a ledger without the cycle's manifest",
        lambda: mod.bind(got, [r for r in records if r.get("type") != "manifest"]),
        f"{fid}: {none}",
    )
    for kind in (snapshot.TYPE, "manifest"):
        moved_away = [{**r, "body": {**r["body"], "cycle_id": CYCLE}} if r.get("type") == kind else r for r in records]
        c.refuses(f"a {kind} of another cycle", lambda m=moved_away: mod.bind(got, m), f"{fid}: {none}")  # type: ignore[misc]
    gone = got.removed[0]
    c.refuses(
        "a snapshot that cannot be rebuilt",
        lambda: mod.bind(got, [r for r in records if r.get("id") != gone]),
        f"{fid}: the snapshot of its cycle cannot be rebuilt: the evidence record {gone} was not given",
    )

    def unlisted(r: dict[str, Any]) -> dict[str, Any]:
        if r.get("type") != "manifest":
            return r
        return {**r, "body": {**r["body"], "entries": [{**e, "symbol": "ETH"} for e in r["body"]["entries"]]}}

    c.refuses(
        "a symbol the manifest does not include",
        lambda: mod.bind(got, [unlisted(r) for r in records]),
        f"{fid}: BTC is not a candidate of its cycle",
    )

    def excluded(r: dict[str, Any]) -> dict[str, Any]:
        if r.get("type") != "manifest":
            return r
        return {**r, "body": {**r["body"], "entries": [{**e, "inclusion": "EXCLUDED"} for e in r["body"]["entries"]]}}

    c.refuses(
        "a symbol the manifest left out",
        lambda: mod.bind(got, [excluded(r) for r in records]),
        f"{fid}: BTC is not a candidate of its cycle",
    )
    return c.bad


def check_for_site(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    records, committed = league()
    spot = mod.read(real_challenge(mod, "binance-spot", scripted))
    feats = mod.read(real_challenge(mod, "hoshitori.features", scripted))
    rows = mod.for_site([feats, spot], records)
    c.eq("a row for each, held to the ledger", rows, (mod.row(spot), mod.row(feats)))
    c.eq("none given, none shown", mod.for_site([], records), ())
    c.refuses(
        "one question asked twice",
        lambda: mod.for_site([spot, feats, spot], records),
        f"{committed['id']} without binance-spot is challenged twice",
    )
    c.refuses(
        "a file of another ledger",
        lambda: mod.for_site([replace(spot, asked=replace(spot.asked, forecast=FORECAST))], records),
        f"{FORECAST}: this ledger holds no such forecast",
    )
    held: list[Any] = []
    real_bind = mod.bind
    mod.bind = lambda challenge, ledger: held.append(challenge.asked.forecast)
    try:
        some = [
            replace(spot, asked=replace(spot.asked, symbol=s, hours=h, forecast=f, source=src))
            for s, h, f, src in (
                ("BTC", 4, "fc" + "c" * 26, "b-source"),
                ("AAA", 24, "fc" + "d" * 26, "a-source"),
                ("BTC", 1, "fc" + "e" * 26, "z-source"),
                ("BTC", 4, "fc" + "f" * 26, "a-source"),
                ("BTC", 4, "fc" + "b" * 26, "a-source"),
            )
        ]
        shown = mod.for_site(some, records)
    finally:
        mod.bind = real_bind
    c.eq("every one is held to the ledger", held, [x.asked.forecast for x in some])
    c.eq(
        "in the order symbol, horizon, source, forecast",
        [(r.symbol, r.hours, r.source, r.forecast_id[2]) for r in shown],
        [
            ("AAA", 24, "a-source", "d"),
            ("BTC", 1, "z-source", "e"),
            ("BTC", 4, "a-source", "b"),
            ("BTC", 4, "a-source", "f"),
            ("BTC", 4, "b-source", "c"),
        ],
    )
    return c.bad


def counts(t: paired_stats.Pairs) -> tuple[int, ...]:
    return (t.same_same, t.same_changed, t.changed_same, t.changed_changed)


def check_recorded(mod: types.ModuleType) -> list[str]:
    """The three recorded files read and give the C1 numbers; none holds a key or a path."""
    c = Case(mod)
    want = {
        "BTC": ("EFFECT_UNCERTAIN", (16, 3, 1, 0), (18, 1, 1, 0), "0.1", "0.625"),
        "ETH": ("NOT_EVALUATED(unstable_baseline)", (12, 4, 4, 0), (16, 0, 4, 0), "0.0", "1.0"),
        "SOL": ("EFFECT_UNCERTAIN", (12, 3, 4, 1), (9, 6, 3, 2), "-0.05", "1.0"),
    }
    for symbol, (outcome, treated, control, shift, p) in want.items():
        data = (RECORDED / f"{symbol}.json").read_bytes()
        got = mod.read(data)
        c.eq(
            f"{symbol}: the C1 result",
            (
                got.outcome,
                counts(got.treated),
                counts(got.control),
                repr(got.verdict.shift),
                repr(got.verdict.p),
                got.failed,
                got.attempted,
            ),
            (outcome, treated, control, shift, p, 0, 60),
        )
        c.eq(
            f"{symbol}: what was asked",
            (got.asked.cycle, got.asked.symbol, got.asked.hours, got.asked.source, got.asked.original, got.asked.pairs),
            ("cymsmyj5ihfp3dzsfqr7hdbustpx", symbol, 1, "binance-funding", "UP", 20),
        )
        c.eq(
            f"{symbol}: the arm as committed",
            (got.asked.model, got.asked.prompt_version, got.asked.temperature, got.asked.seed, got.asked.agent_version),
            (
                "nvidia/nemotron-3.5-super-vl-120b-a12b",
                "l1-v5",
                "0.7",
                None,
                "badd2fb9997b8dbc108ab0443253f1546eee2b43d72cf143cdd465cb2e99eea0",
            ),
        )
        c.eq(f"{symbol}: every run answered at its first call", {(len(r.calls), r.calls[0].outcome) for r in got.runs}, {(1, "VALID")})
        lowered = data.lower()
        c.eq(
            f"{symbol}: no key, no path, no prompt",
            [w for w in (b"/users/", b"nvapi", b"llm_api_key", b"bearer", b"<<<data", b"system", b"messages", b"\\n") if w in lowered],
            [],
        )
    return c.bad


def check_page_words(mod: types.ModuleType) -> list[str]:
    """The What if page has words for every outcome, mechanism and committed action a file can hold, and for no other (``web/src/format.ts``)."""
    c = Case(mod)
    text = FORMAT_TS.read_text(encoding="utf-8")
    for name, values in (("MODEL_OUTCOME_WORDS", mod.OUTCOMES), ("MECHANISM_WORDS", mod.MECHANISMS), ("COMMITTED_WORDS", mod.ORIGINALS)):
        block = re.search(rf"export const {name}: Record<string, string> = \{{\n(.*?)\n\}};", text, re.DOTALL)
        keys = [] if block is None else re.findall(r'^  "?([A-Za-z_()]+)"?:', block.group(1), re.MULTILINE)
        c.eq(f"{name} has words for exactly what a file can hold", sorted(keys), sorted(values))
    return c.bad


def placebo_file(
    mod: types.ModuleType, script: Callable[[str, int], str], cite_placebo: str | None = None, pairs: int = 20
) -> tuple[bytes, Any]:
    """A placebo challenge of the league's committed single-judgment forecast: the arm asked on the evidence and on the evidence with a
    neutral item added, recorded as a file of its own source. ``cite_placebo`` names the arm whose runs cite the added item."""
    records, committed = league()
    body = committed["body"]
    shot = next(r for r in records if r.get("type") == snapshot.TYPE)
    evidence = [r for r in records if r.get("type") == "evidence"]
    frozen = snapshot.rebuild(shot, {r["id"]: r for r in evidence})
    store = lineage.rebuild([{k: v for k, v in r.items() if k != "committed_at"} for r in evidence])
    slot = frozen.slots[0]
    seeds = model_challenge.seeds(None, pairs)
    done: dict[tuple[str, int], arm_l1.Decision] = {}
    tape = Tape()
    added = intervention_added(frozen, slot)

    def lookup(reader: Any, i: int, arm: str) -> arm_l1.Decision:
        cite = added if arm == cite_placebo else slot.item_ids[0]
        done[(arm, i)] = decided(script(arm, i), (arm, i), seeds[(arm, i)], tape, cite)
        return done[(arm, i)]

    result = model_challenge.placebo_challenge(frozen, store, slot.candidate_id, (slot.item_ids[0],), lookup, "UP", pairs)
    about = asked(
        mod,
        cycle=body["cycle_id"],
        forecast=committed["id"],
        symbol=body["symbol"],
        hours=body["horizon_ms"] // HOUR_MS,
        source="hoshitori.placebo",
        agent_version=body["agent_version_hash"],
        pairs=pairs,
    )
    return mod.record(about, result, done), added


def intervention_added(frozen: Any, slot: Any) -> str:
    from hoshitori import intervention

    return intervention.add_placebo(frozen, slot.candidate_id, (slot.item_ids[0],)).item_id


def check_placebo_file(mod: types.ModuleType) -> list[str]:
    """A placebo challenge is stored as a challenge of the source ``hoshitori.placebo``: what is listed is the neutral item that was added, not
    anything taken out; the full and control runs cannot cite it, the run that saw it may; it can be held to the ledger."""
    c = Case(mod)
    records, committed = league()
    data, added = placebo_file(mod, lambda arm, i: "up")
    got = mod.read(data)
    c.eq(
        "it reads back",
        (got.asked.source, got.outcome, got.leaks, got.mechanism),
        ("hoshitori.placebo", "EFFECT_UNCERTAIN", (), None),
    )
    c.eq(
        "it lists the one item that was added, as both what was removed and what was withheld",
        (got.removed, got.withheld),
        ((added,), (added,)),
    )
    c.eq("and it is held to the ledger", mod.bind(got, records), None)
    c.eq("the page's row says one item", mod.row(got).items, 1)
    again, _ = placebo_file(mod, lambda arm, i: "up")
    c.eq("one content, one file", again, data)
    moved, _ = placebo_file(mod, lambda arm, i: "abstain" if arm == "withheld" else "up")
    c.eq("a decision that moves on the neutral item is a change like any other", mod.read(moved).outcome, "DECISION_CHANGED")
    seen, _ = placebo_file(mod, lambda arm, i: "up", cite_placebo="withheld")
    c.eq(
        "a run that saw the neutral item may cite it",
        (mod.read(seen).outcome, mod.bind(mod.read(seen), records)),
        ("EFFECT_UNCERTAIN", None),
    )
    c.refuses(
        "a run on the evidence alone cannot cite an item it was not shown",
        lambda: mod.read(placebo_file(mod, lambda arm, i: "up", cite_placebo="full")[0]),
        "run full 0: it cites an item that was not shown to it",
    )
    c.refuses(
        "nor the control run",
        lambda: mod.read(placebo_file(mod, lambda arm, i: "up", cite_placebo="control")[0]),
        "run control 0: it cites an item that was not shown to it",
    )
    c.refuses(
        "a placebo with nothing listed as added",
        lambda: mod.read(edited(data, put(("removed",), []))),
        "the removed items are at least one evidence ids, sorted and once each",
    )
    c.refuses(
        "a placebo that lists more than the one item",
        lambda: mod.read(edited(data, put(("removed",), sorted([added, evidence_id(9)])))),
        "a placebo file lists the one item that was added",
    )
    c.refuses(
        "what is withheld must be that item and no other",
        lambda: mod.read(edited(data, put(("withheld",), [evidence_id(9)]))),
        "a placebo file lists the one item that was added",
    )
    c.refuses(
        "or something that still stands on it",
        lambda: mod.read(edited(data, put(("leaks",), [evidence_id(9)]))),
        "a placebo adds an item and removes nothing, so nothing can leak",
    )
    real_add = mod.intervention.add_placebo

    def refuses_to_add(*args: Any, **kwargs: Any) -> Any:
        raise mod.intervention.InterventionError("no item to size it from")

    mod.intervention.add_placebo = refuses_to_add
    try:
        c.refuses(
            "a snapshot a placebo cannot be added to",
            lambda: mod.bind(got, records),
            f"{committed['id']}: a placebo cannot be added to this ledger's snapshot: no item to size it from",
        )
    finally:
        mod.intervention.add_placebo = real_add
    other = replace(got, removed=(evidence_id(9),), withheld=(evidence_id(9),))
    c.refuses(
        "held to a ledger whose placebo item is another",
        lambda: mod.bind(other, records),
        f"{committed['id']}: the item added is not the one this ledger's snapshot gives for a placebo",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (
        check_constants,
        check_round_trip,
        check_without_runs,
        check_record,
        check_bytes_and_members,
        check_runs,
        check_run_set,
        check_tape,
        check_derived,
        check_rows,
        check_bind,
        check_for_site,
        check_recorded,
        check_page_words,
        check_placebo_file,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
