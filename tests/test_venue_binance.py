"""The Binance adapters (spot and USD-M): the profile cases, and mutants."""

from __future__ import annotations

import inspect
import re

import pytest

from hoshitori import venue_binance
from tests.mutation import mutant
from tests.profiles_binance import spot, usdm
from tests.venue_profile import profile_failures


def failures(mod, parts=("spot", "usdm")):  # type: ignore[no-untyped-def]
    out: list[str] = []
    if "spot" in parts:
        out += profile_failures(mod, spot(mod))
    if "usdm" in parts:
        out += profile_failures(mod, usdm(mod))
    return out


def test_real_module_passes_every_case() -> None:
    assert failures(venue_binance) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    # --- constants and shape
    "an interval is missing": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 86400: "1d"}',
    ),
    "an interval is spelled wrongly": (
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4h", 86400: "1d"}',
        'INTERVALS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4h", 86400: "1d"}',
    ),
    "five minutes are three": ('300: "5m"', '300: "3m"'),
    "the daily interval is a week": ('86400: "1d"', '86400: "1w"'),
    "the futures weight limit is the spot one": ("USDM_WEIGHT_LIMIT: Final = 2400", "USDM_WEIGHT_LIMIT: Final = 6000"),
    "the weight limit is different": ("IP_WEIGHT_LIMIT: Final = 6000", "IP_WEIGHT_LIMIT: Final = 1200"),
    "symbols may be lower case": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Za-z0-9\\u4e00-\\u9fff]{2,20}")',
    ),
    "symbols may be one character": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{1,20}")',
    ),
    "symbols may be longer": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,40}")',
    ),
    "symbols may be shorter": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,19}")',
    ),
    "symbols may hold underscores": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Z0-9_\\u4e00-\\u9fff]{2,20}")',
    ),
    "futures may have no date": (
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}(_[0-9]{6})?")',
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
    ),
    "futures may have any suffix": (
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}(_[0-9]{6})?")',
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}(_[A-Z0-9]+)?")',
    ),
    "futures may have a short date": (
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}(_[0-9]{6})?")',
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}(_[0-9]{2,6})?")',
    ),
    "a symbol is matched in part": (
        "        return self.symbol_pattern.fullmatch(raw_id) is not None",
        "        return self.symbol_pattern.match(raw_id) is not None",
    ),
    "the futures use the spot pattern": (
        "    symbol_pattern: ClassVar[re.Pattern[str]] = FUTURE_SYMBOL",
        "    symbol_pattern: ClassVar[re.Pattern[str]] = SYMBOL",
    ),
    "the spot uses the futures pattern": (
        "    symbol_pattern: ClassVar[re.Pattern[str]] = SYMBOL\n\n    def valid_instrument",
        "    symbol_pattern: ClassVar[re.Pattern[str]] = FUTURE_SYMBOL\n\n    def valid_instrument",
    ),
    "the spot path is the futures path": (
        '    venue: ClassVar[str] = "binance-spot"\n    api: ClassVar[str] = "/api/v3"',
        '    venue: ClassVar[str] = "binance-spot"\n    api: ClassVar[str] = "/fapi/v1"',
    ),
    "the futures path is the spot path": (
        '    venue: ClassVar[str] = "binance-usdm"\n    api: ClassVar[str] = "/fapi/v1"',
        '    venue: ClassVar[str] = "binance-usdm"\n    api: ClassVar[str] = "/api/v3"',
    ),
    "the futures venue has another name": ('venue: ClassVar[str] = "binance-usdm"', 'venue: ClassVar[str] = "binance-futures"'),
    "the futures have no kinds": (
        'instrument_kinds: ClassVar[tuple[str, ...]] = ("perpetual", "future")',
        "instrument_kinds: ClassVar[tuple[str, ...]] = ()",
    ),
    "the futures have a third kind": (
        'instrument_kinds: ClassVar[tuple[str, ...]] = ("perpetual", "future")',
        'instrument_kinds: ClassVar[tuple[str, ...]] = ("perpetual", "future", "spot")',
    ),
    # --- requests
    "the time path is wrong": (
        '            return VenueRequest("GET", f"{self.api}/time")',
        '            return VenueRequest("GET", f"{self.api}/ping")',
    ),
    "the time is a POST": (
        '            return VenueRequest("GET", f"{self.api}/time")',
        '            return VenueRequest("POST", f"{self.api}/time")',
    ),
    "the interval is not read": (
        '            interval_s = int(args["interval_s"])\n            start, end',
        "            interval_s = 60\n            start, end",
    ),
    "the symbol is wrong": ('                "symbol": str(args["instrument"]),', '                "symbol": "BTCUSDT",'),
    "the interval is the seconds": ('                "interval": INTERVALS[interval_s],', '                "interval": str(interval_s),'),
    "the start is in seconds": ('                "startTime": to_ms(start),', '                "startTime": to_ms(start) // 1000,'),
    "the start is the end": ('                "startTime": to_ms(start),', '                "startTime": to_ms(end),'),
    "the end is inclusive": ('                "endTime": to_ms(end) - 1,', '                "endTime": to_ms(end),'),
    "the end is two milliseconds early": ('                "endTime": to_ms(end) - 1,', '                "endTime": to_ms(end) - 2,'),
    "the limit is the bar count": (
        "            bars = -(-(end - start) // timedelta(seconds=interval_s)) + 1",
        "            bars = -(-(end - start) // timedelta(seconds=interval_s))",
    ),
    "the limit rounds down": (
        "            bars = -(-(end - start) // timedelta(seconds=interval_s)) + 1",
        "            bars = (end - start) // timedelta(seconds=interval_s) + 1",
    ),
    "the limit is a constant": ('                "limit": bars,', '                "limit": 500,'),
    "the query is not sorted": ("urlencode(sorted(query.items()))", "urlencode(list(query.items()))"),
    "the klines path is wrong": (
        'f"{self.api}/klines?{urlencode(sorted(query.items()))}"',
        'f"{self.api}/uiKlines?{urlencode(sorted(query.items()))}"',
    ),
    "the list is requested whole with permission sets": (
        '        return f"{self.api}/exchangeInfo?showPermissionSets=false"',
        '        return f"{self.api}/exchangeInfo"',
    ),
    "the list is requested for trading symbols only": (
        '        return f"{self.api}/exchangeInfo?showPermissionSets=false"',
        '        return f"{self.api}/exchangeInfo?showPermissionSets=false&symbolStatus=TRADING"',
    ),
    "the futures list asks for something": (
        '        return f"{self.api}/exchangeInfo"\n\n    def cost',
        '        return f"{self.api}/exchangeInfo?symbol=BTCUSDT"\n\n    def cost',
    ),
    "an unknown operation is a request": (
        '        if operation == "instruments":\n            return VenueRequest("GET", self.instruments_path())\n        raise KeyError(operation)',
        '        return VenueRequest("GET", self.instruments_path())',
    ),
    # --- time and bars
    "the time key is wrong": (
        '        if not isinstance(data, dict) or "serverTime" not in data:',
        '        if not isinstance(data, dict) or "time" not in data:',
    ),
    "the time need not be an object": (
        '        if not isinstance(data, dict) or "serverTime" not in data:',
        '        if "serverTime" not in data:',
    ),
    "the time is in seconds": (
        '        return ms_to_datetime(whole_number(data["serverTime"], "serverTime"))',
        '        return ms_to_datetime(whole_number(data["serverTime"], "serverTime") * 1000)',
    ),
    "the time may be text": (
        'return ms_to_datetime(whole_number(data["serverTime"], "serverTime"))',
        'return ms_to_datetime(whole_number(data["serverTime"], "serverTime", text_ok=True))',
    ),
    "klines need not be a list": ('        if not isinstance(rows, list):\n            raise VenueDataError("klines are not a list")', ""),
    "a row may be short": (
        "            if not isinstance(row, list) or len(row) < 9:",
        "            if not isinstance(row, list) or len(row) < 6:",
    ),
    "a row may be long enough with eight members": (
        "            if not isinstance(row, list) or len(row) < 9:",
        "            if not isinstance(row, list) or len(row) < 8:",
    ),
    "a row need not be a list": ("            if not isinstance(row, list) or len(row) < 9:", "            if len(row) < 9:"),
    "the open time is in seconds": (
        'opened = ms_to_datetime(whole_number(row[0], "open time"))',
        'opened = ms_to_datetime(whole_number(row[0], "open time") * 1000)',
    ),
    "the open time may be text": ('whole_number(row[0], "open time")', 'whole_number(row[0], "open time", text_ok=True)'),
    "the close time is not checked": (
        '            if whole_number(row[6], "close time") + 1 != to_ms(opened) + interval_s * 1000:\n                raise VenueDataError("a bar\'s close time does not match the interval asked for")\n',
        "",
    ),
    "the close time may be the next open": (
        '            if whole_number(row[6], "close time") + 1 != to_ms(opened) + interval_s * 1000:',
        '            if whole_number(row[6], "close time") != to_ms(opened) + interval_s * 1000:',
    ),
    "the close time may be late": (
        '            if whole_number(row[6], "close time") + 1 != to_ms(opened) + interval_s * 1000:',
        '            if whole_number(row[6], "close time") + 1 < to_ms(opened) + interval_s * 1000:',
    ),
    "the close time may be early": (
        '            if whole_number(row[6], "close time") + 1 != to_ms(opened) + interval_s * 1000:',
        '            if whole_number(row[6], "close time") + 1 > to_ms(opened) + interval_s * 1000:',
    ),
    "the close time is read from the wrong member": ('whole_number(row[6], "close time")', 'whole_number(row[7], "close time")'),
    "the bar lasts a minute": (
        "                    close_time=opened + step,",
        "                    close_time=opened + timedelta(seconds=60),",
    ),
    "the bar lasts as long as it likes": (
        "                    close_time=opened + step,",
        "                    close_time=opened + timedelta(milliseconds=to_ms(opened) % 1000 + 60000),",
    ),
    "the open is read from the wrong member": ('open=decimal_text(row[1], "open"),', 'open=decimal_text(row[4], "open"),'),
    "the high is read from the wrong member": ('high=decimal_text(row[2], "high"),', 'high=decimal_text(row[3], "high"),'),
    "the low is read from the wrong member": ('low=decimal_text(row[3], "low"),', 'low=decimal_text(row[2], "low"),'),
    "the close is read from the wrong member": ('close=decimal_text(row[4], "close"),', 'close=decimal_text(row[1], "close"),'),
    "the volume is read from the wrong member": ('volume=decimal_text(row[5], "volume"),', 'volume=decimal_text(row[7], "volume"),'),
    "the quote volume is read from the wrong member": (
        'quote_volume=decimal_text(row[7], "quote volume"),',
        'quote_volume=decimal_text(row[5], "quote volume"),',
    ),
    "the trades are read from the wrong member": ('trades=whole_number(row[8], "trades"),', 'trades=whole_number(row[0], "trades"),'),
    "the trades may be text": ('trades=whole_number(row[8], "trades"),', 'trades=whole_number(row[8], "trades", text_ok=True),'),
    "the bars are not returned": ("        return bars\n\n    # --- errors", "        return []\n\n    # --- errors"),
    # --- errors and rates
    "a ban is not recognised": (
        '        if status == 418:\n            return ConnectorError(ErrorClass.BANNED, "the IP is banned (HTTP 418)", retry_after_s=wait)\n',
        "",
    ),
    "a ban is a rate limit": (
        'ConnectorError(ErrorClass.BANNED, "the IP is banned (HTTP 418)", retry_after_s=wait)',
        'ConnectorError(ErrorClass.RATE_LIMIT, "the IP is banned (HTTP 418)", retry_after_s=wait)',
    ),
    "a ban loses its wait": (
        'ConnectorError(ErrorClass.BANNED, "the IP is banned (HTTP 418)", retry_after_s=wait)',
        'ConnectorError(ErrorClass.BANNED, "the IP is banned (HTTP 418)")',
    ),
    "the wait is not read": ('        wait = parse_retry_after(headers.get("retry-after"), now)', "        wait = None"),
    "successes are inspected for errors": (
        "        if status < 400:\n            return None",
        "        if False:\n            return None",
    ),
    "the success limit is wrong": ("        if status < 400:", "        if status <= 400:"),
    "the error body is read with a larger bound": (
        "            data = jsonnum.parse(body, max_bytes=4096)",
        "            data = jsonnum.parse(body, max_bytes=1)",
    ),
    "a bad error body is raised": (
        "        except jsonnum.JsonError:\n            return None\n        if not isinstance(data, dict)",
        "        except ZeroDivisionError:\n            return None\n        if not isinstance(data, dict)",
    ),
    "an error body need not be an object": (
        '        if not isinstance(data, dict) or not isinstance(data.get("msg"), str):',
        '        if not isinstance(data.get("msg"), str):',
    ),
    "an error message may be missing": (' or not isinstance(data.get("msg"), str):', ":"),
    "a fractional code is a crash": (
        "        except VenueDataError:\n            return None\n        message",
        "        except ZeroDivisionError:\n            return None\n        message",
    ),
    "the message is not cut": ("        message = f\"{code} {data['msg']}\"[:200]", "        message = f\"{code} {data['msg']}\""),
    "the code is not in the message": ("        message = f\"{code} {data['msg']}\"[:200]", "        message = f\"{data['msg']}\"[:200]"),
    "a rate limit loses its wait": (
        "        return ConnectorError(error_class, message, retry_after_s=wait)",
        "        return ConnectorError(error_class, message)",
    ),
    "an unknown symbol is an argument error": (
        "        elif code == -1121:\n            error_class = ErrorClass.NOT_FOUND",
        "        elif code == -1121:\n            error_class = ErrorClass.INVALID_ARGS",
    ),
    "an unknown symbol is not recognised": ("        elif code == -1121:", "        elif False:"),
    "the used weight is not read": (
        '        used = headers.get("x-mbx-used-weight-1m")',
        '        used = headers.get("x-mbx-used-weight")',
    ),
    "a used weight that is text is read": (
        '        if used is None or re.fullmatch(r"[0-9]{1,9}", used.strip()) is None:',
        "        if used is None:",
    ),
    "a negative used weight is read": ('re.fullmatch(r"[0-9]{1,9}", used.strip())', 're.fullmatch(r"-?[0-9]{1,9}", used.strip())'),
    "a fractional used weight is read": (
        're.fullmatch(r"[0-9]{1,9}", used.strip())',
        're.fullmatch(r"[0-9]{1,9}(\\.[0-9]+)?", used.strip())',
    ),
    "a long used weight is read": ('re.fullmatch(r"[0-9]{1,9}", used.strip())', 're.fullmatch(r"[0-9]{1,20}", used.strip())'),
    "a padded used weight is not read": ('re.fullmatch(r"[0-9]{1,9}", used.strip())', 're.fullmatch(r"[0-9]{1,9}", used)'),
    "the used weight is subtracted from nothing": (
        "remaining=float(max(self.weight_limit - int(used), 0))",
        "remaining=float(max(0 - int(used), 0))",
    ),
    "the remaining weight may be negative": (
        "remaining=float(max(self.weight_limit - int(used), 0))",
        "remaining=float(self.weight_limit - int(used))",
    ),
    "the remaining weight is the used weight": ("remaining=float(max(self.weight_limit - int(used), 0))", "remaining=float(int(used))"),
    "the reset is the date": (
        "        reset = None if moment is None else moment.replace(second=0, microsecond=0) + timedelta(minutes=1)",
        "        reset = moment",
    ),
    "the reset is the minute start": (
        "        reset = None if moment is None else moment.replace(second=0, microsecond=0) + timedelta(minutes=1)",
        "        reset = None if moment is None else moment.replace(second=0, microsecond=0)",
    ),
    "the reset is two minutes away": (
        "moment.replace(second=0, microsecond=0) + timedelta(minutes=1)",
        "moment.replace(second=0, microsecond=0) + timedelta(minutes=2)",
    ),
    "the date is not passed": ("reset_at=reset, response_date=moment)", "reset_at=reset)"),
    "the reset is not passed": (
        "remaining=float(max(self.weight_limit - int(used), 0)), reset_at=reset, response_date=moment)",
        "remaining=float(max(self.weight_limit - int(used), 0)), response_date=moment)",
    ),
    # --- instruments
    "the filter is looked up loosely": (
        '        if isinstance(item, dict) and item.get("filterType") == name:',
        '        if isinstance(item, dict) and name in str(item.get("filterType")):',
    ),
    "filters need not be a list": (
        "    if not isinstance(filters, list):\n        return None\n    for item in filters:",
        "    for item in filters or []:",
    ),
    "filter items need not be objects": (
        '        if isinstance(item, dict) and item.get("filterType") == name:',
        '        if item.get("filterType") == name:',
    ),
    "a missing filter field is a crash": (
        "    if item is None or field not in item:\n        return None",
        "    if item is None:\n        return None",
    ),
    "a text member may be empty": (
        "    if not isinstance(value, str) or not value:\n        raise VenueDataError",
        "    if not isinstance(value, str):\n        raise VenueDataError",
    ),
    "a text member may be a number": (
        "    if not isinstance(value, str) or not value:\n        raise VenueDataError",
        "    if value is None:\n        raise VenueDataError",
    ),
    "a filter value is not read as a decimal": ('    return decimal_text(item[field], f"{name}.{field}")', "    return str(item[field])"),
    "the list need not be an object": (
        '        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):\n            raise VenueDataError("the exchange information has no symbols list")\n        found: dict[str, Instrument] = {}\n        unmapped',
        '        if not isinstance(data.get("symbols"), list):\n            raise VenueDataError("the exchange information has no symbols list")\n        found: dict[str, Instrument] = {}\n        unmapped',
    ),
    "the symbols need not be a list": (
        '        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):\n            raise VenueDataError("the exchange information has no symbols list")\n        found: dict[str, Instrument] = {}\n        unmapped',
        '        if not isinstance(data, dict):\n            raise VenueDataError("the exchange information has no symbols list")\n        found: dict[str, Instrument] = {}\n        unmapped',
    ),
    "the futures list need not be an object": (
        '        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):\n            raise VenueDataError("the exchange information has no symbols list")\n        wanted',
        '        if not isinstance(data.get("symbols"), list):\n            raise VenueDataError("the exchange information has no symbols list")\n        wanted',
    ),
    "the futures symbols need not be a list": (
        '        if not isinstance(data, dict) or not isinstance(data.get("symbols"), list):\n            raise VenueDataError("the exchange information has no symbols list")\n        wanted',
        '        if not isinstance(data, dict):\n            raise VenueDataError("the exchange information has no symbols list")\n        wanted',
    ),
    "a spot symbol may be anything": (
        "                if not isinstance(raw, str) or SYMBOL.fullmatch(raw) is None:",
        "                if not isinstance(raw, str):",
    ),
    "a spot symbol need not be text": (
        "                if not isinstance(raw, str) or SYMBOL.fullmatch(raw) is None:",
        "                if SYMBOL.fullmatch(str(raw)) is None:",
    ),
    "a repeated spot symbol is accepted": (
        '                if raw in found:\n                    raise VenueDataError("the symbol is listed twice")\n',
        "",
    ),
    "the last repeated spot symbol wins": (
        '                if raw in found:\n                    raise VenueDataError("the symbol is listed twice")\n',
        "                pass\n",
    ),
    "the spot base is not read": (
        'base, quote, status = _text(row, "baseAsset"), _text(row, "quoteAsset"), _text(row, "status")',
        'base, quote, status = "X", _text(row, "quoteAsset"), _text(row, "status")',
    ),
    "the spot quote is not read": (
        'base, quote, status = _text(row, "baseAsset"), _text(row, "quoteAsset"), _text(row, "status")',
        'base, quote, status = _text(row, "baseAsset"), "USDT", _text(row, "status")',
    ),
    "the spot status is not read": (
        'base, quote, status = _text(row, "baseAsset"), _text(row, "quoteAsset"), _text(row, "status")',
        'base, quote, status = _text(row, "baseAsset"), _text(row, "quoteAsset"), "TRADING"',
    ),
    "the spot kind is wrong": ("                    kind=InstrumentKind.SPOT,", "                    kind=InstrumentKind.PERPETUAL,"),
    "the spot settle is set": (
        "                    multiplier=multiplier_of(base),\n                    settle=None,",
        "                    multiplier=multiplier_of(base),\n                    settle=quote,",
    ),
    "the spot tick is the lot": (
        'tick_size=_increment(row.get("filters"), "PRICE_FILTER", "tickSize"),\n                    lot_size=_increment(row.get("filters"), "LOT_SIZE", "stepSize"),\n                    min_notional=_increment(row.get("filters"), "NOTIONAL"',
        'tick_size=_increment(row.get("filters"), "LOT_SIZE", "stepSize"),\n                    lot_size=_increment(row.get("filters"), "LOT_SIZE", "stepSize"),\n                    min_notional=_increment(row.get("filters"), "NOTIONAL"',
    ),
    "the spot lot is the min quantity": (
        'lot_size=_increment(row.get("filters"), "LOT_SIZE", "stepSize"),\n                    min_notional=_increment(row.get("filters"), "NOTIONAL"',
        'lot_size=_increment(row.get("filters"), "LOT_SIZE", "minQty"),\n                    min_notional=_increment(row.get("filters"), "NOTIONAL"',
    ),
    "the spot notional is not read": (
        'min_notional=_increment(row.get("filters"), "NOTIONAL", "minNotional")\n                    or _increment(row.get("filters"), "MIN_NOTIONAL", "minNotional"),',
        "min_notional=None,",
    ),
    "the old notional filter is not read": ('\n                    or _increment(row.get("filters"), "MIN_NOTIONAL", "minNotional"),', ","),
    "the new notional filter is not read": (
        'min_notional=_increment(row.get("filters"), "NOTIONAL", "minNotional")\n                    or _increment(row.get("filters"), "MIN_NOTIONAL", "minNotional"),',
        'min_notional=_increment(row.get("filters"), "MIN_NOTIONAL", "minNotional"),',
    ),
    "a trading spot symbol is halted": (
        '                    status=InstrumentStatus.TRADING\n                    if status == "TRADING"',
        '                    status=InstrumentStatus.HALTED\n                    if status == "TRADING"',
    ),
    "a halted spot symbol is trading": (
        "                    else InstrumentStatus.HALTED\n                    if status in SPOT_HALTED",
        "                    else InstrumentStatus.TRADING\n                    if status in SPOT_HALTED",
    ),
    "an unknown spot status is halted": (
        "                    else InstrumentStatus.UNKNOWN,\n                )",
        "                    else InstrumentStatus.HALTED,\n                )",
    ),
    "an unreadable spot row is dropped": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
        "            except VenueDataError:\n                pass\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
    ),
    "an unreadable spot row loses its id": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
    ),
    "an unreadable spot row loses its reason": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
        "            except VenueDataError:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, 'unreadable'))\n        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
    ),
    "the spot venue is wrong": (
        "        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm",
        '        return InstrumentList("binance", tuple(found.values()), tuple(unmapped))\n\n\nclass BinanceUsdm',
    ),
    "the spot cost of time is wrong": (
        '        return {"time": 1.0, "candles": 2.0, "instruments": 20.0}[operation]',
        '        return {"time": 2.0, "candles": 2.0, "instruments": 20.0}[operation]',
    ),
    "the spot cost of candles is wrong": (
        '        return {"time": 1.0, "candles": 2.0, "instruments": 20.0}[operation]',
        '        return {"time": 1.0, "candles": 1.0, "instruments": 20.0}[operation]',
    ),
    "the spot cost of the list is wrong": (
        '        return {"time": 1.0, "candles": 2.0, "instruments": 20.0}[operation]',
        '        return {"time": 1.0, "candles": 2.0, "instruments": 10.0}[operation]',
    ),
    "the futures cost of the list is wrong": (
        '        if operation != "candles":\n            return 1.0',
        '        if operation != "candles":\n            return 20.0',
    ),
    "the futures cost ignores the window": ("        return 1.0 if limit < 100 else 2.0 ", "        return 1.0 if limit < 1000 else 2.0 "),
    "the futures band starts late": ("        return 1.0 if limit < 100 else 2.0 ", "        return 1.0 if limit <= 100 else 2.0 "),
    "the futures band starts early": ("        return 1.0 if limit < 100 else 2.0 ", "        return 1.0 if limit < 99 else 2.0 "),
    "the futures cost is always one": ("        return 1.0 if limit < 100 else 2.0 ", "        return 1.0 if limit < 100 else 1.0 "),
    "the futures limit is not counted": (
        '        limit = -(-(end - start) // timedelta(seconds=int(args["interval_s"]))) + 1\n        return',
        '        limit = -(-(end - start) // timedelta(seconds=int(args["interval_s"])))\n        return',
    ),
    "the futures contract type is not read": (
        'contract, status = _text(row, "contractType"), _text(row, "status")',
        'contract, status = "PERPETUAL", _text(row, "status")',
    ),
    "the futures margin is not read": (
        'base, quote, margin = _text(row, "baseAsset"), _text(row, "quoteAsset"), _text(row, "marginAsset")',
        'base, quote, margin = _text(row, "baseAsset"), _text(row, "quoteAsset"), "USDT"',
    ),
    "the futures settle is the quote": ("                    settle=margin,", "                    settle=quote,"),
    "the futures settle is empty": ("                    settle=margin,", "                    settle=None,"),
    "a perpetual is a future": (
        "kind, expiry = InstrumentKind.PERPETUAL, _delivery(row)",
        "kind, expiry = InstrumentKind.FUTURE, _delivery(row)",
    ),
    "an unknown contract is a perpetual": (
        '                else:\n                    raise VenueDataError(f"the contract type {contract!r} is not one this adapter reads")',
        "                else:\n                    kind, expiry = InstrumentKind.PERPETUAL, None",
    ),
    "the delivery date is not read": (
        "                    kind, expiry = InstrumentKind.FUTURE, _delivery(row)",
        "                    kind, expiry = InstrumentKind.FUTURE, None",
    ),
    "a far delivery date is an expiry": ("ms_to_datetime(delivery) if delivery < FAR_FUTURE_MS else None", "ms_to_datetime(delivery)"),
    "every delivery date is far": ("ms_to_datetime(delivery) if delivery < FAR_FUTURE_MS else None", "None"),
    "a delivery date may be text": (
        'whole_number(row.get("deliveryDate"), "deliveryDate")',
        'whole_number(row.get("deliveryDate"), "deliveryDate", text_ok=True)',
    ),
    "the kind filter is ignored": ("                if wanted is not None and kind.value != wanted:\n                    continue\n", ""),
    "the kind filter keeps the wrong ones": (
        "                if wanted is not None and kind.value != wanted:",
        "                if wanted is not None and kind.value == wanted:",
    ),
    "the kind filter is applied to nothing": ('        wanted = args.get("kind")', "        wanted = None"),
    "a repeated futures symbol is accepted": (
        '                if raw in seen:\n                    raise VenueDataError("the symbol is listed twice")\n                seen.add(raw)\n',
        "",
    ),
    "a repeated futures symbol is only caught if kept": (
        '                if raw in seen:\n                    raise VenueDataError("the symbol is listed twice")\n                seen.add(raw)\n',
        '                if raw in found:\n                    raise VenueDataError("the symbol is listed twice")\n',
    ),
    "the futures multiplier is not read": (
        "                    multiplier=multiplier_of(base),\n                    settle=margin,",
        '                    multiplier="1",\n                    settle=margin,',
    ),
    "the futures notional filter is wrong": (
        '_increment(row.get("filters"), "MIN_NOTIONAL", "notional")',
        '_increment(row.get("filters"), "MIN_NOTIONAL", "minNotional")',
    ),
    "the futures notional is the new filter": (
        '_increment(row.get("filters"), "MIN_NOTIONAL", "notional")',
        '_increment(row.get("filters"), "NOTIONAL", "notional")',
    ),
    "the futures expiry is not recorded": ("                    contract_expiry=expiry,", "                    contract_expiry=None,"),
    "a trading future is halted": (
        '    if status == "TRADING":\n        return InstrumentStatus.TRADING\n    if status in DELIVERY_PATH',
        '    if status == "TRADING":\n        return InstrumentStatus.HALTED\n    if status in DELIVERY_PATH',
    ),
    "a pending future is trading": (
        "    if status in FUTURES_HALTED:\n        return InstrumentStatus.HALTED",
        "    if status in FUTURES_HALTED:\n        return InstrumentStatus.TRADING",
    ),
    "a delivered future is not delisted": (
        '    if status in ("DELIVERING", "DELIVERED"):\n        return InstrumentStatus.DELISTED',
        '    if status in ("DELIVERING",):\n        return InstrumentStatus.DELISTED',
    ),
    "a delivering future is not delisted": (
        '    if status in ("DELIVERING", "DELIVERED"):\n        return InstrumentStatus.DELISTED',
        '    if status in ("DELIVERED",):\n        return InstrumentStatus.DELISTED',
    ),
    "a delivered future is halted": (
        '    if status in ("DELIVERING", "DELIVERED"):\n        return InstrumentStatus.DELISTED',
        '    if status in ("DELIVERING", "DELIVERED"):\n        return InstrumentStatus.HALTED',
    ),
    "an unknown future status is trading": (
        "        return InstrumentStatus.DELISTED\n    return InstrumentStatus.UNKNOWN",
        "        return InstrumentStatus.DELISTED\n    return InstrumentStatus.TRADING",
    ),
    "the error body bound is one byte short": ("jsonnum.parse(body, max_bytes=4096)", "jsonnum.parse(body, max_bytes=4095)"),
    "the error body bound is one byte long": ("jsonnum.parse(body, max_bytes=4096)", "jsonnum.parse(body, max_bytes=4097)"),
    "the error code is required to be there": ('whole_number(data.get("code"), "code")', 'whole_number(data["code"], "code")'),
    "the spot multiplier is not read": (
        "                    multiplier=multiplier_of(base),\n                    settle=None,",
        '                    multiplier="1",\n                    settle=None,',
    ),
    "a perpetual delivery date is not read": ('_delivery(row) if "deliveryDate" in row else None', "None"),
    "a perpetual delivery date is required": ('_delivery(row) if "deliveryDate" in row else None', "_delivery(row)"),
    "a delisting is not recognised": (
        "    if status in DELIVERY_PATH and expiry is not None and server is not None and expiry <= server:",
        "    if False:",
    ),
    "a scheduled settlement is a delisting": (" and expiry <= server:", " and True:"),
    "a delisting at the instant is not one": ("expiry <= server:", "expiry < server:"),
    "a delisting needs no date": (
        "status in DELIVERY_PATH and expiry is not None and server is not None",
        "status in DELIVERY_PATH and server is not None",
    ),
    "a delisting needs no server time": (
        "status in DELIVERY_PATH and expiry is not None and server is not None",
        "status in DELIVERY_PATH and expiry is not None",
    ),
    "the server time is required": ('if "serverTime" in data else None', "if True else None"),
    "the server time is not read": (
        'server = ms_to_datetime(whole_number(data["serverTime"], "serverTime")) if "serverTime" in data else None',
        "server = None",
    ),
    "the server time may be text": (
        'whole_number(data["serverTime"], "serverTime")) if "serverTime" in data',
        'whole_number(data["serverTime"], "serverTime", text_ok=True)) if "serverTime" in data',
    ),
    "a halt past its date is a delisting": (
        'DELIVERY_PATH: Final = frozenset({"PRE_DELIVERING", "PRE_SETTLE", "SETTLING", "CLOSE"})',
        'DELIVERY_PATH: Final = frozenset({"PRE_DELIVERING", "PRE_SETTLE", "SETTLING", "CLOSE", "TRADING_HALT"})',
    ),
    "a far date is an expiry": (
        "    return ms_to_datetime(delivery) if delivery < FAR_FUTURE_MS else None",
        "    return ms_to_datetime(delivery)",
    ),
    "a date at the far limit is an expiry": ("delivery < FAR_FUTURE_MS else None", "delivery <= FAR_FUTURE_MS else None"),
    "the far limit is lower": ("FAR_FUTURE_MS: Final = 4_000_000_000_000", "FAR_FUTURE_MS: Final = 3_000_000_000_000"),
    "the far limit is higher": ("FAR_FUTURE_MS: Final = 4_000_000_000_000", "FAR_FUTURE_MS: Final = 5_000_000_000_000"),
    "a ticker may not be CJK": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Z0-9]{2,20}")',
    ),
    "a futures ticker may not be CJK": (
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}(_[0-9]{6})?")',
        'FUTURE_SYMBOL: Final = re.compile(r"[A-Z0-9]{2,20}(_[0-9]{6})?")',
    ),
    "a ticker may be hiragana": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u3040-\\u9fff]{2,20}")',
    ),
    "a ticker may be any word character": (
        'SYMBOL: Final = re.compile(r"[A-Z0-9\\u4e00-\\u9fff]{2,20}")',
        'SYMBOL: Final = re.compile(r"\\w{2,20}")',
    ),
    "the status class is not used": (
        "error_class = classify_status(status) or ErrorClass.UNCLASSIFIED",
        "error_class = ErrorClass.INVALID_ARGS",
    ),
    "the status class is always unclassified": (
        "error_class = classify_status(status) or ErrorClass.UNCLASSIFIED",
        "error_class = ErrorClass.UNCLASSIFIED",
    ),
    "the declared model is a bucket": (
        "VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 60.0)",
        "VenueRate(RateModel.TOKEN_BUCKET, IP_WEIGHT_LIMIT, 60.0)",
    ),
    "the declared window is a second": (
        "VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 60.0)",
        "VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 1.0)",
    ),
    "the declared window is two minutes": (
        "VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 60.0)",
        "VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 120.0)",
    ),
    "the declared limit is the spot order limit": (
        "VenueRate(RateModel.FIXED_WINDOW, IP_WEIGHT_LIMIT, 60.0)",
        "VenueRate(RateModel.FIXED_WINDOW, 1200, 60.0)",
    ),
}


