"""Checks of the RYO response readers, runnable against the real module or a mutant."""

from __future__ import annotations

import copy
import json
import types
from datetime import UTC, datetime
from typing import Any

from tests import ryo_data as D
from tests.ryo_support import SIX

NOW = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)
DROP = object()


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    E = mod.ErrorClass

    def drift_of(label: str, call: Any, error_class: Any) -> None:
        try:
            call()
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as exc:
            if exc.error_class is not error_class:
                bad.append(f"{label}: classed {exc.error_class.value}, expected {error_class.value} ({exc.message})")
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    def rest_drift(label: str, tool: str, res: dict[str, Any], error_class: Any = None) -> None:
        drift_of(label, lambda: mod.read_rest_success(D.rest_body(tool, res), tool), error_class or E.CONTRACT_DRIFT)

    # --- every tool's good answer is read ----------------------------------------------------------------------------------------------
    for tool in SIX:
        try:
            env = mod.read_rest_success(D.rest_body(tool), tool)
        except Exception as exc:
            bad.append(f"{tool}: a valid answer was refused ({exc})")
            continue
        good = D.result(tool)
        checks = {
            "tool": env.tool == tool,
            "schema": env.schema_version == "1.0",
            "status": env.status == "ok",
            "mode": env.data_mode == "live",
            "as_of": env.as_of == datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC),
            "availability": dict(env.availability) == good["availability"],
            "warnings": env.warnings == (),
            "headline": env.summary["headline"] == good["summary"]["headline"],
            "latency": str(env.latency_ms) == "642",
            "extras": env.extra_keys == (),
            "data keys": sorted(env.data) == sorted(good["data"]),
            "request keys": sorted(env.request) == sorted(good["request"]),
        }
        for name, ok in checks.items():
            if not ok:
                bad.append(f"{tool}: {name} was read wrongly")
    env = mod.read_rest_success(D.rest_body("analyze_token"), "analyze_token")
    if not isinstance(env.data["market"]["price_usd"], mod.jsonnum.Num) or str(env.data["market"]["price_usd"]) != "100.5":
        bad.append("a number in data lost its spelling")
    raw = (
        b'{"tool":"analyze_token","result":'
        + json.dumps(D.result("analyze_token")).encode().replace(b'"price_usd": 100.5', b'"price_usd": 1.0E+2')
        + b"}"
    )
    if str(mod.read_rest_success(raw, "analyze_token").data["market"]["price_usd"]) != "1.0E+2":
        bad.append("an exponent spelling was not kept")
    if mod.read_rest_success(D.rest_body("analyze_token", latency_ms=None), "analyze_token").latency_ms is not None:
        bad.append("a missing latency should be None")
    for status in ("ok", "partial", "unavailable"):
        if mod.read_rest_success(D.rest_body("analyze_token", D.result("analyze_token", status=status)), "analyze_token").status != status:
            bad.append(f"status {status} was not kept")
    for mode in ("live", "mixed", "simulated", "unknown"):
        if (
            mod.read_rest_success(D.rest_body("analyze_token", D.result("analyze_token", data_mode=mode)), "analyze_token").data_mode
            != mode
        ):
            bad.append(f"data mode {mode} was not kept")
    pad = mod.read_rest_success(
        D.rest_body("analyze_token", D.result("analyze_token", warnings=["a limit", "another"], as_of="2026-10-29T14:00:00+02:00")),
        "analyze_token",
    )
    if pad.warnings != ("a limit", "another") or pad.as_of != datetime(2026, 10, 29, 12, 0, tzinfo=UTC):
        bad.append(f"warnings or an offset time were read wrongly: {pad.warnings} {pad.as_of}")

    # --- additions are kept apart, not refused -----------------------------------------------------------------------------------------
    extra = mod.read_rest_success(D.rest_body("analyze_token", D.result("analyze_token", cache_age=5)), "analyze_token")
    if extra.extra_keys != ("cache_age",):
        bad.append(f"an extra member: {extra.extra_keys}")
    traced = mod.read_rest_success(D.rest_body("analyze_token", D.result("analyze_token", trace_id="abc123abc123")), "analyze_token")
    if traced.extra_keys != ():
        bad.append("trace_id is a known optional member and not an extra")
    wrapped = json.dumps({"tool": "analyze_token", "result": D.result("analyze_token"), "latency_ms": 5, "cache": True}).encode()
    if mod.read_rest_success(wrapped, "analyze_token").extra_keys != ("body.cache",):
        bad.append("an extra wrapper member was not noted")
    both = json.dumps({"tool": "analyze_token", "result": D.result("analyze_token", zeta=1, alpha=2), "cache": True}).encode()
    if mod.read_rest_success(both, "analyze_token").extra_keys != ("alpha", "body.cache", "zeta"):
        bad.append("extra members are not listed sorted")
    many = dict.fromkeys(("h", "g", "f", "e", "d", "c", "b", "a"), 1)
    if mod.read_rest_success(D.rest_body("analyze_token", D.result("analyze_token", **many)), "analyze_token").extra_keys != tuple(
        sorted(many)
    ):
        bad.append("eight extra members are not listed sorted")
    if mod.read_envelope(D.result("analyze_token", **many), "analyze_token").extra_keys != tuple(sorted(many)):
        bad.append("eight extra members of a bare envelope are not listed sorted")

    # --- an unreadable body is MALFORMED -------------------------------------------------------------------------------------------------
    for label, raw in (
        ("empty", b""),
        ("html", b"<html>502</html>"),
        ("truncated", D.rest_body("analyze_token")[:-5]),
        ("nan", b'{"tool":"analyze_token","result":{"x":NaN}}'),
        ("duplicate keys", b'{"tool":"analyze_token","tool":"analyze_token","result":{}}'),
        ("a byte order mark", b"\xef\xbb\xbf" + D.rest_body("analyze_token")),
        ("not utf-8", b'{"tool":"\xff"}'),
        ("too deep", b"[" * 100 + b"]" * 100),
    ):
        drift_of(label, lambda r=raw: mod.read_rest_success(r, "analyze_token"), E.MALFORMED)
    drift_of(
        "too large",
        lambda: mod.read_rest_success(b'{"tool":"analyze_token","x":"' + b"a" * (9 * 1024 * 1024) + b'"}', "analyze_token"),
        E.MALFORMED,
    )

    # --- JSON that is not the contract is CONTRACT_DRIFT ---------------------------------------------------------------------------------
    for label, raw in (
        ("a list", b"[]"),
        ("a string", b'"ok"'),
        ("no result", b'{"tool":"analyze_token"}'),
        ("a result that is a list", b'{"tool":"analyze_token","result":[]}'),
        ("a result that is text", b'{"tool":"analyze_token","result":"ok"}'),
    ):
        drift_of(label, lambda r=raw: mod.read_rest_success(r, "analyze_token"), E.CONTRACT_DRIFT)
    drift_of("another tool in the wrapper", lambda: mod.read_rest_success(D.rest_body("scan_market"), "analyze_token"), E.CONTRACT_DRIFT)
    drift_of(
        "no tool in the wrapper",
        lambda: mod.read_rest_success(json.dumps({"result": D.result("analyze_token")}).encode(), "analyze_token"),
        E.CONTRACT_DRIFT,
    )
    drift_of(
        "a numeric latency as text",
        lambda: mod.read_rest_success(
            json.dumps({"tool": "analyze_token", "result": D.result("analyze_token"), "latency_ms": "5"}).encode(), "analyze_token"
        ),
        E.CONTRACT_DRIFT,
    )
    for key in mod.ENVELOPE_KEYS:
        res = D.result("analyze_token")
        res.pop(key)
        rest_drift(f"missing {key}", "analyze_token", res)
    rest_drift("another tool inside", "analyze_token", D.result("analyze_token", tool="scan_market"))
    for version in ("2.0", "1", "", 1.0, None):
        rest_drift(f"schema_version {version!r}", "analyze_token", D.result("analyze_token", schema_version=version))
    for status in ("degraded", "OK", "", None, 1, ["ok"]):
        rest_drift(f"status {status!r}", "analyze_token", D.result("analyze_token", status=status))
    for mode in ("recorded", "LIVE", "", None, 1, ["live"], {}):
        rest_drift(f"data_mode {mode!r}", "analyze_token", D.result("analyze_token", data_mode=mode))
    for value in (None, 5, "yesterday", "2026-10-29T12:00:00", "2026-10-29 12:00:00Z", "2026-13-29T12:00:00Z", ["2026-10-29T12:00:00Z"]):
        rest_drift(f"as_of {value!r}", "analyze_token", D.result("analyze_token", as_of=value))
    for key in ("request", "data"):
        for value in ([], "x", None, 5):
            rest_drift(f"{key} {value!r}", "analyze_token", D.result("analyze_token", **{key: value}))
    for label, summary in (
        ("a summary that is a list", []),
        ("no headline", {"key_points": []}),
        ("a numeric headline", {"headline": 5, "key_points": []}),
        ("no key points", {"headline": "h"}),
        ("key points that are text", {"headline": "h", "key_points": "a"}),
        ("a numeric key point", {"headline": "h", "key_points": [1]}),
    ):
        rest_drift(label, "analyze_token", D.result("analyze_token", summary=summary))
    for label, availability in (
        ("availability as a list", []),
        ("a numeric availability value", {"market_data": 1}),
        ("a null availability value", {"market_data": None}),
    ):
        rest_drift(label, "analyze_token", D.result("analyze_token", availability=availability))
    for label, warnings in (("warnings as text", "none"), ("a numeric warning", [1]), ("warnings as a mapping", {})):
        rest_drift(label, "analyze_token", D.result("analyze_token", warnings=warnings))
    ok = mod.read_envelope(D.result("analyze_token", availability={}, summary={"headline": "", "key_points": []}), "analyze_token")
    if ok.availability != {} or ok.summary["headline"] != "":
        bad.append("an empty availability and an empty headline are valid")
    drift_of("read_envelope on a list", lambda: mod.read_envelope([], "analyze_token"), E.CONTRACT_DRIFT)
    drift_of("read_envelope on a number", lambda: mod.read_envelope(mod.jsonnum.Num("5"), "analyze_token"), E.CONTRACT_DRIFT)
    drift_of("read_envelope on text", lambda: mod.read_envelope("schema_version", "analyze_token"), E.CONTRACT_DRIFT)
    drift_of("read_envelope on nothing", lambda: mod.read_envelope(None, "analyze_token"), E.CONTRACT_DRIFT)
    bare = copy.deepcopy(D.result("analyze_token"))
    if mod.read_envelope(bare, "analyze_token").latency_ms is not None:
        bad.append("an envelope read without its wrapper has no latency")

    # --- MCP --------------------------------------------------------------------------------------------------------------------------------
    mcp = mod.read_mcp_call(D.mcp_text_body("scan_market"), "scan_market")
    if mcp.tool != "scan_market" or mcp.status != "ok" or sorted(mcp.data) != sorted(D.scan_market_data()):
        bad.append("an MCP success was read wrongly")
    if mcp.latency_ms is not None:
        bad.append("an MCP answer has no latency member")
    drift_of("mcp for another tool", lambda: mod.read_mcp_call(D.mcp_text_body("scan_market"), "analyze_token"), E.CONTRACT_DRIFT)

    def mcp_error(text: str, retry_after: str | None = None) -> Any:
        try:
            mod.read_mcp_call(D.mcp_error_body(text), "analyze_token", retry_after=retry_after, now=NOW)
        except mod.ConnectorError as exc:
            return exc
        bad.append(f"an MCP error was accepted: {text!r}")
        return None

    exc = mcp_error("Rate limit exceeded (6/min for mcp_fanout).", "60")
    if exc is None or exc.error_class is not E.RATE_LIMIT or exc.retry_after_s != 60.0:
        bad.append("an MCP rate limit was not read with its wait")
    exc = mcp_error("Rate limit exceeded (6/min for mcp_fanout).")
    if exc is None or exc.error_class is not E.RATE_LIMIT or exc.retry_after_s is not None:
        bad.append("an MCP rate limit with no header has no wait")
    exc = mcp_error("analyze_token requires a 'symbol' (trace_id: 0123456789abcdef0123456789abcdef)")
    if exc is None or exc.error_class is not E.INVALID_ARGS or exc.trace_id != "0123456789abcdef0123456789abcdef":
        bad.append(f"a missing argument over MCP: {exc and (exc.error_class, exc.trace_id)}")
    for text in ("boom (trace_id: ZZZZ12345678)", "boom (trace_id: abc)", "boom (trace_id: )", "boom (trace_id:0123456789abcdef)"):
        exc = mcp_error(text)
        if exc is None or exc.trace_id is not None:
            bad.append(f"{text!r} carries no usable trace id")
    exc = mcp_error("boom (trace_id: 0123456789abcdef-more)")
    if exc is None or exc.trace_id != "0123456789abcdef":
        bad.append("a trace id is the hex run after the label")
    exc = mcp_error("couldn't analyze ZZQXQ right now (trace_id: abcdef0123456789)")
    if exc is None or exc.error_class is not E.NOT_FOUND or exc.trace_id != "abcdef0123456789":
        bad.append("an unknown symbol over MCP")
    exc = mcp_error("Unknown tool: nope")
    if exc is None or exc.error_class is not E.CONTRACT_DRIFT:
        bad.append("an unknown tool over MCP")
    exc = mcp_error("Something new went wrong")
    if exc is None or exc.error_class is not E.UNCLASSIFIED:
        bad.append("an unknown MCP error must be UNCLASSIFIED")
    exc = mcp_error("x" * 5000)
    if exc is None or len(exc.message) != 2000:
        bad.append("a long MCP error text is cut at 2000")
    for text, expected in (
        ("Rate limit exceeded (6/min).", E.RATE_LIMIT),
        ("a Rate limit exceeded", E.UNCLASSIFIED),
        ("Unknown tool x", E.CONTRACT_DRIFT),
        ("xUnknown tool", E.UNCLASSIFIED),
        ("deep_analysis requires a 'symbol'", E.INVALID_ARGS),
        ("foo requires 'symbol'", E.UNCLASSIFIED),
        ("couldn't analyze SOL right now", E.NOT_FOUND),
        ("we couldn't analyze", E.UNCLASSIFIED),
        ("", E.UNCLASSIFIED),
        ("compare_tokens requires an 'intent'", E.INVALID_ARGS),
    ):
        if mod.classify_mcp_text(text) is not expected:
            bad.append(f"classify_mcp_text({text!r}) = {mod.classify_mcp_text(text).value}, expected {expected.value}")

    def rpc_error(code: Any, message: Any, data: Any = None) -> bytes:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return json.dumps({"jsonrpc": "2.0", "id": 3, "error": err}).encode()

    for label, raw, expected, trace in (
        (
            "an unknown tool",
            rpc_error(-32602, "Unknown tool: nope", {"trace_id": "aa11bb22cc33dd44"}),
            E.CONTRACT_DRIFT,
            "aa11bb22cc33dd44",
        ),
        ("an unknown tool with another code", rpc_error(-32601, "Unknown tool: nope"), E.UPSTREAM, None),
        ("a server error", rpc_error(-32603, "Internal error", {"trace_id": "aa11bb22cc33dd44"}), E.UPSTREAM, "aa11bb22cc33dd44"),
        ("a trace in the text", rpc_error(-32603, "boom (trace_id: 0011223344556677)"), E.UPSTREAM, "0011223344556677"),
        ("a bad trace in data", rpc_error(-32603, "boom", {"trace_id": 5}), E.UPSTREAM, None),
        ("a message that is a different problem", rpc_error(-32602, "Invalid params"), E.UPSTREAM, None),
        ("a long RPC message", rpc_error(-32603, "z" * 5000), E.UPSTREAM, None),
    ):
        try:
            mod.read_mcp_call(raw, "analyze_token")
            bad.append(f"{label}: accepted")
        except mod.ConnectorError as err:
            if err.error_class is not expected or err.trace_id != trace:
                bad.append(f"{label}: {err.error_class.value}, trace {err.trace_id}")
            if len(err.message) > 2000 + len(err.error_class.value) + 2:
                bad.append(f"{label}: the message was not cut")
    for label, raw in (
        ("a non-JSON body", b"nope"),
        ("a body that is a list", b"[]"),
        ("another protocol version", D.mcp_text_body("analyze_token").replace(b'"jsonrpc":"2.0"', b'"jsonrpc":"1.0"')),
        ("no protocol version", D.mcp_text_body("analyze_token").replace(b'"jsonrpc":"2.0",', b"")),
        ("no result and no error", json.dumps({"jsonrpc": "2.0", "id": 1}).encode()),
        ("an error that is text", json.dumps({"jsonrpc": "2.0", "id": 1, "error": "x"}).encode()),
        ("an error without a message", json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": 1}}).encode()),
        ("a result without isError", json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": []}}).encode()),
        (
            "an isError that is text",
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "x"}], "isError": "no"}}).encode(),
        ),
        ("empty content", json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [], "isError": False}}).encode()),
        ("content that is not a list", json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": "x", "isError": False}}).encode()),
        (
            "an image block",
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "image", "text": "x"}], "isError": False}}).encode(),
        ),
        ("content that is an object", json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": {"a": 1}, "isError": False}}).encode()),
        ("content that is a number", json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": 5, "isError": False}}).encode()),
        (
            "a block with no text",
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text"}], "isError": False}}).encode(),
        ),
        ("a block that is text", json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": ["x"], "isError": False}}).encode()),
    ):
        exp = E.MALFORMED if label == "a non-JSON body" else E.CONTRACT_DRIFT
        drift_of(label, lambda r=raw: mod.read_mcp_call(r, "analyze_token"), exp)
    drift_of(
        "text that is not JSON",
        lambda: mod.read_mcp_call(
            json.dumps(
                {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "plain words"}], "isError": False}}
            ).encode(),
            "analyze_token",
        ),
        E.MALFORMED,
    )

    # --- REST error bodies -----------------------------------------------------------------------------------------------------------------------
    body = mod.read_error_body(D.error_body("UPSTREAM_UNAVAILABLE", "Credential verification is temporarily unavailable."))
    if (
        body is None
        or body.code != "UPSTREAM_UNAVAILABLE"
        or body.message != "Credential verification is temporarily unavailable."
        or body.trace_id != "431518c21f1b42a0b810d5bb0a22bdbe"
    ):
        bad.append(f"an error body was read wrongly: {body}")
    if mod.read_error_body(D.error_body("X", "m", trace_id=None)).trace_id is not None:  # type: ignore[union-attr]
        bad.append("a null trace id should stay None")
    if mod.read_error_body(D.error_body("X", "m", trace_id="not a trace")).trace_id is not None:  # type: ignore[union-attr]
        bad.append("a trace id that is not hex is dropped")
    if mod.read_error_body(D.error_body("X", "m", trace_id="0123456789abcdef-zzz")).trace_id is not None:  # type: ignore[union-attr]
        bad.append("a trace id must be hex from end to end")
    if mod.read_error_body(D.error_body("X", "m", details={"limit": 6})).details is None:  # type: ignore[union-attr]
        bad.append("details are kept")
    if mod.read_error_body(json.dumps({"message": "m"}).encode()).code is not None:  # type: ignore[union-attr]
        bad.append("a missing code is None")
    if mod.read_error_body(json.dumps({"code": 5, "message": "m"}).encode()).code is not None:  # type: ignore[union-attr]
        bad.append("a numeric code is not a code")
    if len(mod.read_error_body(D.error_body("X", "y" * 5000)).message) != 2000:  # type: ignore[union-attr]
        bad.append("a long error message is cut at 2000")
    for label, raw in (
        ("html", b"<html>Bad Gateway</html>"),
        ("empty", b""),
        ("a list", b"[]"),
        ("no message", json.dumps({"code": "X"}).encode()),
        ("a numeric message", json.dumps({"message": 5}).encode()),
    ):
        if mod.read_error_body(raw) is not None:
            bad.append(f"{label} is not an error body")

    # --- whoami and health ---------------------------------------------------------------------------------------------------------------------------
    def whoami_body(**overrides: Any) -> bytes:
        body: dict[str, Any] = {
            "key_id": "5ebd7286",
            "label": "batch",
            "scopes": ["tools:read"],
            "expires_at": "2026-12-17T02:33:00Z",
            "rate_limit_per_min": 60,
            "rate_limit_remaining": 59,
            "call_allowance": {"call_limit": None, "allowed": True},
            "tools": ["market_overview", "scan_market"],
        }
        body.update(overrides)
        return json.dumps({k: v for k, v in body.items() if v is not DROP}).encode()

    who = mod.read_whoami(whoami_body())
    if (who.key_id, who.label, who.scopes, who.tools, who.call_allowed) != (
        "5ebd7286",
        "batch",
        ("tools:read",),
        ("market_overview", "scan_market"),
        True,
    ):
        bad.append(f"whoami was read wrongly: {who}")
    if (
        who.expires_at != datetime(2026, 12, 17, 2, 33, tzinfo=UTC)
        or str(who.rate_limit_per_min) != "60"
        or str(who.rate_limit_remaining) != "59"
    ):
        bad.append("whoami's time or limits were read wrongly")
    who = mod.read_whoami(whoami_body(label=DROP, rate_limit_remaining=DROP, call_allowance=DROP))
    if who.label is not None or who.rate_limit_remaining is not None or who.call_allowed is not None:
        bad.append("optional whoami members should be None")
    who = mod.read_whoami(whoami_body(label=5, call_allowance={"allowed": "yes"}))
    if who.label is not None or who.call_allowed is not None:
        bad.append("a label that is not text and an allowed that is not a flag are unknown")
    # what is read cannot be changed afterwards
    for what, read, member in (
        ("an envelope", mod.read_rest_success(D.rest_body("analyze_token", D.result("analyze_token")), "analyze_token"), "status"),
        ("an error body", mod.read_error_body(D.error_body("X", "m")), "code"),
        ("a key status", who, "key_id"),
    ):
        try:
            setattr(read, member, "other")
            bad.append(f"{what} can be changed after it is read")
        except AttributeError:
            pass
    if mod.read_whoami(whoami_body(call_allowance={"allowed": False})).call_allowed is not False:
        bad.append("allowed false is kept")
    # the call allowance's remainder, in the two shapes RYO has sent: no limit (6 Nov) and a monthly limit with the key not allowed (9 Nov)
    nov6 = {
        "call_limit": None,
        "quota_period": "monthly",
        "used": 99,
        "remaining": None,
        "resets_at": "2026-12-01T00:00:00Z",
        "allowed": True,
    }
    nov9 = {
        "call_limit": 200,
        "quota_period": "monthly",
        "used": 0,
        "remaining": 200,
        "resets_at": "2026-12-01T00:00:00Z",
        "allowed": False,
    }
    for label, allowance, want in (
        ("no limit", nov6, (True, None)),
        ("a limit, not allowed", nov9, (False, 200)),
        ("none left", {"remaining": 0, "allowed": True}, (True, 0)),
        ("one left", {"remaining": 1}, (None, 1)),
        ("a whole number written with a point", {"remaining": 3.0}, (None, 3)),
        ("no allowance", DROP, (None, None)),
        ("an allowance that is not an object", "lots", (None, None)),
        ("a remainder that is text", {"remaining": "200"}, (None, None)),
    ):
        got = mod.read_whoami(whoami_body(call_allowance=allowance))
        if (got.call_allowed, got.call_remaining) != want:
            bad.append(f"the allowance ({label}) was read as {(got.call_allowed, got.call_remaining)}, not {want}")
    for label, remaining in (("a fraction", 2.5), ("a negative count", -1)):
        drift_of(
            f"a remaining call count that is {label}",
            lambda r=remaining: mod.read_whoami(whoami_body(call_allowance={"remaining": r})),
            E.CONTRACT_DRIFT,
        )
    for label, raw, error_class in (
        ("whoami html", b"<html>", E.MALFORMED),
        ("whoami a list", b"[]", E.CONTRACT_DRIFT),
        ("no key id", whoami_body(key_id=DROP), E.CONTRACT_DRIFT),
        ("an empty key id", whoami_body(key_id=""), E.CONTRACT_DRIFT),
        ("a numeric key id", whoami_body(key_id=5), E.CONTRACT_DRIFT),
        ("scopes as text", whoami_body(scopes="tools:read"), E.CONTRACT_DRIFT),
        ("a numeric scope", whoami_body(scopes=[1]), E.CONTRACT_DRIFT),
        ("tools as text", whoami_body(tools="all"), E.CONTRACT_DRIFT),
        ("no tools", whoami_body(tools=DROP), E.CONTRACT_DRIFT),
        ("no scopes", whoami_body(scopes=DROP), E.CONTRACT_DRIFT),
        ("no expiry", whoami_body(expires_at=DROP), E.CONTRACT_DRIFT),
        ("a numeric expiry", whoami_body(expires_at=5), E.CONTRACT_DRIFT),
        ("a naive expiry", whoami_body(expires_at="2026-12-17T02:33:00"), E.CONTRACT_DRIFT),
        ("a text limit", whoami_body(rate_limit_per_min="60"), E.CONTRACT_DRIFT),
        ("no limit", whoami_body(rate_limit_per_min=DROP), E.CONTRACT_DRIFT),
        ("a text remaining count", whoami_body(rate_limit_remaining="59"), E.CONTRACT_DRIFT),
    ):
        drift_of(label, lambda r=raw: mod.read_whoami(r), error_class)
    health = mod.read_health(json.dumps({"status": "ok", "protocol_version": "2024-11-05", "server": "ryo-chan", "tools": 6}).encode())
    if health["server"] != "ryo-chan":
        bad.append("health was read wrongly")
    drift_of("health not ok", lambda: mod.read_health(b'{"status":"degraded"}'), E.UPSTREAM)
    drift_of("health without a status", lambda: mod.read_health(b'{"server":"x"}'), E.CONTRACT_DRIFT)
    drift_of("health with a numeric status", lambda: mod.read_health(b'{"status":1}'), E.CONTRACT_DRIFT)
    drift_of("health a list", lambda: mod.read_health(b"[]"), E.CONTRACT_DRIFT)
    drift_of("health html", lambda: mod.read_health(b"<html>"), E.MALFORMED)
    return bad
