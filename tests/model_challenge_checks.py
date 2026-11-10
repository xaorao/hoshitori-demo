"""Checks of the challenge of a model arm (``hoshitori.model_challenge``) with scripted stand-ins for the arm, runnable against a mutant."""

from __future__ import annotations

import dataclasses
import json
import types
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from typing import Any

from hoshitori import arm_input, arm_l1, intervention, model_client, snapshot
from hoshitori.snapshot import Access, Reader
from tests.intervention_checks import Liar, world
from tests.model_world import model_world, ok

D = Decimal
V, R, G = arm_l1.Verdict, arm_l1.Reason, arm_l1.Gate
HOUR_MS = 3_600_000
LARGEST_SEED = 2**31 - 1  # the seed a call record takes (model_call_record.check)
ABSTAIN = json.dumps({"decision": "NO_CALL", "no_call_reason": "OUT_OF_SCOPE", "invalidations": [], "cites": [], "assertions": []})


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
        except self.mod.ChallengeError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except TimeoutError:
            raise
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def proposal(decision: str = "CALL", p: str | None = "0.6") -> arm_l1.Proposal:
    return arm_l1.Proposal(decision, None if p is None else D(p), None, (), (), (), None)


def made(
    verdict: Any = V.CALL, p: str | None = "0.6", prop: arm_l1.Proposal | None = None, gate: Any = G.NONE, reason: Any = None
) -> arm_l1.Decision:
    return arm_l1.Decision("cd-btc", 1, verdict, reason, None if p is None else D(p), (), "h", (), "v", prop, gate, None, ())


def up() -> arm_l1.Decision:
    return made(prop=proposal())


def down() -> arm_l1.Decision:
    return made(p="0.4", prop=proposal(p="0.4"))


def gated() -> arm_l1.Decision:
    return made(V.NO_CALL, None, proposal(), G.GROUNDING, R.ESSENTIAL_PREMISE_UNPROVEN)


Script = Callable[[int, str], arm_l1.Decision]


class Asked:
    def __init__(self, script: Script) -> None:
        self.script, self.calls = script, []

    def __call__(self, reader: Any, i: int, arm: str) -> arm_l1.Decision:
        self.calls.append((i, arm))
        return self.script(i, arm)


def run(mod: types.ModuleType, script: Script, runs: int = 30, original: str = "UP", removed: str = "ryo") -> tuple[Any, Asked]:
    w, snap, *_ = world()
    asked = Asked(script)
    pick = {"ryo": w.btc_ryo, "bars": w.btc_bars}[removed]
    return mod.challenge(snap, w.store, (pick,), asked, original, runs), asked


def check_classes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("up", mod.action_class(up()), "UP")
    c.eq("down", mod.action_class(down()), "DOWN")
    c.eq("flat", mod.action_class(made(p="0.5", prop=proposal(p="0.5"))), "FLAT")
    c.eq("no call", mod.action_class(made(V.NO_CALL, None)), "NO_CALL")
    c.eq("missing evidence", mod.action_class(made(V.FAILED_EVIDENCE, None, reason=R.MISSING_EVIDENCE)), "FAILED_EVIDENCE")
    c.eq("a run that failed is not an answer", mod.action_class(made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE)), None)
    c.eq("a call with no probability is its verdict", mod.action_class(made(V.CALL, None)), "CALL")
    c.eq("proposal up", mod.proposal_class(up()), "UP")
    c.eq("proposal down", mod.proposal_class(down()), "DOWN")
    c.eq("proposal flat", mod.proposal_class(made(prop=proposal(p="0.5"))), "FLAT")
    c.eq("the proposal that the gate turned into no call", mod.proposal_class(gated()), "UP")
    c.eq("a proposal of no call", mod.proposal_class(made(V.NO_CALL, None, proposal("NO_CALL", None))), "NO_CALL")
    c.eq("a call without a proposal falls back to the action", mod.proposal_class(made()), "UP")
    c.eq("a proposal of a call with no probability", mod.proposal_class(made(V.NO_CALL, None, proposal("CALL", None))), "NO_CALL")
    c.eq("missing evidence", mod.proposal_class(made(V.FAILED_EVIDENCE, None, reason=R.MISSING_EVIDENCE)), "FAILED_EVIDENCE")
    c.eq("a failed run", mod.proposal_class(made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE)), None)
    c.eq("the vocabulary", (mod.FULL, mod.CONTROL, mod.WITHHELD, mod.REQUIRED), ("full", "control", "withheld", "EVIDENCE_REQUIRED"))
    c.eq("the action of a failed run, and the runs of a pair", (mod.RUN_FAILED, mod.RUNS_PER_PAIR), ("RUN_FAILED", 3))
    c.eq(
        "the mechanisms",
        (mod.MODEL_REVISED, mod.GATE_BLOCKED, mod.BOTH, mod.VARIATION_ONLY),
        ("MODEL_REVISED", "GATE_BLOCKED", "BOTH", "VARIATION_ONLY"),
    )
    c.eq("frozen", mod.ModelChallenge.__dataclass_params__.frozen, True)
    return c.bad


