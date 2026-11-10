"""evidence_sensitivity: the answers are recomputed independently, every refusal names its field, and the envelope matches its schema."""

from __future__ import annotations

import copy
import itertools
import json
import threading
import urllib.error
import urllib.request
from fractions import Fraction
from http.server import ThreadingHTTPServer
from typing import Any

import jsonschema
import pytest

from evidence_sensitivity import OUTPUT_SCHEMA, InputError, run
from evidence_sensitivity.schema import EXAMPLE_CALL, TOOL_DEFINITIONS
from evidence_sensitivity.server import RUNNERS, handle_rpc, make_http_handler


def action(pairs: list[tuple[float, float]], threshold: float, minimum: int = 1) -> str:
    """A second, direct implementation of the rule with exact fractions and no shared code."""
    if len(pairs) < minimum or sum(Fraction(str(w)) for _, w in pairs) == 0:
        return "NO_CALL"
    mean = sum(Fraction(str(s)) * Fraction(str(w)) for s, w in pairs) / sum(Fraction(str(w)) for _, w in pairs)
    t = Fraction(str(threshold))
    return "LONG" if mean >= t else "SHORT" if mean <= -t else "NO_CALL"


def request(*items: tuple[str, float | None, float, str | None], threshold: float = 0.25, minimum: int | None = None) -> dict[str, Any]:
    evidence = [{"id": i, "signal": s, "weight": w, **({} if g is None else {"group": g})} for i, s, w, g in items]
    rule: dict[str, Any] = {"threshold": threshold}
    if minimum is not None:
        rule["min_evidence"] = minimum
    return {"evidence": evidence, "rule": rule}


def test_the_example_matches_a_hand_calculation() -> None:
    out = run(EXAMPLE_CALL)
    jsonschema.validate(out, OUTPUT_SCHEMA)
    d = out["data"]
    assert (d["baseline"]["score"], d["baseline"]["action"]) == (0.375, "LONG")
    by = {r["id"]: r for r in d["items"]}
    assert by["momentum_7d"]["withheld"]["action"] == "NO_CALL" and by["momentum_7d"]["reading"] == "REQUIRED"
    assert by["funding"]["reading"] == "OPPOSING" and by["news_tone"]["reading"] == "SUPPORTING"
    assert by["momentum_7d"]["flip"] == {"signal_must_be": "below", "boundary": 0.35, "distance": 0.25}
    assert d["required_items"] == ["momentum_7d"] and d["sufficient_set"]["ids"] == ["momentum_7d"]


@pytest.mark.parametrize("seed", range(40))
def test_every_withheld_action_agrees_with_an_independent_rule(seed: int) -> None:
    import random

    rng = random.Random(seed)
    items = [
        (f"e{i}", rng.choice([-1, -0.5, -0.2, 0, 0.1, 0.4, 0.8, 1]), rng.choice([0, 1, 1, 2, 3.5]), rng.choice([None, "a", "b"]))
        for i in range(rng.randint(1, 6))
    ]
    threshold = rng.choice([0.1, 0.25, 0.5])
    minimum = rng.randint(1, 3)
    out = run(request(*items, threshold=threshold, minimum=minimum))
    jsonschema.validate(out, OUTPUT_SCHEMA)
    pairs = [(s, w) for _, s, w, _ in items]
    base = action(pairs, threshold, minimum)
    assert out["data"]["baseline"]["action"] == base
    for row, (ident, _s, w, _) in zip(out["data"]["items"], items, strict=True):
        rest = [(s2, w2) for i2, s2, w2, _ in items if i2 != ident]
        assert row["withheld"]["action"] == action(rest, threshold, minimum)
        assert row["neutralised"]["action"] == action([*rest, (0, w)], threshold, minimum)
        flip = row["flip"]
        if flip is not None:  # a signal just past the boundary changes the action; one just short of it does not
            b, eps = float(flip["boundary"]), 1e-5
            past, short = (b - eps, b + eps) if flip["signal_must_be"] in ("below", "at or below") else (b + eps, b - eps)
            if abs(past) <= 1:
                assert action([*rest, (past, w)], threshold, minimum) != base
            if abs(short) <= 1:
                assert action([*rest, (short, w)], threshold, minimum) == base
    for group in out["data"]["groups"]:
        rest = [(s2, w2) for _, s2, w2, g2 in items if g2 != group["group"]]
        assert group["action"] == action(rest, threshold, minimum)


