"""Checks of arm L2 (``hoshitori.arm_l2``), runnable against a mutant: the debate, the judge, the cap, the collapse and the failures."""

from __future__ import annotations

import hashlib
import json
import threading
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import arm_l1, canonical, model_client, snapshot, typed
from hoshitori.capture import RawCapture
from tests.arm_l1_checks import reply as judge_reply
from tests.model_world import chat_body
from tests.snapshot_checks import S, World

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
HOUR = 3_600_000


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")


class ByStage:
    """A fetcher that answers each stage from its own list, whatever the order the stages arrive in."""

    def __init__(self, **replies: list[Any]) -> None:
        self.replies = {k: list(v) for k, v in replies.items()}
        self.sent: dict[str, list[Any]] = {}
        self._lock = threading.Lock()

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        with self._lock:
            self.sent.setdefault(stage, []).append(args)
            return self.replies[stage].pop(0)


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


def side(support: list[str], oppose: list[str], claim: str = "Up", **over: Any) -> str:
    body = {"claim": claim, "support": support, "oppose": oppose, "uncertainty": "Thin data", "would_change_if": "A close below the last"}
    body.update(over)
    return json.dumps(body)


def scene() -> tuple[World, Any, snapshot.Candidate, Any]:
    w = World()
    reader = snapshot.Reader(w.freeze(snapshot), snapshot.Access.INDEPENDENT)
    config = arm_l1.Config("test/model")
    return w, reader, snapshot.Candidate("cd-btc", "tk-btc"), (config, config, config)