def check_challenge(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    r, asked = run(mod, lambda i, arm: down() if arm == "withheld" else up())
    c.eq("the model changes its mind", (r.outcome, r.mechanism, r.failed, r.runs), ("DECISION_CHANGED", "MODEL_REVISED", 0, 30))
    c.eq("with its counts", (r.treated.n, r.treated.same_changed, r.control.same_changed), (30, 30, 0))
    c.eq("and its verdict", (r.verdict.n, round(r.verdict.shift, 2)), (30, 1.0))
    c.eq("each run is asked once: full, withheld and control", sorted(set(asked.calls))[:3], [(0, "control"), (0, "full"), (0, "withheld")])
    c.eq("and the first withheld run is the probe, not asked twice", asked.calls.count((0, "withheld")), 1)
    c.eq("ninety runs for thirty pairs", len(asked.calls), 90)
    r, _ = run(mod, lambda i, arm: gated() if arm == "withheld" else up())
    c.eq("a gate stops it: not the model changing its mind", (r.outcome, r.mechanism), ("DECISION_CHANGED", "GATE_BLOCKED"))
    r, _ = run(
        mod,
        lambda i, arm: made(V.NO_CALL, None, proposal(p="0.4"), G.GROUNDING, R.ESSENTIAL_PREMISE_UNPROVEN) if arm == "withheld" else up(),
    )
    c.eq("both the proposal and the gate moved", (r.outcome, r.mechanism), ("DECISION_CHANGED", "BOTH"))
    r, _ = run(mod, lambda i, arm: made(V.NO_CALL, None, proposal("NO_CALL", None)) if arm == "withheld" else up())
    c.eq("the model abstains without a gate", (r.outcome, r.mechanism), ("DECISION_CHANGED", "MODEL_REVISED"))

    def unreliable_proposal(i: int, arm: str) -> arm_l1.Decision:
        if arm == "withheld":
            return down()
        if arm == "control":
            return made(prop=proposal(p="0.4"))
        return up()

    r, _ = run(mod, unreliable_proposal)
    c.eq(
        "the action moved but the proposals disagreed with themselves: variation only",
        (r.outcome, r.mechanism),
        ("DECISION_CHANGED", "VARIATION_ONLY"),
    )

    def both_ways(i: int, arm: str) -> arm_l1.Decision:
        if arm == "full":
            return down() if i < 6 else up()
        return up() if i < 6 else down()

    r, _ = run(mod, both_ways)
    c.eq(
        "pairs that move both ways are counted both ways",
        (r.treated.same_same, r.treated.same_changed, r.treated.changed_same, r.treated.changed_changed),
        (0, 24, 6, 0),
    )
    r, _ = run(mod, lambda i, arm: up(), runs=1)
    c.eq("one pair is allowed and says nothing", (r.outcome, r.runs, r.treated.n), ("EFFECT_UNCERTAIN", 1, 1))
    r, _ = run(mod, lambda i, arm: up(), runs=60)
    c.eq("sixty identical pairs are stable", (r.outcome, r.mechanism), ("DECISION_STABLE", None))
    r, _ = run(mod, lambda i, arm: up(), runs=10)
    c.eq("ten are not enough to say", r.outcome, "EFFECT_UNCERTAIN")
    r, _ = run(mod, lambda i, arm: down() if arm == "control" else up())
    c.eq("a model that disagrees with itself is not read", (r.outcome, r.mechanism), ("NOT_EVALUATED(unstable_baseline)", None))
    r, _ = run(mod, lambda i, arm: up(), original="DOWN", runs=60)
    c.eq("the original action decides what counts as unchanged", r.outcome, "DECISION_STABLE")
    r, _ = run(mod, lambda i, arm: down() if i % 2 else up(), runs=30)
    c.eq("alternating answers alike in all three: variation only, uncertain", r.outcome, "EFFECT_UNCERTAIN")

    def abstain() -> arm_l1.Decision:
        return made(V.NO_CALL, None, proposal("NO_CALL", None))

    def varied(i: int, arm: str) -> arm_l1.Decision:
        # every withheld run proposes DOWN; asked again on the same evidence, the arm abstains now and then (in other pairs each time)
        if arm == "withheld":
            return down()
        return abstain() if (arm == "full" and i in (0, 1, 2)) or (arm == "control" and i in (3, 4, 5)) else up()

    r, _ = run(mod, varied, runs=20)
    c.eq(
        "the proposal moved in every pair without the evidence and in six of twenty when asked again: the model revised it",
        (r.outcome, r.mechanism),
        ("DECISION_CHANGED", "MODEL_REVISED"),
    )

    def fails_without(i: int, arm: str) -> arm_l1.Decision:
        return made(V.FAILED, None, reason=R.MODEL_OUTPUT_INVALID) if arm == "withheld" and i % 6 == 5 else up()

    r, _ = run(mod, fails_without, runs=60)
    c.eq(
        "taking the evidence out makes one withheld run in six fail and no other: a change, kept in the pairs and counted",
        (r.outcome, r.failed, r.treated.n, r.treated.same_changed),
        ("DECISION_CHANGED", 10, 60, 10),
    )

    def other_classes(i: int, arm: str) -> arm_l1.Decision:
        # with the evidence the arm says UP or DOWN; without it, UP or no call: the share of UP is the same, DOWN became no call
        if i % 2 == 0:
            return up()
        return abstain() if arm == "withheld" else down()

    r, _ = run(mod, other_classes, runs=60)
    c.eq("a move between the actions that are not the original is a change (the whole paired table)", r.outcome, "DECISION_CHANGED")

    def mixed(i: int, arm: str) -> arm_l1.Decision:
        # failures spread over the two asks of the unchanged input alike: two full runs and two control runs
        if (arm == "full" and i < 2) or (arm == "control" and i in (2, 3)):
            return made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE)
        return down() if arm == "withheld" else up()

    r, _ = run(mod, mixed)
    c.eq("a failed run stays in its pair as an action of its own and is counted", (r.outcome, r.failed, r.treated.n), (
        "DECISION_CHANGED",
        4,
        30,
    ))  # fmt: skip
    c.eq(
        "and its counts", (r.treated.same_changed, r.treated.changed_changed, r.control.same_changed, r.control.changed_same), (28, 2, 2, 2)
    )

    def one_sided(i: int, arm: str) -> arm_l1.Decision:
        if arm == "full" and i < 4:
            return made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE)
        return down() if arm == "withheld" else up()

    r, _ = run(mod, one_sided)
    c.eq(
        "failures on one of two asks of the unchanged input only: the baseline already shifts",
        (r.outcome, r.failed),
        ("NOT_EVALUATED(unstable_baseline)", 4),
    )

    def heavy(i: int, arm: str) -> arm_l1.Decision:
        return made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE) if i < 10 else (down() if arm == "withheld" else up())

    r, _ = run(mod, heavy)
    c.eq("too many failures: nothing is evaluated", (r.outcome, r.failed, r.verdict.note), (
        "NOT_EVALUATED(structural_failure)",
        30,
        "30 of 90 runs failed",
    ))  # fmt: skip

    def few(i: int, arm: str) -> arm_l1.Decision:
        return made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE) if arm == "withheld" and i < 6 else up()

    r, _ = run(mod, few)
    c.eq(
        "removing the evidence makes six withheld runs fail and nothing else moves: a change of what the model gave, made by no gate",
        (r.outcome, r.failed, r.mechanism),
        ("DECISION_CHANGED", 6, "MODEL_REVISED"),
    )
    r, _ = run(mod, lambda i, arm: made(V.FAILED, None, reason=R.MODEL_UNAVAILABLE), runs=5)
    c.eq(
        "every run failed",
        (r.outcome, r.treated, r.verdict.note, r.failed),
        ("NOT_EVALUATED(structural_failure)", arm_l1_pairs(0, 0, 0, 5), "15 of 15 runs failed", 15),
    )
    return c.bad


