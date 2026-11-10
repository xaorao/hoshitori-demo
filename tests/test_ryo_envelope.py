"""The RYO response readers: scenarios, golden tests on the live recordings, and mutants."""

from __future__ import annotations

import pytest

from hoshitori import ryo_envelope
from tests.mutation import mutant
from tests.ryo_envelope_checks import failures
from tests.ryo_support import live_fixture


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_envelope) == []


@pytest.mark.parametrize(
    ("prefix", "tool"),
    [
        ("call_analyze_token", "analyze_token"),
        ("call_deep_analysis", "deep_analysis"),
        ("call_scan_market", "scan_market"),
        ("call_monitor_market_sentiment_shift", "monitor_market_sentiment_shift"),
    ],
)
def test_the_live_recordings_are_read(prefix: str, tool: str) -> None:
    live = live_fixture(prefix)
    env = ryo_envelope.read_rest_success(live["raw"], tool)
    assert env.status in ("ok", "partial")
    assert env.data_mode == "live"
    assert env.schema_version == "1.0"
    assert env.extra_keys == ()
    assert env.latency_ms is not None


@pytest.mark.parametrize(
    ("prefix", "code", "message_part"),
    [("call_market_overview", "UPSTREAM_UNAVAILABLE", "Credential verification"), ("invalid_unknown_tool", "NOT_FOUND", "Unknown tool")],
)
def test_the_live_error_bodies_are_read(prefix: str, code: str, message_part: str) -> None:
    live = live_fixture(prefix)
    body = ryo_envelope.read_error_body(live["raw"])
    assert body is not None
    assert body.code == code
    assert message_part in body.message
    assert body.trace_id == live["response"]["headers"]["x-trace-id"]


def test_the_live_whoami_and_health_are_read() -> None:
    who = ryo_envelope.read_whoami(live_fixture("whoami.cdd0")["raw"])
    assert who.scopes == ("tools:read",)
    assert len(who.tools) == 6
    assert who.call_allowed is True
    assert ryo_envelope.read_health(live_fixture("health.")["raw"])["server"] == "ryo-chan"