def member_removals(constant: str, label: str) -> dict[str, tuple[str, str]]:
    """One mutant per member of a set constant of the module: the set without that member. Each member is a documented value, so each must
    have a case in the profile that fails without it."""
    source = inspect.getsource(venue_binance)
    match = re.search(rf"^{constant}: Final = frozenset\(\s*\{{(?P<members>[^}}]*)\}}\s*\)", source, flags=re.M)
    assert match is not None, constant
    raw = match.group("members")
    members = [m.strip() for m in raw.split(",") if m.strip()]
    return {
        f"{label}: {member} is not a member": ("{" + raw + "}", "{" + ", ".join(m for m in members if m != member) + "}")
        for member in members
    }


MUTATIONS.update(member_removals("_RATE_ERRORS", "rate codes"))
MUTATIONS.update(member_removals("_UPSTREAM_ERRORS", "upstream codes"))
MUTATIONS.update(member_removals("_AUTH_ERRORS", "auth codes"))
MUTATIONS.update(member_removals("SPOT_HALTED", "spot halts"))
MUTATIONS.update(member_removals("PERPETUAL_TYPES", "perpetual types"))
MUTATIONS.update(member_removals("DATED_TYPES", "dated types"))
MUTATIONS.update(member_removals("FUTURES_HALTED", "futures halts"))
MUTATIONS.update(member_removals("DELIVERY_PATH", "delivery path"))


# Redundant guards without mutants: the explicit `{"close", ...}` future statuses that the profile cannot reach with a name the venue is known to
# use (PRE_DELIVERING, CLOSE and PRE_SETTLE are read from the venue's documentation, not from a recording), and the spot-only `showPermissionSets`
# query being sent on the futures list (it is not).


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_binance", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
