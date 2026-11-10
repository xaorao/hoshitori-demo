"""Checks of arm L1 (``hoshitori.arm_l1``), runnable against a mutant: every way a proposal becomes a call, an abstention or a failure."""

from __future__ import annotations

import hashlib
import json
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from hoshitori import assertions, canonical, grounding, model_client, snapshot, typed
from hoshitori.capture import RawCapture
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

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except typed.TypedError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


class Script:
    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.sent: list[tuple[Any, str, float]] = []

    def __call__(self, args: Any, stage: str, timeout_s: float) -> Any:
        self.sent.append((args, stage, timeout_s))
        return self.replies.pop(0)


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
    reader = snapshot.Reader(w.freeze(snapshot), snapshot.Access.INDEPENDENT)
    return w, reader, snapshot.Candidate("cd-btc", "tk-btc")


def reply(w: World, **over: Any) -> str:
    body: dict[str, Any] = {
        "decision": "CALL",
        "p": 0.62,
        "invalidations": [{"field": "last_close", "op": "below", "level": 78000}],
        "cites": [w.btc_bars],
        "assertions": [
            {
                "id": "a1",
                "kind": "NUMERIC",
                "op": "eq",
                "value": 79000,
                "unit": "price",
                "window": "spot",
                "cites": [{"evidence_id": w.btc_bars, "field_path": "last_close"}],
            }
        ],
        "summary": "The close is 79000.",
    }
    body.update(over)
    return json.dumps({k: v for k, v in body.items() if v is not None})


def decide(mod: types.ModuleType, script: Script, reader: Any, cand: Any, **cfg: Any) -> Any:
    config = mod.Config("test/model", **cfg)
    return mod.decide(model_client.ModelClient(script), reader, cand, "BTC", HOUR, S, config)


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("names", (mod.ARM, mod.STAGE, mod.PROMPT_VERSION), ("L1", "l1_judgment", "l1-v5"))
    c.eq("limits", (mod.MAX_INVALIDATIONS, mod.MAX_CITES, mod.MAX_SUMMARY), (3, 16, 600))
    c.eq(
        "the abstentions",
        mod.ABSTENTIONS,
        ("EVIDENCE_CONFLICT_UNRESOLVED", "ESSENTIAL_PREMISE_UNPROVEN", "OUT_OF_SCOPE"),
    )
    c.eq("the verdicts", [v.value for v in mod.Verdict], ["CALL", "NO_CALL", "FAILED_EVIDENCE", "FAILED"])
    c.eq(
        "the reasons",
        [r.value for r in mod.Reason],
        [
            "MISSING_EVIDENCE",
            "MODEL_UNAVAILABLE",
            "MODEL_OUTPUT_INVALID",
            "MODEL_TRUNCATED",
            "MODEL_EMPTY",
            "MODEL_REFUSED",
            "BUDGET_SPENT",
            "EVIDENCE_CONFLICT_UNRESOLVED",
            "ESSENTIAL_PREMISE_UNPROVEN",
            "BELOW_MINIMUM_EDGE",
            "OUT_OF_SCOPE",
        ],
    )
    c.eq("the gates", [g.value for g in mod.Gate], ["NONE", "GROUNDING"])
    c.eq(
        "the system text",
        mod.SYSTEM,
        "You are one forecaster in a controlled comparison. You are given observations about one asset, taken at one "
        "moment, and nothing else. You forecast a probability; you do not trade. Use only the items given. Give a "
        "probability whenever the items let you form any view: 0.5 is the right answer when they show no edge either "
        "way. Abstain only if the items contradict one another in a way you cannot weigh, if a premise you need is not "
        "shown by them, or if the question is out of scope, and say which. A CALL has p, one to three invalidations (a "
        "field of an item, above or below a level that would prove you wrong) and the ids of the items you used in "
        "cites. Every number you state about the evidence goes in a typed assertion that names the item and the field; "
        "your own words go only in the summary and in event, mechanism or opinion assertions. Cite only the ids listed "
        "in the task. Assertions are optional: an empty list is allowed, and one assertion of the wrong shape makes the "
        "whole answer invalid, so state only what you are sure of. Give each assertion an id a1, a2 and so on. The "
        "kinds, and exactly what each holds: NUMERIC has op (eq, approx, gt, ge, lt or le), value, unit (ratio, "
        "percent, price or count), window (spot, 1h, 4h, 24h, 7d or 30d) and cites with exactly ONE entry. Copy every "
        "number exactly as it appears in the item, with all its digits. A field's window is the one its name says "
        "(ret_1h is 1h, ret_4h is 4h, ret_24h is 24h); a price, a count and atr14 are spot. COMPARISON has op (gt, ge, "
        "lt or le), unit, window and cites with exactly TWO entries, and no value. DIRECTION has op (rose, fell or "
        "flat), window and cites with exactly ONE entry, and no value and no unit. EVENT and MECHANISM have text, quote "
        "(words copied exactly from a cited item) and at least one cite, and no op, value, unit or window; if you have "
        "no quote, use OPINION. OPINION has only id, kind, text and an empty cites list. A cite is an object with "
        "evidence_id and field_path. Write one NUMERIC assertion for each number you state about the evidence. Reply "
        "with the JSON object and nothing else.",
    )
    c.eq(
        "the shape of the answer, as the model is told it",
        typed.describe(mod.SCHEMA),
        '{"decision": "CALL" | "NO_CALL", "p"?: number(0..1, at most 6 decimal places), '
        '"no_call_reason"?: "EVIDENCE_CONFLICT_UNRESOLVED" | "ESSENTIAL_PREMISE_UNPROVEN" | "OUT_OF_SCOPE", '
        '"invalidations": [{"field": ' + " | ".join(f'"{name}"' for name in grounding.FIELD_FACTS) + ', "op": "above" | "below", '
        '"level": number(-1000000000000..1000000000000, at most 8 decimal places)}] (0..3 items), '
        '"cites": [text(28..28 characters)] (0..16 items, all different), '
        '"assertions": ' + typed.describe(assertions.ASSERTIONS_SCHEMA) + ', "summary"?: text(1..600 characters)}',
    )
    for cls in (mod.Config, mod.Proposal, mod.Decision):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    cfg = mod.Config("m")
    c.eq(
        "the defaults of a configuration",
        (cfg.max_tokens, cfg.temperature, cfg.seed, cfg.response_format, cfg.timeout_s),
        (6000, "0", None, "json_schema", 120.0),
    )
    return c.bad


