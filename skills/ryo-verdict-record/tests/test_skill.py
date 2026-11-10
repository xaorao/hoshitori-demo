"""RYO's verdicts scored from a record: hand-computed cases, properties on random records, hostile input, and the server and command line."""

from __future__ import annotations

import datetime as dt
import json
import random
import threading
import urllib.error
import urllib.request
from typing import Any

import jsonschema
import pytest

from ryo_verdict_record import (
    OUTPUT_SCHEMA,
    RULES_OUTPUT_SCHEMA,
    TOOL_DEFINITIONS,
    TOOL_NAME,
    InputError,
    platform_skills,
    run,
    run_rules,
    server,
)
from ryo_verdict_record.__main__ import main
from ryo_verdict_record.skill import hit_of, scored_direction, wilson

NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)


def out(symbol: str, reading: str, direction: str | None, hit: bool | None = None, plan: str = "NO_PLAN") -> dict[str, Any]:
    state = "SETTLED" if direction in ("ROSE", "FELL") else ("VOID" if direction == "TIE" else "OPEN")
    return {"symbol": symbol, "reading": reading, "state": state, "direction": direction, "hit": hit, "plan": plan}


def call(outcomes: list[dict[str, Any]], **kw: Any) -> dict[str, Any]:
    result = run({"record": {"outcomes": outcomes}, **kw}, now=NOW)
    jsonschema.validate(result, OUTPUT_SCHEMA)
    return result


def test_the_rule_for_a_hit() -> None:
    assert hit_of("UP", "ROSE") is True and hit_of("UP", "FELL") is False
    assert hit_of("DOWN", "FELL") is True and hit_of("DOWN", "ROSE") is False
    assert hit_of("NOT_LONG", "FELL") is True and hit_of("NOT_LONG", "ROSE") is False
    assert hit_of("UP", "TIE") is None and hit_of("UP", None) is None
    assert hit_of("NONE", "ROSE") is None and hit_of("UNREAD", "FELL") is None
    assert scored_direction("UP", "ROSE") is None
    assert [scored_direction("UP", None), scored_direction("UP", "TIE"), scored_direction("NONE", "ROSE")] == [
        "not_settled",
        "tie",
        "not_a_direction",
    ]


def test_wilson_against_known_values() -> None:
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(5, 10)
    assert (round(lo, 4), round(hi, 4)) == (0.2366, 0.7634)  # the textbook interval for 5 of 10
    lo, hi = wilson(0, 10)
    assert round(lo, 4) == 0.0 and round(hi, 4) == 0.2775
    lo, hi = wilson(10, 10)
    assert round(hi, 4) == 1.0 and round(lo, 4) == 0.7225
    for hits, n in ((1, 7), (3, 8), (50, 120)):
        lo, hi = wilson(hits, n)
        assert 0 <= lo <= hits / n <= hi <= 1


def test_counts_and_unscored_reasons() -> None:
    result = call(
        [
            out("SOL", "UP", "ROSE", True, "TARGET"),
            out("SOL", "UP", "FELL", False, "STOP"),
            out("SOL", "DOWN", "FELL", True, "TIMEOUT"),
            out("ETH", "NOT_LONG", "TIE"),
            out("ETH", "UP", None, None, "WAITING"),
            out("ETH", "NONE", "ROSE"),
            out("ETH", "UNREAD", "FELL"),
        ]
    )
    d = result["data"]
    assert (d["reads"], d["scored"]) == (7, 3)
    assert d["unscored"] == {"not_a_direction": 2, "not_settled": 1, "tie": 1}
    assert d["by_reading"]["UP"]["reads"] == 3 and d["by_reading"]["UP"]["scored"] == 2 and d["by_reading"]["UP"]["hits"] == 1
    assert d["by_reading"]["DOWN"]["hit_rate"] == 1.0 and d["by_reading"]["NOT_LONG"]["scored"] == 0
    assert d["by_reading"]["NOT_LONG"]["hit_rate"] is None and d["by_reading"]["NOT_LONG"]["interval_95"] is None
    assert d["market_rate_rose"] == round(1 / 3, 4)  # of the three scored windows one rose
    assert d["plans"]["TARGET"] == 1 and d["plans"]["WAITING"] == 1 and d["plans"]["NO_PLAN"] == 3
    assert result["status"] == "ok" and result["availability"] == {"outcomes": "available"}


def test_an_unscored_read_is_never_a_miss() -> None:
    result = call([out("SOL", "UP", None), out("SOL", "UP", "TIE"), out("SOL", "UP", None)])
    d = result["data"]
    assert d["scored"] == 0 and d["by_reading"]["UP"]["hits"] == 0 and d["by_reading"]["UP"]["hit_rate"] is None
    assert d["by_reading"]["UP"]["verdict"] == "insufficient" and "none could be scored" in result["summary"]["headline"]