def test_the_sufficient_set_is_the_smallest_by_trying_every_subset() -> None:
    items = [("a", 0.9, 1, None), ("b", 0.5, 1, None), ("c", 0.4, 1, None), ("d", -0.1, 1, None)]
    out = run(request(*items, threshold=0.4))
    ids = out["data"]["sufficient_set"]["ids"]
    pairs = {i: (s, w) for i, s, w, _ in items}
    assert action([pairs[i] for i in ids], 0.4) == "LONG"
    smaller = [c for n in range(1, len(ids)) for c in itertools.combinations(pairs, n) if action([pairs[i] for i in c], 0.4) == "LONG"]
    assert smaller == []


def test_a_missing_signal_counts_for_nothing_and_says_whether_it_could_matter() -> None:
    out = run(request(("a", 0.3, 1, None), ("b", None, 5, None), threshold=0.25))
    assert out["status"] == "partial" and out["data"]["missing"] == ["b"] and out["availability"] == {"evidence": "partial"}
    b = out["data"]["items"][1]
    assert b["reading"] == "MISSING" and b["withheld"] is None and b["could_change_call_if_it_arrived"] is True
    assert out["data"]["baseline"]["action"] == "LONG" and out["data"]["baseline"]["available"] == 1
    tiny = run(request(("a", 0.9, 100, None), ("b", None, 1, None), threshold=0.25))
    assert tiny["data"]["items"][1]["could_change_call_if_it_arrived"] is False


def test_too_little_evidence_is_no_call_with_no_score() -> None:
    out = run(request(("a", 0.9, 1, None), threshold=0.25, minimum=2))
    assert out["data"]["baseline"] == {"score": None, "action": "NO_CALL", "available": 1, "threshold": 0.25, "min_evidence": 2}
    assert out["data"]["sufficient_set"] is None and all(r["flip"] is None for r in out["data"]["items"])
    assert "too little evidence" in out["summary"]["headline"]


def test_all_weights_zero_is_no_call_not_a_division_error() -> None:
    out = run(request(("a", 0.9, 0, None), threshold=0.25))
    assert out["data"]["baseline"]["action"] == "NO_CALL" and out["data"]["baseline"]["score"] is None


def test_the_envelope_says_what_it_does_not_know() -> None:
    out = run(EXAMPLE_CALL)
    assert (
        out["data_mode"] == "unknown" and out["tool"] == "evidence_sensitivity" and out["request"]["defaults_applied"] == ["min_evidence"]
    )
    assert any("not whether the evidence is true" in w for w in out["warnings"]) and any(
        "nothing was fetched" in w for w in out["warnings"]
    )


def test_same_input_same_answer_and_input_is_not_changed() -> None:
    before = copy.deepcopy(EXAMPLE_CALL)
    a, b = run(EXAMPLE_CALL), run(EXAMPLE_CALL)
    a["as_of"] = b["as_of"] = ""
    assert a == b and before == EXAMPLE_CALL