def arm_l1_pairs(*counts: int) -> Any:
    from hoshitori.paired_stats import Pairs

    return Pairs(*counts)


def check_required(mod: types.ModuleType) -> list[str]:
    c = Case(mod)

    def missing(i: int, arm: str) -> arm_l1.Decision:
        return made(V.FAILED_EVIDENCE, None, reason=R.MISSING_EVIDENCE) if arm == "withheld" else up()

    r, asked = run(mod, missing, removed="bars")
    c.eq(
        "the arm cannot decide without it: known without a run",
        (r.outcome, r.treated, r.verdict, r.mechanism),
        ("EVIDENCE_REQUIRED", None, None, None),
    )
    c.eq("and nothing was run or lost", (r.runs, r.failed), (0, 0))
    c.eq("and the model was not asked at all beyond the probe", asked.calls, [(0, "withheld")])
    c.eq("the intervention is kept", (r.intervention.status, r.intervention.removed != ()), ("READY", True))

    def other(i: int, arm: str) -> arm_l1.Decision:
        return made(V.FAILED_EVIDENCE, None, reason=R.MODEL_UNAVAILABLE) if arm == "withheld" else up()

    r, _ = run(mod, other, runs=30)
    c.eq("a failed evidence run for another reason is just a class", r.outcome, "DECISION_CHANGED")
    # the real arm, not a stand-in: taking out a required source must end the run before any model is asked
    w, snap, *_ = world()

    class NoModel:
        asked = 0

        def generate(self, *args: Any, **kwargs: Any) -> Any:
            NoModel.asked += 1
            raise AssertionError("the model was asked although a required source was missing")

    def real(reader: Any, i: int, arm: str) -> arm_l1.Decision:
        cand = next(x for x in w.candidates(__import__("hoshitori.snapshot", fromlist=["Candidate"])) if x.candidate_id == "cd-btc")
        return arm_l1.decide(NoModel(), reader, cand, "BTC", 3_600_000, snap.snapshot_at_ms, arm_l1.Config("m"))  # type: ignore[arg-type]

    r = mod.challenge(snap, w.store, (w.btc_bars,), real, "UP", 20)
    c.eq("the real arm without a required source: known without a model", (r.outcome, NoModel.asked), ("EVIDENCE_REQUIRED", 0))
    return c.bad