def test_the_comparison_with_the_market() -> None:
    # twenty UP reads, nineteen right; ten DOWN reads, all wrong: the market rose in 19 of 30 windows
    reads = [out("A", "UP", "ROSE", True)] * 19 + [out("A", "UP", "FELL", False)] + [out("A", "DOWN", "ROSE", False)] * 10
    d = call(reads)["data"]
    assert d["market_rate_rose"] == round(29 / 30, 4)
    assert d["by_reading"]["UP"]["verdict"] == "not_distinguishable"  # 19 of 20 against a market that rose 29 of 30
    assert d["by_reading"]["DOWN"]["verdict"] == "not_distinguishable"  # right 0 of 10, interval (0, 0.28), when the market fell 1 in 30
    above = call([out("A", "UP", "ROSE", True)] * 20 + [out("A", "DOWN", "FELL", True)] * 20 + [out("A", "NOT_LONG", "FELL", True)] * 20)[
        "data"
    ]
    assert above["by_reading"]["UP"]["verdict"] == "above_market"  # the market rose 20 of 60, and UP is right 20 of 20
    assert above["by_reading"]["UP"]["market_rate"] == round(1 / 3, 4)


def test_below_market() -> None:
    # UP right 1 of 20 while the market rose in 21 of 21+? : twenty UP reads that fell and one that rose; twenty DOWN reads that rose
    reads = [out("A", "UP", "FELL", False)] * 19 + [out("A", "UP", "ROSE", True)] + [out("A", "NOT_LONG", "ROSE", False)] * 40
    d = call(reads)["data"]
    assert d["market_rate_rose"] == round(41 / 60, 4)
    assert d["by_reading"]["UP"]["verdict"] == "below_market"  # 1 of 20, interval high end about 0.24, under 0.68
    assert d["by_reading"]["NOT_LONG"]["verdict"] == "below_market"  # 0 of 40 when the market fell 19 in 60


def test_above_market_needs_the_whole_interval_above() -> None:
    reads = [out("A", "UP", "ROSE", True)] * 20 + [out("A", "DOWN", "ROSE", False)] * 5 + [out("A", "NOT_LONG", "FELL", True)] * 40
    d = call(reads)["data"]
    assert d["market_rate_rose"] == round(25 / 65, 4)
    assert d["by_reading"]["UP"]["verdict"] == "above_market"  # 20 of 20: the interval's low end is about 0.84, above 0.38


def test_min_scored_and_symbol() -> None:
    reads = [out("A", "UP", "ROSE", True)] * 3 + [out("B", "UP", "FELL", False)] * 2
    assert call(reads)["data"]["by_reading"]["UP"]["verdict"] == "insufficient"
    d = call(reads, min_scored=2, symbol="A")["data"]
    assert (d["reads"], d["by_reading"]["UP"]["scored"], d["symbol"]) == (3, 3, "A")
    assert call(reads, min_scored=6)["data"]["by_reading"]["UP"]["verdict"] == "insufficient"  # five scored reads
    assert call(reads, min_scored=5)["data"]["by_reading"]["UP"]["verdict"] != "insufficient"  # exactly enough


def test_a_record_that_disagrees_with_the_rule_is_reported() -> None:
    result = call([out("A", "UP", "ROSE", False), out("A", "UP", "FELL", True)])
    assert result["status"] == "partial" and any("2 recorded hit(s) disagree" in w for w in result["warnings"])
    assert result["data"]["by_reading"]["UP"]["hits"] == 1  # the rule's, not the record's
    assert call([out("A", "UP", "ROSE", None)])["warnings"] == []  # no recorded hit to disagree with


def test_malformed_outcomes_and_unknown_plans_are_counted_not_dropped_silently() -> None:
    result = call([{"symbol": "A"}, "x", out("A", "UP", "ROSE", True), {**out("A", "UP", "ROSE", True), "plan": "WEIRD"}])
    assert result["data"]["reads"] == 2 and result["data"]["plans"]["OTHER"] == 1
    assert any("2 outcome(s) could not be read" in w for w in result["warnings"]) and any("OTHER" in w for w in result["warnings"])
    assert result["status"] == "partial"


def test_an_empty_record_is_unavailable_and_not_a_finding() -> None:
    result = call([])
    assert result["status"] == "unavailable" and "not a finding about RYO" in result["summary"]["headline"]
    assert result["data"]["market_rate_rose"] is None and result["availability"] == {"outcomes": "unavailable"}


def test_a_bare_list_is_accepted() -> None:
    result = run({"record": [out("A", "UP", "ROSE", True)]}, now=NOW)
    assert result["data"]["reads"] == 1