@pytest.mark.parametrize(
    ("args", "field"),
    [
        (None, "arguments"),
        ({}, "evidence"),
        ({"evidence": [], "rule": {"threshold": 0.2}}, "evidence"),
        ({"evidence": [{"id": "a", "signal": 2}], "rule": {"threshold": 0.2}}, "evidence[0].signal"),
        ({"evidence": [{"id": "a", "signal": "x"}], "rule": {"threshold": 0.2}}, "evidence[0].signal"),
        ({"evidence": [{"id": "a", "signal": True}], "rule": {"threshold": 0.2}}, "evidence[0].signal"),
        ({"evidence": [{"id": "a b", "signal": 0}], "rule": {"threshold": 0.2}}, "evidence[0].id"),
        ({"evidence": [{"id": "a", "signal": 0}, {"id": "a", "signal": 0}], "rule": {"threshold": 0.2}}, "evidence[1].id"),
        ({"evidence": [{"id": "a", "signal": 0, "weight": -1}], "rule": {"threshold": 0.2}}, "evidence[0].weight"),
        ({"evidence": [{"id": "a", "signal": 0, "extra": 1}], "rule": {"threshold": 0.2}}, "evidence[0]"),
        ({"evidence": [{"id": "a", "signal": 0}], "rule": {"threshold": 0}}, "rule.threshold"),
        ({"evidence": [{"id": "a", "signal": 0}], "rule": {"threshold": 1.5}}, "rule.threshold"),
        ({"evidence": [{"id": "a", "signal": 0}], "rule": {"threshold": 0.2, "min_evidence": 0}}, "rule.min_evidence"),
        ({"evidence": [{"id": "a", "signal": 0}], "rule": {}}, "rule"),
        ({"evidence": [{"id": "a", "signal": 0}], "rule": {"threshold": 0.2}, "x": 1}, "arguments"),
        ({"evidence": [{"id": f"i{n}", "signal": 0} for n in range(31)], "rule": {"threshold": 0.2}}, "evidence"),
    ],
)
def test_every_refusal_names_its_field_and_gives_a_working_example(args: object, field: str) -> None:
    with pytest.raises(InputError) as caught:
        run(args)
    assert caught.value.field == field and caught.value.example == EXAMPLE_CALL
    env = caught.value.to_envelope("t")
    assert env["code"] == "INVALID_ARGUMENTS" and env["details"]["example"] == EXAMPLE_CALL
    run(EXAMPLE_CALL)  # the example it gives works


def test_nan_and_infinity_are_refused() -> None:
    for bad in (float("nan"), float("inf")):
        with pytest.raises(InputError):
            run({"evidence": [{"id": "a", "signal": bad}], "rule": {"threshold": 0.2}})


def test_more_than_sixteen_items_is_greedy_and_says_so() -> None:
    items = [(f"e{i}", 0.5, 1, None) for i in range(17)]
    out = run(request(*items, threshold=0.25))
    assert "greedy" in out["data"]["sufficient_set"]["method"] and any("greedy" in w for w in out["warnings"])


def test_the_tool_definition_is_in_the_house_style() -> None:
    (tool,) = TOOL_DEFINITIONS
    for part in ("Delivery:", "Use when:", "Core inputs:", "Limits:"):
        assert part in tool["description"]
    assert tool["inputSchema"]["additionalProperties"] is False and tool["annotations"]["readOnlyHint"] is True
    jsonschema.Draft202012Validator.check_schema(tool["inputSchema"])
    jsonschema.Draft202012Validator.check_schema(tool["outputSchema"])
    jsonschema.validate(EXAMPLE_CALL, tool["inputSchema"])


def test_mcp_tools_list_and_call() -> None:
    listed = handle_rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, RUNNERS)
    assert [t["name"] for t in listed.reply["result"]["tools"]] == ["evidence_sensitivity"]
    called = handle_rpc(
        {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "evidence_sensitivity", "arguments": EXAMPLE_CALL}}, RUNNERS
    )
    result = called.reply["result"]
    assert result["isError"] is False and json.loads(result["content"][0]["text"])["data"]["baseline"]["action"] == "LONG"
    bad = handle_rpc(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "evidence_sensitivity", "arguments": {}}}, RUNNERS
    )
    assert bad.reply["result"]["isError"] is True


def test_rest_call_and_refusal() -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_http_handler(RUNNERS))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        req = urllib.request.Request(
            base + "/tools/evidence_sensitivity/call", data=json.dumps(EXAMPLE_CALL).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as r:
            assert json.loads(r.read())["result"]["data"]["baseline"]["action"] == "LONG"
        req = urllib.request.Request(base + "/tools/evidence_sensitivity/call", data=b"{}", headers={"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(req, timeout=10)
        assert caught.value.code == 400 and json.loads(caught.value.read())["code"] == "INVALID_ARGUMENTS"
    finally:
        server.shutdown()
        server.server_close()
