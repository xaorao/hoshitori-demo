"""The RYO adapter: scenarios, the live recordings classified, and mutants."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hoshitori import ryo, ryo_catalogue
from hoshitori.capture import RawCapture, new_capture_id
from hoshitori.ryo_pin import PIN
from tests.mutation import mutant
from tests.ryo_checks import failures
from tests.ryo_support import live_fixture

NOW = datetime(2026, 10, 29, 13, 0, 0, tzinfo=UTC)


def test_real_module_passes_every_check() -> None:
    assert failures(ryo) == []


def _live_capture(fixture: dict, operation: str) -> RawCapture:  # type: ignore[type-arg]
    response = fixture["response"]
    headers = {k.lower(): v for k, v in response["headers"].items() if "auth" not in k.lower()}
    return RawCapture(
        capture_id=new_capture_id(),
        source="ryo",
        request={"operation": operation, "surface": "rest"},
        status=response["status"],
        headers=headers,
        body=fixture["raw"],
        t_send=NOW,
        received_at=NOW,
        trace_id=response["headers"].get("x-trace-id"),
    )


@pytest.mark.parametrize(
    ("prefix", "tool"),
    [
        ("call_analyze_token", "analyze_token"),
        ("call_deep_analysis", "deep_analysis"),
        ("call_scan_market", "scan_market"),
        ("call_monitor_market_sentiment_shift", "monitor_market_sentiment_shift"),
    ],
)
def test_the_live_answers_are_usable_evidence(prefix: str, tool: str) -> None:
    adapter = ryo.RyoAdapter(ryo_catalogue.load_pin(PIN))
    capture = _live_capture(live_fixture(prefix), tool)
    assert adapter.classify(capture, NOW) is None
    observation = adapter.rate_observation(capture)
    assert observation is not None and observation.limit == 60.0 and observation.remaining is not None
    assert observation.reset_at is not None and observation.response_date is not None


@pytest.mark.parametrize(
    ("prefix", "tool", "expected"),
    [
        ("call_market_overview", "market_overview", "UPSTREAM"),
        ("call_compare_tokens_valid", "compare_tokens", "UPSTREAM"),
        ("invalid_unknown_tool", "does_not_exist", "CONTRACT_DRIFT"),
        ("invalid_wrong_type", "analyze_token", "UPSTREAM"),
    ],
)
def test_the_live_errors_are_classified(prefix: str, tool: str, expected: str) -> None:
    adapter = ryo.RyoAdapter(ryo_catalogue.load_pin(PIN))
    error = adapter.classify(_live_capture(live_fixture(prefix), tool), NOW)
    assert error is not None and error.error_class.value == expected
    assert error.trace_id is not None


def test_the_real_comparison_is_classified_as_a_success() -> None:
    adapter = ryo.RyoAdapter(ryo_catalogue.load_pin(PIN))
    assert adapter.classify(_live_capture(live_fixture("call_compare_tokens_ok"), "compare_tokens"), NOW) is None


def test_the_live_catalogue_and_identity_are_usable() -> None:
    adapter = ryo.RyoAdapter(ryo_catalogue.load_pin(PIN))
    assert adapter.classify(_live_capture(live_fixture("tools."), "_catalogue"), NOW) is None
    assert adapter.classify(_live_capture(live_fixture("whoami.cdd0"), "_whoami"), NOW) is None
    assert adapter.classify(_live_capture(live_fixture("health."), "_health"), NOW) is None


MUTATIONS: dict[str, tuple[str, str]] = {
    # --- configuration -------------------------------------------------------------------------------------------------------------
    "a cap below RYO's timeout is allowed": (
        "        if self.attempt_cap_s <= RYO_SERVER_TIMEOUT_S and not self.testing:",
        "        if False:",
    ),
    "a cap equal to RYO's timeout is allowed": (
        "        if self.attempt_cap_s <= RYO_SERVER_TIMEOUT_S and not self.testing:",
        "        if self.attempt_cap_s < RYO_SERVER_TIMEOUT_S and not self.testing:",
    ),
    "testing is never allowed": ("RYO_SERVER_TIMEOUT_S and not self.testing:", "RYO_SERVER_TIMEOUT_S:"),
    "a zero minimum is allowed": (
        "        if not 0 < self.min_attempt_s <= self.attempt_cap_s:",
        "        if not self.min_attempt_s <= self.attempt_cap_s:",
    ),
    "a minimum above the cap is allowed": (
        "        if not 0 < self.min_attempt_s <= self.attempt_cap_s:",
        "        if not 0 < self.min_attempt_s:",
    ),
    "a minimum equal to the cap is refused": (
        "        if not 0 < self.min_attempt_s <= self.attempt_cap_s:",
        "        if not 0 < self.min_attempt_s < self.attempt_cap_s:",
    ),
    "a zero top_n limit is allowed": ("        if self.max_top_n < 1:", "        if self.max_top_n < 0:"),
    "the default cap is short": ("    attempt_cap_s: float = 170.0", "    attempt_cap_s: float = 171.0"),
    "the default minimum is different": ("    min_attempt_s: float = 30.0", "    min_attempt_s: float = 31.0"),
    "the default top_n limit is different": ("    max_top_n: int = 50  #", "    max_top_n: int = 51  #"),
    # --- operations and drift ------------------------------------------------------------------------------------------------------
    "the meta operations are missing": ("        return frozenset(tools | META_OPERATIONS.keys())", "        return frozenset(tools)"),
    "a blocked tool stays": (
        "name in SPECS and name in self._pinned.tools and name not in self._blocked",
        "name in SPECS and name in self._pinned.tools",
    ),
    "an unknown tool is callable": (
        "name in SPECS and name in self._pinned.tools and name not in self._blocked",
        "name in self._pinned.tools and name not in self._blocked",
    ),
    "only the pin decides what is callable": (
        "name in SPECS and name in self._pinned.tools and name not in self._blocked",
        "name in SPECS and name in self._pinned.tools",
    ),
    "drift is not kept": ("        self._drift = drift\n", ""),
    "the live catalogue is not kept": ("        self._live = live\n        self._drift = drift", "        self._drift = drift"),
    "drift is taken against the last catalogue": (
        "        drift = tuple(diff(self._pinned, live))",
        "        drift = tuple(diff(self._live, live))",
    ),
    "nothing is blocked": ("        self._blocked = frozenset(d.tool for d in drift if d.blocks)", "        self._blocked = frozenset()"),
    "everything that changed is blocked": (
        "        self._blocked = frozenset(d.tool for d in drift if d.blocks)",
        "        self._blocked = frozenset(d.tool for d in drift)",
    ),
    "the provider name is fixed": (
        "        self.provider = provider\n        self.config",
        "        self.provider = PROVIDER\n        self.config",
    ),
    "the default configuration is not used": (
        "        self.config = config or RyoConfig()",
        "        self.config = config or RyoConfig(attempt_cap_s=200.0)",
    ),
    # --- arguments -----------------------------------------------------------------------------------------------------------------
    "meta operations accept arguments": (
        '        if operation in META_OPERATIONS:\n            if args:\n                raise _invalid(f"{operation} takes no arguments")\n            return {}',
        "        if operation in META_OPERATIONS:\n            return {}",
    ),
    "an unknown operation is passed": (
        '        if spec is None:\n            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"{operation} is not in the live catalogue")\n        out = check_arguments(spec, args)',
        "        out = check_arguments(spec, args) if spec is not None else dict(args)",
    ),
    "the schema is not checked": ("        out = check_arguments(spec, args)\n", "        out = dict(args)\n"),
    "symbols are not checked": ('        out["symbol"] = _symbol(out["symbol"], "symbol")', "        pass"),
    "a symbol is not trimmed": (
        "    text = value.strip()\n    if SYMBOL.fullmatch(text) is None:",
        "    text = value\n    if SYMBOL.fullmatch(text) is None:",
    ),
    "a symbol may be matched in part": ("    if SYMBOL.fullmatch(text) is None:", "    if SYMBOL.match(text) is None:"),
    "a symbol is upper-cased": ("    return text\n\n\ndef _symbols", "    return text.upper()\n\n\ndef _symbols"),
    "symbols may be longer": (
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}")',
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,40}")',
    ),
    "symbols may be shorter": (
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}")',
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,18}")',
    ),
    "symbols may start with a dash": (
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}")',
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9._-]{1,20}")',
    ),
    "symbols may hold slashes": ('r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}"', 'r"[A-Za-z0-9][A-Za-z0-9._/-]{0,19}"'),
    "symbols may be unicode": ('r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}"', 'r"\\w[\\w._-]{0,19}"'),
    "addresses are not named": ("        if len(text) > 20 and text.isalnum():", "        if False:"),
    "everything long is an address": ("        if len(text) > 20 and text.isalnum():", "        if len(text) > 3:"),
    "an address needs only to be long": ("        if len(text) > 20 and text.isalnum():", "        if len(text) > 20:"),
    "symbols are not read": (
        '        if "symbols" in out:\n            out["symbols"] = _symbols(out["symbols"])',
        '        if "symbols" in out:\n            pass',
    ),
    "the chain is not read": (
        '        if "chain" in out:\n            out["chain"] = _chain(out["chain"])',
        '        if "chain" in out:\n            pass',
    ),
    "the theme is not read": (
        '        if "theme" in out:\n            out["theme"] = _theme(out["theme"])',
        '        if "theme" in out:\n            pass',
    ),
    "top_n is not read": (
        '        if "top_n" in out:\n            out["top_n"] = _top_n(out["top_n"], self.config.max_top_n)',
        '        if "top_n" in out:\n            pass',
    ),
    "the catalogue hash is the pin's": ("        return self._live.sha256", "        return self._pinned.sha256"),
    "the pin does not decide what is callable": (
        "name in SPECS and name in self._pinned.tools and name not in self._blocked",
        "name in SPECS and name not in self._blocked",
    ),
    "symbols are split on commas only": (
        '    for part in re.split(r"[,\\s]+", value.strip()):',
        '    for part in re.split(r",", value.strip()):',
    ),
    "symbols are split on spaces only": (
        '    for part in re.split(r"[,\\s]+", value.strip()):',
        '    for part in re.split(r"\\s+", value.strip()):',
    ),
    "empty parts are kept": ("        if part:\n            seen.setdefault", "        if True:\n            seen.setdefault"),
    "duplicates are case-sensitive": (
        'seen.setdefault(_symbol(part, "symbols").lower(), part)',
        'seen.setdefault(_symbol(part, "symbols"), part)',
    ),
    "the last spelling of a duplicate is kept": (
        'seen.setdefault(_symbol(part, "symbols").lower(), part)',
        'seen.__setitem__(_symbol(part, "symbols").lower(), part)',
    ),
    "one symbol is a comparison": ("    if not 2 <= len(seen) <= 4:", "    if not 1 <= len(seen) <= 4:"),
    "five symbols are a comparison": ("    if not 2 <= len(seen) <= 4:", "    if not 2 <= len(seen) <= 5:"),
    "three is not enough": ("    if not 2 <= len(seen) <= 4:", "    if not 3 <= len(seen) <= 4:"),
    "three is too many": ("    if not 2 <= len(seen) <= 4:", "    if not 2 <= len(seen) <= 3:"),
    "compared symbols are joined without a space": ('    return ", ".join(seen.values())', '    return ",".join(seen.values())'),
    "compared symbols are sorted": ('    return ", ".join(seen.values())', '    return ", ".join(sorted(seen.values()))'),
    "a chain is not lower-cased": (
        "    text = value.strip().lower()\n    if CHAIN.fullmatch(text) is None:",
        "    text = value.strip()\n    if CHAIN.fullmatch(text) is None:",
    ),
    "a chain may be anything": (
        '    if CHAIN.fullmatch(text) is None:\n        raise _invalid("chain is not a chain slug such as eth or bsc")\n',
        "",
    ),
    "a chain may be longer": (
        'CHAIN: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")',
        'CHAIN: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")',
    ),
    "a chain may be matched in part": ("    if CHAIN.fullmatch(text) is None:", "    if CHAIN.match(text) is None:"),
    "a theme may be empty": (
        "    if not 1 <= len(text) <= MAX_THEME or not text.isprintable():",
        "    if not len(text) <= MAX_THEME or not text.isprintable():",
    ),
    "a theme may be longer": (
        "    if not 1 <= len(text) <= MAX_THEME or not text.isprintable():",
        "    if not 1 <= len(text) or not text.isprintable():",
    ),
    "a theme may be one too long": (
        "    if not 1 <= len(text) <= MAX_THEME or not text.isprintable():",
        "    if not 1 <= len(text) <= MAX_THEME + 1 or not text.isprintable():",
    ),
    "a theme may hold control characters": (
        "    if not 1 <= len(text) <= MAX_THEME or not text.isprintable():",
        "    if not 1 <= len(text) <= MAX_THEME:",
    ),
    "a theme is not trimmed": (
        "    text = value.strip()\n    if not 1 <= len(text) <= MAX_THEME",
        "    text = value\n    if not 1 <= len(text) <= MAX_THEME",
    ),
    "top_n may be fractional": ("    if not float(value).is_integer() or not 1 <= value <= limit:", "    if not 1 <= value <= limit:"),
    "top_n may be zero": (
        "    if not float(value).is_integer() or not 1 <= value <= limit:",
        "    if not float(value).is_integer() or not 0 <= value <= limit:",
    ),
    "top_n may be above the limit": (
        "    if not float(value).is_integer() or not 1 <= value <= limit:",
        "    if not float(value).is_integer() or not 1 <= value:",
    ),
    "top_n may be one above the limit": (
        "    if not float(value).is_integer() or not 1 <= value <= limit:",
        "    if not float(value).is_integer() or not 1 <= value <= limit + 1:",
    ),
    "top_n may not be the limit": (
        "    if not float(value).is_integer() or not 1 <= value <= limit:",
        "    if not float(value).is_integer() or not 1 <= value < limit:",
    ),
    "top_n stays a float": ("    return int(value)", "    return float(value)"),
    "top_n ignores the configuration": ('_top_n(out["top_n"], self.config.max_top_n)', '_top_n(out["top_n"], 50)'),
    "the direction is not checked": (
        '        if "filter_direction" in out and out["filter_direction"] not in DIRECTIONS:',
        "        if False:",
    ),
    "the direction ignores case": ('out["filter_direction"] not in DIRECTIONS', 'out["filter_direction"].lower() not in DIRECTIONS'),
    "the direction set has a fourth": (
        'DIRECTIONS: Final = frozenset({"positive", "negative", "all"})',
        'DIRECTIONS: Final = frozenset({"positive", "negative", "all", "up"})',
    ),
    "a call costs two": (
        "    def plan(self, operation: str, args: Mapping[str, Any]) -> float:\n        return 1.0",
        "    def plan(self, operation: str, args: Mapping[str, Any]) -> float:\n        return 2.0",
    ),
    "the attempt cap is not the configured one": ("        return self.config.attempt_cap_s", "        return 60.0"),
    "the minimum attempt is not the configured one": ("        return self.config.min_attempt_s", "        return 5.0"),
    # --- classification ------------------------------------------------------------------------------------------------------------
    "no response is a success": (
        '        if capture.status is None:\n            return ConnectorError(ErrorClass.UPSTREAM, "no response", trace_id=capture.trace_id)',
        "        if capture.status is None:\n            return None",
    ),
    "no response loses its trace": (
        'ConnectorError(ErrorClass.UPSTREAM, "no response", trace_id=capture.trace_id)',
        'ConnectorError(ErrorClass.UPSTREAM, "no response")',
    ),
    "the status is not classified": (
        "        error_class = classify_status(capture.status)\n        if error_class is not None:\n            return self._failure(capture, error_class, now)",
        "        error_class = None",
    ),
    "a missing operation is fine": (
        '        if not isinstance(operation, str):\n            return ConnectorError(ErrorClass.MALFORMED, "the capture does not name its operation", trace_id=trace)\n',
        "",
    ),
    "a missing operation is drift": (
        'ConnectorError(ErrorClass.MALFORMED, "the capture does not name its operation", trace_id=trace)',
        'ConnectorError(ErrorClass.CONTRACT_DRIFT, "the capture does not name its operation", trace_id=trace)',
    ),
    "a read failure is ignored": (
        "        try:\n            read_answer(operation, capture.body)\n        except ConnectorError as exc:\n            return ConnectorError(exc.error_class, exc.message, retry_after_s=exc.retry_after_s, trace_id=exc.trace_id or trace)\n        return None",
        "        return None",
    ),
    "a read failure loses the trace": ("trace_id=exc.trace_id or trace)", "trace_id=exc.trace_id)"),
    "a read failure loses its class": (
        "ConnectorError(exc.error_class, exc.message, retry_after_s=exc.retry_after_s, trace_id=exc.trace_id or trace)",
        "ConnectorError(ErrorClass.UPSTREAM, exc.message, retry_after_s=exc.retry_after_s, trace_id=exc.trace_id or trace)",
    ),
    "the catalogue is not read": (
        '    if operation == "_catalogue":\n        parse_catalogue(body)',
        '    if operation == "_catalogue":\n        pass',
    ),
    "whoami is not read": ('    elif operation == "_whoami":\n        read_whoami(body)', '    elif operation == "_whoami":\n        pass'),
    "health is not read": ('    elif operation == "_health":\n        read_health(body)', '    elif operation == "_health":\n        pass'),
    "the envelope is not read": (
        "        envelope = read_rest_success(body, operation)\n        walk(operation, envelope.data)",
        "        pass",
    ),
    "the data is not walked": ("        walk(operation, envelope.data)", "        pass"),
    "the data of another tool is walked": ("        walk(operation, envelope.data)", "        walk('market_overview', envelope.data)"),
    "an unknown operation is a success": (
        '    else:\n        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"{operation} is not an operation of this provider")',
        "    else:\n        pass",
    ),
    "an error body is not read": ("        body = read_error_body(capture.body)\n        trace", "        body = None\n        trace"),
    "the body trace is ignored": (
        "        trace = (body.trace_id if body is not None else None) or capture.trace_id",
        "        trace = capture.trace_id",
    ),
    "the header trace is ignored": (
        "        trace = (body.trace_id if body is not None else None) or capture.trace_id",
        "        trace = body.trace_id if body is not None else None",
    ),
    "the header trace wins": (
        "        trace = (body.trace_id if body is not None else None) or capture.trace_id",
        "        trace = capture.trace_id or (body.trace_id if body is not None else None)",
    ),
    "the status is left out of the message": ('f"HTTP {capture.status}: {message}"', "message"),
    "the body message is left out": (
        '        message = body.message if body is not None else f"HTTP {capture.status}"',
        '        message = f"HTTP {capture.status}"',
    ),
    "the wait header is ignored": ('        wait = parse_retry_after(capture.headers.get("retry-after"), now)', "        wait = None"),
    "unknown tool is not drift": (
        '        if error_class is ErrorClass.NOT_FOUND and body is not None and body.message.startswith("Unknown tool"):',
        "        if False:",
    ),
    "unknown tool matches anywhere": ('body.message.startswith("Unknown tool")', '"Unknown tool" in body.message'),
    "every not found is drift": (
        '        if error_class is ErrorClass.NOT_FOUND and body is not None and body.message.startswith("Unknown tool"):',
        "        if error_class is ErrorClass.NOT_FOUND:",
    ),
    "the reset time is not read": (
        "        if wait is None and error_class is ErrorClass.RATE_LIMIT and body is not None:\n            wait = self._reset_wait(body.details, now)",
        "",
    ),
    "the reset time is read for every error": (
        "        if wait is None and error_class is ErrorClass.RATE_LIMIT and body is not None:",
        "        if wait is None and body is not None:",
    ),
    "the reset time overrides the header": (
        "        if wait is None and error_class is ErrorClass.RATE_LIMIT and body is not None:",
        "        if error_class is ErrorClass.RATE_LIMIT and body is not None:",
    ),
    "details need not be an object": ("        if not isinstance(details, dict):\n            return None\n        reset", "        reset"),
    "an epoch is not read": (
        "        if isinstance(reset, jsonnum.Num):\n            moment = _epoch(reset.text)",
        "        if isinstance(reset, jsonnum.Num):\n            moment = None",
    ),
    "a text time is not read": (
        "        elif isinstance(reset, str):\n            try:\n                moment = parse_rfc3339(reset)\n            except TimeError:\n                moment = None",
        "        elif isinstance(reset, str):\n            moment = None",
    ),
    "a bad time is not caught": (
        "            except TimeError:\n                moment = None",
        "            except ZeroDivisionError:\n                moment = None",
    ),
    "a missing time is a zero wait": (
        "        if moment is None:\n            return None\n        return float(min(",
        "        if moment is None:\n            return 0.0\n        return float(min(",
    ),
    "a passed reset is a negative wait": (
        "        return float(min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_S))",
        "        return float(min((moment - now).total_seconds(), MAX_RETRY_AFTER_S))",
    ),
    "a far reset is believed": (
        "        return float(min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_S))",
        "        return float(max((moment - now).total_seconds(), 0.0))",
    ),
    "an epoch may be anything": ('    if not re.fullmatch(r"[0-9]{1,12}", text):\n        return None', ""),
    "an epoch may be longer": ('    if not re.fullmatch(r"[0-9]{1,12}", text):', '    if not re.fullmatch(r"[0-9]{1,30}", text):'),
    # --- rate headers --------------------------------------------------------------------------------------------------------------
    "rate numbers may be anything": (
        '            if value is None or not re.fullmatch(r"[0-9]{1,12}(\\.[0-9]{1,6})?", value.strip()):\n                return None',
        "            if value is None:\n                return None",
    ),
    "rate numbers are not trimmed": ('r"[0-9]{1,12}(\\.[0-9]{1,6})?", value.strip())', 'r"[0-9]{1,12}(\\.[0-9]{1,6})?", value)'),
    "rate numbers may be negative": ('r"[0-9]{1,12}(\\.[0-9]{1,6})?"', 'r"-?[0-9]{1,12}(\\.[0-9]{1,6})?"'),
    "rate numbers may be longer": ('r"[0-9]{1,12}(\\.[0-9]{1,6})?"', 'r"[0-9]{1,30}(\\.[0-9]{1,6})?"'),
    "the limit header is not read": ('number("x-ratelimit-limit")', "None"),
    "the remaining header is not read": ('number("x-ratelimit-remaining")', "None"),
    "the reset header is not read": ('number("x-ratelimit-reset")', "None"),
    "a reset in milliseconds is believed": ("reset < 4e10", "True"),
    "a reset in seconds is not believed": ("reset < 4e10", "reset > 4e10"),
    "the date is not read": ('        if "date" in headers:', "        if False:"),
    "a date without a zone is believed": (
        "response_date = parsed.astimezone(UTC) if parsed.tzinfo is not None else None",
        "response_date = parsed.replace(tzinfo=UTC)",
    ),
    "a date is not converted": ("response_date = parsed.astimezone(UTC) if parsed.tzinfo is not None else None", "response_date = parsed"),
    "a bad date is raised": (
        "            except (TypeError, ValueError):\n                response_date = None",
        "            except ZeroDivisionError:\n                response_date = None",
    ),
    "no headers is still an observation": (
        "        if limit is None and remaining is None and reset_at is None:\n            return None",
        "",
    ),
    "a date alone is an observation": (
        "        if limit is None and remaining is None and reset_at is None:",
        "        if limit is None and remaining is None and reset_at is None and response_date is None:",
    ),
    "the date is dropped from the observation": (
        "RateObservation(limit=limit, remaining=remaining, reset_at=reset_at, response_date=response_date)",
        "RateObservation(limit=limit, remaining=remaining, reset_at=reset_at)",
    ),
    "the reset is dropped from the observation": (
        "RateObservation(limit=limit, remaining=remaining, reset_at=reset_at, response_date=response_date)",
        "RateObservation(limit=limit, remaining=remaining, response_date=response_date)",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.ryo", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
