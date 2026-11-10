"""The Kraken adapter: the profile cases, and mutants."""

from __future__ import annotations

import pytest

from hoshitori import venue_kraken
from tests.mutation import mutant
from tests.profiles_kraken import kraken
from tests.venue_profile import profile_failures


def failures(mod):  # type: ignore[no-untyped-def]
    return profile_failures(mod, kraken(mod))


def test_real_module_passes_every_case() -> None:
    assert failures(venue_kraken) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    "the 60-second interval is missing": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "the 60-second interval is spelled wrongly": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 2, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "the 300-second interval is missing": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "the 300-second interval is spelled wrongly": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 6, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "the 900-second interval is missing": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "the 900-second interval is spelled wrongly": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 16, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "the 3600-second interval is missing": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 14400: 240, 86400: 1440}",
    ),
    "the 3600-second interval is spelled wrongly": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 61, 14400: 240, 86400: 1440}",
    ),
    "the 14400-second interval is missing": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 86400: 1440}",
    ),
    "the 14400-second interval is spelled wrongly": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 241, 86400: 1440}",
    ),
    "the 86400-second interval is missing": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240}",
    ),
    "the 86400-second interval is spelled wrongly": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1441}",
    ),
    "a 30-minute interval is offered": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 1800: 30, 3600: 60, 14400: 240, 86400: 1440}",
    ),
    "a weekly interval is offered": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440, 604800: 10080}",
    ),
    "a 6-hour interval is offered": (
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 86400: 1440}",
        "INTERVALS: Final = {60: 1, 300: 5, 900: 15, 3600: 60, 14400: 240, 21600: 21600, 86400: 1440}",
    ),
    "keys may be lower case": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Za-z0-9]{2,20}")'),
    "keys may be one character": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Z0-9]{1,20}")'),
    "keys may be longer": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Z0-9]{2,40}")'),
    "keys may be shorter": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Z0-9]{2,19}")'),
    "keys may hold a dot": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Z0-9.]{2,20}")'),
    "keys may hold a slash": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Z0-9/]{2,20}")'),
    "keys may hold underscores": ('PAIR: Final = re.compile(r"[A-Z0-9]{2,20}")', 'PAIR: Final = re.compile(r"[A-Z0-9_]{2,20}")'),
    "a key is matched in part": ("return PAIR.fullmatch(raw_id) is not None", "return PAIR.match(raw_id) is not None"),
    "the error bound is one byte short": ("_ERROR_BODY_BYTES: Final = 4096", "_ERROR_BODY_BYTES: Final = 4095"),
    "the error bound is one byte long": ("_ERROR_BODY_BYTES: Final = 4096", "_ERROR_BODY_BYTES: Final = 4097"),
    "the rate limit message is not known": (
        '_RATE_MESSAGES: Final = frozenset({"EAPI:Rate limit exceeded", "EGeneral:Too many requests"})',
        '_RATE_MESSAGES: Final = frozenset({"EGeneral:Too many requests"})',
    ),
    "too many requests is not known": (
        '_RATE_MESSAGES: Final = frozenset({"EAPI:Rate limit exceeded", "EGeneral:Too many requests"})',
        '_RATE_MESSAGES: Final = frozenset({"EAPI:Rate limit exceeded"})',
    ),
    "throttled needs a space": (
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,12})")',
        '_THROTTLED: Final = re.compile(r"EService:Throttled: (?P<at>[0-9]{1,12})")',
    ),
    "throttled forbids a space": (
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,12})")',
        '_THROTTLED: Final = re.compile(r"EService:Throttled:(?P<at>[0-9]{1,12})")',
    ),
    "throttled takes few digits": (
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,12})")',
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,5})")',
    ),
    "throttled takes no digits": (
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,12})")',
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{0,12})")',
    ),
    "throttled takes letters": (
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9]{1,12})")',
        '_THROTTLED: Final = re.compile(r"EService:Throttled: ?(?P<at>[0-9a-z]{1,12})")',
    ),
    "throttled is matched in part": ("throttled = _THROTTLED.fullmatch(errors[0])", "throttled = _THROTTLED.match(errors[0])"),
    "an error entry need not be text": (
        "    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):",
        "    if not isinstance(entries, list):",
    ),
    "the error member need not be a list": (
        "    if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):",
        "    if not all(isinstance(entry, str) for entry in entries):",
    ),
    "the error member may be missing": ('    entries = data.get("error")', '    entries = data.get("error", [])'),
    "a warning is an error": ('    return [entry for entry in entries if entry.startswith("E")]', "    return list(entries)"),
    "an error is a warning": (
        '    return [entry for entry in entries if entry.startswith("E")]',
        '    return [entry for entry in entries if entry.startswith("W")]',
    ),
    "only the first letter is looked at": (
        '    return [entry for entry in entries if entry.startswith("E")]',
        '    return [entry for entry in entries if entry[:1] in "EW"]',
    ),
    "the answer need not be an object": (
        '    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):',
        '    if not isinstance(data.get("result"), dict):',
    ),
    "the result need not be an object": (
        '    if not isinstance(data, dict) or not isinstance(data.get("result"), dict):',
        "    if not isinstance(data, dict):",
    ),
    "an error beside a result is ignored": (
        '    errors = _errors(data)\n    if errors:\n        raise VenueDataError(f"the venue reported {errors[0]}")\n    result',
        "    result",
    ),
    "the result is returned whole": ('    result: Mapping[str, Any] = data["result"]\n    return result', "    return data"),
    "a rate message is not recognised": (
        '    if message in _RATE_MESSAGES or message.startswith("EService:Throttled"):',
        '    if message.startswith("EService:Throttled"):',
    ),
    "throttling is not recognised": (
        '    if message in _RATE_MESSAGES or message.startswith("EService:Throttled"):',
        "    if message in _RATE_MESSAGES:",
    ),
    "throttling is a service error": (
        '    if message in _RATE_MESSAGES or message.startswith("EService:Throttled"):',
        '    if message in _RATE_MESSAGES or message.startswith("EService:Throttled:X"):',
    ),
    "a lockout is not recognised": ('    if message == "EGeneral:Temporary lockout":', '    if message == "EGeneral:Never":'),
    "a lockout is a rate limit": (
        '    if message == "EGeneral:Temporary lockout":\n        return ErrorClass.BANNED',
        '    if message == "EGeneral:Temporary lockout":\n        return ErrorClass.RATE_LIMIT',
    ),
    "a lockout is matched in part": ('    if message == "EGeneral:Temporary lockout":', '    if message.startswith("EGeneral:Temporary"):'),
    "an invalid key is not recognised": (
        '    if message.startswith(("EAPI:Invalid", "EAuth:", "EGeneral:Permission denied")):',
        '    if message.startswith(("EAuth:", "EGeneral:Permission denied")):',
    ),
    "an authentication error is not recognised": (
        '    if message.startswith(("EAPI:Invalid", "EAuth:", "EGeneral:Permission denied")):',
        '    if message.startswith(("EAPI:Invalid", "EGeneral:Permission denied")):',
    ),
    "a denied permission is not recognised": (
        '    if message.startswith(("EAPI:Invalid", "EAuth:", "EGeneral:Permission denied")):',
        '    if message.startswith(("EAPI:Invalid", "EAuth:")):',
    ),
    "an authentication error is an argument error": ("        return ErrorClass.AUTH", "        return ErrorClass.INVALID_ARGS"),
    "any API error is an authentication error": (
        '    if message.startswith(("EAPI:Invalid", "EAuth:", "EGeneral:Permission denied")):',
        '    if message.startswith(("EAPI:", "EAuth:", "EGeneral:Permission denied")):',
    ),
    "an unknown pair is not recognised": ('    if message == "EQuery:Unknown asset pair":', '    if message == "EQuery:Never":'),
    "an unknown pair is an argument error": (
        '    if message == "EQuery:Unknown asset pair":\n        return ErrorClass.NOT_FOUND',
        '    if message == "EQuery:Unknown asset pair":\n        return ErrorClass.INVALID_ARGS',
    ),
    "an unknown pair is matched in part": (
        '    if message == "EQuery:Unknown asset pair":',
        '    if message.startswith("EQuery:Unknown"):',
    ),
    "invalid arguments are not recognised": (
        '    if message.startswith(("EGeneral:Invalid arguments", "EQuery:")):',
        '    if message.startswith(("EQuery:",)):',
    ),
    "other query errors are not recognised": (
        '    if message.startswith(("EGeneral:Invalid arguments", "EQuery:")):',
        '    if message.startswith(("EGeneral:Invalid arguments",)):',
    ),
    "invalid arguments are upstream failures": ("        return ErrorClass.INVALID_ARGS", "        return ErrorClass.UPSTREAM"),
    "an internal error is not recognised": (
        '    if message.startswith(("EGeneral:Internal error", "EService:")):',
        '    if message.startswith(("EService:",)):',
    ),
    "a service error is not recognised": (
        '    if message.startswith(("EGeneral:Internal error", "EService:")):',
        '    if message.startswith(("EGeneral:Internal error",)):',
    ),
    "an upstream failure is an argument error": (
        "        return ErrorClass.UPSTREAM\n    return ErrorClass.UNCLASSIFIED",
        "        return ErrorClass.INVALID_ARGS\n    return ErrorClass.UNCLASSIFIED",
    ),
    "an unknown error is an upstream failure": (
        "        return ErrorClass.UPSTREAM\n    return ErrorClass.UNCLASSIFIED",
        "        return ErrorClass.UPSTREAM\n    return ErrorClass.UPSTREAM",
    ),
    "an unknown error is an argument error": (
        "    return ErrorClass.UNCLASSIFIED\n\n\ndef _text",
        "    return ErrorClass.INVALID_ARGS\n\n\ndef _text",
    ),
    "a text member may be empty": ("    if not isinstance(value, str) or not value:", "    if not isinstance(value, str):"),
    "a text member may be anything": ("    if not isinstance(value, str) or not value:", "    if value is None:"),
    "the venue is named otherwise": ('    venue: ClassVar[str] = "kraken"', '    venue: ClassVar[str] = "kraken-spot"'),
    "the time path is wrong": ('VenueRequest("GET", "/0/public/Time")', 'VenueRequest("GET", "/0/public/SystemStatus")'),
    "the time is a POST": ('VenueRequest("GET", "/0/public/Time")', 'VenueRequest("POST", "/0/public/Time")'),
    "the list path is wrong": ('VenueRequest("GET", "/0/public/AssetPairs")', 'VenueRequest("GET", "/0/public/Assets")'),
    "the candles path is wrong": ('f"/0/public/OHLC?{urlencode(query)}"', 'f"/0/public/Trades?{urlencode(query)}"'),
    "the candles are a POST": (
        '            return VenueRequest("GET", f"/0/public/OHLC?{urlencode(query)}")',
        '            return VenueRequest("POST", f"/0/public/OHLC?{urlencode(query)}")',
    ),
    "an unknown operation is a request": (
        '            return VenueRequest("GET", "/0/public/AssetPairs")\n        raise KeyError(operation)',
        '            return VenueRequest("GET", "/0/public/AssetPairs")\n        return VenueRequest("GET", "/")',
    ),
    "the interval is a constant": ('"interval": INTERVALS[int(args["interval_s"])],', '"interval": 1,'),
    "the interval is in seconds": ('"interval": INTERVALS[int(args["interval_s"])],', '"interval": int(args["interval_s"]),'),
    "the pair is a constant": ('"pair": args["instrument"],', '"pair": "XXBTZUSD",'),
    "the start is not read": (
        '            start = parse_rfc3339(str(args["start"]))',
        '            start = parse_rfc3339(str(args["end"]))',
    ),
    "the start is floored": ('"since": int(ceil_second(start).timestamp()),', '"since": int(start.timestamp()),'),
    "the start is one second early": ('"since": int(ceil_second(start).timestamp()),', '"since": int(ceil_second(start).timestamp()) - 1,'),
    "the start is one second late": ('"since": int(ceil_second(start).timestamp()),', '"since": int(ceil_second(start).timestamp()) + 1,'),
    "the start is written as a float": ('"since": int(ceil_second(start).timestamp()),', '"since": ceil_second(start).timestamp(),'),
    "the since is the end": ('"since": int(ceil_second(start).timestamp()),', '"since": int(parse_rfc3339(str(args["end"])).timestamp()),'),
    "the time need not have a result": (
        "        result = _result(body)\n        moment",
        '        result = jsonnum.parse(body)["result"]\n        moment',
    ),
    "the unix time is in milliseconds": (
        's_to_datetime(whole_number(result.get("unixtime"), "unixtime"))',
        's_to_datetime(whole_number(result.get("unixtime"), "unixtime") // 1000)',
    ),
    "the unix time may be text": (
        'whole_number(result.get("unixtime"), "unixtime")',
        'whole_number(result.get("unixtime"), "unixtime", text_ok=True)',
    ),
    "the unix time is read from another member": ('result.get("unixtime"), "unixtime"', 'result.get("rfc1123"), "unixtime"'),
    "the spelled time is required": ("        if stated is not None:", "        if True:"),
    "the spelled time is ignored": ("        if stated is not None:", "        if False:"),
    "the spelled time need not be text": (
        '            if not isinstance(stated, str):\n                raise VenueDataError("rfc1123 is not text")\n',
        "",
    ),
    "a spelled time that is not a time is a crash": (
        '            except (TypeError, ValueError) as exc:\n                raise VenueDataError("rfc1123 is not a time") from exc',
        '            except ZeroDivisionError as exc:\n                raise VenueDataError("rfc1123 is not a time") from exc',
    ),
    "a spelled time may differ": ("            if spelled != moment:", "            if False:"),
    "a spelled time may differ by a second": (
        "            if spelled != moment:",
        "            if abs((spelled - moment).total_seconds()) > 1:",
    ),
    "the spelled time is returned": (
        "        return moment\n\n    def parse_candles_body",
        "        return spelled\n\n    def parse_candles_body",
    ),
    "the last is not read": ('        committed = whole_number(result.get("last"), "last")', "        committed = 10**12"),
    "the last may be text": ('whole_number(result.get("last"), "last")', 'whole_number(result.get("last"), "last", text_ok=True)'),
    "any number of pairs is read": ('        if len(pairs) != 1 or pairs[0] != args["instrument"]:', "        if len(pairs) < 1:"),
    "the pair is not compared": ('        if len(pairs) != 1 or pairs[0] != args["instrument"]:', "        if len(pairs) != 1:"),
    "the pair count is not checked": (
        '        if len(pairs) != 1 or pairs[0] != args["instrument"]:',
        '        if pairs[0] != args["instrument"]:',
    ),
    "the bars need not be a list": (
        '        if not isinstance(rows, list):\n            raise VenueDataError("the bars are not a list")\n',
        "",
    ),
    "a row may be short": (
        "            if not isinstance(row, list) or len(row) < 8:",
        "            if not isinstance(row, list) or len(row) < 7:",
    ),
    "a row must be exactly eight": (
        "            if not isinstance(row, list) or len(row) < 8:",
        "            if not isinstance(row, list) or len(row) != 8:",
    ),
    "a row need not be a list": ("            if not isinstance(row, list) or len(row) < 8:", "            if len(row) < 8:"),
    "the bar time may be text": ('whole_number(row[0], "bar open")', 'whole_number(row[0], "bar open", text_ok=True)'),
    "the open is the high": ('open=decimal_text(row[1], "open"),', 'open=decimal_text(row[2], "open"),'),
    "the open is the vwap": ('open=decimal_text(row[1], "open"),', 'open=decimal_text(row[5], "open"),'),
    "the high is the open": ('high=decimal_text(row[2], "high"),', 'high=decimal_text(row[1], "high"),'),
    "the high is the low": ('high=decimal_text(row[2], "high"),', 'high=decimal_text(row[3], "high"),'),
    "the low is the high": ('low=decimal_text(row[3], "low"),', 'low=decimal_text(row[2], "low"),'),
    "the low is the close": ('low=decimal_text(row[3], "low"),', 'low=decimal_text(row[4], "low"),'),
    "the close is the open": ('close=decimal_text(row[4], "close"),', 'close=decimal_text(row[1], "close"),'),
    "the close is the vwap": ('close=decimal_text(row[4], "close"),', 'close=decimal_text(row[5], "close"),'),
    "the volume is the vwap": ('volume=decimal_text(row[6], "volume"),', 'volume=decimal_text(row[5], "volume"),'),
    "the volume is the count": ('volume=decimal_text(row[6], "volume"),', 'volume=decimal_text(row[7], "volume"),'),
    "the count is the volume": ('trades=whole_number(row[7], "count"),', 'trades=whole_number(row[6], "count"),'),
    "the count may be text": ('trades=whole_number(row[7], "count"),', 'trades=whole_number(row[7], "count", text_ok=True),'),
    "the bar closes after a minute": ("close_time=opened + step,", "close_time=opened + timedelta(seconds=60),"),
    "a bar at the last is not final": ("confirmed=opened_s <= committed,", "confirmed=opened_s < committed,"),
    "a bar after the last is final": ("confirmed=opened_s <= committed,", "confirmed=True,"),
    "a bar before the last is not final": ("confirmed=opened_s <= committed,", "confirmed=opened_s == committed,"),
    "a bar is never final": ("confirmed=opened_s <= committed,", "confirmed=False,"),
    "the bars are not sorted": ("        return sorted(bars, key=lambda bar: bar.open_time)", "        return bars"),
    "the bars are sorted newest first": (
        "        return sorted(bars, key=lambda bar: bar.open_time)",
        "        return sorted(bars, key=lambda bar: bar.open_time, reverse=True)",
    ),
    "a key may be anything": ("                if PAIR.fullmatch(key) is None:", "                if False:"),
    "a pair need not be an object": (
        '                if not isinstance(row, dict):\n                    raise VenueDataError("the pair is not an object")\n',
        "",
    ),
    "the base is not read": (
        'base, quote, status = _text(row, "base"), _text(row, "quote"), _text(row, "status")',
        'base, quote, status = "X", _text(row, "quote"), _text(row, "status")',
    ),
    "the quote is not read": (
        'base, quote, status = _text(row, "base"), _text(row, "quote"), _text(row, "status")',
        'base, quote, status = _text(row, "base"), "ZUSD", _text(row, "status")',
    ),
    "the status is not read": (
        'base, quote, status = _text(row, "base"), _text(row, "quote"), _text(row, "status")',
        'base, quote, status = _text(row, "base"), _text(row, "quote"), "online"',
    ),
    "the kind is wrong": ("kind=InstrumentKind.SPOT,", "kind=InstrumentKind.PERPETUAL,"),
    "the raw id is the altname": ("raw_id=key,", 'raw_id=row.get("altname", key),'),
    "the multiplier is not read": ("multiplier=multiplier_of(base),", 'multiplier="1",'),
    "the settle is the quote": ("                        settle=None,", "                        settle=quote,"),
    "the quote class is the base": ("quote_class=quote_class(quote),", "quote_class=quote_class(base),"),
    "the tick is not read": (
        'tick_size=decimal_text(row.get("tick_size"), "tick_size") if "tick_size" in row else None,',
        "tick_size=None,",
    ),
    "the tick is required": (
        'tick_size=decimal_text(row.get("tick_size"), "tick_size") if "tick_size" in row else None,',
        'tick_size=decimal_text(row["tick_size"], "tick_size"),',
    ),
    "the tick is the cost": (
        'tick_size=decimal_text(row.get("tick_size"), "tick_size")',
        'tick_size=decimal_text(row.get("costmin"), "costmin")',
    ),
    "the lot is not read": ('lot_size=_lot_size(row.get("lot_decimals")) if "lot_decimals" in row else None,', "lot_size=None,"),
    "the lot is required": (
        'lot_size=_lot_size(row.get("lot_decimals")) if "lot_decimals" in row else None,',
        'lot_size=_lot_size(row["lot_decimals"]),',
    ),
    "the lot is the minimum order": (
        'lot_size=_lot_size(row.get("lot_decimals")) if "lot_decimals" in row else None,',
        'lot_size=decimal_text(row.get("ordermin"), "ordermin") if "ordermin" in row else None,',
    ),
    "the notional is not read": (
        'min_notional=decimal_text(row.get("costmin"), "costmin") if "costmin" in row else None,',
        "min_notional=None,",
    ),
    "the notional is required": (
        'min_notional=decimal_text(row.get("costmin"), "costmin") if "costmin" in row else None,',
        'min_notional=decimal_text(row["costmin"], "costmin"),',
    ),
    "the notional is the minimum order": (
        'min_notional=decimal_text(row.get("costmin"), "costmin")',
        'min_notional=decimal_text(row.get("ordermin"), "ordermin")',
    ),
    "an unreadable pair is dropped": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(key, str(exc)))",
        "            except VenueDataError:\n                pass",
    ),
    "an unreadable pair loses its reason": ("unmapped.append(Unmapped(key, str(exc)))", 'unmapped.append(Unmapped(key, "unreadable"))'),
    "an unreadable pair loses its key": ("unmapped.append(Unmapped(key, str(exc)))", "unmapped.append(Unmapped(None, str(exc)))"),
    "the list is of another venue": (
        "        return InstrumentList(self.venue, tuple(found), tuple(unmapped))",
        '        return InstrumentList("kraken-pro", tuple(found), tuple(unmapped))',
    ),
    "the unmapped pairs are not returned": ("tuple(found), tuple(unmapped))", "tuple(found), ())"),
    "a lot of no places is refused": ("    if not 0 <= places <= 30:", "    if not 1 <= places <= 30:"),
    "a negative lot is accepted": ("    if not 0 <= places <= 30:", "    if not places <= 30:"),
    "a lot of thirty-one places is accepted": ("    if not 0 <= places <= 30:", "    if not 0 <= places <= 40:"),
    "a lot of thirty places is refused": ("    if not 0 <= places <= 30:", "    if not 0 <= places < 30:"),
    "a lot is written in scientific notation": (
        '    return format(Decimal(1).scaleb(-places), "f")',
        "    return str(Decimal(1).scaleb(-places))",
    ),
    "a lot is the places": ('    return format(Decimal(1).scaleb(-places), "f")', "    return str(places)"),
    "a lot grows with the places": (
        '    return format(Decimal(1).scaleb(-places), "f")',
        '    return format(Decimal(1).scaleb(places), "f")',
    ),
    "the lot places may be text": ('whole_number(value, "lot_decimals")', 'whole_number(value, "lot_decimals", text_ok=True)'),
    "an online pair is halted": (
        '    if status in ("online", "limit_only"):\n        return InstrumentStatus.TRADING',
        '    if status in ("online", "limit_only"):\n        return InstrumentStatus.HALTED',
    ),
    "an online pair is not recognised": ('    if status in ("online", "limit_only"):', '    if status in ("limit_only",):'),
    "a limit-only pair is not recognised": ('    if status in ("online", "limit_only"):', '    if status in ("online",):'),
    "a cancel-only pair is trading": (
        '    if status in ("cancel_only", "post_only", "reduce_only"):\n        return InstrumentStatus.HALTED',
        '    if status in ("cancel_only", "post_only", "reduce_only"):\n        return InstrumentStatus.TRADING',
    ),
    "a cancel-only pair is not recognised": (
        '    if status in ("cancel_only", "post_only", "reduce_only"):',
        '    if status in ("post_only", "reduce_only"):',
    ),
    "a post-only pair is not recognised": (
        '    if status in ("cancel_only", "post_only", "reduce_only"):',
        '    if status in ("cancel_only", "reduce_only"):',
    ),
    "a reduce-only pair is not recognised": (
        '    if status in ("cancel_only", "post_only", "reduce_only"):',
        '    if status in ("cancel_only", "post_only"):',
    ),
    "an unknown status is halted": (
        "        return InstrumentStatus.HALTED\n    return InstrumentStatus.UNKNOWN",
        "        return InstrumentStatus.HALTED\n    return InstrumentStatus.HALTED",
    ),
    "an unknown status is trading": (
        "    return InstrumentStatus.UNKNOWN\n\n\n__all__",
        "    return InstrumentStatus.TRADING\n\n\n__all__",
    ),
    "a status is matched in part": ('    if status in ("online", "limit_only"):', '    if "online" in status or status == "limit_only":'),
    "an error body bound is not applied": ("data = jsonnum.parse(body, max_bytes=_ERROR_BODY_BYTES)", "data = jsonnum.parse(body)"),
    "a bad error body is a crash": (
        "        except (jsonnum.JsonError, VenueDataError):\n            return None",
        "        except ZeroDivisionError:\n            return None",
    ),
    "an error list that is not text is a crash": (
        "        except (jsonnum.JsonError, VenueDataError):",
        "        except (jsonnum.JsonError,):",
    ),
    "an error body need not be an object": (
        "            errors = _errors(data) if isinstance(data, dict) else []",
        "            errors = _errors(data)",
    ),
    "an error body that is not an object is an error": (
        "            errors = _errors(data) if isinstance(data, dict) else []",
        '            errors = _errors(data) if isinstance(data, dict) else ["EGeneral:Internal error"]',
    ),
    "an answer without errors is an error": ("        if not errors:\n            return None\n", ""),
    "the first error is not the one classified": (
        "        error_class = _classify_message(errors[0])",
        "        error_class = _classify_message(errors[-1])",
    ),
    "the wait is not read": ('        wait = parse_retry_after(headers.get("retry-after"), now)', "        wait = None"),
    "a throttled wait is not read": ("        if throttled is not None:\n            wait", "        if False:\n            wait"),
    "a throttled wait is in milliseconds": (
        'throttled.group("at")) - int(now.timestamp())',
        'throttled.group("at")) - int(now.timestamp() * 1000)',
    ),
    "a throttled wait may be negative": (
        '            wait = max(float(int(throttled.group("at")) - int(now.timestamp())), 0.0)',
        '            wait = float(int(throttled.group("at")) - int(now.timestamp()))',
    ),
    "a throttled wait is the time": (
        '            wait = max(float(int(throttled.group("at")) - int(now.timestamp())), 0.0)',
        '            wait = float(int(throttled.group("at")))',
    ),
    "a throttled wait is a minimum of a second": (
        '            wait = max(float(int(throttled.group("at")) - int(now.timestamp())), 0.0)',
        '            wait = max(float(int(throttled.group("at")) - int(now.timestamp())), 1.0)',
    ),
    "the messages are not joined": ('"; ".join(errors)[:200]', "errors[0][:200]"),
    "the messages are joined with a comma": ('"; ".join(errors)[:200]', '", ".join(errors)[:200]'),
    "the message is not cut": ('"; ".join(errors)[:200]', '"; ".join(errors)'),
    "the message is cut short": ('"; ".join(errors)[:200]', '"; ".join(errors)[:100]'),
    "the wait is not passed": (
        'ConnectorError(error_class, "; ".join(errors)[:200], retry_after_s=wait)',
        'ConnectorError(error_class, "; ".join(errors)[:200])',
    ),
    "the status does not decide an unknown error": (
        "        if error_class is ErrorClass.UNCLASSIFIED:  # the status says more than nothing",
        "        if False:",
    ),
    "the status decides every error": (
        "        if error_class is ErrorClass.UNCLASSIFIED:  # the status says more than nothing",
        "        if True:",
    ),
    "the status is not consulted": (
        "            error_class = classify_status(status) or ErrorClass.UNCLASSIFIED",
        "            error_class = ErrorClass.UNCLASSIFIED",
    ),
    "the status is the only decider": (
        "            error_class = classify_status(status) or ErrorClass.UNCLASSIFIED",
        "            error_class = classify_status(status) or ErrorClass.INVALID_ARGS",
    ),
    "the declared model is a window": (
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_MAX / COUNTER_DECAY_PER_S)",
        "VenueRate(RateModel.FIXED_WINDOW, COUNTER_MAX, COUNTER_MAX / COUNTER_DECAY_PER_S)",
    ),
    "the declared capacity is the decay": (
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_MAX / COUNTER_DECAY_PER_S)",
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_DECAY_PER_S, COUNTER_MAX / COUNTER_DECAY_PER_S)",
    ),
    "the declared refill is the inverse": (
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_MAX / COUNTER_DECAY_PER_S)",
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_DECAY_PER_S / COUNTER_MAX)",
    ),
    "the declared refill is the decay": (
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_MAX / COUNTER_DECAY_PER_S)",
        "VenueRate(RateModel.TOKEN_BUCKET, COUNTER_MAX, COUNTER_DECAY_PER_S)",
    ),
    "the counter is another size": ("COUNTER_MAX: Final = 15", "COUNTER_MAX: Final = 20"),
    "the counter decays faster": ("COUNTER_DECAY_PER_S: Final = 0.33", "COUNTER_DECAY_PER_S: Final = 0.5"),
}

# Redundant guards without mutants: none known yet (every guard of the adapter has a profile case that fails without it).


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_kraken", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