def run(mod: types.ModuleType, fetch: ByStage, reader: Any, cand: Any, roles: Any, cap: int = 10_000) -> Any:
    cfg = mod.Config(roles[0], roles[1], roles[2], cap)
    return mod.decide(model_client.ModelClient(fetch), reader, cand, "BTC", HOUR, S, cfg)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("names", (mod.ARM, mod.BULL, mod.BEAR, mod.JUDGE, mod.MAX_SIDE), ("L2", "bull", "bear", "judge", 8))
    c.eq(
        "stages and prompt versions",
        (mod.STAGES, mod.PROMPT_VERSIONS),
        ({"bull": "l2_bull", "bear": "l2_bear", "judge": "l2_judge"}, {"bull": "l2-bull-v2", "bear": "l2-bear-v2", "judge": "l2-judge-v5"}),
    )
    digests = {role: hashlib.sha256(text.encode()).hexdigest() for role, text in mod.SYSTEMS.items()}
    c.eq(
        "the system texts",
        digests,
        {
            "bull": "b90e604328fa2cab7a9edc140588955268404c71a340ef0f06fadffe2ae9da27",
            "bear": "115d2c10e6f50833721208c61d634672d230b0bb3d043f6201325d2034894c91",
            "judge": "671586ec22bf030be9f4dfd4ae488339c21d6d154ee75c2decc7976a401b158f",
        },
    )
    c.eq(
        "the shape of a side, as a debater is told it",
        typed.describe(mod.ROLE_SCHEMA),
        '{"claim": text(1..300 characters), "support": [text(28..28 characters)] (0..8 items, all different), '
        '"oppose": [text(28..28 characters)] (0..8 items, all different), "uncertainty": text(1..200 characters), '
        '"would_change_if": text(1..200 characters)}',
    )
    for cls in (mod.Config, mod.RoleAnswer, mod.Disagreement, mod.Debate):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_version(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    a = arm_l1.Config("a")
    cfg = mod.Config(a, a, a, 1000)
    want = hashlib.sha256(
        canonical.dumps(
            {
                "arm": "L2",
                "prompts": dict(mod.PROMPT_VERSIONS),
                "systems": {r: hashlib.sha256(t.encode()).hexdigest() for r, t in mod.SYSTEMS.items()},
                "role_schema": typed.json_schema(mod.ROLE_SCHEMA),
                "judge_schema": typed.json_schema(arm_l1.SCHEMA),
                "settings": {
                    r: {
                        "model": "a",
                        "max_tokens": 6000,
                        "temperature": "0",
                        "seed": None,
                        "response_format": "json_schema",
                        "thinking": None,
                    }
                    for r in ("bull", "bear", "judge")
                },
                "cap_tokens": 1000,
            }
        )
    ).hexdigest()
    c.eq("the version of the debate", mod.agent_version_hash(cfg), want)
    b = arm_l1.Config("b")
    hashes = {
        mod.agent_version_hash(cfg),
        mod.agent_version_hash(mod.Config(b, a, a, 1000)),
        mod.agent_version_hash(mod.Config(a, b, a, 1000)),
        mod.agent_version_hash(mod.Config(a, a, b, 1000)),
        mod.agent_version_hash(mod.Config(a, a, a, 999)),
        mod.agent_version_hash(mod.Config(arm_l1.Config("a", temperature="0.5"), a, a, 1000)),
        mod.agent_version_hash(mod.Config(a, arm_l1.Config("a", thinking=False), a, 1000)),
    }
    c.eq("each role's model and settings and the cap make a new debate", len(hashes), 7)
    return c.bad


def check_pieces(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand, _ = scene()
    from hoshitori.arm_input import build_view

    view = build_view(reader, cand, "BTC", HOUR, S)
    check = mod.make_check(view) if hasattr(mod, "make_check") else mod._role_check(view)

    def says(value: dict[str, Any]) -> str | None:
        try:
            check(value)
        except typed.TypedError as exc:
            return str(exc)
        return None

    good = typed.parse(mod.ROLE_SCHEMA, side([w.btc_bars], [w.market]).encode())
    c.eq("a good side", says(good), None)
    c.eq("a support that was not shown", says({**good, "support": ["ev" + "z" * 26]}), "$.support[0]: is not one of the citable ids")
    c.eq(
        "an opposition that was not shown",
        says({**good, "oppose": [w.market, "ev" + "y" * 26]}),
        "$.oppose[1]: is not one of the citable ids",
    )
    c.eq("an item on both sides", says({**good, "oppose": [w.btc_bars]}), "$.oppose: an item cannot both support and oppose the claim")
    names = [f"ev{chr(97 + i)}" for i in range(10)]
    zz = ("zz1", "zz9", "zz3", "zz7", "zz5", "zz2")
    many_bull = mod.RoleAnswer("bull", "x", tuple(names[:7]), tuple(names[7:]), "u", "w")
    many_bear = mod.RoleAnswer("bear", "y", zz, (), "u", "w")
    big = mod.disagreement(many_bull, many_bear, tuple(reversed([*names, *zz])))
    ordered = ("zz1", "zz2", "zz3", "zz5", "zz7", "zz9")
    c.eq(
        "every list of items is in order",
        (big.bull_only, big.bear_only, big.judge_on_bull, big.judge_on_bear),
        (tuple(names), ordered, tuple(names), ordered),
    )
    both = mod.disagreement(
        mod.RoleAnswer("bull", "x", tuple(names), (), "u", "w"), mod.RoleAnswer("bear", "y", tuple(reversed(names)), (), "u", "w"), ()
    )
    c.eq("and the shared ones", both.shared, tuple(names))
    accent = mod._debate_text(mod.RoleAnswer("bull", "Caf\u00e9", (), (), "u", "w"), mod.RoleAnswer("bear", "y", (), (), "u", "w"))
    c.eq("text is written as it is, not escaped", "Caf\u00e9" in accent, True)
    bull = mod.RoleAnswer("bull", "Up", ("a", "b"), ("c",), "u", "w")
    bear = mod.RoleAnswer("bear", "Down", ("b",), ("d",), "u", "w")
    d = mod.disagreement(bull, bear, ("a", "d", "b", "z"))
    c.eq("who cited what", (d.bull_only, d.bear_only, d.shared), (("a", "c"), ("d",), ("b",)))
    c.eq("and which side the judge leaned on", (d.judge_on_bull, d.judge_on_bear), (("a",), ("d",)))
    text = mod._debate_text(bull, bear)
    c.eq(
        "the debate as the judge reads it",
        text,
        'BULL\n{\n "claim": "Up",\n "oppose": [\n  "c"\n ],\n "support": [\n  "a",\n  "b"\n ],\n "uncertainty": "u",\n "would_change_if": "w"\n}'
        '\n\nBEAR\n{\n "claim": "Down",\n "oppose": [\n  "d"\n ],\n "support": [\n  "b"\n ],\n "uncertainty": "u",\n "would_change_if": "w"\n}',
    )
    return c.bad


def check_decide(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand, roles = scene()
    fetch = ByStage(
        l2_bull=[answer(side([w.btc_bars], [], "Up"))],
        l2_bear=[answer(side([w.market], [], "Down", uncertainty="Flat tape"), number=2)],
        l2_judge=[answer(judge_reply(w), number=3)],
    )
    out = run(mod, fetch, reader, cand, roles)
    d = out.decision
    c.eq(
        "a debate that ends in a call",
        (d.verdict, d.p, d.gate, out.failed_role, out.collapsed),
        (arm_l1.Verdict.CALL, __import__("decimal").Decimal("0.62"), arm_l1.Gate.NONE, None, False),
    )
    c.eq(
        "the sides, kept",
        (out.bull.role, out.bull.claim, out.bull.support, out.bear.role, out.bear.claim, out.bear.uncertainty),
        ("bull", "Up", (w.btc_bars,), "bear", "Down", "Flat tape"),
    )
    c.eq(
        "the disagreement",
        (out.disagreement.bull_only, out.disagreement.bear_only, out.disagreement.shared),
        ((w.btc_bars,), (w.market,), ()),
    )
    c.eq("and the judge leaned on the bull's item", (out.disagreement.judge_on_bull, out.disagreement.judge_on_bear), ((w.btc_bars,), ()))
    c.eq(
        "three calls, the bull's, the bear's and the judge's, in that order",
        [x.stage for x in out.calls],
        ["l2_bull", "l2_bear", "l2_judge"],
    )
    c.eq("their tokens are summed", out.tokens, 180)
    c.eq("the agent is the debate, not the judge alone", d.agent_version, mod.agent_version_hash(mod.Config(*roles, 10_000)))
    c.eq("each stage asked once", {k: len(v) for k, v in fetch.sent.items()}, {"l2_bull": 1, "l2_bear": 1, "l2_judge": 1})
    bull_sent, bear_sent, judge_sent = fetch.sent["l2_bull"][0], fetch.sent["l2_bear"][0], fetch.sent["l2_judge"][0]
    c.eq(
        "the bull and the bear read the items and not each other",
        ("<<<DATA debate" in bull_sent["messages"][1]["content"], "<<<DATA debate" in bear_sent["messages"][1]["content"]),
        (False, False),
    )
    c.eq("their systems differ", bull_sent["messages"][0]["content"] != bear_sent["messages"][0]["content"], True)
    judge_user = judge_sent["messages"][1]["content"]
    c.eq(
        "the judge reads the debate as untrusted data beside the items",
        (judge_user.count("<<<DATA debate"), "Flat tape" in judge_user, judge_user.count("<<<DATA item_")),
        (1, True, 3),
    )
    c.eq("the judge answers in the shape of arm L1", judge_sent["response_format"]["json_schema"]["name"], "l2_judge")
    c.eq(
        "the roles answer in the shape of a side", bull_sent["response_format"]["json_schema"]["schema"], typed.json_schema(mod.ROLE_SCHEMA)
    )
    # collapse
    same = side([w.btc_bars], [w.market], "Up")
    fetch = ByStage(l2_bull=[answer(same)], l2_bear=[answer(same, number=2)], l2_judge=[answer(judge_reply(w), number=3)])
    out = run(mod, fetch, reader, cand, roles)
    c.eq(
        "two roles that say the same thing are a collapsed debate, and the decision stands",
        (out.collapsed, out.decision.verdict),
        (True, arm_l1.Verdict.CALL),
    )
    for field, value in (("claim", "Different"), ("uncertainty", "Else"), ("would_change_if", "Other")):
        fetch = ByStage(
            l2_bull=[answer(same)],
            l2_bear=[answer(side([w.btc_bars], [w.market], **{field: value}), number=2)],
            l2_judge=[answer(judge_reply(w), number=3)],
        )
        c.eq(f"a different {field} is not a collapse", run(mod, fetch, reader, cand, roles).collapsed, False)
    for support, oppose in (([w.market], [w.market and w.btc_ryo]), ([w.btc_bars], [w.btc_ryo])):
        fetch = ByStage(
            l2_bull=[answer(same)], l2_bear=[answer(side(support, oppose, "Up"), number=2)], l2_judge=[answer(judge_reply(w), number=3)]
        )
        c.eq("different items are not a collapse", run(mod, fetch, reader, cand, roles).collapsed, False)
    # no consensus
    none = judge_reply(
        w,
        decision="NO_CALL",
        p=None,
        no_call_reason="EVIDENCE_CONFLICT_UNRESOLVED",
        invalidations=[],
        cites=[],
        assertions=[],
        summary=None,
    )
    fetch = ByStage(
        l2_bull=[answer(side([w.btc_bars], []))],
        l2_bear=[answer(side([w.market], [], "Down"), number=2)],
        l2_judge=[answer(none, number=3)],
    )
    out = run(mod, fetch, reader, cand, roles)
    c.eq(
        "no consensus is an abstention",
        (out.decision.verdict, out.decision.reason, out.decision.p),
        (arm_l1.Verdict.NO_CALL, arm_l1.Reason.EVIDENCE_CONFLICT_UNRESOLVED, None),
    )
    # the grounding gate applies to the judge
    false = {
        "id": "a1",
        "kind": "NUMERIC",
        "op": "eq",
        "value": 80000,
        "unit": "price",
        "window": "spot",
        "cites": [{"evidence_id": w.btc_bars, "field_path": "last_close"}],
    }
    fetch = ByStage(
        l2_bull=[answer(side([w.btc_bars], []))],
        l2_bear=[answer(side([w.market], [], "Down"), number=2)],
        l2_judge=[answer(judge_reply(w, assertions=[false]), number=3)],
    )
    out = run(mod, fetch, reader, cand, roles)
    c.eq(
        "a judge that states what is false is stopped by the gate",
        (out.decision.verdict, out.decision.reason, out.decision.gate),
        (arm_l1.Verdict.NO_CALL, arm_l1.Reason.ESSENTIAL_PREMISE_UNPROVEN, arm_l1.Gate.GROUNDING),
    )
    return c.bad


def check_concurrency(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand, roles = scene()
    gate = threading.Barrier(2, timeout=5)

    class Meeting(ByStage):
        def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
            if stage in ("l2_bull", "l2_bear"):
                gate.wait()
            return super().__call__(args, stage, timeout_s)

    fetch = Meeting(
        l2_bull=[answer(side([w.btc_bars], []))],
        l2_bear=[answer(side([w.market], [], "Down"), number=2)],
        l2_judge=[answer(judge_reply(w), number=3)],
    )
    out = run(mod, fetch, reader, cand, roles)
    c.eq("the two sides are asked at the same time", (out.decision.verdict, out.failed_role), (arm_l1.Verdict.CALL, None))
    c.eq("the sides, in the order of the roles", (mod.SIDES, [x.stage for x in out.calls][:2]), (("bull", "bear"), ["l2_bull", "l2_bear"]))
    return c.bad


def check_failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand, roles = scene()
    outcomes = model_client.Outcome
    good_bull, good_bear = answer(side([w.btc_bars], [])), answer(side([w.market], [], "Down"), number=2)

    def go(bull: Any, bear: Any, judge: Any = None) -> Any:
        def listed(x: Any) -> list[Any]:
            return x if isinstance(x, list) else ([] if x is None else [x])

        fetch = ByStage(l2_bull=listed(bull), l2_bear=listed(bear), l2_judge=listed(judge))
        return run(mod, fetch, reader, cand, roles), fetch

    out, fetch = go(model_client.Gone(outcomes.UNAVAILABLE, "down"), good_bear)
    d = out.decision
    c.eq("a debate that ended in a failure was not collapsed", out.collapsed, False)
    c.eq(
        "the bull is down",
        (d.verdict, d.reason, out.failed_role, out.bull, out.bear, out.disagreement, "l2_judge" in fetch.sent),
        (arm_l1.Verdict.FAILED, arm_l1.Reason.MODEL_UNAVAILABLE, "bull", None, None, None, False),
    )
    c.eq(
        "the failure keeps what happened and what it cost",
        ([x.stage for x in out.calls], [x.outcome for x in out.calls], out.tokens),
        (["l2_bull", "l2_bear"], [outcomes.UNAVAILABLE, outcomes.VALID], 60),
    )
    out, _ = go(good_bull, answer(None, number=2, finish="length"))
    c.eq("the bear is cut short", (out.decision.reason, out.failed_role), (arm_l1.Reason.MODEL_TRUNCATED, "bear"))
    out, _ = go([answer("nope"), answer("nope", number=4)], good_bear)
    c.eq("both fail: the bull is named first", out.failed_role, "bull")
    out, _ = go(good_bull, good_bear, model_client.Gone(outcomes.UNAVAILABLE, "down"))
    c.eq(
        "the judge fails after a debate: the sides are kept, no role is blamed",
        (out.decision.verdict, out.decision.reason, out.failed_role, out.bull.claim, out.bear.claim),
        (arm_l1.Verdict.FAILED, arm_l1.Reason.MODEL_UNAVAILABLE, None, "Up", "Down"),
    )
    c.eq("and the debate's own cost is counted", out.tokens, 120)
    # the cap
    fetch = ByStage(l2_bull=[good_bull], l2_bear=[good_bear], l2_judge=[])
    out = run(mod, fetch, reader, cand, roles, cap=100)
    c.eq(
        "a cap spent by the two sides leaves nothing for the judge, who is not asked",
        (out.decision.reason, out.tokens, "l2_judge" in fetch.sent),
        (arm_l1.Reason.BUDGET_SPENT, 120, False),
    )
    fetch = ByStage(l2_bull=[good_bull], l2_bear=[good_bear], l2_judge=[answer(judge_reply(w), number=3)])
    out = run(mod, fetch, reader, cand, roles, cap=121)
    c.eq("a cap not yet spent lets the judge answer", (out.decision.verdict, out.tokens), (arm_l1.Verdict.CALL, 180))
    # an incomplete view
    w2 = World()
    short = snapshot.Reader(w2.freeze(snapshot, required=["binance-spot", "ryo"]), snapshot.Access.INDEPENDENT)
    fetch = ByStage()
    out = run(mod, fetch, short, snapshot.Candidate("cd-eth", "tk-eth"), roles)
    c.eq("and was not collapsed", out.collapsed, False)
    c.eq(
        "a source missing: FAILED_EVIDENCE, nobody is asked",
        (out.decision.verdict, out.decision.reason, out.calls, out.tokens, fetch.sent),
        (arm_l1.Verdict.FAILED_EVIDENCE, arm_l1.Reason.MISSING_EVIDENCE, (), 0, {}),
    )
    # two sources that disagree on a price: nobody is asked
    from tests.arm_l1_checks import conflict_scene

    _, clash, check_id = conflict_scene("110600")
    fetch = ByStage()
    out = run(mod, fetch, clash, snapshot.Candidate("cd-btc", "tk-btc"), roles)
    c.eq(
        "a conflict between sources: NO_CALL, nobody is asked",
        (out.decision.verdict, out.decision.reason, out.decision.evidence_ids, out.calls, out.tokens, out.collapsed, fetch.sent),
        (arm_l1.Verdict.NO_CALL, arm_l1.Reason.EVIDENCE_CONFLICT_UNRESOLVED, (check_id,), (), 0, False, {}),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_version, check_pieces, check_decide, check_concurrency, check_failures):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
