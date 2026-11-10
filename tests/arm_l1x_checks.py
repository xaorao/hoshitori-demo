"""Checks of arm L1x (``hoshitori.arm_l1x``), runnable against a mutant: the size of the control, its aggregate and its flags."""

from __future__ import annotations

import hashlib
import threading
import types
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hoshitori import arm_l1, canonical, model_client, snapshot, typed
from hoshitori.capture import RawCapture
from tests.arm_l1_checks import reply
from tests.model_world import chat_body
from tests.snapshot_checks import S, World

D = Decimal
T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
HOUR = 3_600_000


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")


class BySeed:
    """A fetcher that answers each sample from its own list, by the seed it was asked with."""

    def __init__(self, replies: dict[int, list[Any]]) -> None:
        self.replies = {k: list(v) for k, v in replies.items()}
        self.sent: list[Any] = []
        self._lock = threading.Lock()

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        with self._lock:
            self.sent.append(args)
            return self.replies[args["seed"]].pop(0)


def answer(content: str | None, number: int = 1, **kw: Any) -> RawCapture:
    return RawCapture(
        "cap-" + f"{number:032x}",
        "model-t",
        {"operation": "chat", "arguments": {}},
        200,
        {},
        chat_body(content, **kw),
        T0,
        T0 + timedelta(seconds=2),
    )


def scene() -> tuple[World, Any, snapshot.Candidate]:
    w = World()
    return w, snapshot.Reader(w.freeze(snapshot), snapshot.Access.INDEPENDENT), snapshot.Candidate("cd-btc", "tk-btc")


def run(mod: types.ModuleType, fetch: BySeed, reader: Any, cand: Any, target: int, **cfg: Any) -> Any:
    config = mod.Config(arm_l1.Config("test/model", temperature="0.7"), **cfg)
    return mod.decide(model_client.ModelClient(fetch), reader, cand, "BTC", HOUR, S, config, target)


def call(w: World, p: float, **over: Any) -> str:
    return reply(w, p=p, **over)


def abstain(w: World, reason: str = "OUT_OF_SCOPE") -> str:
    return reply(w, decision="NO_CALL", p=None, no_call_reason=reason, invalidations=[], cites=[], assertions=[], summary=None)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("names", (mod.ARM, mod.FIRST, mod.MAX_SAMPLES, mod.TOLERANCE), ("L1x", 3, 15, D("0.1")))
    for cls in (mod.Config, mod.Control):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    cfg = mod.Config(arm_l1.Config("m"))
    c.eq("defaults", (cfg.seed_base, cfg.max_samples), (1000, 15))
    sample = arm_l1.Config("m", temperature="0.7")
    want = hashlib.sha256(
        canonical.dumps(
            {
                "arm": "L1x",
                "sample": arm_l1.agent_version_hash(sample),
                "seed_base": 1000,
                "max_samples": 15,
                "first": 3,
                "tolerance": "0.1",
                "schema": typed.json_schema(arm_l1.SCHEMA),
            }
        )
    ).hexdigest()
    c.eq("the version of the control", mod.agent_version_hash(mod.Config(sample)), want)
    c.eq(
        "the sample's settings, the seed base and the cap make a new control",
        len(
            {
                mod.agent_version_hash(mod.Config(sample)),
                mod.agent_version_hash(mod.Config(arm_l1.Config("m", temperature="0.2"))),
                mod.agent_version_hash(mod.Config(sample, 7)),
                mod.agent_version_hash(mod.Config(sample, 1000, 9)),
            }
        ),
        4,
    )
    return c.bad


