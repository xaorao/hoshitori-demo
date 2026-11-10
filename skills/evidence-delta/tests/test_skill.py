"""Field-by-field comparison: hand-built cases, properties on random documents, hostile input, and the server and command line."""

from __future__ import annotations

import copy
import datetime as dt
import json
import random
from typing import Any

import jsonschema
import pytest

from evidence_delta import OUTPUT_SCHEMA, RULES_OUTPUT_SCHEMA, TOOL_DEFINITIONS, TOOL_NAME, InputError, run, run_rules, server
from evidence_delta.__main__ import main
from evidence_delta.skill import compare_leaf, flatten

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)


def call(before: Any, after: Any, **kw: Any) -> dict[str, Any]:
    out = run({"before": before, "after": after, **kw}, now=NOW)
    jsonschema.validate(out, OUTPUT_SCHEMA)
    return out


def env(data: dict[str, Any], as_of: str = "2026-11-09T10:00:00Z", **meta: Any) -> dict[str, Any]:
    return {"tool": "analyze_token", "as_of": as_of, "data": data, **meta}


def test_a_changed_number_is_reported_with_its_size() -> None:
    out = call(env({"price": 100.0}), env({"price": 104.0}, "2026-11-09T11:00:00Z"))
    d = out["data"]
    assert d["verdict"] == "changed" and d["interval_seconds"] == 3600
    assert d["changes"] == [{"path": "price", "before": 100.0, "after": 104.0, "change_pct": 4.0}]
    assert "price +4.00%" in out["summary"]["headline"] and "over 60 minutes" in out["summary"]["headline"]


def test_equal_known_fields_are_unchanged() -> None:
    out = call({"a": 1, "b": "x", "c": [1, 2], "d": {}}, {"a": 1, "b": "x", "c": [1, 2], "d": {}})
    assert out["data"]["verdict"] == "unchanged" and out["data"]["counts"]["unchanged"] == 5  # a, b, c.0, c.1, d
    assert out["status"] == "ok"


def test_a_missing_value_is_never_no_change() -> None:
    out = call({"a": 1, "b": 2}, {"a": 1, "b": None})
    d = out["data"]
    assert d["verdict"] == "unchanged_where_known" and d["unknown"][0]["reasons"] == {"after": "null"}
    assert "not no change" in out["summary"]["headline"] and out["status"] == "partial"


def test_absent_and_null_are_told_apart() -> None:
    d = call({"a": 1, "b": None}, {"b": None, "c": 3})["data"]
    reasons = {u["path"]: u["reasons"] for u in d["unknown"]}
    assert reasons == {"a": {"after": "absent"}, "b": {"before": "null", "after": "null"}, "c": {"before": "absent"}}
    assert d["verdict"] == "insufficient_evidence" and d["counts"]["unchanged"] == 0


def test_nothing_comparable_is_unavailable() -> None:
    out = call({"a": None}, {"a": None})
    assert (out["status"], out["data"]["verdict"], out["availability"]) == (
        "unavailable",
        "insufficient_evidence",
        {"comparison": "unavailable"},
    )


def test_min_change_pct_and_zero_baselines() -> None:
    assert compare_leaf(100.0, 100.4, 0.5)["outcome"] == "unchanged"
    assert compare_leaf(100.0, 100.6, 0.5)["outcome"] == "changed"
    assert compare_leaf(0, 5, 50) == {
        "outcome": "changed",
        "change_pct": None,
    }  # no percentage from a zero baseline, and it is never hidden
    assert compare_leaf(-100.0, -90.0, 0)["change_pct"] == pytest.approx(10.0)  # relative to the size of the baseline
    assert compare_leaf(1, 1.0, 0)["outcome"] == "unchanged"


def test_types_are_not_blurred() -> None:
    assert compare_leaf(True, 1, 0)["outcome"] == "changed"
    assert compare_leaf("1", 1, 0)["outcome"] == "changed"
    assert compare_leaf("Hold", "hold", 0)["outcome"] == "changed"
    assert compare_leaf(float("nan"), 1.0, 0)["reasons"] == {"before": "unusable_number"}
    assert compare_leaf(1.0, float("inf"), 0)["reasons"] == {"after": "unusable_number"}