def check_version(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    base = mod.Config("test/model")
    want = hashlib.sha256(
        canonical.dumps(
            {
                "arm": "L1",
                "prompt_version": "l1-v5",
                "system_sha256": hashlib.sha256(mod.SYSTEM.encode()).hexdigest(),
                "schema": typed.json_schema(mod.SCHEMA),
                "model": "test/model",
                "max_tokens": 6000,
                "temperature": "0",
                "seed": None,
                "response_format": "json_schema",
                "thinking": None,
                "price_conflict_gate": "0.02",
            }
        )
    ).hexdigest()
    c.eq("the version of the arm", mod.agent_version_hash(base), want)
    hashes = {
        mod.agent_version_hash(base),
        mod.agent_version_hash(mod.Config("other")),
        mod.agent_version_hash(mod.Config("test/model", max_tokens=5000)),
        mod.agent_version_hash(mod.Config("test/model", temperature="0.5")),
        mod.agent_version_hash(mod.Config("test/model", temperature=None)),
        mod.agent_version_hash(mod.Config("test/model", seed=1)),
        mod.agent_version_hash(mod.Config("test/model", response_format=None)),
        mod.agent_version_hash(mod.Config("test/model", thinking=False)),
        mod.agent_version_hash(mod.Config("test/model", thinking=True)),
    }
    c.eq("each setting is a new agent", len(hashes), 9)
    c.eq(
        "the timeout is not part of the agent",
        mod.agent_version_hash(mod.Config("test/model", timeout_s=30.0)),
        mod.agent_version_hash(base),
    )
    return c.bad


def check_rules(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand = scene()
    view = mod.build_view(reader, cand, "BTC", HOUR, S)
    check = mod.make_check(view)

    def says(value: dict[str, Any]) -> str | None:
        try:
            check(value)
        except typed.TypedError as exc:
            return str(exc)
        return None

    good = typed.parse(mod.SCHEMA, reply(w).encode())
    c.eq("a good answer", says(good), None)
    no_call = {"decision": "NO_CALL", "no_call_reason": "OUT_OF_SCOPE", "invalidations": [], "cites": [], "assertions": []}
    c.eq("a good abstention", says(no_call), None)
    cases = (
        ("a call without a probability", {k: v for k, v in good.items() if k != "p"}, "$.p: a call has a probability"),
        (
            "a call with a reason to abstain",
            {**good, "no_call_reason": "OUT_OF_SCOPE"},
            "$.no_call_reason: a call has no reason to abstain",
        ),
        (
            "a call without an invalidation",
            {**good, "invalidations": []},
            "$.invalidations: a call has at least one invalidation condition",
        ),
        ("a call without a cite", {**good, "cites": []}, "$.cites: a call cites the items it used"),
        (
            "an abstention without a reason",
            {k: v for k, v in no_call.items() if k != "no_call_reason"},
            "$.no_call_reason: an abstention has a reason",
        ),
        ("an abstention with a probability", {**no_call, "p": D("0.5")}, "$.p: an abstention has no probability"),
        ("a cite that was not shown", {**good, "cites": [w.btc_bars, "ev" + "z" * 26]}, "$.cites[1]: is not one of the citable ids"),
        (
            "an assertion of the wrong shape",
            {**good, "assertions": [{"id": "a1", "kind": "NUMERIC", "cites": []}]},
            "$.assertions: assertion a1: a numeric assertion has an operator, a value, a unit, a window and one cite and nothing else",
        ),
        (
            "two assertions with one id",
            {**good, "assertions": [good["assertions"][0], good["assertions"][0]]},
            "$.assertions: the ids of assertions are all different",
        ),
    )
    for label, value, want in cases:
        c.eq(label, says(value), want)
    c.eq("an abstention may cite nothing, or something", says({**no_call, "cites": [w.btc_bars]}), None)
    # C1: the field of an invalidation is one of the registered names, in the schema itself, so a provider that enforces it (the
    # endpoint does) cannot name another; before, the schema had the path pattern and the check across members refused the name later
    names = list(grounding.FIELD_FACTS)
    told = typed.json_schema(mod.SCHEMA)["properties"]["invalidations"]["items"]["properties"]["field"]
    c.eq("a provider is told the registered field names for an invalidation", told.get("enum"), names)
    allowed = "the text is not one of " + str(names)
    for label, position, field in (
        ("an invalidation on a field with no meaning", 0, "rsi"),
        ("a registered name written as a path", 0, "features.close"),
        ("the second invalidation", 1, "rsi"),
    ):
        answer = json.loads(reply(w))
        answer["invalidations"] = [answer["invalidations"][0]] * position + [{"field": field, "op": "below", "level": 30}]
        c.refuses(
            label,
            lambda answer=answer: typed.parse(mod.SCHEMA, json.dumps(answer).encode()),
            f"$.invalidations[{position}].field: {allowed}",
        )
    c.eq(
        "an invalidation is rendered",
        mod.render_invalidation({"field": "last_close", "op": "below", "level": D("78000.50")}),
        "last_close below 78000.5",
    )
    return c.bad


def check_decide(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    w, reader, cand = scene()
    script = Script(answer(reply(w)))
    d = decide(mod, script, reader, cand)
    c.eq("a call", (d.verdict, d.reason, d.p, d.gate), (mod.Verdict.CALL, None, D("0.62"), mod.Gate.NONE))
    c.eq(
        "it names what it used, and the hash of exactly that",
        (d.evidence_ids, d.observation_set_hash),
        ((w.btc_bars,), reader.set_hash("cd-btc", [w.btc_bars])),
    )
    c.eq("the hash is not that of everything shown", d.observation_set_hash != reader.set_hash("cd-btc"), True)
    c.eq("the invalidation, as text", d.invalidation, ("last_close below 78000",))
    c.eq(
        "the candidate, the horizon and the agent",
        (d.candidate_id, d.horizon_hours, d.agent_version),
        ("cd-btc", 1, mod.agent_version_hash(mod.Config("test/model"))),
    )
    c.eq(
        "the proposal is kept whole",
        (d.proposal.decision, d.proposal.p, d.proposal.cites, d.proposal.summary),
        ("CALL", D("0.62"), (w.btc_bars,), "The close is 79000."),
    )
    c.eq(
        "the report: one assertion, supported, its citation exists",
        (d.report.counts(), d.report.citations_exist, d.report.ungrounded_numbers),
        ({"SUPPORTS": 1}, 1, ()),
    )
    c.eq(
        "one call of the model, recorded",
        ([x.outcome for x in d.calls], [x.stage for x in d.calls]),
        ([model_client.Outcome.VALID], ["l1_judgment"]),
    )
    sent = script.sent[0][0]
    c.eq(
        "it asked the configured model with the configured parameters",
        (sent["model"], sent["max_tokens"], sent["temperature"], "seed" in sent),
        ("test/model", 6000, "0", False),
    )
    c.eq("with the answer schema as the response format", sent["response_format"]["json_schema"]["name"], "l1_judgment")
    user = sent["messages"][1]["content"]
    c.eq("and every item the reader may give, as data", user.count("<<<DATA item_"), 3)
    c.eq("the interpretation of the snapshot (a verdict) is not in the prompt", ("bullish" in user, w.btc_int in user), (False, False))
    c.eq("a seed is passed on", decide(mod, Script(answer(reply(w))), reader, cand, seed=7).calls[0].seed, 7)
    # abstentions
    for reason in mod.ABSTENTIONS:
        body = reply(w, decision="NO_CALL", p=None, no_call_reason=reason, invalidations=[], cites=[], assertions=[], summary=None)
        d = decide(mod, Script(answer(body)), reader, cand)
        c.eq(
            f"abstains for {reason}",
            (d.verdict, d.reason, d.p, d.gate, d.invalidation, d.evidence_ids),
            (mod.Verdict.NO_CALL, mod.Reason(reason), None, mod.Gate.NONE, (), ()),
        )
    d = decide(
        mod,
        Script(answer(reply(w, decision="NO_CALL", p=None, no_call_reason="OUT_OF_SCOPE", invalidations=[], cites=[w.btc_bars]))),
        reader,
        cand,
    )
    c.eq(
        "an abstention that cites keeps what it used",
        (d.evidence_ids, d.observation_set_hash),
        ((w.btc_bars,), reader.set_hash("cd-btc", [w.btc_bars])),
    )
    # the grounding gate
    false = {
        "id": "a1",
        "kind": "NUMERIC",
        "op": "eq",
        "value": 80000,
        "unit": "price",
        "window": "spot",
        "cites": [{"evidence_id": w.btc_bars, "field_path": "last_close"}],
    }
    d = decide(mod, Script(answer(reply(w, assertions=[false]))), reader, cand)
    c.eq(
        "a false assertion: the call becomes an abstention for an unproven premise",
        (d.verdict, d.reason, d.p, d.gate, d.invalidation, d.report.false_statements),
        (mod.Verdict.NO_CALL, mod.Reason.ESSENTIAL_PREMISE_UNPROVEN, None, mod.Gate.GROUNDING, (), ("a1",)),
    )
    c.eq("and the proposal stays on the record", (d.proposal.decision, d.proposal.p, d.evidence_ids), ("CALL", D("0.62"), (w.btc_bars,)))
    no_call_false = reply(w, decision="NO_CALL", p=None, no_call_reason="OUT_OF_SCOPE", invalidations=[], assertions=[false])
    d = decide(mod, Script(answer(no_call_false)), reader, cand)
    c.eq(
        "a false assertion in an abstention is the gate's too",
        (d.reason, d.gate),
        (mod.Reason.ESSENTIAL_PREMISE_UNPROVEN, mod.Gate.GROUNDING),
    )
    unverified = {
        "id": "a1",
        "kind": "EVENT",
        "text": "x",
        "quote": "not in any text",
        "cites": [{"evidence_id": w.btc_bars, "field_path": "x"}],
    }
    d = decide(mod, Script(answer(reply(w, assertions=[unverified]))), reader, cand)
    c.eq(
        "an assertion that is only unsupported is shown and does not block",
        (d.verdict, d.gate, d.report.counts()),
        (mod.Verdict.CALL, mod.Gate.NONE, {"NOT_ENOUGH_INFO": 1}),
    )
    d = decide(mod, Script(answer(reply(w, summary="It rose 12% and closed at 79000."))), reader, cand)
    c.eq(
        "a number in the summary that nothing shows is listed and does not block",
        (d.verdict, d.report.ungrounded_numbers),
        (mod.Verdict.CALL, ("12%",)),
    )
    d = decide(mod, Script(answer(reply(w, summary=None))), reader, cand)
    c.eq("no summary", (d.verdict, d.proposal.summary, d.report.ungrounded_numbers), (mod.Verdict.CALL, None, ()))
    d = decide(mod, Script(answer(reply(w, assertions=[]))), reader, cand)
    c.eq("no assertions", (d.verdict, d.report.verdicts), (mod.Verdict.CALL, ()))
    # a repair through the stage's own rule
    script = Script(answer(reply(w, p=None)), answer(reply(w), number=2))
    d = decide(mod, script, reader, cand)
    c.eq(
        "a call without a probability is repaired once",
        (d.verdict, [x.outcome for x in d.calls], d.calls[0].detail),
        (mod.Verdict.CALL, [model_client.Outcome.INVALID, model_client.Outcome.VALID], "$.p: a call has a probability"),
    )
    return c.bad


def check_failures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    _w, reader, cand = scene()
    verdict, why, outcomes = mod.Verdict, mod.Reason, model_client.Outcome

    def fails(*replies: Any, **cfg: Any) -> Any:
        return decide(mod, Script(*replies), reader, cand, **cfg)

    for label, replies, reason, outcome in (
        ("a model that is down", (model_client.Gone(outcomes.UNAVAILABLE, "down"),), why.MODEL_UNAVAILABLE, outcomes.UNAVAILABLE),
        (
            "a replay with no stored answer",
            (model_client.Gone(outcomes.REPLAY_MISSING, "none"),),
            why.MODEL_UNAVAILABLE,
            outcomes.REPLAY_MISSING,
        ),
        (
            "an answer that is not a chat answer",
            (RawCapture("cap-" + "9" * 32, "model-t", {"operation": "chat", "arguments": {}}, 200, {}, b"<html>", T0, T0),),
            why.MODEL_UNAVAILABLE,
            outcomes.UNREADABLE,
        ),
        ("an answer cut short", (answer(None, finish="length"),), why.MODEL_TRUNCATED, outcomes.TRUNCATED),
        ("no content", (answer(None),), why.MODEL_EMPTY, outcomes.EMPTY),
        ("a filtered answer", (answer(None, finish="content_filter"),), why.MODEL_REFUSED, outcomes.REFUSED),
        ("two answers that do not fit", (answer("nope"), answer("nope", number=2)), why.MODEL_OUTPUT_INVALID, outcomes.INVALID),
    ):
        d = fails(*replies)
        c.eq(
            label,
            (d.verdict, d.reason, d.p, d.evidence_ids, d.invalidation, d.proposal, d.gate, d.report, d.calls[-1].outcome),
            (verdict.FAILED, reason, None, (), (), None, mod.Gate.NONE, None, outcome),
        )
    c.eq("a failure keeps the hash of what was shown, for the record", fails(answer(None)).observation_set_hash, reader.set_hash("cd-btc"))
    # a spent budget
    budget = model_client.TokenBudget(1)
    budget.charge(1)
    d = mod.decide(model_client.ModelClient(Script()), reader, cand, "BTC", HOUR, S, mod.Config("test/model"), budget)
    c.eq(
        "a budget that is spent", (d.verdict, d.reason, [x.outcome for x in d.calls]), (verdict.FAILED, why.BUDGET_SPENT, [outcomes.BUDGET])
    )
    # a required source missing: no model call
    w2 = World()
    short = snapshot.Reader(w2.freeze(snapshot, required=["binance-spot", "ryo"]), snapshot.Access.INDEPENDENT)
    script = Script()
    d = mod.decide(
        model_client.ModelClient(script), short, snapshot.Candidate("cd-eth", "tk-eth"), "ETH", HOUR, S, mod.Config("test/model")
    )
    c.eq(
        "a candidate with a source missing is FAILED_EVIDENCE and the model is not asked",
        (d.verdict, d.reason, d.calls, len(script.sent)),
        (verdict.FAILED_EVIDENCE, why.MISSING_EVIDENCE, (), 0),
    )
    c.eq(
        "it still says which agent it was and what it saw",
        (d.agent_version, d.observation_set_hash),
        (mod.agent_version_hash(mod.Config("test/model")), short.set_hash("cd-eth")),
    )
    return c.bad


def conflict_scene(price: str) -> tuple[World, Any, str]:
    """BTC with a second RYO price and the check of that price against the exchange's close, in the candidate's slot."""
    from hoshitori import crosscheck
    from tests.snapshot_checks import digest_of

    w = World()
    ryo = w.ryo("btc2", "tk-btc", fields=(("market.price_usd", price),), unknown_fields=(), path="observation2")
    ryo_id = w.add(ryo)
    bars = w.store.get(w.btc_bars)
    check = crosscheck.price_check(ryo, digest_of(ryo), bars, digest_of(bars), S)
    assert check is not None
    check_id = w.add(check)
    frozen = w.freeze(snapshot, by_candidate={"cd-btc": [w.btc_ryo, w.btc_int, w.btc_bars, ryo_id, check_id], "cd-eth": [w.eth_bars]})
    return w, snapshot.Reader(frozen, snapshot.Access.INDEPENDENT), check_id


def check_conflict(mod: types.ModuleType) -> list[str]:
    """Two sources that state one price and disagree: the arm stands down before it asks a model, and says which check it stood on."""
    c = Case(mod)
    why, verdict = mod.Reason, mod.Verdict
    w, reader, check_id = conflict_scene("110600")
    cand = snapshot.Candidate("cd-btc", "tk-btc")
    script = Script()
    d = decide(mod, script, reader, cand)
    c.eq(
        "a price check that says CONFLICT is a no-call before any model is asked",
        (d.verdict, d.reason, d.p, d.calls, len(script.sent), d.gate, d.proposal),
        (verdict.NO_CALL, why.EVIDENCE_CONFLICT_UNRESOLVED, None, (), 0, mod.Gate.NONE, None),
    )
    c.eq(
        "it cites the check and has read that and nothing else",
        (d.evidence_ids, d.observation_set_hash),
        ((check_id,), reader.set_hash("cd-btc", (check_id,))),
    )
    c.eq("it says which agent it was", d.agent_version, mod.agent_version_hash(mod.Config("test/model")))
    c.eq("and for which candidate and horizon", (d.candidate_id, d.horizon_hours), ("cd-btc", 1))
    w2, agree, _ = conflict_scene("79012.5")
    script = Script(answer(reply(w2)))
    d = decide(mod, script, agree, cand)
    c.eq("a price check that says AGREE does not stop the arm", (d.verdict, len(script.sent)), (verdict.CALL, 1))
    script = Script(answer(reply(w)), answer(reply(w), 2))
    decide(mod, script, reader, snapshot.Candidate("cd-eth", "tk-eth"))
    c.eq("another candidate is judged as it was: the model is asked", len(script.sent) >= 1, True)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_version, check_rules, check_decide, check_failures, check_conflict):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad


_ = (Callable,)