def check_size(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for target, mean, cap, want in (
        (180, 60, 15, 3),
        (300, 60, 15, 5),
        (360, 60, 15, 7),
        (330, 60, 15, 7),  # 5.5 rounds to the even 6, then up to 7
        (210, 60, 15, 5),  # 3.5 rounds to 4, then up to 5
        (150, 60, 15, 3),  # 2.5 rounds to 2, and at least three
        (60, 60, 15, 3),
        (0, 60, 15, 3),
        (1000, 60, 15, 15),
        (1000, 60, 14, 13),
        (1000, 60, 3, 3),
        (500, 0, 15, 3),
        (5, 1, 15, 5),
        (500, -5, 15, 3),
    ):
        c.eq(f"{target} tokens at {mean} a sample, at most {cap}", mod.samples_needed(target, D(mean), cap), want)
    c.eq("the median of three", mod.median([D("0.7"), D("0.5"), D("0.6")]), D("0.6"))
    c.eq("the median of one", mod.median([D("0.7")]), D("0.7"))
    c.eq("the median of four is the mean of the middle two", mod.median([D("0.1"), D("0.2"), D("0.3"), D("0.9")]), D("0.25"))
    c.eq("and is kept to six places", mod.median([D("0.1"), D("0.2000005")]), D("0.150000"))
    return c.bad


def check_decide(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand = scene()
    d = lambda p, **o: [answer(call(w, p, **o), number=int(p * 100))]  # noqa: E731
    fetch = BySeed({1000: d(0.6, summary="a"), 1001: d(0.7, summary="b"), 1002: d(0.5, summary="c")})
    out = run(mod, fetch, reader, cand, 180)
    dec = out.decision
    c.eq(
        "three samples match 180 tokens at 60 each",
        (len(out.samples), out.tokens, out.target, out.mismatch, out.collapsed),
        (3, 180, 180, False, False),
    )
    c.eq(
        "a call at the median probability",
        (dec.verdict, dec.p, dec.reason, dec.gate),
        (arm_l1.Verdict.CALL, D("0.6"), None, arm_l1.Gate.NONE),
    )
    c.eq("the seeds are distinct and counted from the base", sorted(a["seed"] for a in fetch.sent), [1000, 1001, 1002])
    c.eq("each sample is asked at the configured temperature", {a["temperature"] for a in fetch.sent}, {"0.7"})
    c.eq(
        "it is the control's own version",
        dec.agent_version,
        mod.agent_version_hash(mod.Config(arm_l1.Config("test/model", temperature="0.7"))),
    )
    c.eq("it keeps every call", (len(out.calls), len(dec.calls)), (3, 3))
    c.eq(
        "the cites and the hash of what the calls used",
        (dec.evidence_ids, dec.observation_set_hash),
        ((w.btc_bars,), reader.set_hash("cd-btc", [w.btc_bars])),
    )
    c.eq("the invalidation of the calls, once", dec.invalidation, ("last_close below 78000",))
    c.eq("a base seed", sorted(a["seed"] for a in BySeedProbe(mod, w, reader, cand, seed_base=50)), [50, 51, 52])
    # five samples for 300 tokens
    fetch = BySeed({1000 + i: d(0.5 + i / 100, summary=str(i)) for i in range(5)})
    out = run(mod, fetch, reader, cand, 300)
    c.eq("five samples for 300", (len(out.samples), out.tokens, out.mismatch, out.decision.p), (5, 300, False, D("0.52")))
    # a cap
    fetch = BySeed({1000 + i: d(0.5 + i / 100, summary=str(i)) for i in range(5)})
    out = run(mod, fetch, reader, cand, 10_000, max_samples=5)
    c.eq("a cap on the samples is kept, and the shortfall is a mismatch", (len(out.samples), out.mismatch), (5, True))
    # the tolerance
    fetch = BySeed({1000 + i: d(0.5 + i / 1000, summary=str(i)) for i in range(15)})
    out = run(mod, fetch, reader, cand, 1000)
    c.eq("900 billed for 1000 is exactly ten percent away: not a mismatch", (len(out.samples), out.tokens, out.mismatch), (15, 900, False))
    fetch = BySeed({1000 + i: d(0.5 + i / 1000, summary=str(i)) for i in range(15)})
    out = run(mod, fetch, reader, cand, 1001)
    c.eq("and for 1001 it is", (out.tokens, out.mismatch), (900, True))
    fetch = BySeed({1000 + i: d(0.5 + i / 1000, summary=str(i)) for i in range(3)})
    out = run(mod, fetch, reader, cand, 100)
    c.eq("three samples bill 180 against 100: more than a tenth over is a mismatch too", (out.tokens, out.mismatch), (180, True))
    # collapse
    same = [answer(call(w, 0.6), number=7)]
    out = run(mod, BySeed({1000: list(same), 1001: list(same), 1002: list(same)}), reader, cand, 180)
    c.eq(
        "three samples that say the same are a collapsed control",
        (out.collapsed, out.decision.verdict, out.decision.p),
        (True, arm_l1.Verdict.CALL, D("0.6")),
    )
    out = run(mod, BySeed({1000: d(0.6), 1001: d(0.6), 1002: d(0.6, summary="other")}), reader, cand, 180)
    c.eq("one that differs is not", out.collapsed, False)
    gone = model_client.Gone(model_client.Outcome.UNAVAILABLE, "x")
    out = run(mod, BySeed({1000: list(same), 1001: list(same), 1002: [gone]}), reader, cand, 120)
    c.eq("two usable samples that say the same are collapsed", out.collapsed, True)
    c.eq(
        "a single usable sample is no collapse",
        run(
            mod,
            BySeed(
                {
                    1000: list(same),
                    1001: [model_client.Gone(model_client.Outcome.UNAVAILABLE, "x")],
                    1002: [model_client.Gone(model_client.Outcome.UNAVAILABLE, "x")],
                }
            ),
            reader,
            cand,
            60,
        ).collapsed,
        False,
    )
    return c.bad


def BySeedProbe(mod: types.ModuleType, w: World, reader: Any, cand: Any, seed_base: int) -> list[dict[str, Any]]:
    fetch = BySeed({seed_base + i: [answer(call(w, 0.5 + i / 100, summary=str(i)), number=i + 1)] for i in range(3)})
    run(mod, fetch, reader, cand, 180, seed_base=seed_base)
    return fetch.sent


def check_vote(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand = scene()
    A = lambda body, n=1: [answer(body, number=n)]  # noqa: E731
    gone = [model_client.Gone(model_client.Outcome.UNAVAILABLE, "down")]
    V, R = arm_l1.Verdict, arm_l1.Reason

    def vote(a: list[Any], b: list[Any], c3: list[Any], target: int = 180) -> Any:
        return run(mod, BySeed({1000: a, 1001: b, 1002: c3}), reader, cand, target)

    out = vote(A(abstain(w), 1), A(abstain(w), 2), A(call(w, 0.6), 3))
    c.eq(
        "two abstentions out of three: an abstention",
        (out.decision.verdict, out.decision.reason, out.decision.p, out.decision.evidence_ids, out.decision.invalidation),
        (V.NO_CALL, R.OUT_OF_SCOPE, None, (), ()),
    )
    c.eq("and the hash of nothing used", out.decision.observation_set_hash, reader.set_hash("cd-btc", ()))
    out = vote(
        A(abstain(w, "OUT_OF_SCOPE"), 1), A(abstain(w, "EVIDENCE_CONFLICT_UNRESOLVED"), 2), A(abstain(w, "ESSENTIAL_PREMISE_UNPROVEN"), 3)
    )
    c.eq("reasons that tie: the first by name", out.decision.reason, R.ESSENTIAL_PREMISE_UNPROVEN)
    out = vote(
        A(abstain(w, "OUT_OF_SCOPE"), 1), A(abstain(w, "EVIDENCE_CONFLICT_UNRESOLVED"), 2), A(abstain(w, "EVIDENCE_CONFLICT_UNRESOLVED"), 3)
    )
    c.eq("the most common reason", out.decision.reason, R.EVIDENCE_CONFLICT_UNRESOLVED)
    out = vote(gone, A(call(w, 0.6), 2), A(call(w, 0.8, summary="s"), 3), 120)
    c.eq(
        "a sample that failed does not vote: two calls of two",
        (out.decision.verdict, out.decision.p, [s.verdict for s in out.samples]),
        (V.CALL, D("0.7"), [V.FAILED, V.CALL, V.CALL]),
    )
    out = vote(gone, A(call(w, 0.6), 2), A(abstain(w), 3), 120)
    c.eq("one call of two is not a majority", (out.decision.verdict, out.decision.reason), (V.NO_CALL, R.OUT_OF_SCOPE))
    out = vote(gone, gone, A(call(w, 0.6), 3), 60)
    c.eq("one call of one is", (out.decision.verdict, out.decision.p), (V.CALL, D("0.6")))
    out = vote(gone, gone, gone)
    c.eq(
        "all failed: a failure with the first one's reason",
        (out.decision.verdict, out.decision.reason, out.decision.p, out.decision.proposal, len(out.calls)),
        (V.FAILED, R.MODEL_UNAVAILABLE, None, None, 3),
    )
    false = {
        "id": "a1",
        "kind": "NUMERIC",
        "op": "eq",
        "value": 80000,
        "unit": "price",
        "window": "spot",
        "cites": [{"evidence_id": w.btc_bars, "field_path": "last_close"}],
    }
    out = vote(A(call(w, 0.6, assertions=[false]), 1), A(call(w, 0.7), 2), A(call(w, 0.8, summary="s"), 3))
    c.eq(
        "a sample stopped by the grounding gate votes to abstain: two calls of three still call",
        (out.decision.verdict, out.decision.p),
        (V.CALL, D("0.75")),
    )
    out = vote(A(call(w, 0.6, assertions=[false]), 1), A(call(w, 0.7, assertions=[false], summary="x"), 2), A(call(w, 0.8, summary="s"), 3))
    c.eq(
        "and two of three stopped is an abstention for the premise",
        (out.decision.verdict, out.decision.reason),
        (V.NO_CALL, R.ESSENTIAL_PREMISE_UNPROVEN),
    )
    # the union of what the calls used, and the invalidations
    other = reply(w, cites=[w.market], invalidations=[{"field": "close", "op": "above", "level": 90000}], assertions=[], summary="s")
    out = vote(A(call(w, 0.6), 1), A(other, 2), A(call(w, 0.7, summary="t"), 3))
    c.eq("the cites are the union, in order", out.decision.evidence_ids, tuple(sorted([w.btc_bars, w.market])))
    third = reply(w, cites=[w.btc_bars, w.market, w.btc_ryo], assertions=[], summary="u")
    out = vote(A(call(w, 0.6), 1), A(third, 2), A(other, 3))
    from types import SimpleNamespace

    many = [f"ev{i:02d}" for i in range(12)]
    shown = [SimpleNamespace(evidence_ids=tuple(reversed(many[:8]))), SimpleNamespace(evidence_ids=tuple(reversed(many[4:])))]
    c.eq("what the calls cited is sorted and once each, whatever order they came in", mod.cited(shown), tuple(many))
    c.eq("and nothing cited is nothing", mod.cited([]), ())
    c.eq("and sorted whatever the number", out.decision.evidence_ids, tuple(sorted([w.btc_bars, w.market, w.btc_ryo])))
    c.eq(
        "the invalidations are those of the calls, once, in order of the samples",
        out.decision.invalidation,
        ("last_close below 78000", "close above 90000"),
    )
    many = [reply(w, invalidations=[{"field": "close", "op": "below", "level": 70000 + i}], summary=str(i)) for i in range(5)]
    out = run(mod, BySeed({1000 + i: [answer(many[i], number=i + 1)] for i in range(5)}), reader, cand, 300)
    c.eq("at most three invalidations", len(out.decision.invalidation), 3)
    # a source missing: nobody is asked
    w2 = World()
    short = snapshot.Reader(w2.freeze(snapshot, required=["binance-spot", "ryo"]), snapshot.Access.INDEPENDENT)
    fetch = BySeed({})
    out = run(mod, fetch, short, snapshot.Candidate("cd-eth", "tk-eth"), 180)
    c.eq(
        "a source missing",
        (out.decision.verdict, out.decision.reason, out.samples, out.calls, out.tokens, out.mismatch, out.collapsed, fetch.sent),
        (V.FAILED_EVIDENCE, R.MISSING_EVIDENCE, (), (), 0, False, False, []),
    )
    # two sources that disagree on a price: nobody is asked
    from tests.arm_l1_checks import conflict_scene

    _, clash, check_id = conflict_scene("110600")
    fetch = BySeed({})
    out = run(mod, fetch, clash, snapshot.Candidate("cd-btc", "tk-btc"), 180)
    c.eq(
        "a conflict between sources",
        (
            out.decision.verdict,
            out.decision.reason,
            out.decision.evidence_ids,
            (out.samples, out.calls, out.tokens, out.target, out.mismatch, out.collapsed),
            fetch.sent,
        ),
        (V.NO_CALL, R.EVIDENCE_CONFLICT_UNRESOLVED, (check_id,), ((), (), 0, 180, False, False), []),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_size, check_decide, check_vote):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
