"""Scenarios for turning a RYO answer into evidence (``hoshitori.ryo_evidence``), runnable against the real module or a mutant of it.

The answers are the synthetic ones of ``ryo_data`` (built by hand from the documented shapes; a test that needs the live recordings is in
``test_ryo_evidence.py`` and skips without them). What an answer must become, worked out by hand from ``analyze_token_data``:

* an OBSERVATION item of the RYO origin family that holds the 14 observation fields with a value (16 are specified; ``asset.chain`` and
  ``asset.contract`` are null, so they are unknown fields of the reason NULL, never an empty string or a zero);
* a separate INTERPRETATION item that holds the 5 interpretation fields (``verdict``, ``technical_analysis.trend`` and the three prose
  fields of ``intelligence``); an independent arm is never given it;
* a number spelled as the answer spelled it (``100.5``), a status, a data mode and the availability words as RYO gave them, the request that
  was sent, the time RYO says the answer is as of, and the capture and hash it came from;
* a zero in an answer that was not wholly available is not a measurement: it is an unknown field of the reason DEGRADED_ZERO, while the
  same zero in a wholly available answer is a zero;
* no item at all for an answer that says it is unavailable, that is not live, that is about another token than the one asked for, or that
  does not fit the contract.
"""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from hoshitori import canonical
from hoshitori.capture import RawCapture
from hoshitori.connector import ConnectorError
from hoshitori.evidence import RYO, Kind, Redistribution, Unknown
from hoshitori.timeline import to_ms
from tests import ryo_data as D

SENT = datetime(2026, 10, 29, 12, 0, 5, tzinfo=UTC)
RECEIVED = SENT + timedelta(seconds=55)
AS_OF_MS = to_ms(datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC))


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str, *, kind: type[Exception] | None = None) -> None:
        try:
            call()
        except Exception as exc:
            wanted = kind or self.mod.RyoEvidenceError
            if not isinstance(exc, wanted):
                self.bad.append(f"{label}: raised {type(exc).__name__}, expected {wanted.__name__}")
            elif says and str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected {says!r}")
            return
        self.bad.append(f"{label}: was accepted")


def capture(tool: str, result: dict[str, Any] | None = None, arguments: dict[str, Any] | None = None) -> RawCapture:
    return RawCapture(
        "cap-" + "1" * 32,
        "ryo",
        cast(dict[str, canonical.JsonValue], {"operation": tool, "arguments": arguments if arguments is not None else D.REQUESTS[tool]}),
        200,
        {"content-type": "application/json"},
        D.rest_body(tool, result),
        SENT,
        RECEIVED,
        trace_id="0123456789abcdef0123456789abcdef",
    )


def analyze(mod: types.ModuleType, **over: Any) -> Any:
    return mod.items_from(capture("analyze_token", D.result("analyze_token", **over)), "analyze_token", {"symbol": "SOL"}, "ryo-evidence-1")


