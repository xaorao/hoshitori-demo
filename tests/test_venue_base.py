"""The shared venue adapter on a made-up venue: scenarios and mutants."""

from __future__ import annotations

import pytest

from hoshitori import venue_base
from tests.mutation import mutant
from tests.venue_base_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(venue_base) == []


# Redundant guards without mutants: the missing-header default of `date_header` (an empty text fails to parse and gives the same None), the
# arguments returned by `time` (they are always empty there), and the boolean test of an interval (a boolean is never a key of the venue's
# interval table).
MUTATIONS: dict[str, tuple[str, str]] = {
    # --- configuration
    "a zero minimum is allowed": (
        "        if not 0 < self.min_attempt_s <= self.attempt_cap_s:",
        "        if not self.min_attempt_s <= self.attempt_cap_s:",
    ),
    "a minimum above the cap is allowed": (
        "        if not 0 < self.min_attempt_s <= self.attempt_cap_s:",
        "        if not 0 < self.min_attempt_s:",
    ),
    "a minimum at the cap is refused": (
        "        if not 0 < self.min_attempt_s <= self.attempt_cap_s:",
        "        if not 0 < self.min_attempt_s < self.attempt_cap_s:",
    ),
    "an empty window is allowed": (
        "        if not 1 <= self.max_window_bars <= MAX_WINDOW_BARS:",
        "        if not self.max_window_bars <= MAX_WINDOW_BARS:",
    ),
    "a huge window is allowed": (
        "        if not 1 <= self.max_window_bars <= MAX_WINDOW_BARS:",
        "        if not 1 <= self.max_window_bars:",
    ),
    "the window limit at its maximum is refused": (
        "        if not 1 <= self.max_window_bars <= MAX_WINDOW_BARS:",
        "        if not 1 <= self.max_window_bars < MAX_WINDOW_BARS:",
    ),
    "the default cap is different": ("    attempt_cap_s: float = 20.0", "    attempt_cap_s: float = 25.0"),
    "the default minimum is different": ("    min_attempt_s: float = 1.0", "    min_attempt_s: float = 2.0"),
    "the page size is different": ("MAX_WINDOW_BARS: Final = 300", "MAX_WINDOW_BARS: Final = 500"),
    "the provider is the venue": ("        self.provider = provider or self.venue", "        self.provider = self.venue"),
    "a rate may have no limit": (
        "        if not isinstance(self.model, RateModel) or not self.limit > 0 or not self.window_s > 0:",
        "        if not isinstance(self.model, RateModel) or not self.window_s > 0:",
    ),
    "a rate may have no window": (
        "        if not isinstance(self.model, RateModel) or not self.limit > 0 or not self.window_s > 0:",
        "        if not isinstance(self.model, RateModel) or not self.limit > 0:",
    ),
    "a rate may have any model": (
        "        if not isinstance(self.model, RateModel) or not self.limit > 0 or not self.window_s > 0:",
        "        if not self.limit > 0 or not self.window_s > 0:",
    ),
    "a rate may have a negative limit": ("not self.limit > 0 or not self.window_s > 0:", "self.limit == 0 or not self.window_s > 0:"),
    "a rate may have a negative window": ("not self.limit > 0 or not self.window_s > 0:", "not self.limit > 0 or self.window_s == 0:"),
    "a rate may have an unknown limit": ("not self.limit > 0 or not self.window_s > 0:", "self.limit <= 0 or not self.window_s > 0:"),
    "a rate may have an unknown window": ("not self.limit > 0 or not self.window_s > 0:", "not self.limit > 0 or self.window_s <= 0:"),
    "the configuration is ignored": ("        self.config = config or VenueConfig()", "        self.config = VenueConfig()"),
    "an operation is missing": ('OPERATIONS: Final = ("time", "candles", "instruments")', 'OPERATIONS: Final = ("time", "candles")'),
    "the cost comes from nowhere": ("        return self.cost(operation, args)", "        return 1.0"),
    "the cap comes from nowhere": ("        return self.config.attempt_cap_s", "        return 20.0"),
    "the minimum comes from nowhere": ("        return self.config.min_attempt_s", "        return 1.0"),
    # --- times
    "a time is not text": (
        '    if not isinstance(text, str):\n        raise _invalid(f"{what} must be an RFC 3339 time with an offset")\n',
        "",
    ),
    "a bad time is a crash": (
        "    except TimeError as exc:\n        raise _invalid(",
        "    except ZeroDivisionError as exc:\n        raise _invalid(",
    ),
    "the argument is not named": (
        '        raise _invalid(f"{what} is not an RFC 3339 time with an offset: {text!r}") from exc',
        '        raise _invalid(f"not an RFC 3339 time with an offset: {text!r}") from exc',
    ),
    "a bad date is a crash": (
        "    except (TypeError, ValueError):\n        return None",
        "    except ZeroDivisionError:\n        return None",
    ),
    "a date with no zone is believed": (
        "    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None",
        "    return parsed.replace(tzinfo=UTC)",
    ),
    "a date is not converted": ("    return parsed.astimezone(UTC) if parsed.tzinfo is not None else None", "    return parsed"),
    # --- arguments
    "time takes arguments": (
        '        if operation == "time":\n            if args:\n                raise _invalid("time takes no arguments")\n            return {}',
        '        if operation == "time":\n            return {}',
    ),
    "an unknown operation is accepted": (
        '        if operation != "candles":\n            raise _invalid(f"{self.venue} has no operation {operation!r}")\n',
        "",
    ),
    "unknown arguments are accepted": (
        '        unknown = set(args) - {"instrument", "interval_s", "start", "end"}',
        "        unknown = set()",
    ),
    "missing arguments are accepted": (
        '        missing = {"instrument", "interval_s", "start", "end"} - set(args)',
        "        missing = set()",
    ),
    "the argument check is lenient": ("        if unknown or missing:", "        if unknown and missing:"),
    "an instrument may be any text": (
        "        if not isinstance(instrument, str) or not self.valid_instrument(instrument):",
        "        if not isinstance(instrument, str):",
    ),
    "an instrument may be anything": (
        "        if not isinstance(instrument, str) or not self.valid_instrument(instrument):",
        "        if False:",
    ),
    "an instrument may not be checked by the venue": (
        "        if not isinstance(instrument, str) or not self.valid_instrument(instrument):",
        "        if not isinstance(instrument, str) or not instrument:",
    ),
    "an interval may be anything": (
        "        if isinstance(interval, bool) or not isinstance(interval, int) or interval not in self.intervals:",
        "        if isinstance(interval, bool) or not isinstance(interval, int):",
    ),
    "an interval may be a float": (
        "        if isinstance(interval, bool) or not isinstance(interval, int) or interval not in self.intervals:",
        "        if isinstance(interval, bool) or interval not in self.intervals:",
    ),
    "an interval may be text": (
        "        if isinstance(interval, bool) or not isinstance(interval, int) or interval not in self.intervals:",
        "        if isinstance(interval, bool) or interval not in self.intervals and str(interval) not in map(str, self.intervals):",
    ),
    "times are not read": (
        '        start, end = parse_time(args["start"], "start"), parse_time(args["end"], "end")',
        "        start, end = NOW_PLACEHOLDER, NOW_PLACEHOLDER",
    ),
    "an empty window is accepted": ('        if not start < end:\n            raise _invalid("start must be before end")\n', ""),
    "an empty window is a window": ("        if not start < end:", "        if start > end:"),
    "a reversed window is a window": ("        if not start < end:", "        if start == end:"),
    "the window is not limited": (
        '        if bars > self.config.max_window_bars:\n            raise _invalid(f"the window holds {bars:.0f} bars; one call serves at most {self.config.max_window_bars}")\n',
        "",
    ),
    "the window is limited one bar early": (
        "        if bars > self.config.max_window_bars:",
        "        if bars >= self.config.max_window_bars:",
    ),
    "the window is limited one bar late": (
        "        if bars > self.config.max_window_bars:",
        "        if bars > self.config.max_window_bars + 1:",
    ),
    "the window limit is a constant": ("        if bars > self.config.max_window_bars:", "        if bars > MAX_WINDOW_BARS:"),
    "the window is counted in minutes": (
        "        bars = (end - start) / timedelta(seconds=interval)",
        "        bars = (end - start) / timedelta(seconds=60)",
    ),
    "the window is counted in whole bars": (
        "        bars = (end - start) / timedelta(seconds=interval)",
        "        bars = (end - start) // timedelta(seconds=interval)",
    ),
    "times keep their offset": ('"start": format_rfc3339_ms(start)}', '"start": args["start"]}'),
    "the end keeps its offset": ('return {"end": format_rfc3339_ms(end),', 'return {"end": args["end"],'),
    "the interval is dropped": ('"interval_s": interval, "start"', '"start"'),
    "the instrument is dropped": ('"instrument": instrument, "interval_s"', '"interval_s"'),
    "instruments take anything": (
        '        unknown = set(args) - {"kind"}\n        if unknown:\n            raise _invalid(f"instruments takes only kind (unknown {sorted(unknown)})")\n',
        "",
    ),
    "instruments lose the kind": ('        return {"kind": kind}', "        return {}"),
    "any kind is accepted": ("        if kind not in self.instrument_kinds:", "        if False:"),
    "a kind may be unhashable": ("        if kind not in self.instrument_kinds:", "        if not isinstance(kind, str) and False:"),
    "no kind is a kind": (
        '        if "kind" not in args:\n            return {}',
        '        if "kind" not in args:\n            return {"kind": "spot"}',
    ),
    # --- classification
    "no response is a success": (
        '        if capture.status is None:\n            return ConnectorError(ErrorClass.UPSTREAM, "no response")',
        "        if capture.status is None:\n            return None",
    ),
    "no response is malformed": (
        'ConnectorError(ErrorClass.UPSTREAM, "no response")',
        'ConnectorError(ErrorClass.MALFORMED, "no response")',
    ),
    "the operation is not checked": (
        "        if operation not in OPERATIONS or not isinstance(arguments, dict):",
        "        if not isinstance(arguments, dict):",
    ),
    "the arguments are not checked": (
        "        if operation not in OPERATIONS or not isinstance(arguments, dict):",
        "        if operation not in OPERATIONS:",
    ),
    "the venue's own error is ignored": (
        "        own = self.error_in_body(capture.status, capture.body, capture.headers, now)\n        if own is not None:\n            return own\n",
        "",
    ),
    "the venue's own error comes second": (
        "        own = self.error_in_body(capture.status, capture.body, capture.headers, now)\n        if own is not None:\n            return own\n        error_class = classify_status(capture.status)\n        if error_class is not None:",
        "        error_class = classify_status(capture.status)\n        if error_class is None:\n            own = self.error_in_body(capture.status, capture.body, capture.headers, now)\n            if own is not None:\n                return own\n        if error_class is not None:",
    ),
    "the status is not classified": (
        "        error_class = classify_status(capture.status)\n        if error_class is not None:",
        "        error_class = None\n        if error_class is not None:",
    ),
    "the wait is ignored": ('            wait = parse_retry_after(capture.headers.get("retry-after"), now)', "            wait = None"),
    "the wait is not passed": (
        'ConnectorError(error_class, f"HTTP {capture.status}: {self._snippet(capture.body)}", retry_after_s=wait)',
        'ConnectorError(error_class, f"HTTP {capture.status}: {self._snippet(capture.body)}")',
    ),
    "the status is not in the message": ('f"HTTP {capture.status}: {self._snippet(capture.body)}"', "self._snippet(capture.body)"),
    "the text is not in the message": ('f"HTTP {capture.status}: {self._snippet(capture.body)}"', 'f"HTTP {capture.status}"'),
    "the snippet is not cut": (
        '        return body[:MESSAGE_CUT].decode("utf-8", "replace").strip().replace("\\n", " ")',
        '        return body.decode("utf-8", "replace").strip().replace("\\n", " ")',
    ),
    "the snippet is cut short": (
        '        return body[:MESSAGE_CUT].decode("utf-8", "replace").strip().replace("\\n", " ")',
        '        return body[:20].decode("utf-8", "replace").strip().replace("\\n", " ")',
    ),
    "the snippet keeps newlines": ('.strip().replace("\\n", " ")', ".strip()"),
    "the snippet is not trimmed": (
        '.decode("utf-8", "replace").strip().replace("\\n", " ")',
        '.decode("utf-8", "replace").replace("\\n", " ")',
    ),
    "the snippet is not decoded safely": ('.decode("utf-8", "replace")', '.decode("utf-8", "ignore")'),
    "the body is not read": (
        "        try:\n            self._read(str(operation), arguments, capture.body)",
        "        try:\n            pass",
    ),
    "a data error crashes": (
        "        except (VenueDataError, jsonnum.JsonError, ConnectorError) as exc:",
        "        except (jsonnum.JsonError, ConnectorError) as exc:",
    ),
    "a JSON error crashes": (
        "        except (VenueDataError, jsonnum.JsonError, ConnectorError) as exc:",
        "        except (VenueDataError, ConnectorError) as exc:",
    ),
    "any other error crashes": (
        '        except Exception as exc:  # a body is hostile input: whatever shape it has, reading it must not take the gateway down\n            return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} {operation}: unreadable body ({type(exc).__name__})"[:MESSAGE_CUT])',
        '        except ZeroDivisionError as exc:\n            return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} {operation}: unreadable body ({type(exc).__name__})"[:MESSAGE_CUT])',
    ),
    "a malformed body is upstream": (
        'return ConnectorError(ErrorClass.MALFORMED, f"{self.venue} {operation}: {exc}"[:MESSAGE_CUT])',
        'return ConnectorError(ErrorClass.UPSTREAM, f"{self.venue} {operation}: {exc}"[:MESSAGE_CUT])',
    ),
    "a malformed message is not cut": ('f"{self.venue} {operation}: {exc}"[:MESSAGE_CUT]', 'f"{self.venue} {operation}: {exc}"'),
    "a malformed message does not say where": ('f"{self.venue} {operation}: {exc}"[:MESSAGE_CUT]', 'f"{exc}"[:MESSAGE_CUT]'),
    "candles are not checked": (
        "            bars = self.parse_candles_body(body, arguments)\n            check_bars(bars, _interval_of(arguments))\n            return bars",
        "            bars = self.parse_candles_body(body, arguments)\n            return bars",
    ),
    "candles are checked against another interval": (
        "            check_bars(bars, _interval_of(arguments))\n            return bars",
        "            check_bars(bars, 60)\n            return bars",
    ),
    "time is not read": (
        '        if operation == "time":\n            return self.parse_time_body(body)\n        if operation == "candles":\n            bars',
        '        if operation == "time":\n            return None\n        if operation == "candles":\n            bars',
    ),
    "instruments are not read": (
        "        return self.parse_instruments_body(body, arguments)\n\n    def parse(",
        "        return None\n\n    def parse(",
    ),
    "the rate observation is dropped": ("        return self.observe_rate(capture.headers)", "        return None"),
    # --- parse
    "parse reads time wrongly": (
        '        if operation == "time":\n            return self.parse_time_body(capture.body)\n        if operation == "instruments":',
        '        if operation == "time":\n            return NOW_PLACEHOLDER\n        if operation == "instruments":',
    ),
    "parse does not read instruments": (
        '        if operation == "instruments":\n            return self.parse_instruments_body(capture.body, arguments)',
        '        if operation == "instruments":\n            return InstrumentList(self.venue, (), ())',
    ),
    "parse needs no server time": (
        '        if server_time is None:\n            raise ValueError("candles can be read only against the venue\'s server time")\n',
        "",
    ),
    "parse uses the wrong instrument": ('str(arguments["instrument"])', 'str(arguments["interval_s"])'),
    "parse uses the wrong window": (
        '        start, end = parse_rfc3339(str(arguments["start"])), parse_rfc3339(str(arguments["end"]))',
        '        start, end = parse_rfc3339(str(arguments["end"])), parse_rfc3339(str(arguments["start"]))',
    ),
    "parse names the wrong venue": ("build_series(self.venue, ", 'build_series("x", '),
    "an interval that is not a number is read": (
        '    if isinstance(interval, bool) or not isinstance(interval, int):\n        raise VenueDataError("the capture does not hold a whole-second interval")',
        '    if isinstance(interval, bool):\n        raise VenueDataError("the capture does not hold a whole-second interval")',
    ),
    "a boolean interval is read": (
        "    if isinstance(interval, bool) or not isinstance(interval, int):",
        "    if not isinstance(interval, int):",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    new = new.replace("NOW_PLACEHOLDER", "datetime(2000, 1, 1, tzinfo=UTC)")
    module = mutant("hoshitori.venue_base", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