def test_fields_filter_and_requested_but_absent_paths() -> None:
    before, after = {"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"x": 1, "y": 3}, "b": 9}
    d = call(before, after, fields=["a"])["data"]
    assert [c["path"] for c in d["changes"]] == ["a.y"] and d["counts"]["compared"] == 2
    out = call(before, after, fields=["nope", "b"])
    assert [u["path"] for u in out["data"]["unknown"]] == ["nope"]
    assert any("in neither read" in w for w in out["warnings"])


def test_the_envelope_of_a_partial_or_simulated_read_is_warned_about() -> None:
    out = call(env({"a": 1}, status="partial", data_mode="simulated"), env({"a": 1}, status="ok", data_mode="live"))
    assert out["data_mode"] == "simulated" and out["status"] == "partial"
    assert any("partial read" in w for w in out["warnings"]) and any("simulated" in w for w in out["warnings"])
    assert call(env({"a": 1}, data_mode="live"), env({"a": 1}, data_mode="live"))["data_mode"] == "live"
    assert call({"a": 1}, {"a": 1})["data_mode"] == "unknown"


def test_dates_are_checked() -> None:
    out = call(env({"a": 1}, "2026-11-09T11:00:00Z"), env({"a": 2}, "2026-11-09T10:00:00Z"))
    assert any("earlier than before" in w for w in out["warnings"])
    assert any("no as_of" in w for w in call({"a": 1}, {"a": 2})["warnings"])
    assert call(env({"a": 1}, "yesterday"), env({"a": 2}))["data"]["interval_seconds"] is None


def test_two_different_tools_are_refused() -> None:
    with pytest.raises(InputError) as err:
        run({"before": {"tool": "analyze_token", "data": {"a": 1}}, "after": {"tool": "deep_analysis", "data": {"a": 1}}})
    assert err.value.field == "after"


@pytest.mark.parametrize(
    "args,field",
    [
        ([], "arguments"),
        ({"before": {}}, "after"),
        ({"after": {}}, "before"),
        ({"before": [], "after": {}}, "before"),
        ({"before": {}, "after": "x"}, "after"),
        ({"before": {}, "after": {}, "fields": []}, "fields"),
        ({"before": {}, "after": {}, "fields": ["a", "a"]}, "fields"),
        ({"before": {}, "after": {}, "fields": [1]}, "fields"),
        ({"before": {}, "after": {}, "min_change_pct": -1}, "min_change_pct"),
        ({"before": {}, "after": {}, "min_change_pct": True}, "min_change_pct"),
        ({"before": {}, "after": {}, "min_change_pct": float("nan")}, "min_change_pct"),
        ({"before": {}, "after": {}, "extra": 1}, "extra"),
    ],
)
def test_refusals_name_the_field(args: Any, field: str) -> None:
    with pytest.raises(InputError) as err:
        run(args)
    assert err.value.field == field and err.value.to_envelope("t")["details"]["example"]


def test_limits() -> None:
    deep: dict[str, Any] = {}
    node = deep
    for _ in range(14):
        node["n"] = {}
        node = node["n"]
    with pytest.raises(InputError):
        run({"before": deep, "after": {}})
    with pytest.raises(InputError):
        run({"before": {str(i): i for i in range(2001)}, "after": {}})
    big = {str(i): i for i in range(300)}
    d = call(big, {k: v + 1 for k, v in big.items()})["data"]
    assert len(d["changes"]) == 200 and d["truncated"]["changes"] and d["counts"]["changed"] == 300


def random_doc(rng: random.Random, depth: int = 0) -> Any:
    kind = rng.choice(["int", "float", "str", "bool", "none", "list", "dict"] if depth < 3 else ["int", "float", "str", "bool", "none"])
    if kind == "int":
        return rng.randint(-5, 5)
    if kind == "float":
        return rng.choice([0.0, 1.5, -2.25, 100.0])
    if kind == "str":
        return rng.choice(["a", "b", ""])
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "none":
        return None
    if kind == "list":
        return [random_doc(rng, depth + 1) for _ in range(rng.randint(0, 3))]
    return {rng.choice("pqrs"): random_doc(rng, depth + 1) for _ in range(rng.randint(0, 3))}


def test_properties_on_random_documents() -> None:
    rng = random.Random(7)
    for _ in range(300):
        a, b = {"x": random_doc(rng)}, {"x": random_doc(rng)}
        ab, ba = call(a, b)["data"], call(b, a)["data"]
        assert ab["counts"] == ba["counts"]  # symmetric in what changed, unchanged and unknown
        assert {c["path"] for c in ab["changes"]} == {c["path"] for c in ba["changes"]}
        assert {u["path"] for u in ab["unknown"]} == {u["path"] for u in ba["unknown"]}
        c = ab["counts"]
        assert c["changed"] + c["unchanged"] + c["unknown"] == c["compared"]
        same = call(a, copy.deepcopy(a))["data"]
        assert same["counts"]["changed"] == 0 and same["verdict"] in ("unchanged", "unchanged_where_known", "insufficient_evidence")
        assert all(u["reasons"] for u in ab["unknown"])  # an unknown always says why
        # a path is never in two lists
        paths = [x["path"] for x in ab["changes"]] + [x["path"] for x in ab["unknown"]] + ab["unchanged"]
        assert len(paths) == len(set(paths)) == c["compared"]


def test_flatten_makes_empty_containers_leaves_and_keeps_lists_apart_from_objects() -> None:
    assert flatten({"a": {}, "b": [], "c": [None, {"d": 1}]}) == {("a",): {}, ("b",): [], ("c", 0): None, ("c", 1, "d"): 1}
    assert flatten({"x": [5]}) != flatten({"x": {"0": 5}})
    assert flatten({"a.b": 1}) != flatten({"a": {"b": 1}})


def test_structure_is_not_lost_to_the_dotted_spelling() -> None:
    for before, after in (({"x": [5]}, {"x": {"0": 5}}), ({"a.b": 1}, {"a": {"b": 1}})):
        d = call(before, after)["data"]
        assert d["verdict"] == "insufficient_evidence" and d["counts"]["unchanged"] == 0  # absent on each side, never "unchanged"
    twins = call({"a.b": 1, "a": {"b": 1}}, {"a.b": 2, "a": {"b": 1}})
    assert twins["data"]["counts"] == {"compared": 2, "changed": 1, "unchanged": 1, "unknown": 0}
    assert any("written the same" in w for w in twins["warnings"])


def test_a_huge_integer_does_not_crash() -> None:
    big = 10**400
    d = call({"n": 1}, {"n": big})["data"]  # the ratio is too large for a float
    assert d["verdict"] == "changed" and d["changes"][0]["change_pct"] is None
    assert call({"n": big}, {"n": big + 10**399})["data"]["changes"][0]["change_pct"] == pytest.approx(10.0)


def test_rules_tool() -> None:
    out = run_rules({}, now=NOW)
    jsonschema.validate(out, RULES_OUTPUT_SCHEMA)
    assert "never reads a missing value as no change" in out["summary"]["headline"]
    with pytest.raises(InputError):
        run_rules({"x": 1})


def test_definitions_follow_the_catalogue_template() -> None:
    assert [d["name"] for d in TOOL_DEFINITIONS] == [TOOL_NAME, "evidence_delta_rules"]
    for d in TOOL_DEFINITIONS:
        text = d["description"]
        order = [text.index(k) for k in ("Delivery:", "Use when:", "Core inputs:", "Limits:")]
        assert order == sorted(order) and d["inputSchema"]["additionalProperties"] is False


def test_mcp_call_and_refusal() -> None:
    good = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": TOOL_NAME, "arguments": {"before": {"a": 1}, "after": {"a": 2}}},
    }
    reply = server.handle_rpc(good, {TOOL_NAME: run}).reply
    assert reply is not None and reply["result"]["isError"] is False
    jsonschema.validate(json.loads(reply["result"]["content"][0]["text"]), OUTPUT_SCHEMA)
    bad = {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": TOOL_NAME, "arguments": {"before": {}}}}
    reply = server.handle_rpc(bad, {TOOL_NAME: run}).reply
    assert reply is not None and "INVALID_ARGUMENTS" in json.dumps(reply)


def test_cli(tmp_path: Any, capsys: Any) -> None:
    b, a = tmp_path / "b.json", tmp_path / "a.json"
    b.write_text(json.dumps(env({"p": 1.0})))
    a.write_text(json.dumps(env({"p": 2.0})))
    assert main(["compare", str(b), str(a), "--min-change-pct", "1"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["verdict"] == "changed"
    a.write_text("not json")
    assert main(["compare", str(b), str(a)]) == 2
    assert main(["compare", str(b), str(tmp_path / "missing.json")]) == 2
    assert main(["rules"]) == 0 and main(["example"]) == 0 and main(["tool"]) == 0
