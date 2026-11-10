"""The shared venue data types and rules: scenarios and mutants."""

from __future__ import annotations

import pytest

from hoshitori import venue_data
from tests.mutation import mutant
from tests.venue_data_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(venue_data) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    # --- the first whole second at or after a time
    "a fractional second is not rounded up": ("    return floor if floor == moment else floor + timedelta(seconds=1)", "    return floor"),
    "a whole second is moved on": (
        "    return floor if floor == moment else floor + timedelta(seconds=1)",
        "    return floor + timedelta(seconds=1)",
    ),
    "a fractional second is moved two seconds": (
        "    return floor if floor == moment else floor + timedelta(seconds=1)",
        "    return floor if floor == moment else floor + timedelta(seconds=2)",
    ),
    "a fractional second is rounded to the nearest": (
        "    return floor if floor == moment else floor + timedelta(seconds=1)",
        "    return floor if moment.microsecond < 500000 else floor + timedelta(seconds=1)",
    ),
    "the fraction is kept": (
        "    floor = moment.replace(microsecond=0)\n    return floor if floor == moment",
        "    floor = moment\n    return floor if floor == moment",
    ),
    # --- numbers as the venues write them
    "a number keeps its exponent": ("            return value.plain()", "            return str(value)"),
    "a plain number text is cleaned": (
        "        if DECIMAL.fullmatch(value) is not None:\n            return value",
        "        if DECIMAL.fullmatch(value) is not None:\n            return value.rstrip('0')",
    ),
    "a number text with an exponent is kept": ('        return format(parsed, "f")', "        return value"),
    "padding is accepted": (
        '        if not parsed.is_finite() or value != value.strip() or "_" in value:',
        '        if not parsed.is_finite() or "_" in value:',
    ),
    "underscores are accepted": (
        '        if not parsed.is_finite() or value != value.strip() or "_" in value:',
        "        if not parsed.is_finite() or value != value.strip():",
    ),
    "non-finite numbers are accepted": (
        '        if not parsed.is_finite() or value != value.strip() or "_" in value:',
        '        if value != value.strip() or "_" in value:',
    ),
    "a bad number text is not caught": (
        '        except InvalidOperation as exc:\n            raise VenueDataError(f"{what} is not a number: {value!r}") from exc\n        if not parsed',
        '        except ZeroDivisionError as exc:\n            raise VenueDataError(f"{what} is not a number: {value!r}") from exc\n        if not parsed',
    ),
    "a huge number is accepted": (
        '        except jsonnum.JsonError as exc:\n            raise VenueDataError(f"{what} cannot be read exactly: {exc.code}") from exc',
        '        except ZeroDivisionError as exc:\n            raise VenueDataError(f"{what} cannot be read exactly: {exc.code}") from exc',
    ),
    "other things are numbers": (
        '    raise VenueDataError(f"{what} is neither a number nor text")\n\n\ndef whole_number',
        "    return str(value)\n\n\ndef whole_number",
    ),
    "a whole number may be a fraction": (
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", text) is None:',
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})(\\.[0-9]+)?", text) is None:',
    ),
    "a whole number may have a leading zero": (
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", text) is None:',
        '    if re.fullmatch(r"-?[0-9]{1,18}", text) is None:',
    ),
    "a whole number may be longer": (
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", text) is None:',
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,30})", text) is None:',
    ),
    "a whole number may be shorter": (
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", text) is None:',
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,15})", text) is None:',
    ),
    "a whole number may be matched in part": (
        '    if re.fullmatch(r"-?(0|[1-9][0-9]{0,17})", text) is None:',
        '    if re.match(r"-?(0|[1-9][0-9]{0,17})", text) is None:',
    ),
    "a whole number may be text of anything": (
        '    elif isinstance(value, str) and text_ok:\n        text = value\n    else:\n        raise VenueDataError(f"{what} is not a JSON number" if not isinstance(value, str) else f"{what} is text, not a number")',
        "    else:\n        text = str(value)",
    ),
    "text is always a whole number": ("    elif isinstance(value, str) and text_ok:", "    elif isinstance(value, str):"),
    "text is never a whole number": ("    elif isinstance(value, str) and text_ok:", "    elif isinstance(value, str) and False:"),
    "a number is not read": (
        "    if isinstance(value, jsonnum.Num):\n        text = value.text\n    elif isinstance(value, str) and text_ok:",
        "    if isinstance(value, jsonnum.Num) and False:\n        text = value.text\n    elif isinstance(value, str) and text_ok:",
    ),
    "the message hides that it is text": (
        'f"{what} is not a JSON number" if not isinstance(value, str) else f"{what} is text, not a number"',
        'f"{what} is not a JSON number"',
    ),
    "the message hides the field": (
        'f"{what} is not a JSON number" if not isinstance(value, str) else f"{what} is text, not a number"',
        '"is not a JSON number" if not isinstance(value, str) else "is text, not a number"',
    ),
    "many bars are not checked for duplicates": (
        '    ordered = sorted(bars, key=lambda b: b.open_time)\n    for before, after in itertools.pairwise(ordered):\n        if before.open_time == after.open_time:\n            raise VenueDataError(f"two bars open at {before.open_time:%Y-%m-%dT%H:%M:%S}")\n    return ordered',
        "    ordered = sorted(bars, key=lambda b: b.open_time)\n    return ordered",
    ),
    "many bars are returned as given": (
        "    return ordered\n\n\n@dataclass(frozen=True)\nclass CandleSeries",
        "    return list(bars)\n\n\n@dataclass(frozen=True)\nclass CandleSeries",
    ),
    "many bars are reordered in place": (
        "    ordered = sorted(bars, key=lambda b: b.open_time)\n    for before",
        "    bars.sort(key=lambda b: b.open_time)\n    ordered = bars\n    for before",
    ),
    # --- decimals, quote classes, multipliers
    "any text is a decimal": ("    if not isinstance(text, str) or DECIMAL.fullmatch(text) is None:", "    if not isinstance(text, str):"),
    "a number is a decimal": (
        "    if not isinstance(text, str) or DECIMAL.fullmatch(text) is None:",
        "    if DECIMAL.fullmatch(str(text)) is None:",
    ),
    "a decimal may be matched in part": ("DECIMAL.fullmatch(text) is None", "DECIMAL.match(text) is None"),
    "a decimal may have an exponent": (
        'DECIMAL: Final = re.compile(r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?")',
        'DECIMAL: Final = re.compile(r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?([eE][0-9]+)?")',
    ),
    "a decimal may have a leading zero": ('r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?"', 'r"-?[0-9]+(\\.[0-9]+)?"'),
    "a decimal may have no integer part": ('r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?"', 'r"-?([0-9]*)(\\.[0-9]+)?"'),
    "a decimal may end in a point": ('r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?"', 'r"-?(0|[1-9][0-9]*)(\\.[0-9]*)?"'),
    "a decimal may not be negative": ('r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?"', 'r"(0|[1-9][0-9]*)(\\.[0-9]+)?"'),
    "a decimal may be unicode": ('r"-?(0|[1-9][0-9]*)(\\.[0-9]+)?"', 'r"-?(0|[1-9]\\d*)(\\.\\d+)?"'),
    "zusd is not usd": ('    if upper in ("USD", "ZUSD"):', '    if upper in ("USD",):'),
    "usd is not usd": ('    if upper in ("USD", "ZUSD"):', '    if upper in ("ZUSD",):'),
    "usdt is not usdt": ('    if upper in ("USDT", "USDC"):', '    if upper in ("USDC",):'),
    "usdc is usdt": ('    if upper in ("USDT", "USDC"):\n        return upper', '    if upper in ("USDT", "USDC"):\n        return "USDT"'),
    "quote classes are case sensitive": ("    upper = quote.upper()", "    upper = quote"),
    "everything is a dollar": ('    return "OTHER"\n\n\ndef _scale', '    return "USD"\n\n\ndef _scale'),
    "no multiplier is found": (
        '    match = MULTIPLIER.fullmatch(base)\n    if match is None:\n        return "1"',
        '    match = None\n    if match is None:\n        return "1"',
    ),
    "a multiplier needs fewer zeros": (
        'MULTIPLIER: Final = re.compile(r"(1(?:0{3,6}|M))([A-Z][A-Z0-9]*)")',
        'MULTIPLIER: Final = re.compile(r"(1(?:0{2,6}|M))([A-Z][A-Z0-9]*)")',
    ),
    "a multiplier may have more zeros": (
        'MULTIPLIER: Final = re.compile(r"(1(?:0{3,6}|M))([A-Z][A-Z0-9]*)")',
        'MULTIPLIER: Final = re.compile(r"(1(?:0{3,9}|M))([A-Z][A-Z0-9]*)")',
    ),
    "a multiplier may have fewer zeros allowed": (
        'MULTIPLIER: Final = re.compile(r"(1(?:0{3,6}|M))([A-Z][A-Z0-9]*)")',
        'MULTIPLIER: Final = re.compile(r"(1(?:0{3,5}|M))([A-Z][A-Z0-9]*)")',
    ),
    "one m is not a million": ("(1(?:0{3,6}|M))", "(1(?:0{3,6}))"),
    "the remainder may start with a digit": ('(1(?:0{3,6}|M))([A-Z][A-Z0-9]*)")', '(1(?:0{3,6}|M))([A-Z0-9]+)")'),
    "the remainder may be empty": ('(1(?:0{3,6}|M))([A-Z][A-Z0-9]*)")', '(1(?:0{3,6}|M))([A-Z0-9]*)")'),
    "a multiplier may be matched in part": (
        "    match = MULTIPLIER.fullmatch(base)\n    if match is None:",
        "    match = MULTIPLIER.match(base)\n    if match is None:",
    ),
    "one m is a thousand": ('return "1000000" if prefix == "1M" else prefix', 'return "1000" if prefix == "1M" else prefix'),
    "the multiplier is the zeros": ('return "1000000" if prefix == "1M" else prefix', 'return "1000000" if prefix == "1M" else prefix[1:]'),
    "no digit prefix is stripped": ("    if match is not None and _scale(match.group(1)) == multiplier:", "    if False:"),
    "the multiplier is not compared": ("    if match is not None and _scale(match.group(1)) == multiplier:", "    if match is not None:"),
    "a million is a thousand in a symbol": (
        "    if match is not None and _scale(match.group(1)) == multiplier:",
        "    if match is not None and match.group(1)[:5] == multiplier[:5]:",
    ),
    "a symbol is stripped by a digit prefix match in part": (
        "    match = MULTIPLIER.fullmatch(base)\n    if match is not None and _scale",
        "    match = MULTIPLIER.match(base)\n    if match is not None and _scale",
    ),
    "the digit prefix is not stripped": ("        return match.group(2)\n    thousand", "        return base\n    thousand"),
    "the digit prefix is kept whole": ("        return match.group(2)\n    thousand", "        return match.group(1)\n    thousand"),
    "a k name is not stripped": (
        '    if thousand is not None and multiplier == "1000":\n        return thousand.group(1)',
        "    if False:\n        return base",
    ),
    "a k name is stripped for any multiplier": ('    if thousand is not None and multiplier == "1000":', "    if thousand is not None:"),
    "a k name keeps its k": ("        return thousand.group(1)", "        return base"),
    "a k name may be lower case": (
        'THOUSAND: Final = re.compile(r"k([A-Z][A-Z0-9]*)")',
        'THOUSAND: Final = re.compile(r"k([A-Za-z][A-Za-z0-9]*)")',
    ),
    "a k name may start with a digit": (
        'THOUSAND: Final = re.compile(r"k([A-Z][A-Z0-9]*)")',
        'THOUSAND: Final = re.compile(r"k([A-Z0-9]+)")',
    ),
    "a k name may be empty": ('THOUSAND: Final = re.compile(r"k([A-Z][A-Z0-9]*)")', 'THOUSAND: Final = re.compile(r"k([A-Z0-9]*)")'),
    "a k name may be matched in part": ("THOUSAND.fullmatch(base)", "THOUSAND.match(base)"),
    # --- one bar
    "unsupported intervals are accepted": (
        '    if interval_s not in SUPPORTED_INTERVALS:\n        raise VenueDataError(f"interval {interval_s} s is not supported")\n',
        "",
    ),
    "the supported intervals are different": (
        "SUPPORTED_INTERVALS: Final = (60, 300, 900, 3600, 14400, 86400)",
        "SUPPORTED_INTERVALS: Final = (60, 300, 900, 3600, 14400, 86400, 120)",
    ),
    "the duration is not checked": (
        '    if close_time - open_time != timedelta(seconds=interval_s):\n        raise VenueDataError(f"the bar at {open_time:%Y-%m-%dT%H:%M:%S} does not last {interval_s} s")\n',
        "",
    ),
    "a longer bar is accepted": (
        "    if close_time - open_time != timedelta(seconds=interval_s):",
        "    if close_time - open_time < timedelta(seconds=interval_s):",
    ),
    "a shorter bar is accepted": (
        "    if close_time - open_time != timedelta(seconds=interval_s):",
        "    if close_time - open_time > timedelta(seconds=interval_s):",
    ),
    "the grid is not checked": (
        '    if int(open_time.timestamp()) % interval_s != 0 or open_time.microsecond != 0:\n        raise VenueDataError(f"the bar at {open_time:%Y-%m-%dT%H:%M:%S} is not on the {interval_s} s grid")\n',
        "",
    ),
    "microseconds are accepted": (
        "    if int(open_time.timestamp()) % interval_s != 0 or open_time.microsecond != 0:",
        "    if int(open_time.timestamp()) % interval_s != 0:",
    ),
    "the grid is the minute": (
        "    if int(open_time.timestamp()) % interval_s != 0 or open_time.microsecond != 0:",
        "    if int(open_time.timestamp()) % 60 != 0 or open_time.microsecond != 0:",
    ),
    "prices are not read": (
        "    o, h, low, c = (decimal(value, name) for value, name in prices)",
        "    o, h, low, c = (Decimal(1), Decimal(2), Decimal(1), Decimal(1))",
    ),
    "prices may be zero": ("    if min(o, h, low, c) <= 0:", "    if min(o, h, low, c) < 0:"),
    "prices may be negative": ('    if min(o, h, low, c) <= 0:\n        raise VenueDataError("a price is not positive")\n', ""),
    "only the open is checked for sign": ("    if min(o, h, low, c) <= 0:", "    if o <= 0:"),
    "the range is not checked": (
        '    if not (low <= o <= h and low <= c <= h):\n        raise VenueDataError(f"the bar at {open_time:%Y-%m-%dT%H:%M:%S} has an open or close outside its low and high")\n',
        "",
    ),
    "the open may be outside": ("    if not (low <= o <= h and low <= c <= h):", "    if not (low <= c <= h):"),
    "the close may be outside": ("    if not (low <= o <= h and low <= c <= h):", "    if not (low <= o <= h):"),
    "the open may touch nothing": ("    if not (low <= o <= h and low <= c <= h):", "    if not (low < o < h and low <= c <= h):"),
    "the low may exceed the open": ("    if not (low <= o <= h and low <= c <= h):", "    if not (o <= h and low <= c <= h):"),
    "the high may fall below the open": ("    if not (low <= o <= h and low <= c <= h):", "    if not (low <= o and low <= c <= h):"),
    "volumes are not checked": (
        '        if value is not None and decimal(value, name) < 0:\n            raise VenueDataError(f"{name} is negative")',
        "        pass",
    ),
    "negative volumes are accepted": (
        "        if value is not None and decimal(value, name) < 0:",
        "        if value is not None and decimal(value, name) < -1:",
    ),
    "volume text is accepted": (
        "        if value is not None and decimal(value, name) < 0:",
        "        if value is not None and value.startswith('-'):",
    ),
    "quote volume is not checked": (
        '    for value, name in ((candle.volume, "volume"), (candle.quote_volume, "quote volume")):',
        '    for value, name in ((candle.volume, "volume"),):',
    ),
    "volume is not checked": (
        '    for value, name in ((candle.volume, "volume"), (candle.quote_volume, "quote volume")):',
        '    for value, name in ((candle.quote_volume, "quote volume"),):',
    ),
    "trades are not checked": (
        '    if candle.trades is not None and (isinstance(candle.trades, bool) or candle.trades < 0):\n        raise VenueDataError("the number of trades is negative")\n',
        "",
    ),
    "negative trades are accepted": ("(isinstance(candle.trades, bool) or candle.trades < 0)", "isinstance(candle.trades, bool)"),
    "boolean trades are accepted": ("(isinstance(candle.trades, bool) or candle.trades < 0)", "candle.trades < 0"),
    # --- a series
    "an empty window is accepted": ('    if not start < end:\n        raise VenueDataError("the window is empty")\n', ""),
    "an empty window is a window": ("    if not start < end:", "    if start > end:"),
    "a huge window is accepted": (
        '    if (end - start).total_seconds() / interval_s > MAX_BARS:\n        raise VenueDataError(f"the window holds more than {MAX_BARS} bars")\n',
        "",
    ),
    "the window limit is one too small": (
        "    if (end - start).total_seconds() / interval_s > MAX_BARS:",
        "    if (end - start).total_seconds() / interval_s >= MAX_BARS:",
    ),
    "the bar limit is not applied": (
        '    if len(bars) > MAX_BARS:\n        raise VenueDataError(f"{len(bars)} bars is more than {MAX_BARS}")\n',
        "",
    ),
    "the bar limit is one too small": ("    if len(bars) > MAX_BARS:", "    if len(bars) >= MAX_BARS:"),
    "bars are not checked": ("    for bar in bars:\n        check_candle(bar, interval_s)\n", ""),
    "bars are not sorted": ("    ordered = sorted(bars, key=lambda b: b.open_time)", "    ordered = list(bars)"),
    "bars are sorted newest first": (
        "    ordered = sorted(bars, key=lambda b: b.open_time)",
        "    ordered = sorted(bars, key=lambda b: b.open_time, reverse=True)",
    ),
    "duplicates are accepted": (
        '    for before, after in itertools.pairwise(ordered):\n        if before.open_time == after.open_time:\n            raise VenueDataError(f"two bars open at {before.open_time:%Y-%m-%dT%H:%M:%S}")\n',
        "",
    ),
    "the window starts after its first bar": (
        "        if not start <= bar.open_time < end:",
        "        if not start < bar.open_time < end:",
    ),
    "the window includes its end": ("        if not start <= bar.open_time < end:", "        if not start <= bar.open_time <= end:"),
    "the window is not applied": ("        if not start <= bar.open_time < end:\n            continue\n", ""),
    "the clock is not consulted": (
        "        if bar.close_time > server_time or bar.confirmed is False:",
        "        if bar.confirmed is False:",
    ),
    "the venue's flag is not consulted": (
        "        if bar.close_time > server_time or bar.confirmed is False:",
        "        if bar.close_time > server_time:",
    ),
    "a bar that closes at the server time is unfinished": (
        "        if bar.close_time > server_time or bar.confirmed is False:",
        "        if bar.close_time >= server_time or bar.confirmed is False:",
    ),
    "the venue's flag counts when it is not given": (
        "        if bar.close_time > server_time or bar.confirmed is False:",
        "        if bar.close_time > server_time or not bar.confirmed:",
    ),
    "unfinished bars are not counted": ("len(unfinished), tuple(missing)", "0, tuple(missing)"),
    "unfinished bars are kept": (
        "        if bar.close_time > server_time or bar.confirmed is False:\n            unfinished.add(bar.open_time)\n        else:\n            complete.append(bar)",
        "        if bar.close_time > server_time or bar.confirmed is False:\n            unfinished.add(bar.open_time)\n        complete.append(bar)",
    ),
    "slots with a bar are missing": ("        if slot in have:\n            continue\n", ""),
    "an unfinished slot is missing": (
        "(pending if slot in unfinished or slot + timedelta(seconds=interval_s) > server_time else missing)",
        "(pending if slot + timedelta(seconds=interval_s) > server_time else missing)",
    ),
    "a slot not closed is missing": (
        "(pending if slot in unfinished or slot + timedelta(seconds=interval_s) > server_time else missing)",
        "(pending if slot in unfinished else missing)",
    ),
    "a slot closing at the server time is pending": (
        "slot + timedelta(seconds=interval_s) > server_time else missing",
        "slot + timedelta(seconds=interval_s) >= server_time else missing",
    ),
    "pending slots are not reported": ("tuple(missing), tuple(pending)\n    )", "tuple(missing), ()\n    )"),
    "missing slots are not reported": ("len(unfinished), tuple(missing), tuple(pending)", "len(unfinished), (), tuple(pending)"),
    "the grid starts before the window": ("    first = -(-micros // step) * step", "    first = (micros // step) * step"),
    "the grid starts at the window": ("    first = -(-micros // step) * step", "    first = micros"),
    "the grid ends at the window's end": (
        "    return [EPOCH + timedelta(microseconds=moment) for moment in range(first, stop, step)]",
        "    return [EPOCH + timedelta(microseconds=moment) for moment in range(first, stop + 1, step)]",
    ),
    "the grid step is wrong": ("    step = interval_s * 1_000_000", "    step = 60 * 1_000_000"),
    "a series is looked up loosely": ("            if candle.open_time == moment:", "            if candle.open_time <= moment:"),
    # --- instruments
    "the id ignores the venue": (
        'canonical.dumps({"kind": kind.value, "raw_id": raw_id, "venue": venue})',
        'canonical.dumps({"kind": kind.value, "raw_id": raw_id})',
    ),
    "the id ignores the raw id": (
        'canonical.dumps({"kind": kind.value, "raw_id": raw_id, "venue": venue})',
        'canonical.dumps({"kind": kind.value, "venue": venue})',
    ),
    "the id ignores the kind": (
        'canonical.dumps({"kind": kind.value, "raw_id": raw_id, "venue": venue})',
        'canonical.dumps({"raw_id": raw_id, "venue": venue})',
    ),
    "the id depends on the status": (
        "return instrument_id_of(self.venue, self.kind, self.raw_id)",
        "return instrument_id_of(self.venue + self.status.value, self.kind, self.raw_id)",
    ),
    "the id is short": (".hexdigest()[:32]", ".hexdigest()[:16]"),
    "an instrument is found in part": (
        "            if instrument.raw_id == raw_id:",
        "            if instrument.raw_id.lower() == raw_id.lower():",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_data", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
