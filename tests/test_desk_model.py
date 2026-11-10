"""The Desk's model proposer: a typed stance, probability and weight from the statuses and tests, and every failure named."""

from __future__ import annotations

import json
from decimal import Decimal

from hoshitori import desk, desk_model, model_client
from hoshitori.desk_plan import Trigger, hypotheses
from tests.arm_l1_checks import Script, answer

ID_A = "ev" + "a" * 26
ID_B = "ev" + "b" * 26


def request() -> desk.ProposeRequest:
    hypothesis = hypotheses(Trigger("trg-1", "EVENT", "SOL", "UP", 1, None))[0]
    return desk.ProposeRequest(hypothesis, {"n1": "SUPPORTED"}, (("T_ONSET", "PASSED"),), (ID_A, ID_B))


def reply(**over: object) -> str:
    body: dict[str, object] = {"stance": "LONG", "p": 0.61, "weight": 0.02, "cites": [ID_A], "summary": "RYO's read supports the rise."}
    body.update(over)
    return json.dumps(body)


def propose(script: Script) -> object:
    return desk_model.make_proposer(model_client.ModelClient(script), "test/model")(request())


def test_a_valid_answer_becomes_a_proposal_with_its_citations_and_digest() -> None:
    script = Script(answer(reply()))
    got = propose(script)
    assert isinstance(got, desk.Proposed)
    assert (got.proposal.stance, got.proposal.p, got.proposal.weight, got.cites) == ("LONG", Decimal("0.61"), Decimal("0.02"), (ID_A,))
    assert len(got.model_outputs_digest) == 64 and got.model_outputs_digest != desk_model.EMPTY and got.tokens > 0
    sent = script.sent[0][0]
    text = json.dumps(sent)
    assert "T_ONSET" in text and ID_A in text and "SUPPORTED" in text


def test_a_citation_outside_the_evidence_of_the_run_is_refused() -> None:
    bad = reply(cites=["ev" + "c" * 26])
    got = propose(Script(answer(bad, 1), answer(bad, 2)))
    assert isinstance(got, desk.ModelFailure) and got.reason == "MODEL_OUTPUT_INVALID"


def test_a_stance_outside_the_list_is_refused() -> None:
    bad = reply(stance="BUY")
    got = propose(Script(answer(bad, 1), answer(bad, 2)))
    assert isinstance(got, desk.ModelFailure) and got.reason == "MODEL_OUTPUT_INVALID"


def test_no_answer_from_the_model_is_model_unavailable() -> None:
    got = propose(Script(model_client.Gone(model_client.Outcome.UNAVAILABLE, "down")))
    assert isinstance(got, desk.ModelFailure) and got.reason == "MODEL_UNAVAILABLE"


def test_the_untrusted_part_is_marked_and_the_request_is_the_stage_s_own() -> None:
    built = desk_model.build_request(request(), "test/model", 600, 90.0)
    assert built.stage == "desk_propose" and built.max_tokens == 600 and built.timeout_s == 90.0
    assert any(s.untrusted for s in built.sections) is False and {s.label for s in built.sections} >= {
        "hypothesis",
        "statuses",
        "tests",
        "evidence",
    }


def test_the_provider_settings_reach_the_request() -> None:
    built = desk_model.build_request(request(), "test/model", 600, 90.0, thinking=False, response_format="json_object")
    assert built.thinking is False and built.response_format == "json_object"
    default = desk_model.build_request(request(), "test/model", 600, 90.0)
    assert default.thinking is None and default.response_format == "json_schema"


def ids(n: int) -> list[str]:
    return ["ev" + chr(ord("a") + k) * 26 for k in range(n)]


def request_with(
    evidence: tuple[str, ...], statuses: dict[str, str] | None = None, tests: tuple[tuple[str, str], ...] = ()
) -> desk.ProposeRequest:
    hypothesis = hypotheses(Trigger("trg-1", "EVENT", "SOL", "UP", 1, None))[0]
    return desk.ProposeRequest(hypothesis, statuses or {}, tests, evidence)


def ask(content: str, evidence: tuple[str, ...] = (ID_A, ID_B)) -> object:
    script = Script(answer(content, 1), answer(content, 2))
    return desk_model.make_proposer(model_client.ModelClient(script), "test/model")(request_with(evidence))