def check_a_whole_answer(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    made = analyze(mod)
    obs, interp = made.observation, made.interpretation
    c.eq("the observation item's kind, origin and source", (obs.kind, obs.origin, obs.source), (Kind.OBSERVATION, RYO, "ryo"))
    c.eq("its tool, status and data mode", (obs.tool, obs.status, obs.data_mode), ("analyze_token", "ok", "live"))
    c.eq("its path and adapter version", (obs.path, obs.adapter_version), ("ryo.analyze_token", "ryo-evidence-1"))
    c.eq("fourteen observation fields hold a value", len(obs.fields), 14)
    values = dict(obs.fields)
    c.eq(
        "numbers are spelled as the answer spelled them",
        (values["market.price_usd"], values["performance.change_24h_pct"], values["technical_analysis.rsi_14"], values["asset.rank"]),
        ("100.5", "-1.02", "39.8", "7"),
    )
    c.eq("and text stays text", (values["asset.symbol"], values["asset.name"]), ("SOL", "Solana"))
    c.eq(
        "a null is an unknown of the reason NULL, in path order",
        obs.unknown_fields,
        (("asset.chain", Unknown.NULL), ("asset.contract", Unknown.NULL)),
    )
    c.eq("no field is in both lists", {p for p, _ in obs.fields} & {p for p, _ in obs.unknown_fields}, set())
    c.eq("the request that was sent", obs.request, (("symbol", "SOL"),))
    c.eq(
        "availability as RYO gave it, in section order",
        obs.availability,
        (("market_data", "available"), ("market_intelligence", "available"), ("technical_analysis", "available")),
    )
    c.eq("no warnings", obs.warnings, ())
    c.eq(
        "the item is as of the time RYO says, and known when it arrived",
        (obs.event_time_ms, obs.received_at_ms, obs.known_at_ms),
        (AS_OF_MS, to_ms(RECEIVED), to_ms(RECEIVED)),
    )
    c.eq("the capture it came from", (obs.capture_id, obs.raw_sha256), ("cap-" + "1" * 32, capture("analyze_token").body_sha256))
    c.eq("RYO's payload is not for the public until the organisers say so", obs.redistribution, Redistribution.PRIVATE_ONLY)
    c.eq(
        "the interpretation item is of the interpretation kind and the same origin",
        (interp.kind, interp.origin, interp.source),
        (Kind.INTERPRETATION, RYO, "ryo"),
    )
    c.eq("its path", interp.path, "ryo.analyze_token.interpretation")
    c.eq(
        "it holds the five interpretation fields and nothing else",
        [p for p, _ in interp.fields],
        ["intelligence.catalysts[0]", "intelligence.narrative", "intelligence.risks[0]", "technical_analysis.trend", "verdict"],
    )
    c.eq(
        "it comes from the same capture and says the same about its answer",
        (interp.capture_id, interp.raw_sha256, interp.status, interp.data_mode),
        (obs.capture_id, obs.raw_sha256, "ok", "live"),
    )
    c.eq("no field is an interpretation in the observation item", {p for p, _ in obs.fields} & {p for p, _ in interp.fields}, set())
    return c.bad


def check_a_partial_answer(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = D.analyze_token_data()
    data["market"]["volume_24h_usd"] = 0
    data["performance"]["change_1h_pct"] = 0.0
    data["technical_analysis"]["rsi_14"] = None
    wholly = mod.items_from(capture("analyze_token", D.result("analyze_token", data=data)), "analyze_token", {"symbol": "SOL"}, "v")
    c.eq("a zero in a wholly available answer is a zero", dict(wholly.observation.fields)["market.volume_24h_usd"], "0")
    c.eq("and a null in it is NULL", dict(wholly.observation.unknown_fields)["technical_analysis.rsi_14"], Unknown.NULL)
    partial_word = dict(D.AVAILABILITY["analyze_token"], market_data="partial")
    got = mod.items_from(
        capture(
            "analyze_token",
            D.result(
                "analyze_token", data=data, status="partial", availability=partial_word, warnings=["The market section is incomplete."]
            ),
        ),
        "analyze_token",
        {"symbol": "SOL"},
        "v",
    ).observation
    c.eq(
        "a partial answer says so",
        (got.status, dict(got.availability)["market_data"], got.warnings),
        ("partial", "partial", ("The market section is incomplete.",)),
    )
    unknown = dict(got.unknown_fields)
    c.eq(
        "a zero in it is not a measurement",
        (unknown["market.volume_24h_usd"], unknown["performance.change_1h_pct"]),
        (Unknown.DEGRADED_ZERO, Unknown.DEGRADED_ZERO),
    )
    c.eq("a null in it is still NULL", unknown["technical_analysis.rsi_14"], Unknown.NULL)
    c.eq("and a value that is not zero stays a value", dict(got.fields)["market.price_usd"], "100.5")
    unavailable_section = dict(D.AVAILABILITY["analyze_token"], market_intelligence="unavailable")
    held = mod.items_from(
        capture("analyze_token", D.result("analyze_token", data=data, availability=unavailable_section)),
        "analyze_token",
        {"symbol": "SOL"},
        "v",
    ).observation
    c.eq(
        "a section that is unavailable makes an answer not whole even when its status says ok",
        dict(held.unknown_fields)["market.volume_24h_usd"],
        Unknown.DEGRADED_ZERO,
    )
    requested = dict(D.AVAILABILITY["analyze_token"], market_intelligence="not_requested")
    fine = mod.items_from(
        capture("analyze_token", D.result("analyze_token", data=data, availability=requested)), "analyze_token", {"symbol": "SOL"}, "v"
    ).observation
    c.eq("a section that was not requested is not a gap", dict(fine.fields)["market.volume_24h_usd"], "0")
    return c.bad


def check_what_is_refused(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.refuses(
        "an answer that says it is unavailable",
        lambda: analyze(mod, status="unavailable"),
        "RYO says the answer is unavailable",
    )
    for mode in ("simulated", "mixed", "unknown"):
        c.refuses(
            f"data mode {mode}",
            lambda mode=mode: analyze(mod, data_mode=mode),  # type: ignore[misc]
            f"RYO's data mode is {mode!r}, not 'live': nothing that is not live enters a cycle",
        )
    replay = RawCapture(
        "cap-" + "3" * 32,
        "ryo-recorded",
        cast(dict[str, canonical.JsonValue], {"mode": "recorded", "operation": "analyze_token", "arguments": {"symbol": "SOL"}}),
        200,
        {"content-type": "application/json"},
        D.rest_body("analyze_token", None),
        SENT,
        RECEIVED,
    )
    c.refuses(
        "a replayed answer, though its body says live",
        lambda: mod.items_from(replay, "analyze_token", {"symbol": "SOL"}, "v"),
        "a recorded answer is a replay: nothing that is not a live read enters a cycle",
    )
    other = D.analyze_token_data()
    other["asset"]["symbol"] = "AVAX"
    c.refuses(
        "an answer about another token",
        lambda: mod.items_from(capture("analyze_token", D.result("analyze_token", data=other)), "analyze_token", {"symbol": "SOL"}, "v"),
        "RYO answered about AVAX, not SOL",
    )
    low = D.analyze_token_data()
    low["asset"]["symbol"] = "sol"
    c.eq(
        "the symbol is compared without regard to case",
        mod.items_from(
            capture("analyze_token", D.result("analyze_token", data=low)), "analyze_token", {"symbol": "SOL"}, "v"
        ).observation.status,
        "ok",
    )
    c.refuses(
        "an answer that is not the tool's",
        lambda: mod.items_from(capture("analyze_token", D.result("scan_market")), "analyze_token", {"symbol": "SOL"}, "v"),
        "",
        kind=ConnectorError,
    )
    c.refuses(
        "a body that is not an envelope",
        lambda: mod.items_from(
            RawCapture("cap-" + "2" * 32, "ryo", {"operation": "analyze_token", "arguments": {}}, 200, {}, b"<html>", SENT, RECEIVED),
            "analyze_token",
            {"symbol": "SOL"},
            "v",
        ),
        "",
        kind=ConnectorError,
    )
    return c.bad


def check_a_tool_without_an_asset(mod: types.ModuleType) -> list[str]:
    """A tool about the whole market has no asset to compare: ``market_overview`` and ``monitor_market_sentiment_shift`` give items whose
    request is what was sent, and ``scan_market`` too; none is checked against a symbol."""
    c = Case(mod)
    for tool in ("market_overview", "monitor_market_sentiment_shift", "scan_market"):
        made = mod.items_from(capture(tool), tool, D.REQUESTS[tool], "v")
        c.eq(
            f"{tool}: an observation item of the same tool and path", (made.observation.tool, made.observation.path), (tool, f"ryo.{tool}")
        )
        c.eq(f"{tool}: its request is the one sent", dict(made.observation.request), D.REQUESTS[tool])
    return c.bad


def check_a_comparison(mod: types.ModuleType) -> list[str]:
    """``compare_tokens`` is one answer about several tokens: its items are the same two kinds, and it must be about exactly the tokens that
    were asked for (a comparison of other tokens, or of fewer, is refused, in any spelling of the symbols)."""
    c = Case(mod)
    asked = D.REQUESTS["compare_tokens"]
    made = mod.items_from(capture("compare_tokens"), "compare_tokens", asked, "v")
    c.eq("an observation item of the tool", (made.observation.tool, made.observation.path), ("compare_tokens", "ryo.compare_tokens"))
    c.eq(
        "and an interpretation item, apart",
        made.interpretation is not None and made.interpretation.path,
        "ryo.compare_tokens.interpretation",
    )
    c.eq("the pick is on the interpretation side only", [p for p, _ in made.observation.fields if "conclusion" in p or "winner" in p], [])
    for spelling in ("sol avax", "AVAX,SOL", " Sol ,  avax ", "avax sol"):
        again = mod.items_from(capture("compare_tokens"), "compare_tokens", {**asked, "symbols": spelling}, "v")
        c.eq(f"asked as {spelling!r}", again.observation.tool, "compare_tokens")
    c.refuses(
        "other tokens",
        lambda: mod.items_from(capture("compare_tokens"), "compare_tokens", {**asked, "symbols": "SOL, BTC"}, "v"),
        "RYO compared ['AVAX', 'SOL'], not ['BTC', 'SOL']",
    )
    c.refuses(
        "more tokens than were asked for",
        lambda: mod.items_from(capture("compare_tokens"), "compare_tokens", {**asked, "symbols": "SOL, AVAX, BTC"}, "v"),
        "RYO compared ['AVAX', 'SOL'], not ['AVAX', 'BTC', 'SOL']",
    )
    c.refuses(
        "no symbols in the request",
        lambda: mod.items_from(capture("compare_tokens"), "compare_tokens", {"intent": "swing"}, "v"),
        "the comparison cannot be checked against the tokens it was asked about",
    )
    return c.bad


def check_shape_and_unknowns(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # a field the specification names and the answer leaves out is unknown (ABSENT), on the side of its class; a null is NULL; order is by path
    data = D.analyze_token_data()
    del data["verdict"]  # an interpretation field
    del data["asset"]["rank"]  # an observation field
    data["technical_analysis"]["rsi_14"] = None
    made = mod.items_from(capture("analyze_token", D.result("analyze_token", data=data)), "analyze_token", {"symbol": "SOL"}, "v")
    c.eq(
        "unknown observation fields, in path order, each with its reason",
        made.observation.unknown_fields,
        (
            ("asset.chain", Unknown.NULL),
            ("asset.contract", Unknown.NULL),
            ("asset.rank", Unknown.ABSENT),
            ("technical_analysis.rsi_14", Unknown.NULL),
        ),
    )
    c.eq(
        "an interpretation field left out is unknown on the interpretation side",
        made.interpretation.unknown_fields,
        (("verdict", Unknown.ABSENT),),
    )
    c.eq("and the others of that side are still known", len(made.interpretation.fields), 4)
    scan = D.scan_market_data()
    del scan["filters"]  # only information about the request: not an observation, not an interpretation
    got = mod.items_from(capture("scan_market", D.result("scan_market", data=scan)), "scan_market", {"top_n": 5}, "v")
    c.eq("a missing field of neither class is not recorded", [p for p, _ in got.observation.unknown_fields if p.startswith("filters")], [])
    # request order: the item's request is in key order whatever order the call was made in
    deep = D.deep_analysis_data()
    deep["asset"]["symbol"] = "SOL"
    asked: dict[str, Any] = {"symbol": "SOL", "include_perp": True}
    got = mod.items_from(capture("deep_analysis", D.result("deep_analysis", data=deep), asked), "deep_analysis", asked, "v")
    c.eq("a request is kept in key order", got.observation.request, (("include_perp", True), ("symbol", "SOL")))
    # text that looks like a zero is text, in an answer that is not whole
    text = D.analyze_token_data()
    text["asset"]["name"] = "0"
    partial = dict(D.AVAILABILITY["analyze_token"], market_data="partial")
    got = mod.items_from(
        capture("analyze_token", D.result("analyze_token", data=text, status="partial", availability=partial)),
        "analyze_token",
        {"symbol": "SOL"},
        "v",
    )
    c.eq("a text field that reads 0 stays text", dict(got.observation.fields).get("asset.name"), "0")
    c.eq("and a number field that is not zero stays a value", dict(got.observation.fields).get("market.price_usd"), "100.5")
    # what is asked of a tool with no symbol, or of a tool that is about no asset
    c.eq(
        "an asset tool asked with no symbol is not compared with one",
        mod.items_from(capture("analyze_token"), "analyze_token", {}, "v").observation.request,
        (),
    )
    c.eq(
        "a tool about the market, asked with a symbol it ignores, is not compared with one",
        mod.items_from(capture("market_overview"), "market_overview", {"symbol": "SOL"}, "v").observation.request,
        (("symbol", "SOL"),),
    )
    # keyed members are visited in key order, which is not always path order: "SOL" comes before "SOL-2" by key, but after it by path
    pair = D.compare_tokens_data()
    pair["factors"][0]["scores"]["SOL-2"] = 0.1
    got = mod.items_from(capture("compare_tokens", D.result("compare_tokens", data=pair)), "compare_tokens", {"symbols": "SOL, AVAX"}, "v")
    scores = [p for p, _ in got.interpretation.fields if p.startswith("factors[0].scores")]
    c.eq("the keyed fields are all there", scores, ["factors[0].scores{AVAX}", "factors[0].scores{SOL-2}", "factors[0].scores{SOL}"])
    paths = [p for p, _ in got.interpretation.fields]
    c.eq("and the fields of an item are in path order whatever order they were visited in", paths, sorted(paths))
    # a deep analysis of another token than the one asked for is refused as an analysis is
    other = D.deep_analysis_data()
    other["asset"]["symbol"] = "AVAX"
    c.refuses(
        "a deep analysis about another token",
        lambda: mod.items_from(capture("deep_analysis", D.result("deep_analysis", data=other)), "deep_analysis", {"symbol": "SOL"}, "v"),
        "RYO answered about AVAX, not SOL",
    )
    # an item is a value
    try:
        made.observation = made.interpretation  # type: ignore[misc]
    except AttributeError:
        pass
    else:
        c.bad.append("the two items of an answer can be replaced after they are made")
    # an answer that holds nothing to observe is refused, and one the evidence layer cannot hold says so
    empty = D.scan_market_data()
    empty["candidates"] = []
    empty["candidate_count"] = 0
    c.refuses(
        "a scan with no candidate holds nothing to observe",
        lambda: mod.items_from(capture("scan_market", D.result("scan_market", data=empty)), "scan_market", {}, "v"),
        "the answer holds no observation",
    )
    long = D.analyze_token_data()
    long["intelligence"]["narrative"] = "x" * 25_000
    try:
        mod.items_from(capture("analyze_token", D.result("analyze_token", data=long)), "analyze_token", {"symbol": "SOL"}, "v")
    except mod.RyoEvidenceError as exc:
        c.eq("a field the evidence layer cannot hold", str(exc).startswith("the answer cannot be recorded as evidence: "), True)
    else:
        c.bad.append("a narrative of 25,000 characters was recorded")
    return c.bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "a_whole_answer": check_a_whole_answer,
    "a_partial_answer": check_a_partial_answer,
    "what_is_refused": check_what_is_refused,
    "a_tool_without_an_asset": check_a_tool_without_an_asset,
    "a_comparison": check_a_comparison,
    "shape_and_unknowns": check_shape_and_unknowns,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:  # a crash in the scenario is a failure of the module under test
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