def check_leak(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, snap, *_ = world()
    liar = Liar()
    for ident in sorted(w.store.ids(), key=lambda i: len(w.store.ancestors(i))):
        liar.add(w.store.get(ident), w.store.digest(ident))
    asked = Asked(lambda i, arm: up())
    r = mod.challenge(snap, liar, (w.btc_bars,), asked, "UP", 20)
    c.eq(
        "a leaking intervention is not evaluated and the model is not asked",
        (r.outcome, r.verdict, r.runs, asked.calls),
        ("NOT_EVALUATED", None, 0, []),
    )
    c.eq("with nothing run or lost", (r.failed, r.treated, r.control, r.mechanism), (0, None, None, None))
    try:
        mod.challenge(snap, w.store, (w.btc_ryo,), asked, "UP", 0)
        c.bad.append("no runs: was accepted")
    except mod.ChallengeError as exc:
        c.eq("no runs", str(exc), "a challenge needs at least one pair of runs")
    return c.bad


def check_mechanism(mod: types.ModuleType) -> list[str]:
    """The in-pair test of who moved: each pair says whether its part moved without the evidence, and whether it moved when asked again."""
    c = Case(mod)
    only_removed, only_anyway, neither, both = (True, False), (False, True), (False, False), (True, True)
    for label, moves, want in (
        ("eight one way, one the other: moved", [only_removed] * 8 + [only_anyway] + [neither] * 11, True),
        ("six one way and none the other: moved", [only_removed] * 6 + [neither] * 14, True),
        ("three one way: too few to say", [only_removed] * 3 + [neither] * 17, False),
        ("ten the other way: steadier without the evidence, not moved by it", [only_anyway] * 10 + [neither] * 10, False),
        ("moving in every pair either way is variation", [both] * 20, False),
        ("nothing moved", [neither] * 20, False),
    ):
        c.eq(label, mod._moved(moves), want)
    moved, still = [only_removed] * 10 + [neither] * 10, [neither] * 20
    c.eq("the proposal moved", mod._mechanism(moved, still), "MODEL_REVISED")
    c.eq("the gate moved", mod._mechanism(still, moved), "GATE_BLOCKED")
    c.eq("both moved", mod._mechanism(moved, moved), "BOTH")
    c.eq("neither moved", mod._mechanism(still, still), "VARIATION_ONLY")
    return c.bad


def check_seeds(mod: types.ModuleType) -> list[str]:
    """The seed of each run (EVIDENCE_CHALLENGE_SPEC section 2, steps 3 to 5): pair i asks its full run and its run without the evidence with
    the seed index i, and its control on the unchanged input with an extra index, runs + i; a seed is the arm's own seed (0 when it has
    none) plus the index."""
    c = Case(mod)
    c.eq(
        "pair by pair: the full run and the run without the evidence share the index of the pair; the control has an extra one",
        list(mod.seeds(None, 3).items()),
        [
            (("full", 0), 0),
            (("withheld", 0), 0),
            (("control", 0), 3),
            (("full", 1), 1),
            (("withheld", 1), 1),
            (("control", 1), 4),
            (("full", 2), 2),
            (("withheld", 2), 2),
            (("control", 2), 5),
        ],
    )
    c.eq(
        "from the arm's own seed",
        mod.seeds(40, 2),
        {("full", 0): 40, ("withheld", 0): 40, ("control", 0): 42, ("full", 1): 41, ("withheld", 1): 41, ("control", 1): 43},
    )
    c.eq("one pair", mod.seeds(0, 1), {("full", 0): 0, ("withheld", 0): 0, ("control", 0): 1})
    c.eq("the largest seed is the one a call record takes", mod.MAX_SEED, LARGEST_SEED)
    first = LARGEST_SEED - 2 * 20 + 1
    c.eq("twenty pairs reach the largest seed and stop there", max(mod.seeds(first, 20).values()), LARGEST_SEED)
    c.refuses(
        "twenty pairs one seed later",
        lambda: mod.seeds(first + 1, 20),
        f"the seeds of 20 pairs from {first + 1} are {first + 1} to {LARGEST_SEED + 1}, and a call takes a seed from 0 to {LARGEST_SEED}",
    )
    c.refuses(
        "a seed below zero",
        lambda: mod.seeds(-1, 2),
        f"the seeds of 2 pairs from -1 are -1 to 2, and a call takes a seed from 0 to {LARGEST_SEED}",
    )
    c.refuses("no pairs", lambda: mod.seeds(None, 0), "a challenge needs at least one pair of runs")
    return c.bad


def _views() -> tuple[Any, Any, tuple[str, ...], Any, dict[str, Any]]:
    """The world of these checks with its ryo item taken out, and what the real arm sees of BTC in each run of a pair."""
    w, snap, *_ = world()
    removed = (w.btc_ryo,)
    taken = intervention.withhold(snap, w.store, removed).snapshot
    candidate = next(x for x in w.candidates(snapshot) if x.candidate_id == "cd-btc")
    readers = {arm: Reader(taken if arm == "withheld" else snap, Access.INDEPENDENT) for arm in ("full", "withheld", "control")}
    return w, snap, removed, candidate, readers


def check_requests(mod: types.ModuleType) -> list[str]:
    """Every run asks its own request (section 6: the seeds are stored; the pairing is measured, not assumed). The gateway gives
    identical calls in flight one fetch and a tape keeps one answer per arguments digest, so two runs with one request would be one answer."""
    c = Case(mod)
    _, snap, _, candidate, readers = _views()
    config = arm_l1.Config("fake/model")
    pairs = 4
    asked: dict[tuple[str, int], dict[str, Any]] = {}
    for (arm, i), seed in mod.seeds(config.seed, pairs).items():
        view = arm_input.build_view(readers[arm], candidate, "BTC", HOUR_MS, snap.snapshot_at_ms)
        request = arm_l1.build_request(dataclasses.replace(config, seed=seed), view)
        asked[(arm, i)] = model_client.build_args(request, model_client.render_messages(request))
    digests = [model_client.args_digest(args) for args in asked.values()]
    c.eq("a request for every run", len(digests), 3 * pairs)
    c.eq("and no two alike", len(set(digests)), 3 * pairs)
    for i in range(pairs):
        full, without, control = asked[("full", i)], asked[("withheld", i)], asked[("control", i)]
        c.eq(
            f"pair {i}: the run without the evidence differs from the full run in the evidence alone",
            sorted(k for k in full.keys() | without.keys() if full.get(k) != without.get(k)),
            ["messages"],
        )
        c.eq(
            f"pair {i}: the control differs from the full run in its seed alone",
            sorted(k for k in full.keys() | control.keys() if full.get(k) != control.get(k)),
            ["seed"],
        )
    return c.bad


def check_gateway(mod: types.ModuleType) -> list[str]:
    """The challenge as scripts/challenge_live.py asks it: every run at once through the real gateway, in the league namespace, to a model
    on loopback that answers slowly enough for all of them to be in flight together. A run whose request repeated another's would share
    its fetch (gateway.submit) and the model would be asked fewer times than there are runs."""
    c = Case(mod)
    w, snap, removed, candidate, readers = _views()
    config = arm_l1.Config("fake/model", max_tokens=2000)
    pairs = 2
    seeds = mod.seeds(config.seed, pairs)
    with model_world([dataclasses.replace(ok(ABSTAIN), delay_s=0.2) for _ in seeds]) as m:
        client = model_client.ModelClient(model_client.GatewayFetcher(m.runner, m.captures, m.clock, m.provider))

        def ask(job: tuple[str, int]) -> arm_l1.Decision:
            settings = dataclasses.replace(config, seed=seeds[job])
            return arm_l1.decide(client, readers[job[0]], candidate, "BTC", HOUR_MS, snap.snapshot_at_ms, settings)

        with ThreadPoolExecutor(max_workers=len(seeds)) as pool:
            done = dict(zip(seeds, pool.map(ask, seeds), strict=True))
        r = mod.challenge(snap, w.store, removed, lambda _reader, i, arm: done[(arm, i)], "UP", pairs)
        seen = sorted(body.get("seed") for body in m.server.requests)
    c.eq("the model was asked once for every run", len(seen), 3 * pairs)
    c.eq("each run with its own seed, a pair's full run and run without the evidence with the same one", seen, [0, 0, 1, 1, 2, 3])
    c.eq("and every run answered", (r.runs, r.failed, sorted({d.verdict.value for d in done.values()})), (2, 0, ["NO_CALL"]))
    return c.bad


def check_standard_set(mod: types.ModuleType) -> list[str]:
    """Several removals of one receipt are one family of tests: each is corrected for the others with Holm's method, so a shift that only
    looks significant because many things were tried is not called a change."""
    from fractions import Fraction

    c = Case(mod)
    w, snap, feats, more = world()
    groups = (("ryo", (w.btc_ryo,)), ("features", (feats,)), ("more", (more,)))

    def script(reader: Any, i: int, arm: str) -> arm_l1.Decision:
        if arm != mod.WITHHELD:
            return up()
        held = set(reader.ids("cd-btc"))
        if w.btc_ryo not in held:
            return down()  # taking RYO out always moves the call
        if feats not in held and i < 6:
            return down()  # taking the features out moves it in six pairs of thirty
        return up()

    out = mod.standard_set(snap, w.store, groups, script, "UP", 30)
    c.eq("a member for each group, in the order given", [m.name for m in out], ["ryo", "features", "more"])
    c.eq("each says what it removed", [m.removed for m in out], [(w.btc_ryo,), (feats,), (more,)])
    raw = [Fraction(2, 2**30), Fraction(2, 2**6), Fraction(1)]
    c.eq("the raw value of each group's test", [m.raw_p for m in out], [float(x) for x in raw])
    holm = [raw[0] * 3, max(raw[0] * 3, raw[1] * 2), max(raw[0] * 3, raw[1] * 2, raw[2])]
    c.eq("and its value corrected for the family", [round(m.adjusted_p or 0, 12) for m in out], [round(float(x), 12) for x in holm])
    c.eq(
        "a change that stands after the correction stays a change",
        (out[0].result.outcome, out[0].result.mechanism is not None),
        (mod.paired_stats.CHANGED, True),
    )
    c.eq(
        "one that was significant alone and is not after it is uncertain, with no mechanism",
        (out[1].result.outcome, out[1].result.mechanism, out[1].result.verdict.p <= 0.05),
        (mod.paired_stats.UNCERTAIN, None, True),
    )
    c.eq(
        "with the reason in words",
        out[1].result.verdict.note,
        "significant alone, not after the correction over the removals tried",
    )
    saved = mod.paired_stats.ALPHA
    mod.paired_stats.ALPHA = out[1].adjusted_p  # a corrected value exactly at the level is not above it
    try:
        at_level = mod.standard_set(snap, w.store, groups, script, "UP", 30)
    finally:
        mod.paired_stats.ALPHA = saved
    c.eq("a corrected value exactly at the level still stands", at_level[1].result.outcome, mod.paired_stats.CHANGED)
    c.eq("one that moved nothing stays what it was", out[2].result.outcome, mod.paired_stats.UNCERTAIN)
    alone = mod.standard_set(snap, w.store, groups[1:2], script, "UP", 30)
    c.eq("a family of one is the test alone", (alone[0].adjusted_p, alone[0].result.outcome), (alone[0].raw_p, mod.paired_stats.CHANGED))

    # a group that cannot be evaluated is not one of the family
    def missing_when_bars_out(reader: Any, i: int, arm: str) -> arm_l1.Decision:
        if arm == mod.WITHHELD and reader.missing("cd-btc"):
            return made(V.FAILED_EVIDENCE, None, reason=R.MISSING_EVIDENCE)
        return script(reader, i, arm)

    mixed = mod.standard_set(snap, w.store, (*groups[:2], ("bars", (w.btc_bars,))), missing_when_bars_out, "UP", 30)
    c.eq(
        "a source the arm cannot decide without is required, has no value, and does not count in the family",
        (mixed[2].result.outcome, mixed[2].raw_p, mixed[2].adjusted_p, [round(m.adjusted_p or 0, 12) for m in mixed[:2]]),
        (mod.REQUIRED, None, None, [round(float(raw[0] * 2), 12), round(float(max(raw[0] * 2, raw[1])), 12)]),
    )
    c.refuses("no group", lambda: mod.standard_set(snap, w.store, (), script, "UP", 30), "a standard set has at least one group")
    c.refuses(
        "a group named twice",
        lambda: mod.standard_set(snap, w.store, (groups[0], groups[0]), script, "UP", 30),
        "each group of a standard set is named once",
    )
    c.eq("the member type is frozen", mod.Member.__dataclass_params__.frozen, True)
    return c.bad


def check_placebo_challenge(mod: types.ModuleType) -> list[str]:
    """The placebo control as a challenge of its own: the arm is asked on the evidence and on the evidence with a neutral item added, so a
    shift of the decision can be read against what longer text alone does."""
    c = Case(mod)
    w, snap, *_ = world()
    seen: list[tuple[str, bool]] = []

    def decide(reader: Any, i: int, arm: str) -> arm_l1.Decision:
        seen.append((arm, any(x.source == "hoshitori.placebo" for x in reader.items("cd-btc"))))
        return up()

    out = mod.placebo_challenge(snap, w.store, "cd-btc", (w.btc_ryo, w.btc_int), decide, "UP", 60)
    c.eq("the arm sees the neutral item in the withheld run and in no other", {x for x in seen if x[1]}, {(mod.WITHHELD, True)})
    c.eq("every run of the full and control arms is without it", {x for x in seen if not x[1]}, {(mod.FULL, False), (mod.CONTROL, False)})
    c.eq("three runs of each of the pairs", len(seen), 180)
    c.eq(
        "nothing taken out, nothing left over",
        (out.intervention.status, out.intervention.removed, out.intervention.withheld, out.intervention.leaks),
        ("READY", (), (), ()),
    )
    c.eq(
        "the what-if snapshot holds the neutral item",
        any(i.source == "hoshitori.placebo" for i, _ in out.intervention.snapshot.items),
        True,
    )
    c.eq("60 pairs with no change is stable", (out.outcome, out.failed, out.runs), (mod.paired_stats.STABLE, 0, 60))
    c.eq("and has no mechanism to explain", out.mechanism, None)
    moved = mod.placebo_challenge(
        snap, w.store, "cd-btc", (w.btc_ryo,), lambda reader, i, arm: down() if arm == mod.WITHHELD else up(), "UP", 30
    )
    c.eq("a decision that moves when only text is added is a change like any other", moved.outcome, mod.paired_stats.CHANGED)
    one = mod.placebo_challenge(snap, w.store, "cd-btc", (w.btc_ryo,), decide, "UP", 1)
    c.eq("one pair is enough to ask", (one.runs, one.outcome != mod.REQUIRED, one.intervention.status), (1, True, "READY"))
    lost = mod.placebo_challenge(
        snap,
        w.store,
        "cd-btc",
        (w.btc_ryo,),
        lambda reader, i, arm: made(V.FAILED_EVIDENCE, None, reason=R.MISSING_EVIDENCE) if arm == mod.WITHHELD else up(),
        "UP",
        30,
    )
    c.eq("an arm that fails on the neutral item is a change, never evidence the arm requires", lost.outcome, mod.paired_stats.CHANGED)
    c.refuses(
        "no runs",
        lambda: mod.placebo_challenge(snap, w.store, "cd-btc", (w.btc_ryo,), decide, "UP", 0),
        "a challenge needs at least one pair of runs",
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Any, ...] = (
        check_classes,
        check_challenge,
        check_required,
        check_leak,
        check_mechanism,
        check_seeds,
        check_requests,
        check_gateway,
        check_standard_set,
        check_placebo_challenge,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