MUTATIONS: dict[str, tuple[str, str]] = {
    "the schema version is not checked": ('    if obj["schema_version"] != SCHEMA_VERSION:', "    if False:"),
    "any schema version above is accepted": ('obj["schema_version"] != SCHEMA_VERSION', 'str(obj["schema_version"]) < SCHEMA_VERSION'),
    "the tool is not checked inside": (
        '    if obj["tool"] != tool:\n        raise _drift(f"answers for tool {obj[\'tool\']!r}, not {tool!r}")\n',
        "",
    ),
    "the wrapper tool is not checked": ('    if body.get("tool") != tool:', "    if False:"),
    "the status is not checked": ('    if not (isinstance(obj["status"], str) and obj["status"] in STATUSES):', "    if False:"),
    "an unhashable status crashes": ('isinstance(obj["status"], str) and obj["status"] in STATUSES', 'obj["status"] in STATUSES'),
    "a fourth status is accepted": (
        'STATUSES: Final = frozenset({"ok", "partial", "unavailable"})',
        'STATUSES: Final = frozenset({"ok", "partial", "unavailable", "degraded"})',
    ),
    "the data mode is not checked": ('    if not (isinstance(obj["data_mode"], str) and obj["data_mode"] in DATA_MODES):', "    if False:"),
    "an unhashable data mode crashes": (
        'isinstance(obj["data_mode"], str) and obj["data_mode"] in DATA_MODES',
        'obj["data_mode"] in DATA_MODES',
    ),
    "a recorded data mode is accepted": (
        'DATA_MODES: Final = frozenset({"live", "mixed", "simulated", "unknown"})',
        'DATA_MODES: Final = frozenset({"live", "mixed", "simulated", "unknown", "recorded"})',
    ),
    "a missing member is not noticed": ('    if missing:\n        raise _drift(f"lacks {missing}")\n', ""),
    "the envelope need not be an object": ('    if not isinstance(obj, dict):\n        raise _drift("is not an object")\n', ""),
    "as_of need not be text": ('    if not isinstance(as_of, str):\n        raise _drift("has an as_of that is not text")\n', ""),
    "as_of is not parsed": ("        moment = parse_rfc3339(as_of)", "        moment = datetime.now()"),
    "a bad as_of is accepted": (
        '    except TimeError as exc:\n        raise _drift(f"has an as_of that is not',
        '    except ZeroDivisionError as exc:\n        raise _drift(f"has an as_of that is not',
    ),
    "request and data need not be objects": (
        '    if not isinstance(obj["request"], dict) or not isinstance(obj["data"], dict):',
        "    if False:",
    ),
    "data need not be an object": (
        'not isinstance(obj["request"], dict) or not isinstance(obj["data"], dict)',
        'not isinstance(obj["request"], dict)',
    ),
    "request need not be an object": (
        'not isinstance(obj["request"], dict) or not isinstance(obj["data"], dict)',
        'not isinstance(obj["data"], dict)',
    ),
    "the summary is not checked": (
        '    if not (isinstance(summary, dict) and isinstance(summary.get("headline"), str) and _text_list(summary.get("key_points"))):',
        "    if False:",
    ),
    "the headline need not be text": ('isinstance(summary.get("headline"), str) and ', ""),
    "the key points need not be text": (' and _text_list(summary.get("key_points"))):', "):"),
    "availability is not checked": (
        "    if not (isinstance(availability, dict) and all(isinstance(v, str) for v in availability.values())):",
        "    if False:",
    ),
    "availability values need not be words": (" and all(isinstance(v, str) for v in availability.values())):", "):"),
    "warnings are not checked": ("    if not _text_list(warnings):", "    if False:"),
    "a list of anything is text": (
        "    return isinstance(value, list) and all(isinstance(v, str) for v in value)",
        "    return isinstance(value, list)",
    ),
    "extras are not recorded": ("    extra = tuple(sorted(set(obj) - set(ENVELOPE_KEYS) - OPTIONAL_ENVELOPE_KEYS))", "    extra = ()"),
    "trace_id counts as an extra": (
        'OPTIONAL_ENVELOPE_KEYS: Final = frozenset({"trace_id"})',
        "OPTIONAL_ENVELOPE_KEYS: Final = frozenset()",
    ),
    "extras are not sorted": (
        "    extra = tuple(sorted(set(obj) - set(ENVELOPE_KEYS) - OPTIONAL_ENVELOPE_KEYS))",
        "    extra = tuple(set(obj) - set(ENVELOPE_KEYS) - OPTIONAL_ENVELOPE_KEYS)",
    ),
    "wrapper extras are lost": (
        '    extra = tuple(sorted(set(envelope.extra_keys) | {f"body.{key}" for key in body if key not in ("tool", "result", "latency_ms")}))',
        "    extra = envelope.extra_keys",
    ),
    "wrapper extras are not prefixed": ('{f"body.{key}" for key in body', "{key for key in body"),
    "result extras are lost": ('set(envelope.extra_keys) | {f"body.{key}"', 'set() | {f"body.{key}"'),
    "the result need not be an object": (
        '    if not isinstance(body, dict) or not isinstance(body.get("result"), dict):',
        "    if not isinstance(body, dict):",
    ),
    "the wrapper need not be an object": (
        '    if not isinstance(body, dict) or not isinstance(body.get("result"), dict):',
        '    if not isinstance(body.get("result"), dict):',
    ),
    "latency need not be a number": ("    if latency is not None and not isinstance(latency, jsonnum.Num):", "    if False:"),
    "latency is dropped": (
        '    return Envelope(**{**envelope.__dict__, "latency_ms": latency, "extra_keys": extra})',
        '    return Envelope(**{**envelope.__dict__, "extra_keys": extra})',
    ),
    "unreadable JSON is drift": (
        'raise ConnectorError(ErrorClass.MALFORMED, f"RYO\'s response is not readable JSON ({exc.code})") from exc',
        'raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"RYO\'s response is not readable JSON ({exc.code})") from exc',
    ),
    "the standard parser reads the body": (
        "        return jsonnum.parse(raw)\n    except jsonnum.JsonError as exc:\n        raise ConnectorError(ErrorClass.MALFORMED",
        "        return __import__('json').loads(raw)\n    except jsonnum.JsonError as exc:\n        raise ConnectorError(ErrorClass.MALFORMED",
    ),
    "rate limits are not recognised": ('    (re.compile(r"Rate limit exceeded\\b"), ErrorClass.RATE_LIMIT),\n', ""),
    "rate limits are not anchored": (
        '(re.compile(r"Rate limit exceeded\\b"), ErrorClass.RATE_LIMIT)',
        '(re.compile(r".*Rate limit exceeded\\b"), ErrorClass.RATE_LIMIT)',
    ),
    "missing arguments are not recognised": ("    (re.compile(r\"[a-z_]+ requires an? '[a-z_]+'\"), ErrorClass.INVALID_ARGS),\n", ""),
    "missing arguments are matched loosely": ("r\"[a-z_]+ requires an? '[a-z_]+'\"", 'r"[a-z_]+ requires"'),
    "an unknown tool is not recognised": ('    (re.compile(r"Unknown tool\\b"), ErrorClass.CONTRACT_DRIFT),\n', ""),
    "an unknown symbol is not recognised": ('    (re.compile(r"couldn\'t analyze\\b"), ErrorClass.NOT_FOUND),\n', ""),
    "an unknown symbol is an upstream failure": (
        '(re.compile(r"couldn\'t analyze\\b"), ErrorClass.NOT_FOUND)',
        '(re.compile(r"couldn\'t analyze\\b"), ErrorClass.UPSTREAM)',
    ),
    "an unmatched text is an upstream failure": (
        "    return ErrorClass.UNCLASSIFIED\n\n\ndef _trace_in",
        "    return ErrorClass.UPSTREAM\n\n\ndef _trace_in",
    ),
    "patterns search instead of match": ("        if pattern.match(text):", "        if pattern.search(text):"),
    "the trace id is not read from text": (
        '    match = re.search(r"trace_id: ([0-9a-fA-F]{8,64})", text)\n    return match.group(1) if match else None',
        "    return None",
    ),
    "the trace pattern is too loose": ('r"trace_id: ([0-9a-fA-F]{8,64})"', 'r"trace_id: (\\w+)"'),
    "the JSON-RPC version is not checked": (
        '    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":',
        "    if not isinstance(body, dict):",
    ),
    "an MCP body need not be an object": (
        '    if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":',
        '    if body.get("jsonrpc") != "2.0":',
    ),
    "a JSON-RPC error without a message is accepted": (
        '        if not isinstance(error, dict) or not isinstance(error.get("message"), str):\n            raise _drift("has a JSON-RPC error without a message")\n',
        "",
    ),
    "an RPC error is always an upstream failure": (
        '        if isinstance(code, jsonnum.Num) and code.text == "-32602" and error["message"].startswith("Unknown tool"):',
        "        if False:",
    ),
    "the RPC code is not checked": ('code.text == "-32602" and ', ""),
    "the RPC message is not checked": (' and error["message"].startswith("Unknown tool")', ""),
    "the RPC trace id is not taken from data": (
        'trace = data.get("trace_id") if isinstance(data, dict) and isinstance(data.get("trace_id"), str) else _trace_in(error["message"])',
        'trace = _trace_in(error["message"])',
    ),
    "the RPC trace id is not taken from text": ('else _trace_in(error["message"])', "else None"),
    "the RPC trace id need not be text": ('isinstance(data, dict) and isinstance(data.get("trace_id"), str)', "isinstance(data, dict)"),
    "the result is not checked": ('    if not isinstance(result, dict) or not isinstance(result.get("isError"), bool):', "    if False:"),
    "isError need not be a flag": ('or not isinstance(result.get("isError"), bool)', ""),
    "the content is not checked": (
        '    if not (isinstance(first, dict) and first.get("type") == "text" and isinstance(first.get("text"), str)):',
        "    if False:",
    ),
    "the content type is not checked": ('first.get("type") == "text" and ', ""),
    "the content text need not be text": (' and isinstance(first.get("text"), str)):', "):"),
    "the content need not be a block": ("isinstance(first, dict) and first.get", "first.get"),
    "an empty content list is read": (
        "    first = content[0] if isinstance(content, list) and content else None",
        "    first = content[0] if isinstance(content, list) else None",
    ),
    "a non-list content is read": (
        "    first = content[0] if isinstance(content, list) and content else None",
        "    first = content[0] if content else None",
    ),
    "an MCP error is read as a success": ('    if result["isError"]:', "    if False:"),
    "an MCP error text is not cut": (
        "raise ConnectorError(error_class, text[:MAX_TEXT], retry_after_s=wait, trace_id=_trace_in(text))",
        "raise ConnectorError(error_class, text, retry_after_s=wait, trace_id=_trace_in(text))",
    ),
    "an RPC error text is not cut": (
        'raise ConnectorError(ErrorClass.UPSTREAM, error["message"][:MAX_TEXT], trace_id=trace)',
        'raise ConnectorError(ErrorClass.UPSTREAM, error["message"], trace_id=trace)',
    ),
    "the MCP wait is ignored": ("        wait = parse_retry_after(retry_after, now) if now is not None else None", "        wait = None"),
    "the MCP text is not parsed once": ("    return read_envelope(load(text.encode()), tool)", "    return read_envelope(text, tool)"),
    "the error body need not have a message": (
        '    if not isinstance(body, dict) or not isinstance(body.get("message"), str):\n        return None',
        "    if not isinstance(body, dict):\n        return None",
    ),
    "the error body need not be an object": (
        '    if not isinstance(body, dict) or not isinstance(body.get("message"), str):\n        return None',
        '    if not isinstance(body.get("message"), str):\n        return None',
    ),
    "an unreadable error body is raised": (
        "    except jsonnum.JsonError:\n        return None",
        "    except jsonnum.JsonError as exc:\n        raise exc",
    ),
    "the error code need not be text": ("code=code if isinstance(code, str) else None", "code=code"),
    "the error trace id need not be hex": (
        "trace_id=trace if isinstance(trace, str) and TRACE_ID.fullmatch(trace) else None",
        "trace_id=trace if isinstance(trace, str) else None",
    ),
    "the error trace id may be matched in part": ("TRACE_ID.fullmatch(trace)", "TRACE_ID.match(trace)"),
    "the error message is not cut": ('message=body["message"][:MAX_TEXT]', 'message=body["message"]'),
    "the error details are dropped": ('details=body.get("details")', "details=None"),
    "whoami need not be an object": (
        '    body = load(raw)\n    if not isinstance(body, dict):\n        raise _drift("is not an object")\n    key_id, scopes, tools',
        "    body = load(raw)\n    key_id, scopes, tools",
    ),
    "whoami need not have a key id": ('    if not isinstance(key_id, str) or not key_id:\n        raise _drift("has no key id")\n', ""),
    "an empty key id is accepted": ("    if not isinstance(key_id, str) or not key_id:", "    if not isinstance(key_id, str):"),
    "scopes and tools need not be text lists": ("    if not (_text_list(scopes) and _text_list(tools)):", "    if False:"),
    "tools need not be a text list": ("    if not (_text_list(scopes) and _text_list(tools)):", "    if not _text_list(scopes):"),
    "scopes need not be a text list": ("    if not (_text_list(scopes) and _text_list(tools)):", "    if not _text_list(tools):"),
    "whoami need not have an expiry": ('    if not isinstance(expires, str):\n        raise _drift("has no expiry time")\n', ""),
    "the expiry is not parsed": ("        expires_at = parse_rfc3339(expires)", "        expires_at = datetime.now()"),
    "a bad expiry is accepted": (
        '    except TimeError as exc:\n        raise _drift(f"has an expiry that is not',
        '    except ZeroDivisionError as exc:\n        raise _drift(f"has an expiry that is not',
    ),
    "the limit need not be a number": (
        '    if not isinstance(per_minute, jsonnum.Num):\n        raise _drift("has a per-minute limit that is not a number")\n',
        "",
    ),
    "the remaining count need not be a number": (
        '    if remaining is not None and not isinstance(remaining, jsonnum.Num):\n        raise _drift("has a remaining count that is not a number")\n',
        "",
    ),
    "a label need not be text": ("label=label if isinstance(label, str) else None", "label=label"),
    "allowed need not be a flag": ("call_allowed=allowed if isinstance(allowed, bool) else None", "call_allowed=allowed"),
    "the allowance is ignored": ('    allowed = allowance.get("allowed") if isinstance(allowance, dict) else None', "    allowed = None"),
    "health need not be an object": (
        '    if not isinstance(body, dict) or not isinstance(body.get("status"), str):\n        raise _drift("has no status")',
        '    if not isinstance(body.get("status"), str):\n        raise _drift("has no status")',
    ),
    "health need not have a status": (
        '    if not isinstance(body, dict) or not isinstance(body.get("status"), str):',
        "    if not isinstance(body, dict):",
    ),
    "health is always ok": ('    if body["status"] != "ok":', "    if False:"),
    "health not ok is drift": (
        "raise ConnectorError(ErrorClass.UPSTREAM, f\"RYO's health reports",
        "raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f\"RYO's health reports",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.ryo_envelope", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"


EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    from tests.mutation_sweep import survivors

    alive, total = survivors("hoshitori.ryo_envelope", "tests.ryo_envelope_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
