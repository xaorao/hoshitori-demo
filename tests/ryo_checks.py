"""Checks of the RYO adapter (arguments, operations, classification, rate headers), runnable against the real module or a mutant."""

from __future__ import annotations

import json
import types
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import ryo_catalogue
from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.ryo_pin import PIN
from tests import ryo_data as D
from tests.ryo_support import SIX, catalogue_body

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
META = ("_catalogue", "_whoami", "_health")


def capture(
    operation: str | None, status: int | None, body: bytes, headers: dict[str, str] | None = None, trace: str | None = None
) -> RawCapture:
    request: dict[str, Any] = {"surface": "rest", "method": "POST"}
    if operation is not None:
        request["operation"] = operation
    return RawCapture(
        capture_id=new_capture_id(),
        source="ryo",
        request=request,
        status=status,
        headers=headers or {},
        body=body,
        t_send=NOW,
        received_at=NOW + timedelta(seconds=2),
        trace_id=trace,
    )


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    E = mod.ErrorClass
    pinned = ryo_catalogue.load_pin(PIN)

    def adapter(config: Any = None) -> Any:
        return mod.RyoAdapter(pinned, config)

    def catalogue(mutate: Any = None) -> Any:
        return ryo_catalogue.parse_catalogue(catalogue_body(mutate))

    # --- configuration ---------------------------------------------------------------------------------------------------------------
    for label, kwargs in (
        ("a cap below RYO's own timeout", {"attempt_cap_s": 120.0}),
        ("a cap equal to RYO's own timeout", {"attempt_cap_s": 165.0}),
        ("a zero minimum", {"min_attempt_s": 0.0}),
        ("a minimum above the cap", {"min_attempt_s": 200.0}),
        ("a negative minimum", {"min_attempt_s": -1.0}),
        ("a zero top_n limit", {"max_top_n": 0}),
    ):
        try:
            mod.RyoConfig(**kwargs)
            bad.append(f"{label}: accepted")
        except ValueError:
            pass
    try:
        mod.RyoConfig(attempt_cap_s=5.0, min_attempt_s=1.0, testing=True)
    except ValueError:
        bad.append("a short attempt is allowed when testing")
    try:
        mod.RyoConfig(attempt_cap_s=165.5)
    except ValueError:
        bad.append("a cap just above RYO's own timeout is allowed")
    try:
        mod.RyoConfig(attempt_cap_s=170.0, min_attempt_s=170.0)
    except ValueError:
        bad.append("a minimum equal to the cap is allowed")
    config = mod.RyoConfig()
    if (config.attempt_cap_s, config.min_attempt_s, config.max_top_n) != (170.0, 30.0, 50):
        bad.append(f"the default configuration: {config}")

    # --- operations and drift ---------------------------------------------------------------------------------------------------------
    a = adapter()
    if a.provider != "ryo" or mod.RyoAdapter(pinned, provider="ryo-recorded").provider != "ryo-recorded":
        bad.append("the provider name")
    if a.operations() != frozenset(SIX) | frozenset(META):
        bad.append(f"operations at the start: {sorted(a.operations())}")
    if a.drift != () or a.blocked != frozenset():
        bad.append("no drift is known at the start")
    if a.update_catalogue(catalogue()) != () or a.operations() != frozenset(SIX) | frozenset(META):
        bad.append("an identical catalogue changes nothing")
    drift = a.update_catalogue(catalogue(lambda t: t[2]["inputSchema"]["properties"]["symbol"].update(type="number")))
    if (
        [(d.kind, d.tool) for d in drift] != [("SCHEMA_CHANGED", "analyze_token")]
        or "analyze_token" in a.operations()
        or a.blocked != {"analyze_token"}
    ):
        bad.append("a changed schema must make the tool unavailable")
    if a.drift != drift:
        bad.append("the drift is kept")
    if a.operations() != (frozenset(SIX) - {"analyze_token"}) | frozenset(META):
        bad.append("only the changed tool is unavailable")
    a.update_catalogue(catalogue())
    if "analyze_token" not in a.operations() or a.blocked:
        bad.append("a catalogue that matches again restores the tool")
    a.update_catalogue(catalogue(lambda t: t.pop(0)))
    if "market_overview" in a.operations() or a.blocked != {"market_overview"}:
        bad.append("a missing tool must be unavailable")
    a.update_catalogue(
        catalogue(
            lambda t: t.append(
                {
                    "name": "check_safety",
                    "description": "d",
                    "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                }
            )
        )
    )
    if "check_safety" in a.operations() or a.operations() != frozenset(SIX) | frozenset(META) or a.blocked:
        bad.append("a new tool is not called and blocks nothing")
    a.update_catalogue(catalogue(lambda t: t[3].update(description="Reworded.")))
    if a.operations() != frozenset(SIX) | frozenset(META) or a.blocked:
        bad.append("a reworded description blocks nothing")
    live_hash = catalogue(lambda t: t[3].update(description="Reworded.")).sha256
    a.update_catalogue(catalogue(lambda t: t[3].update(description="Reworded.")))
    if a.catalogue_sha256 != live_hash or adapter().catalogue_sha256 != pinned.sha256:
        bad.append("the hash of the catalogue in force")
    # a tool the pin does not have is not called, even if a specification for it exists
    narrow = ryo_catalogue.load_pin({k: v for k, v in PIN.items() if k != "market_overview"})
    b = mod.RyoAdapter(narrow)
    b.update_catalogue(catalogue())
    if "market_overview" in b.operations() or b.blocked:
        bad.append(f"a tool outside the pin must not be callable: {sorted(b.operations())}")
    # a tool the pin has but no field specification describes is never called
    extra_tool = {"type": "object", "properties": {}, "additionalProperties": False}
    wider = mod.RyoAdapter(ryo_catalogue.load_pin({**PIN, "check_safety": extra_tool}))
    wider.update_catalogue(
        ryo_catalogue.parse_catalogue(
            catalogue_body(lambda t: t.append({"name": "check_safety", "description": "d", "inputSchema": extra_tool}))
        )
    )
    if "check_safety" in wider.operations() or wider.blocked:
        bad.append("a pinned tool with no field specification must not be callable")
    a.update_catalogue(
        catalogue(
            lambda t: (
                t[0].update(inputSchema=dict(t[0]["inputSchema"], properties={"x": {"type": "string"}})),
                t[1]["inputSchema"]["properties"].pop("top_n"),
            )
        )
    )
    if a.blocked != {"market_overview", "scan_market"}:
        bad.append(f"two changed tools: {a.blocked}")

    # --- arguments --------------------------------------------------------------------------------------------------------------------
    a = adapter()

    def norm(label: str, operation: str, args: dict[str, Any], expected: dict[str, Any]) -> None:
        try:
            got = a.normalise(operation, args)
        except mod.ConnectorError as exc:
            bad.append(f"{label}: refused ({exc})")
            return
        if got != expected or {k: type(v) for k, v in got.items()} != {k: type(v) for k, v in expected.items()}:
            bad.append(f"{label}: {got!r}, expected {expected!r}")

    def refused(label: str, operation: str, args: dict[str, Any], contains: str | None = None, error_class: Any = None) -> None:
        try:
            a.normalise(operation, args)
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as exc:
            if exc.error_class is not (error_class or E.INVALID_ARGS):
                bad.append(f"{label}: classed {exc.error_class.value}")
            if contains is not None and contains not in exc.message:
                bad.append(f"{label}: the message does not say {contains!r}: {exc.message}")
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    for op in META:
        norm(f"{op} with nothing", op, {}, {})
        refused(f"{op} with arguments", op, {"x": 1})
    refused("an operation that is not in the catalogue", "check_safety", {}, error_class=E.CONTRACT_DRIFT)
    norm("a symbol", "analyze_token", {"symbol": "SOL"}, {"symbol": "SOL"})
    norm("a symbol with spaces around", "analyze_token", {"symbol": "  SOL \n"}, {"symbol": "SOL"})
    norm("a symbol keeps its case", "analyze_token", {"symbol": "stETH"}, {"symbol": "stETH"})
    norm("a symbol and a chain", "analyze_token", {"symbol": "SOL", "chain": " BSC "}, {"symbol": "SOL", "chain": "bsc"})
    for symbol in ("1INCH", "WIF", "BTC.b", "USDC.e", "A-B_C", "a" * 20, "X"):
        norm(f"the symbol {symbol!r}", "analyze_token", {"symbol": symbol}, {"symbol": symbol})
    for symbol in (
        "",
        "   ",
        "SO L",
        "$SOL",
        "SOL/USDT",
        "SOL;",
        "SOL\x00",
        "ＳＯＬ",  # noqa: RUF001
        "a" * 21,
        "-SOL",
        ".SOL",
        "S" + "́",
        "SOL,AVAX",
        "S\nOL",
    ):
        refused(f"the symbol {symbol!r}", "analyze_token", {"symbol": symbol})
    refused("an Ethereum address", "analyze_token", {"symbol": "0x" + "ab" * 20}, contains="address")
    refused("a Solana address", "deep_analysis", {"symbol": "So11111111111111111111111111111111111111112"}, contains="address")
    refused("an address in compare", "compare_tokens", {"symbols": "SOL, 0x" + "ab" * 20}, contains="address")
    refused("long text with punctuation is not an address", "analyze_token", {"symbol": "a-" * 11}, contains="not a token symbol")
    refused("full-width letters are not an address", "analyze_token", {"symbol": "\uff33\uff2f\uff2c\uff2c"}, contains="not a token symbol")
    refused("a short non-symbol is not called an address", "analyze_token", {"symbol": "S L"}, contains="not a token symbol")
    for chain in ("eth", "bsc", "base-2", "a" * 32, "0g"):
        norm(f"the chain {chain!r}", "deep_analysis", {"symbol": "SOL", "chain": chain}, {"symbol": "SOL", "chain": chain})
    for chain in ("", "BSC!", "a" * 33, "-eth", "eth chain", "ETH/BSC"):
        refused(f"the chain {chain!r}", "analyze_token", {"symbol": "SOL", "chain": chain})
    norm("a chain is lower-cased", "scan_market", {"chain": "ETH"}, {"chain": "eth"})
    norm(
        "deep_analysis passes the perp flag",
        "deep_analysis",
        {"symbol": "SOL", "include_perp": False},
        {"symbol": "SOL", "include_perp": False},
    )
    norm("two symbols", "compare_tokens", {"symbols": "SOL AVAX"}, {"symbols": "SOL, AVAX"})
    norm("commas and spaces", "compare_tokens", {"symbols": "SOL , AVAX ,, BNB"}, {"symbols": "SOL, AVAX, BNB"})
    norm("four symbols", "compare_tokens", {"symbols": "A,B,C,D"}, {"symbols": "A, B, C, D"})
    norm("a duplicate in another case", "compare_tokens", {"symbols": "sol, AVAX, SOL"}, {"symbols": "sol, AVAX"})
    norm("an intent", "compare_tokens", {"symbols": "SOL,AVAX", "intent": "hold"}, {"symbols": "SOL, AVAX", "intent": "hold"})
    for symbols in ("SOL", "", "SOL SOL", "sol, SOL", "A,B,C,D,E", " , "):
        refused(f"the comparison {symbols!r}", "compare_tokens", {"symbols": symbols}, contains="two to four" if symbols != "" else None)
    refused("an outside intent", "compare_tokens", {"symbols": "SOL,AVAX", "intent": "day"})
    norm("top_n whole", "scan_market", {"top_n": 5}, {"top_n": 5})
    norm("top_n written as a float", "scan_market", {"top_n": 5.0}, {"top_n": 5})
    norm("top_n at the lower edge", "scan_market", {"top_n": 1}, {"top_n": 1})
    norm("top_n at the upper edge", "scan_market", {"top_n": 50}, {"top_n": 50})
    for value in (0, -1, 51, 2.5, 1000, 0.5):
        refused(f"top_n {value!r}", "scan_market", {"top_n": value}, contains="whole number")
    if mod.RyoAdapter(pinned, mod.RyoConfig(max_top_n=10)).normalise("scan_market", {"top_n": 10}) != {"top_n": 10}:
        bad.append("a custom top_n limit is the edge")
    try:
        mod.RyoAdapter(pinned, mod.RyoConfig(max_top_n=10)).normalise("scan_market", {"top_n": 11})
        bad.append("a custom top_n limit is enforced")
    except mod.ConnectorError:
        pass
    norm("a theme", "scan_market", {"theme": " ai "}, {"theme": "ai"})
    norm("a unicode theme", "scan_market", {"theme": "人工知能"}, {"theme": "人工知能"})
    norm("a theme at the limit", "scan_market", {"theme": "t" * 64}, {"theme": "t" * 64})
    for theme in ("", "   ", "t" * 65, "a\nb", "a\tb", "a\x00b"):
        refused(f"the theme {theme!r}", "scan_market", {"theme": theme})
    for direction in ("positive", "negative", "all"):
        norm(f"the direction {direction}", "scan_market", {"filter_direction": direction}, {"filter_direction": direction})
    for direction in ("up", "ALL", "", "gainers"):
        refused(f"the direction {direction!r}", "scan_market", {"filter_direction": direction})
    norm(
        "everything on scan_market",
        "scan_market",
        {"chain": "bsc", "theme": "ai", "top_n": 3, "filter_direction": "all"},
        {"chain": "bsc", "theme": "ai", "top_n": 3, "filter_direction": "all"},
    )
    norm("no arguments", "market_overview", {}, {})
    norm("the window", "monitor_market_sentiment_shift", {"time_window": "7d"}, {"time_window": "7d"})
    refused("a schema violation is refused first", "analyze_token", {"symbol": 5})
    refused("an unknown argument", "analyze_token", {"symbol": "SOL", "address": "x"})
    refused("a missing required argument", "analyze_token", {})
    original = {"symbol": " SOL "}
    a.normalise("analyze_token", original)
    if original != {"symbol": " SOL "}:
        bad.append("the caller's arguments were changed")
    if any(a.plan(op, {}) != 1.0 for op in (*SIX, *META)):
        bad.append("every call costs one")
    if any(a.attempt_cap_s(op) != 170.0 or a.min_attempt_s(op) != 30.0 for op in (*SIX, *META)):
        bad.append("attempt limits")

    # --- classification of answers ---------------------------------------------------------------------------------------------------------
    def classify(
        op: str | None,
        status: int | None,
        body: bytes,
        headers: dict[str, str] | None = None,
        trace: str | None = None,
        now: datetime = NOW,
    ) -> Any:
        return a.classify(capture(op, status, body, headers, trace), now)

    def expect(label: str, got: Any, error_class: Any, **fields: Any) -> None:
        if error_class is None:
            if got is not None:
                bad.append(f"{label}: expected a usable answer, got {got.error_class.value}: {got.message}")
            return
        if got is None:
            bad.append(f"{label}: expected {error_class.value}, got a usable answer")
            return
        if got.error_class is not error_class:
            bad.append(f"{label}: classed {got.error_class.value}, expected {error_class.value} ({got.message})")
        for name, value in fields.items():
            if getattr(got, name) != value:
                bad.append(f"{label}: {name} is {getattr(got, name)!r}, expected {value!r}")

    expect("no response", a.classify(capture("analyze_token", None, b""), NOW), E.UPSTREAM)
    expect(
        "no response keeps the trace",
        a.classify(capture("analyze_token", None, b"", trace="abc12345"), NOW),
        E.UPSTREAM,
        trace_id="abc12345",
    )
    for tool in SIX:
        expect(f"a good {tool}", classify(tool, 200, D.rest_body(tool)), None)
    for status in ("partial", "unavailable"):
        expect(
            f"status {status} is evidence",
            classify("analyze_token", 200, D.rest_body("analyze_token", D.result("analyze_token", status=status))),
            None,
        )
    for mode in ("simulated", "unknown", "mixed"):
        expect(
            f"data mode {mode} is evidence",
            classify("analyze_token", 200, D.rest_body("analyze_token", D.result("analyze_token", data_mode=mode))),
            None,
        )
    expect(
        "a new field is not a failure",
        classify("analyze_token", 200, D.rest_body("analyze_token", D.result("analyze_token", novelty=1))),
        None,
    )
    expect("a null price is not a failure", classify("analyze_token", 200, _with(lambda d: d["market"].update(price_usd=None))), None)
    expect(
        "a new schema version",
        classify("analyze_token", 200, D.rest_body("analyze_token", D.result("analyze_token", schema_version="2.0")), trace="abcdef12"),
        E.CONTRACT_DRIFT,
        trace_id="abcdef12",
    )
    expect("a price given as text", classify("analyze_token", 200, _with(lambda d: d["market"].update(price_usd="100"))), E.CONTRACT_DRIFT)
    expect(
        "a verdict that became an object",
        classify("analyze_token", 200, _with(lambda d: d.update(verdict={"call": "x"}))),
        E.CONTRACT_DRIFT,
    )
    expect("the wrong tool answers", classify("analyze_token", 200, D.rest_body("scan_market")), E.CONTRACT_DRIFT)
    expect("html with a 200", classify("analyze_token", 200, b"<html>maintenance</html>"), E.MALFORMED)
    expect("an empty 200", classify("analyze_token", 200, b""), E.MALFORMED)
    expect("a 200 for a tool the adapter does not know", classify("check_safety", 200, D.rest_body("analyze_token")), E.CONTRACT_DRIFT)
    expect("a capture that does not name its operation", classify(None, 200, D.rest_body("analyze_token")), E.MALFORMED)
    expect(
        "a capture whose operation is not text",
        a.classify(RawCapture(new_capture_id(), "ryo", {"operation": 5}, 200, {}, b"{}", NOW, NOW), NOW),
        E.MALFORMED,
    )
    expect("the catalogue", classify("_catalogue", 200, catalogue_body()), None)
    expect("a bad catalogue", classify("_catalogue", 200, b"[]"), E.MALFORMED)
    expect(
        "a catalogue that cannot be checked",
        classify("_catalogue", 200, catalogue_body(lambda t: t[0]["inputSchema"].update(oneOf=[]))),
        E.CONTRACT_DRIFT,
    )
    who = json.dumps(
        {"key_id": "5ebd7286", "scopes": ["tools:read"], "expires_at": "2026-12-17T02:33:00Z", "rate_limit_per_min": 60, "tools": ["a"]}
    ).encode()
    expect("whoami", classify("_whoami", 200, who), None)
    expect("a bad whoami", classify("_whoami", 200, b'{"key_id":5}'), E.CONTRACT_DRIFT)
    expect("health", classify("_health", 200, b'{"status":"ok","tools":6}'), None)
    expect("health not ok", classify("_health", 200, b'{"status":"down"}'), E.UPSTREAM)

    msg = D.error_body("UPSTREAM_UNAVAILABLE", "Credential verification is temporarily unavailable.")
    expect("401", classify("analyze_token", 401, D.error_body("UNAUTHENTICATED", "Missing credential.")), E.AUTH)
    expect("403", classify("analyze_token", 403, D.error_body("FORBIDDEN", "Scope.")), E.AUTH)
    expect("404 unknown tool", classify("analyze_token", 404, D.error_body("NOT_FOUND", "Unknown tool 'x'.")), E.CONTRACT_DRIFT)
    expect("404 other", classify("analyze_token", 404, D.error_body("NOT_FOUND", "No such asset.")), E.NOT_FOUND)
    expect("404 as html", classify("analyze_token", 404, b"<html>404</html>"), E.NOT_FOUND, message="HTTP 404: HTTP 404")
    expect(
        "a message that merely contains Unknown tool",
        classify("analyze_token", 404, D.error_body("NOT_FOUND", "No such asset. Unknown tool mentioned later.")),
        E.NOT_FOUND,
    )
    expect("418", classify("analyze_token", 418, b""), E.BANNED)
    expect("400", classify("analyze_token", 400, D.error_body("BAD_REQUEST", "Missing symbol.")), E.INVALID_ARGS)
    expect("422", classify("analyze_token", 422, b""), E.INVALID_ARGS)
    expect("408", classify("analyze_token", 408, b""), E.TIMEOUT)
    expect("451", classify("analyze_token", 451, b""), E.GEO_BLOCKED)
    for status in (500, 502, 504):
        expect(f"{status}", classify("analyze_token", status, b"<html>x</html>"), E.UPSTREAM)
    expect(
        "503 with a short wait",
        classify("analyze_token", 503, msg, {"retry-after": "5"}),
        E.UPSTREAM,
        retry_after_s=5.0,
        trace_id="431518c21f1b42a0b810d5bb0a22bdbe",
    )
    expect("a redirect", classify("analyze_token", 302, b"", {"location": "http://x"}), E.UNCLASSIFIED)
    expect(
        "the message says the status and the text",
        classify("analyze_token", 503, msg),
        E.UPSTREAM,
        message="HTTP 503: Credential verification is temporarily unavailable.",
    )
    expect(
        "the trace comes from the body first",
        classify("analyze_token", 503, D.error_body("X", "m", trace_id="aaaaaaaa"), trace="bbbbbbbb"),
        E.UPSTREAM,
        trace_id="aaaaaaaa",
    )
    expect(
        "the trace falls back to the header", classify("analyze_token", 503, b"<html>", trace="bbbbbbbb"), E.UPSTREAM, trace_id="bbbbbbbb"
    )
    expect(
        "the trace falls back to the header when the body has none",
        classify("analyze_token", 503, D.error_body("X", "m", trace_id=None), trace="bbbbbbbb"),
        E.UPSTREAM,
        trace_id="bbbbbbbb",
    )
    rate = D.error_body("RATE_LIMITED", "Rate limit exceeded (6/min for mcp_fanout).", details={"limit": 6, "reset_at": 1793278300})
    epoch_now = datetime.fromtimestamp(1793278240, UTC)
    expect("429 with the header", classify("analyze_token", 429, rate, {"retry-after": "60"}), E.RATE_LIMIT, retry_after_s=60.0)
    expect(
        "429 with a date header",
        classify("analyze_token", 429, b"", {"retry-after": "Thu, 29 Oct 2026 12:00:45 GMT"}),
        E.RATE_LIMIT,
        retry_after_s=45.0,
    )
    expect("429 with only an epoch in the details", classify("analyze_token", 429, rate, now=epoch_now), E.RATE_LIMIT, retry_after_s=60.0)
    expect(
        "429 with an RFC 3339 time in the details",
        classify("analyze_token", 429, D.error_body("R", "m", details={"reset_at": "2026-10-29T12:00:30Z"})),
        E.RATE_LIMIT,
        retry_after_s=30.0,
    )
    expect(
        "429 whose reset has passed",
        classify("analyze_token", 429, rate, now=epoch_now + timedelta(seconds=500)),
        E.RATE_LIMIT,
        retry_after_s=0.0,
    )
    expect(
        "429 with a reset far away",
        classify("analyze_token", 429, D.error_body("R", "m", details={"reset_at": "2099-01-01T00:00:00Z"})),
        E.RATE_LIMIT,
        retry_after_s=3 * 24 * 3600.0,
    )
    expect(
        "429 with a header wins over the body",
        classify("analyze_token", 429, rate, {"retry-after": "7"}, now=epoch_now),
        E.RATE_LIMIT,
        retry_after_s=7.0,
    )
    for label, details in (
        ("none", None),
        ("text", "soon"),
        ("a list", [1]),
        ("an empty object", {}),
        ("a bad time", {"reset_at": "tomorrow"}),
        ("a boolean", {"reset_at": True}),
        ("an epoch with a fraction", {"reset_at": 1.5}),
        ("a negative epoch", {"reset_at": -5}),
        ("an epoch in milliseconds", {"reset_at": 1793278300000}),
    ):
        expect(
            f"429 with details {label}",
            classify("analyze_token", 429, D.error_body("R", "m", details=details)),
            E.RATE_LIMIT,
            retry_after_s=None,
        )
    expect("429 as html", classify("analyze_token", 429, b"<html>"), E.RATE_LIMIT, retry_after_s=None)
    expect(
        "an epoch details value is read as the number it is",
        classify("analyze_token", 429, D.error_body("R", "m", details={"reset_at": 1793278300}), now=epoch_now),
        E.RATE_LIMIT,
        retry_after_s=60.0,
    )
    expect(
        "a rate limit on a 5xx is not a wait",
        classify("analyze_token", 503, D.error_body("R", "m", details={"reset_at": 1793278300}), now=epoch_now),
        E.UPSTREAM,
        retry_after_s=None,
    )

    # --- rate headers -----------------------------------------------------------------------------------------------------------------
    def observed(headers: dict[str, str]) -> Any:
        return a.rate_observation(capture("analyze_token", 200, b"{}", headers))

    obs = observed(
        {
            "x-ratelimit-limit": "60",
            "x-ratelimit-remaining": "59",
            "x-ratelimit-reset": "1793278239",
            "date": "Thu, 29 Oct 2026 12:50:07 GMT",
        }
    )
    if (
        obs is None
        or (obs.limit, obs.remaining) != (60.0, 59.0)
        or obs.reset_at != datetime.fromtimestamp(1793278239, UTC)
        or obs.response_date != datetime(2026, 10, 29, 12, 50, 7, tzinfo=UTC)
    ):
        bad.append(f"rate headers were read wrongly: {obs}")
    if observed({}) is not None:
        bad.append("no rate headers, no observation")
    if observed({"date": "Thu, 29 Oct 2026 12:50:07 GMT"}) is not None:
        bad.append("a date alone is not a rate observation")
    obs = observed({"x-ratelimit-remaining": "3"})
    if obs is None or (obs.limit, obs.remaining, obs.reset_at, obs.response_date) != (None, 3.0, None, None):
        bad.append(f"a single header: {obs}")
    obs = observed({"x-ratelimit-reset": "1793278239"})
    if obs is None or obs.reset_at != datetime.fromtimestamp(1793278239, UTC) or obs.limit is not None:
        bad.append(f"only a reset header: {obs}")
    obs = observed({"x-ratelimit-limit": "lots", "x-ratelimit-remaining": " 7 ", "x-ratelimit-reset": "17.5"})
    if obs is None or obs.limit is not None or obs.remaining != 7.0 or obs.reset_at != datetime.fromtimestamp(17.5, UTC):
        bad.append(f"garbage headers are unknown: {obs}")
    for value in ("1793278239000", "500000000000", "40000000000"):
        obs = observed({"x-ratelimit-remaining": "5", "x-ratelimit-reset": value})
        if obs is None or obs.reset_at is not None:
            bad.append(f"a reset of {value} is not an epoch in seconds")
    obs = observed({"x-ratelimit-remaining": "5", "x-ratelimit-reset": "39999999999"})
    if obs is None or obs.reset_at != datetime.fromtimestamp(39999999999, UTC):
        bad.append("the largest believed reset")
    for value in ("-1", "1e3", "", "NaN", "99999999999999"):
        obs = observed({"x-ratelimit-remaining": value, "x-ratelimit-limit": "60"})
        if obs is None or obs.remaining is not None:
            bad.append(f"remaining {value!r} must be unknown")
    obs = observed({"x-ratelimit-remaining": "5", "date": "not a date"})
    if obs is None or obs.response_date is not None:
        bad.append("an unreadable date is unknown")
    obs = observed({"x-ratelimit-remaining": "5", "date": "Thu, 29 Oct 2026 12:50:07 +0200"})
    if obs is None or obs.response_date != datetime(2026, 10, 29, 10, 50, 7, tzinfo=UTC):
        bad.append(f"a date with an offset is converted to UTC: {obs}")
    obs = observed({"x-ratelimit-remaining": "5", "date": "Thu, 29 Oct 2026 12:50:07 -0000"})
    if obs is None or obs.response_date is not None:
        bad.append("a date with no zone is unknown")
    return bad


def _with(edit: Any) -> bytes:
    res = D.result("analyze_token")
    edit(res["data"])
    return D.rest_body("analyze_token", res)
