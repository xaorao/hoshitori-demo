"""The Coinbase Exchange adapter: the profile cases, and mutants."""

from __future__ import annotations

import pytest

from hoshitori import venue_coinbase
from tests.mutation import mutant
from tests.profiles_coinbase import spot
from tests.venue_profile import profile_failures


def failures(mod):  # type: ignore[no-untyped-def]
    return profile_failures(mod, spot(mod))


def test_real_module_passes_every_case() -> None:
    assert failures(venue_coinbase) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    "a granularity is missing": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 86400: 86400}",
    ),
    "a granularity is spelled differently": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 1440}",
    ),
    "five minutes are three": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 180, 900: 900, 3600: 3600, 86400: 86400}",
    ),
    "the one-minute granularity is missing": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {300: 300, 900: 900, 3600: 3600, 86400: 86400}",
    ),
    "the five-minute granularity is missing": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 900: 900, 3600: 3600, 86400: 86400}",
    ),
    "the fifteen-minute granularity is missing": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 300, 3600: 3600, 86400: 86400}",
    ),
    "the daily granularity is missing": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600}",
    ),
    "a four-hour granularity is offered": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 14400: 14400, 86400: 86400}",
    ),
    "a six-hour granularity is offered": (
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 86400: 86400}",
        "GRANULARITIES: Final = {60: 60, 300: 300, 900: 900, 3600: 3600, 21600: 21600, 86400: 86400}",
    ),
    "ids may be lower case": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Za-z0-9]{1,20}-[A-Za-z0-9]{1,20}")',
    ),
    "ids may be empty on one side": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{0,20}-[A-Z0-9]{0,20}")',
    ),
    "ids may be longer on the left": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,40}-[A-Z0-9]{1,20}")',
    ),
    "ids may be longer on the right": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,40}")',
    ),
    "ids may be shorter on the left": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,19}-[A-Z0-9]{1,20}")',
    ),
    "ids may be shorter on the right": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,19}")',
    ),
    "ids may hold underscores": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9_]{1,20}-[A-Z0-9_]{1,20}")',
    ),
    "ids may use a slash": (
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'PRODUCT: Final = re.compile(r"[A-Z0-9]{1,20}[-/][A-Z0-9]{1,20}")',
    ),
    "ids may be a prefix": ("return PRODUCT.fullmatch(raw_id) is not None", "return PRODUCT.match(raw_id) is not None"),
    "the list bound is tiny": ("MAX_LIST_BYTES: Final = 8 * 1024 * 1024", "MAX_LIST_BYTES: Final = 1024"),
    "the millisecond is a second": ("_MILLISECOND: Final = timedelta(milliseconds=1)", "_MILLISECOND: Final = timedelta(seconds=1)"),
    "the millisecond is nothing": ("_MILLISECOND: Final = timedelta(milliseconds=1)", "_MILLISECOND: Final = timedelta(0)"),
    "times are written with a fraction": ('strftime("%Y-%m-%dT%H:%M:%SZ")', 'strftime("%Y-%m-%dT%H:%M:%S.000Z")'),
    "times are written without the zone": ('strftime("%Y-%m-%dT%H:%M:%SZ")', 'strftime("%Y-%m-%dT%H:%M:%S")'),
    "times are written as dates": ('strftime("%Y-%m-%dT%H:%M:%SZ")', 'strftime("%Y-%m-%d")'),
    "a flag defaults to true": ("    value = row.get(name, False)", "    value = row.get(name, True)"),
    "a flag may be anything": (
        '    if not isinstance(value, bool):\n        raise VenueDataError(f"{name} is not true or false")\n    return value',
        "    return bool(value)",
    ),
    "a text member may be empty": (
        '    if not isinstance(value, str) or not value:\n        raise VenueDataError(f"{name} is missing or not text")',
        '    if not isinstance(value, str):\n        raise VenueDataError(f"{name} is missing or not text")',
    ),
    "a text member may be anything": (
        '    if not isinstance(value, str) or not value:\n        raise VenueDataError(f"{name} is missing or not text")',
        '    if value is None:\n        raise VenueDataError(f"{name} is missing or not text")',
    ),
    "the venue is named otherwise": ('    venue: ClassVar[str] = "coinbase"', '    venue: ClassVar[str] = "coinbase-exchange"'),
    "the time path is wrong": ('            return VenueRequest("GET", "/time")', '            return VenueRequest("GET", "/ticker")'),
    "the time is a POST": ('            return VenueRequest("GET", "/time")', '            return VenueRequest("POST", "/time")'),
    "the list path is wrong": (
        '            return VenueRequest("GET", "/products")',
        '            return VenueRequest("GET", "/currencies")',
    ),
    "the candles are a POST": (
        '            return VenueRequest("GET", f"/products/{args[\'instrument\']}/candles?{urlencode(query)}")',
        '            return VenueRequest("POST", f"/products/{args[\'instrument\']}/candles?{urlencode(query)}")',
    ),
    "the product is fixed": ("f\"/products/{args['instrument']}/candles?", 'f"/products/BTC-USD/candles?'),
    "the candles path is wrong": ("/candles?{urlencode(query)}", "/trades?{urlencode(query)}"),
    "an unknown operation is a request": (
        '            return VenueRequest("GET", "/products")\n        raise KeyError(operation)',
        '            return VenueRequest("GET", "/products")\n        return VenueRequest("GET", "/")',
    ),
    "the end is inclusive": (
        '            last = parse_rfc3339(str(args["end"])) - _MILLISECOND',
        '            last = parse_rfc3339(str(args["end"]))',
    ),
    "the end is not read": (
        '            last = parse_rfc3339(str(args["end"])) - _MILLISECOND',
        '            last = parse_rfc3339(str(args["start"])) - _MILLISECOND',
    ),
    "a window of one second is refused": ("            if first > last:", "            if first >= last:"),
    "a window of no second is allowed": ("            if first > last:", "            if False:"),
    "a window of no second is the wrong error": (
        'raise ConnectorError(ErrorClass.INVALID_ARGS, "the window holds no whole second, so no bucket can open in it")',
        'raise ConnectorError(ErrorClass.UPSTREAM, "the window holds no whole second, so no bucket can open in it")',
    ),
    "the window check is turned round": ("            if first > last:", "            if first < last:"),
    "the granularity is a constant": ('"granularity": GRANULARITIES[int(args["interval_s"])]', '"granularity": 60'),
    "the granularity is the minutes": (
        '"granularity": GRANULARITIES[int(args["interval_s"])]',
        '"granularity": int(args["interval_s"]) // 60',
    ),
    "the time need not be an object": (
        '        if not isinstance(data, dict) or not isinstance(data.get("iso"), str) or not isinstance(data.get("epoch"), jsonnum.Num):',
        '        if not isinstance(data.get("iso"), str) or not isinstance(data.get("epoch"), jsonnum.Num):',
    ),
    "the iso time need not be text": (
        ' or not isinstance(data.get("iso"), str) or not isinstance(data.get("epoch"), jsonnum.Num):',
        ' or not isinstance(data.get("epoch"), jsonnum.Num):',
    ),
    "the epoch need not be a number": (' or not isinstance(data.get("epoch"), jsonnum.Num):', ":"),
    "the epoch may be text": ('not isinstance(data.get("epoch"), jsonnum.Num)', 'not isinstance(data.get("epoch"), (jsonnum.Num, str))'),
    "a bad iso time is a crash": (
        '        except ValueError as exc:\n            raise VenueDataError("iso is not an RFC 3339 time") from exc',
        '        except ZeroDivisionError as exc:\n            raise VenueDataError("iso is not an RFC 3339 time") from exc',
    ),
    # "a negative epoch is a time" was a mutant here until the time parser refused every instant before the epoch: an iso that the parser
    # accepts is never before 1970, so a negative epoch can no longer equal it, and the lower bound is kept as a second guard that no
    # input reaches alone (the case b'{"iso":"1969-12-31T23:59:59.000Z","epoch":-1}' is refused by the parser).
    "a huge epoch is a time": ("        if not 0 <= epoch <= Decimal(32_503_680_000):", "        if not 0 <= epoch:"),
    "a millisecond epoch is a time": (
        "        if not 0 <= epoch <= Decimal(32_503_680_000):",
        "        if not 0 <= epoch <= Decimal(32_503_680_000_000):",
    ),
    "the epoch limit is one past": (
        "        if not 0 <= epoch <= Decimal(32_503_680_000):",
        "        if not 0 <= epoch < Decimal(32_503_680_000):",
    ),
    "the epoch is read in seconds": ("timedelta(milliseconds=int(epoch * 1000))", "timedelta(seconds=int(epoch))"),
    "the epoch is rounded up": ("timedelta(milliseconds=int(epoch * 1000))", "timedelta(milliseconds=round(epoch * 1000) + 1)"),
    "the two times need not agree": (
        '        if from_epoch != stated:\n            raise VenueDataError("iso and epoch name different times")',
        '        if False:\n            raise VenueDataError("iso and epoch name different times")',
    ),
    "candles need not be a list": (
        '        if not isinstance(rows, list):\n            raise VenueDataError("candles are not a list")',
        "",
    ),
    "a row may be short": (
        "            if not isinstance(row, list) or len(row) < 6:",
        "            if not isinstance(row, list) or len(row) < 5:",
    ),
    "a row must be exactly six": (
        "            if not isinstance(row, list) or len(row) < 6:",
        "            if not isinstance(row, list) or len(row) != 6:",
    ),
    "a row need not be a list": ("            if not isinstance(row, list) or len(row) < 6:", "            if len(row) < 6:"),
    "the bucket start may be text": ('whole_number(row[0], "bucket start")', 'whole_number(row[0], "bucket start", text_ok=True)'),
    "the bucket start is in milliseconds": (
        's_to_datetime(whole_number(row[0], "bucket start"))',
        's_to_datetime(whole_number(row[0], "bucket start") // 1000)',
    ),
    "the open is the low": ('open=decimal_text(row[3], "open"),', 'open=decimal_text(row[1], "open"),'),
    "the open is the close": ('open=decimal_text(row[3], "open"),', 'open=decimal_text(row[4], "open"),'),
    "the high is the low": ('high=decimal_text(row[2], "high"),', 'high=decimal_text(row[1], "high"),'),
    "the high is the open": ('high=decimal_text(row[2], "high"),', 'high=decimal_text(row[3], "high"),'),
    "the low is the high": ('low=decimal_text(row[1], "low"),', 'low=decimal_text(row[2], "low"),'),
    "the low is the open": ('low=decimal_text(row[1], "low"),', 'low=decimal_text(row[3], "low"),'),
    "the close is the open": ('close=decimal_text(row[4], "close"),', 'close=decimal_text(row[3], "close"),'),
    "the close is the volume": ('close=decimal_text(row[4], "close"),', 'close=decimal_text(row[5], "close"),'),
    "the volume is the close": ('volume=decimal_text(row[5], "volume"),', 'volume=decimal_text(row[4], "volume"),'),
    "the volume is not read": ('                    volume=decimal_text(row[5], "volume"),\n', ""),
    "the bar lasts a minute": ("close_time=opened + step,", "close_time=opened + timedelta(seconds=60),"),
    "the bar has no length": ("close_time=opened + step,", "close_time=opened,"),
    "the bars are not sorted": ("        return sorted(bars, key=lambda bar: bar.open_time)", "        return bars"),
    "the bars are sorted newest first": (
        "        return sorted(bars, key=lambda bar: bar.open_time)",
        "        return sorted(bars, key=lambda bar: bar.open_time, reverse=True)",
    ),
    "the bars are sorted by close": (
        "        return sorted(bars, key=lambda bar: bar.open_time)",
        "        return sorted(bars, key=lambda bar: bar.close)",
    ),
    "the list need not be a list": (
        '        if not isinstance(data, list):\n            raise VenueDataError("the product list is not a list")',
        "",
    ),
    "a product row need not be an object": ('raw = row.get("id") if isinstance(row, dict) else None', 'raw = row.get("id")'),
    "an id may be anything": (
        "                if not isinstance(raw, str) or PRODUCT.fullmatch(raw) is None:",
        "                if not isinstance(raw, str):",
    ),
    "a repeated product is accepted": (
        '                if raw in seen:\n                    raise VenueDataError("the product is listed twice")\n                seen.add(raw)\n',
        "",
    ),
    "a repeated product is accepted if the first was unmapped": (
        '                if raw in seen:\n                    raise VenueDataError("the product is listed twice")\n                seen.add(raw)\n',
        '                if raw in found:\n                    raise VenueDataError("the product is listed twice")\n',
    ),
    "the base is not read": (
        'base, quote, status = _text(row, "base_currency"), _text(row, "quote_currency"), _text(row, "status")',
        'base, quote, status = "X", _text(row, "quote_currency"), _text(row, "status")',
    ),
    "the quote is not read": (
        'base, quote, status = _text(row, "base_currency"), _text(row, "quote_currency"), _text(row, "status")',
        'base, quote, status = _text(row, "base_currency"), "USD", _text(row, "status")',
    ),
    "the status is not read": (
        'base, quote, status = _text(row, "base_currency"), _text(row, "quote_currency"), _text(row, "status")',
        'base, quote, status = _text(row, "base_currency"), _text(row, "quote_currency"), "online"',
    ),
    "the id need not name the currencies": ('                if raw != f"{base}-{quote}":', "                if False:"),
    "the id may name them backwards": ('                if raw != f"{base}-{quote}":', '                if raw != f"{quote}-{base}":'),
    "trading_disabled is ignored": ('_flag(row, "trading_disabled") or _flag(row, "cancel_only")', '_flag(row, "cancel_only")'),
    "cancel_only is ignored": ('_flag(row, "cancel_only") or _flag(row, "post_only")', '_flag(row, "post_only")'),
    "post_only is ignored": ('_flag(row, "post_only") or _flag(row, "auction_mode")', '_flag(row, "auction_mode")'),
    "auction_mode is ignored": (' or _flag(row, "auction_mode")', ""),
    "limit_only halts": ('_flag(row, "auction_mode")', '_flag(row, "auction_mode") or _flag(row, "limit_only")'),
    "only the first flag is read": (
        '_flag(row, "trading_disabled") or _flag(row, "cancel_only") or _flag(row, "post_only") or _flag(row, "auction_mode")',
        '_flag(row, "trading_disabled")',
    ),
    "the kind is wrong": ("kind=InstrumentKind.SPOT,", "kind=InstrumentKind.PERPETUAL,"),
    "the multiplier is not read": ("multiplier=multiplier_of(base),", 'multiplier="1",'),
    "the settle is the quote": ("                    settle=None,", "                    settle=quote,"),
    "the quote class is the base": ("quote_class=quote_class(quote),", "quote_class=quote_class(base),"),
    "the tick is not read": (
        'tick_size=decimal_text(row.get("quote_increment"), "quote_increment") if "quote_increment" in row else None,',
        "tick_size=None,",
    ),
    "the tick is required": (
        'tick_size=decimal_text(row.get("quote_increment"), "quote_increment") if "quote_increment" in row else None,',
        'tick_size=decimal_text(row["quote_increment"], "quote_increment"),',
    ),
    "the tick is the lot": (
        'tick_size=decimal_text(row.get("quote_increment"), "quote_increment")',
        'tick_size=decimal_text(row.get("base_increment"), "base_increment")',
    ),
    "the lot is not read": (
        'lot_size=decimal_text(row.get("base_increment"), "base_increment") if "base_increment" in row else None,',
        "lot_size=None,",
    ),
    "the lot is required": (
        'lot_size=decimal_text(row.get("base_increment"), "base_increment") if "base_increment" in row else None,',
        'lot_size=decimal_text(row["base_increment"], "base_increment"),',
    ),
    "the lot is the tick": (
        'lot_size=decimal_text(row.get("base_increment"), "base_increment")',
        'lot_size=decimal_text(row.get("quote_increment"), "quote_increment")',
    ),
    "the notional is not read": (
        'min_notional=decimal_text(row.get("min_market_funds"), "min_market_funds") if "min_market_funds" in row else None,',
        "min_notional=None,",
    ),
    "the notional is required": (
        'min_notional=decimal_text(row.get("min_market_funds"), "min_market_funds") if "min_market_funds" in row else None,',
        'min_notional=decimal_text(row["min_market_funds"], "min_market_funds"),',
    ),
    "the notional is the tick": (
        'min_notional=decimal_text(row.get("min_market_funds"), "min_market_funds")',
        'min_notional=decimal_text(row.get("quote_increment"), "quote_increment")',
    ),
    "an unreadable row is dropped": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))",
        "            except VenueDataError:\n                pass",
    ),
    "an unreadable row loses its id": (
        "unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))",
        "unmapped.append(Unmapped(None, str(exc)))",
    ),
    "an unreadable row loses its reason": (
        "unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))",
        'unmapped.append(Unmapped(raw if isinstance(raw, str) else None, "unreadable"))',
    ),
    "the list is of another venue": (
        "        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))",
        '        return InstrumentList("coinbase-pro", tuple(found.values()), tuple(unmapped))',
    ),
    "the unmapped rows are not returned": ("tuple(found.values()), tuple(unmapped))", "tuple(found.values()), ())"),
    "a delisted product is halted": (
        '    if status == "delisted":\n        return InstrumentStatus.DELISTED',
        '    if status == "delisted":\n        return InstrumentStatus.HALTED',
    ),
    "a delisted product is not recognised": ('    if status == "delisted":', '    if status == "never":'),
    "an online product is always trading": (
        "        return InstrumentStatus.HALTED if disabled else InstrumentStatus.TRADING",
        "        return InstrumentStatus.TRADING",
    ),
    "an online product is never trading": (
        "        return InstrumentStatus.HALTED if disabled else InstrumentStatus.TRADING",
        "        return InstrumentStatus.HALTED",
    ),
    "a disabled product is trading": (
        "        return InstrumentStatus.HALTED if disabled else InstrumentStatus.TRADING",
        "        return InstrumentStatus.TRADING if disabled else InstrumentStatus.HALTED",
    ),
    "an offline product is trading": (
        '    if status == "offline":\n        return InstrumentStatus.HALTED',
        '    if status == "offline":\n        return InstrumentStatus.TRADING',
    ),
    "an offline product is unknown": (
        '    if status == "offline":\n        return InstrumentStatus.HALTED',
        '    if status == "offline":\n        return InstrumentStatus.UNKNOWN',
    ),
    "an offline product is not recognised": ('    if status == "offline":', '    if status == "never":'),
    "an unknown status is halted": (
        "        return InstrumentStatus.HALTED\n    return InstrumentStatus.UNKNOWN",
        "        return InstrumentStatus.HALTED\n    return InstrumentStatus.HALTED",
    ),
    "an unknown status is trading": (
        "    return InstrumentStatus.UNKNOWN\n\n\n__all__",
        "    return InstrumentStatus.TRADING\n\n\n__all__",
    ),
    "an online status is spelled wrongly": ('    if status == "online":', '    if status == "Online":'),
    "a status is matched in part": ('    if status == "online":', '    if "online" in status:'),
    "the end is the start": ('                "end": _iso(last),', '                "end": _iso(first),'),
    "the start is the end": ('                "start": _iso(first),', '                "start": _iso(last),'),
    "the start is not read": (
        '            first = ceil_second(parse_rfc3339(str(args["start"])))',
        '            first = ceil_second(parse_rfc3339(str(args["end"])))',
    ),
    "the start is floored": (
        '            first = ceil_second(parse_rfc3339(str(args["start"])))',
        '            first = parse_rfc3339(str(args["start"])).replace(microsecond=0)',
    ),
    "the end is a second early": (
        '            last = parse_rfc3339(str(args["end"])) - _MILLISECOND',
        '            last = parse_rfc3339(str(args["end"])) - timedelta(seconds=1)',
    ),
    "the declared model is a window": (
        "        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S",
        "        RateModel.FIXED_WINDOW, PUBLIC_BURST, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S",
    ),
    "the declared burst is the sustained rate": (
        "        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S",
        "        RateModel.TOKEN_BUCKET, PUBLIC_REQUESTS_PER_S, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S",
    ),
    "the declared refill is the burst": (
        "        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S",
        "        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_BURST",
    ),
    "the declared refill is slower": (
        "        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_BURST / PUBLIC_REQUESTS_PER_S",
        "        RateModel.TOKEN_BUCKET, PUBLIC_BURST, PUBLIC_REQUESTS_PER_S / PUBLIC_BURST",
    ),
    "the burst is another size": ("PUBLIC_BURST: Final = 15", "PUBLIC_BURST: Final = 30"),
    "the sustained rate is another size": ("PUBLIC_REQUESTS_PER_S: Final = 10", "PUBLIC_REQUESTS_PER_S: Final = 5"),
}

# Redundant guards without mutants: none known yet (every guard of the adapter has a profile case that fails without it).


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_coinbase", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
