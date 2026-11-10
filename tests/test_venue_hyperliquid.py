"""The Hyperliquid adapter: the profile cases, and mutants."""

from __future__ import annotations

import pytest

from hoshitori import venue_hyperliquid
from tests.mutation import mutant
from tests.profiles_hyperliquid import hyperliquid
from tests.venue_profile import profile_failures


def failures(mod):  # type: ignore[no-untyped-def]
    return profile_failures(mod, hyperliquid(mod))


def test_real_module_passes_every_case() -> None:
    assert failures(venue_hyperliquid) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    "the 1m interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "the 1m interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1M", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "the 5m interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "the 5m interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5M", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "the 15m interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "the 15m interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15M", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "the 1h interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 14400: "4h", 86400: "1d"}',
    ),
    "the 1h interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4h", 86400: "1d"}',
    ),
    "the 4h interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 86400: "1d"}',
    ),
    "the 4h interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4H", 86400: "1d"}',
    ),
    "the 1d interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h"}',
    ),
    "the 1d interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1D"}',
    ),
    "a 3-minute interval is offered": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 180: "3m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
    ),
    "a weekly interval is offered": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d", 604800: "1w"}',
    ),
    "names may be lower case": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"[kA-Za-z0-9]{1,20}")'),
    "names may start with any lower-case letter": (
        'PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")',
        'PERP: Final = re.compile(r"[a-z]?[A-Z0-9]{1,20}")',
    ),
    "names may be empty": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k?[A-Z0-9]{0,20}")'),
    "names may be longer": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k?[A-Z0-9]{1,40}")'),
    "names may be shorter": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k?[A-Z0-9]{1,19}")'),
    "names need a k": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k[A-Z0-9]{1,20}")'),
    "names may hold a colon": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k?[A-Z0-9:]{1,20}")'),
    "names may hold a dash": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k?[A-Z0-9-]{1,20}")'),
    "two k are allowed": ('PERP: Final = re.compile(r"k?[A-Z0-9]{1,20}")', 'PERP: Final = re.compile(r"k*[A-Z0-9]{1,20}")'),
    "an index may be empty": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{0,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
    ),
    "an index may be longer": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,9}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
    ),
    "an index may be shorter": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,5}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
    ),
    "an index may be text": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9A-Z]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
    ),
    "a pair may be lower case": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Za-z0-9]{1,20}/[A-Za-z0-9]{1,20}")',
    ),
    "a pair may lack the quote": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/?[A-Z0-9]{0,20}")',
    ),
    "a pair may be longer on the left": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,40}/[A-Z0-9]{1,20}")',
    ),
    "a pair may be longer on the right": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,40}")',
    ),
    "a pair may be shorter on the left": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,19}/[A-Z0-9]{1,20}")',
    ),
    "a pair may be shorter on the right": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,19}")',
    ),
    "a pair may be a dash": (
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}/[A-Z0-9]{1,20}")',
        'SPOT: Final = re.compile(r"@[0-9]{1,6}|[A-Z0-9]{1,20}[/-][A-Z0-9]{1,20}")',
    ),
    "a market may be matched in part": (
        "    def valid_instrument(self, raw_id: str) -> bool:\n        return PERP.fullmatch(raw_id) is not None or SPOT.fullmatch(raw_id) is not None",
        "    def valid_instrument(self, raw_id: str) -> bool:\n        return PERP.fullmatch(raw_id) is not None or SPOT.match(raw_id) is not None",
    ),
    "a perpetual may be matched in part": (
        "        return PERP.fullmatch(raw_id) is not None or SPOT",
        "        return PERP.match(raw_id) is not None or SPOT",
    ),
    "only perpetual names are valid": (
        "        return PERP.fullmatch(raw_id) is not None or SPOT.fullmatch(raw_id) is not None",
        "        return PERP.fullmatch(raw_id) is not None",
    ),
    "only spot names are valid": (
        "        return PERP.fullmatch(raw_id) is not None or SPOT.fullmatch(raw_id) is not None",
        "        return SPOT.fullmatch(raw_id) is not None",
    ),
    "a thousand needs no k": ('THOUSAND: Final = re.compile(r"k[A-Z0-9]{1,20}")', 'THOUSAND: Final = re.compile(r"k?[A-Z0-9]{1,20}")'),
    "a thousand may be K": ('THOUSAND: Final = re.compile(r"k[A-Z0-9]{1,20}")', 'THOUSAND: Final = re.compile(r"[kK][A-Z0-9]{1,20}")'),
    "a thousand is never": ('"1000" if THOUSAND.fullmatch(raw) is not None else "1"', '"1"'),
    "a thousand is a million": (
        '"1000" if THOUSAND.fullmatch(raw) is not None else "1"',
        '"1000000" if THOUSAND.fullmatch(raw) is not None else "1"',
    ),
    "a thousand is always": ('"1000" if THOUSAND.fullmatch(raw) is not None else "1"', '"1000"'),
    "the list bound is tiny": ("MAX_LIST_BYTES: Final = 8 * 1024 * 1024", "MAX_LIST_BYTES: Final = 1024"),
    "the quote is another currency": ('QUOTE: Final = "USDC"', 'QUOTE: Final = "USDT"'),
    "the status weighs more": ("WEIGHT_STATUS: Final = 2.0", "WEIGHT_STATUS: Final = 20.0"),
    "an info call weighs less": ("WEIGHT_INFO: Final = 20.0", "WEIGHT_INFO: Final = 2.0"),
    "bars weigh at fifty": ("BARS_PER_EXTRA_WEIGHT: Final = 60", "BARS_PER_EXTRA_WEIGHT: Final = 50"),
    "bars weigh at seventy": ("BARS_PER_EXTRA_WEIGHT: Final = 60", "BARS_PER_EXTRA_WEIGHT: Final = 70"),
    "the weight limit is different": ("WEIGHT_PER_MINUTE: Final = 1200", "WEIGHT_PER_MINUTE: Final = 600"),
    "the bars of a window miss the early one": (
        '    return -(-(parse_rfc3339(str(args["end"])) - parse_rfc3339(str(args["start"]))) // interval) + 1',
        '    return -(-(parse_rfc3339(str(args["end"])) - parse_rfc3339(str(args["start"]))) // interval)',
    ),
    "the bars of a window round down": (
        '    return -(-(parse_rfc3339(str(args["end"])) - parse_rfc3339(str(args["start"]))) // interval) + 1',
        '    return (parse_rfc3339(str(args["end"])) - parse_rfc3339(str(args["start"]))) // interval + 1',
    ),
    "the bars of a window are counted in minutes": (
        '    interval = timedelta(seconds=int(args["interval_s"]))',
        '    interval = timedelta(minutes=int(args["interval_s"]))',
    ),
    "the bar weight rounds down": (
        "WEIGHT_INFO + float(-(-_bars_in(args) // BARS_PER_EXTRA_WEIGHT))",
        "WEIGHT_INFO + float(_bars_in(args) // BARS_PER_EXTRA_WEIGHT)",
    ),
    "the bar weight is not added": ("WEIGHT_INFO + float(-(-_bars_in(args) // BARS_PER_EXTRA_WEIGHT))", "WEIGHT_INFO"),
    "the bar weight replaces the base": (
        "WEIGHT_INFO + float(-(-_bars_in(args) // BARS_PER_EXTRA_WEIGHT))",
        "float(-(-_bars_in(args) // BARS_PER_EXTRA_WEIGHT))",
    ),
    "the time costs an info call": ("            return WEIGHT_STATUS", "            return WEIGHT_INFO"),
    "the list costs the status weight": (
        "        return WEIGHT_INFO\n\n    # --- reading",
        "        return WEIGHT_STATUS\n\n    # --- reading",
    ),
    "a size step of no places is refused": ("    if not 0 <= count <= 18:", "    if not 1 <= count <= 18:"),
    "a negative size step is accepted": ("    if not 0 <= count <= 18:", "    if not count <= 18:"),
    "a size step of nineteen places is accepted": ("    if not 0 <= count <= 18:", "    if not 0 <= count <= 30:"),
    "a size step of eighteen places is refused": ("    if not 0 <= count <= 18:", "    if not 0 <= count < 18:"),
    "a size step is written in scientific notation": (
        '    return format(Decimal(1).scaleb(-count), "f")',
        "    return str(Decimal(1).scaleb(-count))",
    ),
    "a size step is the places": ('    return format(Decimal(1).scaleb(-count), "f")', "    return str(count)"),
    "a size step grows": ('    return format(Decimal(1).scaleb(-count), "f")', '    return format(Decimal(1).scaleb(count), "f")'),
    "a size step may be text": (
        '    count = whole_number(places, "szDecimals")',
        '    count = whole_number(places, "szDecimals", text_ok=True)',
    ),
    "a text member may be empty": ("    if not isinstance(value, str) or not value:", "    if not isinstance(value, str):"),
    "a text member may be anything": ("    if not isinstance(value, str) or not value:", "    if value is None:"),
    "the venue is named otherwise": ('    venue: ClassVar[str] = "hyperliquid"', '    venue: ClassVar[str] = "hyperliquid-l1"'),
    "the kinds are reversed": ('("perpetual", "spot")', '("spot", "perpetual")'),
    "a future kind is offered": ('("perpetual", "spot")', '("perpetual", "spot", "future")'),
    "no spot kind is offered": ('("perpetual", "spot")', '("perpetual",)'),
    "a kind may be left out": ('        if "kind" not in args:', "        if False:"),
    "a missing kind is the wrong error": (
        "            raise ConnectorError(\n                ErrorClass.INVALID_ARGS,",
        "            raise ConnectorError(\n                ErrorClass.UPSTREAM,",
    ),
    "the base class is not asked": ("        return super()._normalise_instruments(args)", '        return {"kind": args["kind"]}'),
    "the status request is a GET": (
        '            return VenueRequest("POST", "/info", _json_body("exchangeStatus"))',
        '            return VenueRequest("GET", "/info", _json_body("exchangeStatus"))',
    ),
    "the status path is wrong": (
        '            return VenueRequest("POST", "/info", _json_body("exchangeStatus"))',
        '            return VenueRequest("POST", "/exchange", _json_body("exchangeStatus"))',
    ),
    "the status question is wrong": ('_json_body("exchangeStatus")', '_json_body("exchangeStatuses")'),
    "the status request has no body": (
        '            return VenueRequest("POST", "/info", _json_body("exchangeStatus"))',
        '            return VenueRequest("POST", "/info")',
    ),
    "the candle question is wrong": ('_json_body("candleSnapshot", req=window)', '_json_body("candles", req=window)'),
    "the candle request is a GET": (
        '            return VenueRequest("POST", "/info", _json_body("candleSnapshot", req=window))',
        '            return VenueRequest("GET", "/info", _json_body("candleSnapshot", req=window))',
    ),
    "the candle request is not wrapped": ('_json_body("candleSnapshot", req=window)', '_json_body("candleSnapshot", **window)'),
    "the coin is a constant": ('"coin": args["instrument"],', '"coin": "BTC",'),
    "the interval is a constant": ('"interval": INTERVALS[int(args["interval_s"])],', '"interval": "1m",'),
    "the interval is in seconds": ('"interval": INTERVALS[int(args["interval_s"])],', '"interval": str(args["interval_s"]),'),
    "the start is not read": (
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))),',
        '"startTime": to_ms(parse_rfc3339(str(args["end"]))),',
    ),
    "the start is in seconds": (
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))),',
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))) // 1000,',
    ),
    "the start is a millisecond early": (
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))),',
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))) - 1,',
    ),
    "the start is a millisecond late": (
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))),',
        '"startTime": to_ms(parse_rfc3339(str(args["start"]))) + 1,',
    ),
    "the end is inclusive": (
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) - 1',
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"])))',
    ),
    "the end is two milliseconds early": (
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) - 1',
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) - 2',
    ),
    "the end is not read": (
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) - 1',
        '            last_open_ms = to_ms(parse_rfc3339(str(args["start"]))) - 1',
    ),
    "the end is in seconds": (
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) - 1',
        '            last_open_ms = to_ms(parse_rfc3339(str(args["end"]))) // 1000 - 1',
    ),
    "the end is the window key of the start": ('"endTime": last_open_ms,', '"endTime": to_ms(parse_rfc3339(str(args["start"]))),'),
    "the perpetual list question is the spot one": (
        '_json_body("meta" if args["kind"] == "perpetual" else "spotMeta")',
        '_json_body("spotMeta" if args["kind"] == "perpetual" else "meta")',
    ),
    "the list question is always meta": ('_json_body("meta" if args["kind"] == "perpetual" else "spotMeta")', '_json_body("meta")'),
    "the list question is always spot": ('_json_body("meta" if args["kind"] == "perpetual" else "spotMeta")', '_json_body("spotMeta")'),
    "an unknown operation is a request": (
        '            return VenueRequest("POST", "/info", _json_body("meta" if args["kind"] == "perpetual" else "spotMeta"))\n        raise KeyError(operation)',
        '            return VenueRequest("POST", "/info", _json_body("meta" if args["kind"] == "perpetual" else "spotMeta"))\n        return VenueRequest("POST", "/info")',
    ),
    "the status need not be an object": (
        '        if not isinstance(data, dict):\n            raise VenueDataError("the status answer is not an object")\n',
        "",
    ),
    "the time is read from another member": ('whole_number(data.get("time"), "time")', 'whole_number(data.get("specialStatuses"), "time")'),
    "the time may be text": ('whole_number(data.get("time"), "time")', 'whole_number(data.get("time"), "time", text_ok=True)'),
    "the time is in seconds": (
        'ms_to_datetime(whole_number(data.get("time"), "time"))',
        'ms_to_datetime(whole_number(data.get("time"), "time") * 1000)',
    ),
    "candles need not be a list": (
        '        if not isinstance(rows, list):\n            raise VenueDataError("candles are not a list")\n',
        "",
    ),
    "a candle need not be an object": (
        '            if not isinstance(row, dict):\n                raise VenueDataError("a candle is not an object")\n',
        "",
    ),
    "a candle may be for another coin": (
        '            if row.get("s") != args["instrument"] or row.get("i") != INTERVALS[interval_s]:',
        '            if row.get("i") != INTERVALS[interval_s]:',
    ),
    "a candle may be for another interval": (
        '            if row.get("s") != args["instrument"] or row.get("i") != INTERVALS[interval_s]:',
        '            if row.get("s") != args["instrument"]:',
    ),
    "the coin may be missing": (
        '            if row.get("s") != args["instrument"] or row.get("i") != INTERVALS[interval_s]:',
        '            if row.get("s", args["instrument"]) != args["instrument"] or row.get("i") != INTERVALS[interval_s]:',
    ),
    "the open time may be text": ('whole_number(row.get("t"), "open time")', 'whole_number(row.get("t"), "open time", text_ok=True)'),
    "the open time is in seconds": (
        'ms_to_datetime(whole_number(row.get("t"), "open time"))',
        'ms_to_datetime(whole_number(row.get("t"), "open time") * 1000)',
    ),
    "the close time is not checked": (
        '            if whole_number(row.get("T"), "close time") + 1 != to_ms(opened) + interval_s * 1000:\n                raise VenueDataError("a candle\'s close time does not match the interval asked for")\n',
        "",
    ),
    "the close time may be the next open": (
        '            if whole_number(row.get("T"), "close time") + 1 != to_ms(opened) + interval_s * 1000:',
        '            if whole_number(row.get("T"), "close time") != to_ms(opened) + interval_s * 1000:',
    ),
    "the close time may be late": (
        '            if whole_number(row.get("T"), "close time") + 1 != to_ms(opened) + interval_s * 1000:',
        '            if whole_number(row.get("T"), "close time") + 1 < to_ms(opened) + interval_s * 1000:',
    ),
    "the close time may be early": (
        '            if whole_number(row.get("T"), "close time") + 1 != to_ms(opened) + interval_s * 1000:',
        '            if whole_number(row.get("T"), "close time") + 1 > to_ms(opened) + interval_s * 1000:',
    ),
    "the close time may be text": ('whole_number(row.get("T"), "close time")', 'whole_number(row.get("T"), "close time", text_ok=True)'),
    "the close time is read from the open": ('whole_number(row.get("T"), "close time")', 'whole_number(row.get("t"), "close time")'),
    "the bar lasts a minute": ("close_time=opened + timedelta(seconds=interval_s),", "close_time=opened + timedelta(seconds=60),"),
    "the open is the close": ('open=decimal_text(row.get("o"), "open"),', 'open=decimal_text(row.get("c"), "open"),'),
    "the high is the low": ('high=decimal_text(row.get("h"), "high"),', 'high=decimal_text(row.get("l"), "high"),'),
    "the low is the high": ('low=decimal_text(row.get("l"), "low"),', 'low=decimal_text(row.get("h"), "low"),'),
    "the close is the open": ('close=decimal_text(row.get("c"), "close"),', 'close=decimal_text(row.get("o"), "close"),'),
    "the volume is the count": ('volume=decimal_text(row.get("v"), "volume"),', 'volume=decimal_text(row.get("n"), "volume"),'),
    "the count is the volume": ('trades=whole_number(row.get("n"), "trades"),', 'trades=whole_number(row.get("v"), "trades"),'),
    "the count may be text": ('trades=whole_number(row.get("n"), "trades"),', 'trades=whole_number(row.get("n"), "trades", text_ok=True),'),
    "the bars are not sorted": ("        return sorted(bars, key=lambda bar: bar.open_time)", "        return bars"),
    "the bars are sorted newest first": (
        "        return sorted(bars, key=lambda bar: bar.open_time)",
        "        return sorted(bars, key=lambda bar: bar.open_time, reverse=True)",
    ),
    "the list need not be an object": (
        '        if not isinstance(data, dict) or not isinstance(data.get("universe"), list):',
        '        if not isinstance(data.get("universe"), list):',
    ),
    "the universe need not be a list": (
        '        if not isinstance(data, dict) or not isinstance(data.get("universe"), list):',
        "        if not isinstance(data, dict):",
    ),
    "the kind decides nothing": ('        if args["kind"] == "perpetual":', "        if True:"),
    "the kind decides the other way": ('        if args["kind"] == "perpetual":', '        if args["kind"] != "perpetual":'),
    "the collateral token is not checked": (
        '        if "collateralToken" in data and whole_number(data["collateralToken"], "collateralToken") != 0:',
        "        if False:",
    ),
    "the collateral token is required": (
        '        if "collateralToken" in data and whole_number(data["collateralToken"], "collateralToken") != 0:',
        '        if whole_number(data.get("collateralToken"), "collateralToken") != 0:',
    ),
    "the collateral token may be text": (
        'whole_number(data["collateralToken"], "collateralToken")',
        'whole_number(data["collateralToken"], "collateralToken", text_ok=True)',
    ),
    "the collateral token one is accepted": (
        'whole_number(data["collateralToken"], "collateralToken") != 0:',
        'whole_number(data["collateralToken"], "collateralToken") > 1:',
    ),
    "a perpetual row need not be an object": (
        '            raw = row.get("name") if isinstance(row, dict) else None\n            try:\n                if not isinstance(raw, str) or PERP',
        '            raw = row.get("name")\n            try:\n                if not isinstance(raw, str) or PERP',
    ),
    "a perpetual name may be anything": (
        "                if not isinstance(raw, str) or PERP.fullmatch(raw) is None:",
        "                if not isinstance(raw, str):",
    ),
    "a repeated perpetual is accepted": (
        '                if raw in found:\n                    raise VenueDataError("the name is listed twice")\n                delisted',
        "                delisted",
    ),
    "the delisted flag may be anything": (
        '                if not isinstance(delisted, bool):\n                    raise VenueDataError("isDelisted is not true or false")\n',
        "",
    ),
    "the delisted flag defaults to true": ('delisted = row.get("isDelisted", False)', 'delisted = row.get("isDelisted", True)'),
    "the perpetual kind is wrong": ("kind=InstrumentKind.PERPETUAL,", "kind=InstrumentKind.FUTURE,"),
    "the perpetual base is the quote": ("base_raw=raw,", "base_raw=QUOTE,"),
    "the perpetual settle is not set": ("                    settle=QUOTE,", "                    settle=None,"),
    "the perpetual lot is not read": ('lot_size=_size_step(row.get("szDecimals")),', "lot_size=None,"),
    "the perpetual lot is the leverage": ('lot_size=_size_step(row.get("szDecimals")),', 'lot_size=_size_step(row.get("maxLeverage")),'),
    "a delisted perpetual is trading": (
        "status=InstrumentStatus.DELISTED if delisted else InstrumentStatus.TRADING,",
        "status=InstrumentStatus.TRADING,",
    ),
    "a live perpetual is delisted": (
        "status=InstrumentStatus.DELISTED if delisted else InstrumentStatus.TRADING,",
        "status=InstrumentStatus.DELISTED,",
    ),
    "a delisted perpetual is halted": (
        "status=InstrumentStatus.DELISTED if delisted else InstrumentStatus.TRADING,",
        "status=InstrumentStatus.HALTED if delisted else InstrumentStatus.TRADING,",
    ),
    "an unreadable perpetual is dropped": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    def _spot",
        "            except VenueDataError:\n                pass\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    def _spot",
    ),
    "an unreadable perpetual loses its name": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    def _spot",
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    def _spot",
    ),
    "spot needs no tokens": (
        '        if not isinstance(tokens_raw, list):\n            raise VenueDataError("the spot answer has no tokens")\n',
        "",
    ),
    "a token need not be an object": (
        "            if isinstance(token, dict):\n                try:",
        "            if True:\n                try:",
    ),
    "an unreadable token is a failure": (
        "                except VenueDataError:\n                    continue",
        "                except ZeroDivisionError:\n                    continue",
    ),
    "a token index may be text": (
        'whole_number(token.get("index"), "token index")',
        'whole_number(token.get("index"), "token index", text_ok=True)',
    ),
    "a token name may be empty": ('                        _text(token, "name"),\n', '                        token.get("name", ""),\n'),
    "a token step is not read": ('                        _size_step(token.get("szDecimals")),\n', '                        "1",\n'),
    "a spot row need not be an object": (
        '            raw = row.get("name") if isinstance(row, dict) else None\n            try:\n                if not isinstance(raw, str) or SPOT',
        '            raw = row.get("name")\n            try:\n                if not isinstance(raw, str) or SPOT',
    ),
    "a spot name may be anything": (
        "                if not isinstance(raw, str) or SPOT.fullmatch(raw) is None:",
        "                if not isinstance(raw, str):",
    ),
    "a repeated market is accepted": (
        '                if raw in found:\n                    raise VenueDataError("the name is listed twice")\n                pair',
        "                pair",
    ),
    "the market tokens may be anything": (
        "                if not isinstance(pair, list) or len(pair) != 2:",
        "                if not isinstance(pair, list):",
    ),
    "the market tokens may be three": (
        "                if not isinstance(pair, list) or len(pair) != 2:",
        "                if not isinstance(pair, list) or len(pair) < 2:",
    ),
    "the market tokens may be one": (
        "                if not isinstance(pair, list) or len(pair) != 2:",
        "                if not isinstance(pair, list) or len(pair) > 2:",
    ),
    "the market tokens need not be a list": (
        "                if not isinstance(pair, list) or len(pair) != 2:",
        "                if len(pair) != 2:",
    ),
    "a market token index may be text": (
        'whole_number(pair[0], "base token"), whole_number(pair[1], "quote token")',
        'whole_number(pair[0], "base token", text_ok=True), whole_number(pair[1], "quote token", text_ok=True)',
    ),
    "a missing base token is accepted": (
        "                if base_index not in tokens or quote_index not in tokens:",
        "                if quote_index not in tokens:",
    ),
    "a missing quote token is accepted": (
        "                if base_index not in tokens or quote_index not in tokens:",
        "                if base_index not in tokens:",
    ),
    "the base is the quote": (
        "                base, step, base_ref = tokens[base_index]\n                quote = tokens[quote_index][0]",
        "                base, step, base_ref = tokens[quote_index]\n                quote = tokens[base_index][0]",
    ),
    "the lot is the quote's": (
        "                base, step, base_ref = tokens[base_index]\n                quote = tokens[quote_index][0]",
        "                base, _, base_ref = tokens[base_index]\n                step = tokens[quote_index][1]\n                quote = tokens[quote_index][0]",
    ),
    "the spot kind is wrong": ("kind=InstrumentKind.SPOT,", "kind=InstrumentKind.PERPETUAL,"),
    "the spot multiplier is not read": ("multiplier=multiplier_of(base),", 'multiplier="1",'),
    "the spot settle is the quote": ("                    settle=None,", "                    settle=quote,"),
    "the spot quote class is the base": (
        "quote_class=quote_class(quote),\n                    multiplier=multiplier_of(base),",
        "quote_class=quote_class(base),\n                    multiplier=multiplier_of(base),",
    ),
    "the spot lot is not read": ("                    lot_size=step,", "                    lot_size=None,"),
    "a spot market is halted": (
        "                    min_notional=None,\n                    status=InstrumentStatus.TRADING,",
        "                    min_notional=None,\n                    status=InstrumentStatus.HALTED,",
    ),
    "an unreadable market is dropped": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    # --- errors",
        "            except VenueDataError:\n                pass\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    # --- errors",
    ),
    "an unreadable market loses its reason": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    # --- errors",
        '            except VenueDataError:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, "unreadable"))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    # --- errors',
    ),
    "the list is of another venue": (
        "        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n    # --- errors",
        '        return InstrumentList("hl", tuple(found.values()), tuple(unmapped))\n\n    # --- errors',
    ),
    "an unknown coin is not recognised": ('        if status == 500 and body.strip() == b"null":', "        if False:"),
    "an unknown coin is any server error": (
        '        if status == 500 and body.strip() == b"null":',
        '        if status >= 500 and body.strip() == b"null":',
    ),
    "an unknown coin is any status": ('        if status == 500 and body.strip() == b"null":', '        if body.strip() == b"null":'),
    "an unknown coin needs no null": ('        if status == 500 and body.strip() == b"null":', "        if status == 500:"),
    "an unknown coin needs the exact body": (
        '        if status == 500 and body.strip() == b"null":',
        '        if status == 500 and body == b"null":',
    ),
    "an unknown coin is an argument error": (
        '            return ConnectorError(ErrorClass.NOT_FOUND, "HTTP 500',
        '            return ConnectorError(ErrorClass.INVALID_ARGS, "HTTP 500',
    ),
    "an unknown coin loses its message": ('"HTTP 500 with a null body: the venue answers an unknown coin this way"', '"unknown"'),
    "an unknown coin is a null in part": (
        '        if status == 500 and body.strip() == b"null":',
        '        if status == 500 and b"null" in body:',
    ),
    "the declared model is a bucket": (
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 60.0)",
        "VenueRate(RateModel.TOKEN_BUCKET, WEIGHT_PER_MINUTE, 60.0)",
    ),
    "the declared window is a second": (
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 60.0)",
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 1.0)",
    ),
    "the declared window is half a minute": (
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 60.0)",
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 30.0)",
    ),
    "the declared limit is the info weight": (
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_PER_MINUTE, 60.0)",
        "VenueRate(RateModel.FIXED_WINDOW, WEIGHT_INFO, 60.0)",
    ),
    "the base ref is not kept": ("                    base_ref=base_ref,\n", "                    base_ref=None,\n"),
    "the base ref is the quote's": ("                    base_ref=base_ref,\n", "                    base_ref=tokens[quote_index][2],\n"),
    "the token id may be missing": (
        '                    token_id = _text(token, "tokenId")\n                    if TOKEN_ID.fullmatch(token_id) is None:\n                        raise VenueDataError("tokenId is not 0x and 32 hexadecimal digits")\n',
        '                    token_id = str(token.get("tokenId"))\n',
    ),
    "the token id may be any text": (
        '                    if TOKEN_ID.fullmatch(token_id) is None:\n                        raise VenueDataError("tokenId is not 0x and 32 hexadecimal digits")\n',
        "",
    ),
    "the token id may be upper case": (
        'TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{32}")',
        'TOKEN_ID: Final = re.compile(r"0x[0-9a-fA-F]{32}")',
    ),
    "the token id may be shorter": (
        'TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{32}")',
        'TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{31,32}")',
    ),
    "the token id may be longer": ('TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{32}")', 'TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{32,33}")'),
    "the token id may lack its prefix": (
        'TOKEN_ID: Final = re.compile(r"0x[0-9a-f]{32}")',
        'TOKEN_ID: Final = re.compile(r"(0x)?[0-9a-f]{32}")',
    ),
    "the token id is matched at the start only": ("if TOKEN_ID.fullmatch(token_id) is None", "if TOKEN_ID.match(token_id) is None"),
}

# Redundant guards without mutants: none known yet (every guard of the adapter has a profile case that fails without it).


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_hyperliquid", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