@pytest.mark.parametrize(
    "args,field",
    [
        ([], "arguments"),
        ({}, "record"),
        ({"record": "x"}, "record"),
        ({"record": {"outcomes": "x"}}, "record"),
        ({"record": []}, None),
        ({"record": [], "symbol": "sol"}, "symbol"),
        ({"record": [], "symbol": ""}, "symbol"),
        ({"record": [], "symbol": "A" * 21}, "symbol"),
        ({"record": [], "symbol": 5}, "symbol"),
        ({"record": [], "min_scored": 0}, "min_scored"),
        ({"record": [], "min_scored": 1001}, "min_scored"),
        ({"record": [], "min_scored": True}, "min_scored"),
        ({"record": [], "min_scored": 2.5}, "min_scored"),
        ({"record": [], "x": 1}, "x"),
        ({"record": [{}] * 5001}, "record"),
    ],
)
def test_refusals(args: Any, field: str | None) -> None:
    if field is None:
        run(args, now=NOW)
        return
    with pytest.raises(InputError) as err:
        run(args, now=NOW)
    assert err.value.field == field and err.value.to_envelope("t")["details"]["example"]


def test_properties_on_random_records() -> None:
    rng = random.Random(11)
    readings = ["UP", "DOWN", "NOT_LONG", "NONE", "UNREAD"]
    for _ in range(200):
        rows = [out(rng.choice("ABC"), rng.choice(readings), rng.choice(["ROSE", "FELL", "TIE", None])) for _ in range(rng.randint(0, 40))]
        d = call(rows)["data"]
        assert d["reads"] == len(rows) and d["scored"] + sum(d["unscored"].values()) == d["reads"]
        assert sum(line["scored"] for line in d["by_reading"].values()) == d["scored"]
        for line in d["by_reading"].values():
            assert line["hits"] <= line["scored"] <= line["reads"]
            if line["scored"]:
                lo, hi = line["interval_95"]
                assert 0 <= lo <= line["hit_rate"] <= hi <= 1
        shuffled = rows[:]
        rng.shuffle(shuffled)
        assert call(shuffled)["data"] == d  # the order of a record does not matter


def test_rules_tool_and_definitions() -> None:
    result = run_rules({}, now=NOW)
    jsonschema.validate(result, RULES_OUTPUT_SCHEMA)
    assert "never counts an unscored read as a miss" in result["summary"]["headline"]
    with pytest.raises(InputError):
        run_rules({"x": 1})
    assert [d["name"] for d in TOOL_DEFINITIONS] == [TOOL_NAME, "ryo_verdict_record_rules"]
    for d in TOOL_DEFINITIONS:
        order = [d["description"].index(k) for k in ("Delivery:", "Use when:", "Core inputs:", "Limits:")]
        assert order == sorted(order) and d["inputSchema"]["additionalProperties"] is False


def test_mcp_and_platform_routes() -> None:
    from ryo_verdict_record.schema import EXAMPLE_CALL

    msg = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": TOOL_NAME, "arguments": EXAMPLE_CALL}}
    reply = server.handle_rpc(msg, {TOOL_NAME: run}).reply
    assert reply is not None and reply["result"]["isError"] is False
    jsonschema.validate(json.loads(reply["result"]["content"][0]["text"]), OUTPUT_SCHEMA)
    status, body = platform_skills.invoke(TOOL_NAME, {"name": TOOL_NAME, "args": EXAMPLE_CALL}, server.RUNNERS)
    assert status == 200 and body["status"] == "success"
    srv = server.serve_http("127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/api/skills/", timeout=10) as resp:
            assert [s["name"] for s in json.loads(resp.read())] == [t["name"] for t in TOOL_DEFINITIONS]
        req = urllib.request.Request(
            base + f"/tools/{TOOL_NAME}/call", data=json.dumps(EXAMPLE_CALL).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            assert json.loads(resp.read())["tool"] == TOOL_NAME
    except urllib.error.URLError as exc:  # pragma: no cover
        raise AssertionError(str(exc)) from exc
    finally:
        srv.shutdown()
        srv.server_close()


def test_cli(tmp_path: Any, capsys: Any) -> None:
    f = tmp_path / "ryo.json"
    f.write_text(json.dumps({"outcomes": [out("A", "UP", "ROSE", True)]}))
    assert main(["score", str(f), "--min-scored", "1", "--symbol", "A"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["by_reading"]["UP"]["verdict"] in (
        "insufficient",
        "above_market",
        "not_distinguishable",
    )
    f.write_text("nope")
    assert main(["score", str(f)]) == 2
    assert main(["score", str(tmp_path / "missing.json")]) == 2
    assert main(["rules"]) == 0 and main(["example"]) == 0 and main(["tool"]) == 0


def test_a_one_sided_record_says_it_cannot_tell_skill_from_luck() -> None:
    result = call([out("A", "UP", "ROSE", True)] * 20)
    assert result["data"]["by_reading"]["UP"]["verdict"] == "not_distinguishable"
    assert any("went the same way" in w for w in result["warnings"]) and result["status"] == "partial"
    assert not any("went the same way" in w for w in call([out("A", "UP", "ROSE", True), out("A", "UP", "FELL", False)])["warnings"])


def test_a_recorded_hit_that_is_not_a_boolean_is_not_a_disagreement() -> None:
    result = call([{**out("A", "UP", "ROSE"), "hit": 1}])
    assert not any("disagree" in w for w in result["warnings"])