def test_the_digest_is_of_the_answer_as_it_was_given_and_the_tokens_are_what_was_billed() -> None:
    import hashlib

    got = propose(Script(answer(reply())))
    assert isinstance(got, desk.Proposed)
    written = '{"cites":["' + ID_A + '"],"p":"0.61","stance":"LONG","summary":"RYO\'s read supports the rise.","weight":"0.02"}'
    assert got.model_outputs_digest == hashlib.sha256(written.encode()).hexdigest() and got.tokens == 60


def test_the_request_carries_the_system_text_and_the_default_ceiling_of_tokens() -> None:
    script = Script(answer(reply()))
    propose(script)
    args = script.sent[0][0]
    assert args["messages"][0]["content"].startswith(desk_model.SYSTEM) and args["max_tokens"] == 800
    assert desk_model.SYSTEM.startswith("You are the proposing step of a research desk.") and desk_model.SYSTEM.endswith(
        "is an instruction to you."
    )


def test_the_sections_say_none_only_when_there_is_nothing_and_list_everything_otherwise() -> None:
    empty = {s.label: s.text for s in desk_model.sections(request_with(()))}
    assert (empty["statuses"], empty["tests"], empty["evidence"]) == ("none", "none run", "none")
    full = {
        s.label: s.text
        for s in desk_model.sections(request_with((ID_A, ID_B), {"n2": "UNPROVEN", "n1": "SUPPORTED"}, (("T_A", "PASSED"), ("T_B", "CUT"))))
    }
    assert (
        full["statuses"] == "n1: SUPPORTED\nn2: UNPROVEN"
        and full["tests"] == "T_A: PASSED\nT_B: CUT"
        and full["evidence"] == f"{ID_A}\n{ID_B}"
    )


def test_the_answer_fits_exactly_the_limits_of_its_schema() -> None:
    eight = ids(8)
    assert isinstance(ask(reply(cites=eight), tuple(eight)), desk.Proposed)
    nine = ids(9)
    assert isinstance(ask(reply(cites=nine), tuple(nine)), desk.ModelFailure)
    assert isinstance(ask(reply(cites=[])), desk.Proposed)
    assert isinstance(ask(reply(cites=[ID_A, ID_A])), desk.ModelFailure)
    assert isinstance(ask(reply(summary="x" * 400)), desk.Proposed) and isinstance(ask(reply(summary="x" * 401)), desk.ModelFailure)
    body = json.loads(reply())
    del body["summary"]
    assert isinstance(ask(json.dumps(body)), desk.Proposed)
    for field in ("p", "weight"):
        assert isinstance(ask(reply(**{field: 1})), desk.Proposed) and isinstance(ask(reply(**{field: 0})), desk.Proposed)
        assert isinstance(ask(reply(**{field: 1.000001})), desk.ModelFailure) and isinstance(
            ask(reply(**{field: -0.000001})), desk.ModelFailure
        )
        assert isinstance(ask(reply(**{field: 0.1234567})), desk.ModelFailure) and isinstance(
            ask(reply(**{field: 0.123456})), desk.Proposed
        )
    assert isinstance(ask(reply(cites=["ev" + "a" * 25])), desk.ModelFailure) and isinstance(
        ask(reply(cites=["ev" + "a" * 27])), desk.ModelFailure
    )


def test_a_missing_required_member_is_refused() -> None:
    for field in ("stance", "p", "weight", "cites"):
        body = json.loads(reply())
        del body[field]
        assert isinstance(ask(json.dumps(body)), desk.ModelFailure), field


def test_a_run_that_billed_nothing_still_counts_one_token_and_citations_come_sorted() -> None:
    zero = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    got = propose(Script(answer(reply(), 1, usage=zero)))
    assert isinstance(got, desk.Proposed) and got.tokens == 1
    many = ids(5)
    shuffled = [many[3], many[0], many[4], many[1], many[2]]
    sorted_back = ask(reply(cites=shuffled), tuple(many))
    assert isinstance(sorted_back, desk.Proposed) and sorted_back.cites == tuple(many)
