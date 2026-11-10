"""The OKX adapter: the profile cases, and mutants."""

from __future__ import annotations

import inspect
import re

import pytest

from hoshitori import venue_okx
from tests.mutation import mutant
from tests.profiles_okx import okx
from tests.venue_profile import profile_failures


def failures(mod):  # type: ignore[no-untyped-def]
    return profile_failures(mod, okx(mod))


def test_real_module_passes_every_case() -> None:
    assert failures(venue_okx) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    "the 1m bar is missing": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 1m bar is spelled in lower case": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1mx", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 5m bar is missing": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 5m bar is spelled in lower case": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5mx", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 15m bar is missing": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 15m bar is spelled in lower case": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15mx", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 1H bar is missing": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 1H bar is spelled in lower case": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1h", 14400: "4H", 86400: "1Dutc"}',
    ),
    "the 4H bar is missing": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 86400: "1Dutc"}',
    ),
    "the 4H bar is spelled in lower case": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4h", 86400: "1Dutc"}',
    ),
    "the 1Dutc bar is missing": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H"}',
    ),
    "the 1Dutc bar is spelled in lower case": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1dutc"}',
    ),
    "the daily bar opens at UTC+8": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1D"}',
    ),
    "a 3-minute bar is offered": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 180: "3m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
    ),
    "a 6-hour bar is offered": (
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 86400: "1Dutc"}',
        'BARS: Final = {60: "1m", 300: "5m", 900: "15m", 3600: "1H", 14400: "4H", 21600: "6Hutc", 86400: "1Dutc"}',
    ),
    "the instrument types are shifted": (
        'INST_TYPES: Final = {"spot": "SPOT", "perpetual": "SWAP", "future": "FUTURES"}',
        'INST_TYPES: Final = {"spot": "SPOT", "perpetual": "FUTURES", "future": "SWAP"}',
    ),
    "the spot type is margin": (
        'INST_TYPES: Final = {"spot": "SPOT", "perpetual": "SWAP", "future": "FUTURES"}',
        'INST_TYPES: Final = {"spot": "MARGIN", "perpetual": "SWAP", "future": "FUTURES"}',
    ),
    "spot ids may be lower case": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Za-z0-9]{1,20}-[A-Za-z0-9]{1,20}")',
    ),
    "spot ids may be empty on one side": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{0,20}-[A-Z0-9]{0,20}")',
    ),
    "spot ids may be longer on the left": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,40}-[A-Z0-9]{1,20}")',
    ),
    "spot ids may be longer on the right": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,40}")',
    ),
    "spot ids may be shorter on the left": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,19}-[A-Z0-9]{1,20}")',
    ),
    "spot ids may be shorter on the right": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,19}")',
    ),
    "spot ids may hold underscores": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,20}")',
    ),
    "spot ids may lack the dash": (
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}")',
        'SPOT_ID: Final = re.compile(r"[A-Z0-9]{1,20}-?[A-Z0-9]{1,20}")',
    ),
    "swap ids may be lower case": (
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")',
        'SWAP_ID: Final = re.compile(r"[A-Za-z0-9]{1,20}-[A-Za-z0-9]{1,20}-SWAP")',
    ),
    "swap ids may lack the suffix": (
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")',
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}(-SWAP)?")',
    ),
    "swap ids may be longer": (
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")',
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,40}-[A-Z0-9]{1,20}-SWAP")',
    ),
    "swap ids may be shorter": (
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")',
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,19}-[A-Z0-9]{1,20}-SWAP")',
    ),
    "swap ids may hold underscores": (
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")',
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,20}-SWAP")',
    ),
    "swap suffixes may be lower case": (
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-SWAP")',
        'SWAP_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,20}-[Ss][Ww][Aa][Pp]")',
    ),
    "future ids may be lower case": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Za-z0-9]{1,20}-[A-Za-z0-9_]{1,30}-[0-9]{6}")',
    ),
    "future ids may lack the date": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}(-[0-9]{6})?")',
    ),
    "future dates may be shorter": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{4,6}")',
    ),
    "future dates may be longer": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6,8}")',
    ),
    "future dates may be text": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[A-Z0-9]{6}")',
    ),
    "future bases may hold underscores": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9_]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
    ),
    "future families may not hold underscores": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9]{1,30}-[0-9]{6}")',
    ),
    "future families may be longer": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,60}-[0-9]{6}")',
    ),
    "future families may be shorter": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,29}-[0-9]{6}")',
    ),
    "future bases may be longer": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,40}-[A-Z0-9_]{1,30}-[0-9]{6}")',
    ),
    "future bases may be shorter": (
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,20}-[A-Z0-9_]{1,30}-[0-9]{6}")',
        'FUTURE_ID: Final = re.compile(r"[A-Z0-9]{1,19}-[A-Z0-9_]{1,30}-[0-9]{6}")',
    ),
    "an option is valid": (
        "    return any(pattern.fullmatch(raw_id) is not None for pattern in (SPOT_ID, SWAP_ID, FUTURE_ID))",
        '    return any(pattern.fullmatch(raw_id) is not None for pattern in (SPOT_ID, SWAP_ID, FUTURE_ID, re.compile(r"[A-Z0-9]+-[A-Z0-9]+-[0-9]{6}-[0-9]+-[CP]")))',
    ),
    "spot ids are not valid": ("(SPOT_ID, SWAP_ID, FUTURE_ID))\n\n    def _normalise", "(SWAP_ID, FUTURE_ID))\n\n    def _normalise"),
    "swap ids are not valid": ("(SPOT_ID, SWAP_ID, FUTURE_ID))\n\n    def _normalise", "(SPOT_ID, FUTURE_ID))\n\n    def _normalise"),
    "future ids are not valid": ("(SPOT_ID, SWAP_ID, FUTURE_ID))\n\n    def _normalise", "(SPOT_ID, SWAP_ID))\n\n    def _normalise"),
    "an id is matched in part": (
        "pattern.fullmatch(raw_id) is not None for pattern in (SPOT_ID",
        "pattern.match(raw_id) is not None for pattern in (SPOT_ID",
    ),
    "the list bound is tiny": ("MAX_LIST_BYTES: Final = 16 * 1024 * 1024", "MAX_LIST_BYTES: Final = 1024"),
    "the window is another length": ("WINDOW_S: Final = 2.0", "WINDOW_S: Final = 1.0"),
    "the budget is another size": ("BUDGET_PER_WINDOW: Final = 20.0", "BUDGET_PER_WINDOW: Final = 40.0"),
    "the time costs a request": ('        return COST_TIME if operation == "time" else COST_REQUEST', "        return COST_REQUEST"),
    "a request costs two": ("COST_REQUEST: Final = 1.0", "COST_REQUEST: Final = 2.0"),
    "the call limit is smaller": ("MAX_BARS_PER_CALL: Final = 300", "MAX_BARS_PER_CALL: Final = 100"),
    "the call limit is larger": ("MAX_BARS_PER_CALL: Final = 300", "MAX_BARS_PER_CALL: Final = 301"),
    "the error bound is one byte short": ("_ERROR_BODY_BYTES: Final = 4096", "_ERROR_BODY_BYTES: Final = 4095"),
    "the error bound is one byte long": ("_ERROR_BODY_BYTES: Final = 4096", "_ERROR_BODY_BYTES: Final = 4097"),
    "a derivative is never recognised": (
        "    return SWAP_ID.fullmatch(instrument) is not None or FUTURE_ID.fullmatch(instrument) is not None",
        "    return False",
    ),
    "a swap is not a derivative": (
        "    return SWAP_ID.fullmatch(instrument) is not None or FUTURE_ID.fullmatch(instrument) is not None",
        "    return FUTURE_ID.fullmatch(instrument) is not None",
    ),
    "a future is not a derivative": (
        "    return SWAP_ID.fullmatch(instrument) is not None or FUTURE_ID.fullmatch(instrument) is not None",
        "    return SWAP_ID.fullmatch(instrument) is not None",
    ),
    "a spot pair is a derivative": (
        "    return SWAP_ID.fullmatch(instrument) is not None or FUTURE_ID.fullmatch(instrument) is not None",
        "    return True",
    ),
    "a text member may be empty": ("    if not isinstance(value, str) or not value:", "    if not isinstance(value, str):"),
    "a text member may be anything": ("    if not isinstance(value, str) or not value:", "    if value is None:"),
    "the answer need not be an object": (
        '    if not isinstance(parsed, dict) or parsed.get("code") != "0" or not isinstance(parsed.get("data"), list):',
        '    if parsed.get("code") != "0" or not isinstance(parsed.get("data"), list):',
    ),
    "the code need not be zero": (
        '    if not isinstance(parsed, dict) or parsed.get("code") != "0" or not isinstance(parsed.get("data"), list):',
        '    if not isinstance(parsed, dict) or not isinstance(parsed.get("data"), list):',
    ),
    "the data need not be a list": (
        '    if not isinstance(parsed, dict) or parsed.get("code") != "0" or not isinstance(parsed.get("data"), list):',
        '    if not isinstance(parsed, dict) or parsed.get("code") != "0":',
    ),
    "a volume may be negative": ('    if text.startswith("-"):\n        raise VenueDataError(f"{what} is negative")\n', ""),
    "a volume is refused when it is zero": ('    if text.startswith("-"):', '    if text.startswith("-") or text == "0":'),
    "codes are not split at the underscore": ('_error_class(data["code"].split("_")[0])', '_error_class(data["code"])'),
    "codes are split at the first digit": ('_error_class(data["code"].split("_")[0])', '_error_class(data["code"][:3])'),
    "a rate code is not recognised": (
        "    if code in _RATE_CODES:\n        return ErrorClass.RATE_LIMIT",
        "    if False:\n        return ErrorClass.RATE_LIMIT",
    ),
    "a timeout code is not recognised": (
        "    if code in _TIMEOUT_CODES:\n        return ErrorClass.TIMEOUT",
        "    if False:\n        return ErrorClass.TIMEOUT",
    ),
    "an upstream code is not recognised": (
        "    if code in _UPSTREAM_CODES:\n        return ErrorClass.UPSTREAM",
        "    if False:\n        return ErrorClass.UPSTREAM",
    ),
    "a not-found code is not recognised": (
        "    if code in _NOT_FOUND_CODES:\n        return ErrorClass.NOT_FOUND",
        "    if False:\n        return ErrorClass.NOT_FOUND",
    ),
    "an invalid code is not recognised": (
        "    if code in _INVALID_CODES:\n        return ErrorClass.INVALID_ARGS",
        "    if False:\n        return ErrorClass.INVALID_ARGS",
    ),
    "an auth code is not recognised": (
        "    if code in _AUTH_CODES:\n        return ErrorClass.AUTH",
        "    if False:\n        return ErrorClass.AUTH",
    ),
    "a region code is not recognised": (
        "    if code in _REGION_CODES:\n        return ErrorClass.GEO_BLOCKED",
        "    if False:\n        return ErrorClass.GEO_BLOCKED",
    ),
    "a rate code is an upstream failure": (
        "        return ErrorClass.RATE_LIMIT\n    if code in _TIMEOUT_CODES",
        "        return ErrorClass.UPSTREAM\n    if code in _TIMEOUT_CODES",
    ),
    "a timeout code is an upstream failure": (
        "        return ErrorClass.TIMEOUT\n    if code in _UPSTREAM_CODES",
        "        return ErrorClass.UPSTREAM\n    if code in _UPSTREAM_CODES",
    ),
    "an upstream code is a rate limit": (
        "        return ErrorClass.UPSTREAM\n    if code in _NOT_FOUND_CODES",
        "        return ErrorClass.RATE_LIMIT\n    if code in _NOT_FOUND_CODES",
    ),
    "a not-found code is an argument error": (
        "        return ErrorClass.NOT_FOUND\n    if code in _INVALID_CODES",
        "        return ErrorClass.INVALID_ARGS\n    if code in _INVALID_CODES",
    ),
    "an invalid code is not found": (
        "        return ErrorClass.INVALID_ARGS\n    if code in _AUTH_CODES",
        "        return ErrorClass.NOT_FOUND\n    if code in _AUTH_CODES",
    ),
    "an auth code is an argument error": (
        "        return ErrorClass.AUTH\n    if code in _REGION_CODES",
        "        return ErrorClass.INVALID_ARGS\n    if code in _REGION_CODES",
    ),
    "a region code is an auth failure": (
        "        return ErrorClass.GEO_BLOCKED\n    return None",
        "        return ErrorClass.AUTH\n    return None",
    ),
    "an unknown code is classified": (
        "        return ErrorClass.GEO_BLOCKED\n    return None",
        "        return ErrorClass.GEO_BLOCKED\n    return ErrorClass.INVALID_ARGS",
    ),
    "a live pair is halted": (
        '    if state == "live":\n        return InstrumentStatus.TRADING',
        '    if state == "live":\n        return InstrumentStatus.HALTED',
    ),
    "a live pair is not recognised": ('    if state == "live":', '    if state == "never":'),
    "a live pair is matched in part": ('    if state == "live":', '    if "live" in state:'),
    "a suspended pair is not a halt": (
        '    if state in ("suspend", "rebase", "post_only", "preopen"):',
        '    if state in ("rebase", "post_only", "preopen"):',
    ),
    "a rebasing pair is not a halt": (
        '    if state in ("suspend", "rebase", "post_only", "preopen"):',
        '    if state in ("suspend", "post_only", "preopen"):',
    ),
    "a post-only pair is not a halt": (
        '    if state in ("suspend", "rebase", "post_only", "preopen"):',
        '    if state in ("suspend", "rebase", "preopen"):',
    ),
    "a pre-open pair is not a halt": (
        '    if state in ("suspend", "rebase", "post_only", "preopen"):',
        '    if state in ("suspend", "rebase", "post_only"):',
    ),
    "a halt is trading": (
        "        return InstrumentStatus.HALTED\n    return InstrumentStatus.UNKNOWN",
        "        return InstrumentStatus.TRADING\n    return InstrumentStatus.UNKNOWN",
    ),
    "an unknown state is halted": ("    return InstrumentStatus.UNKNOWN\n\n\ndef _lot", "    return InstrumentStatus.HALTED\n\n\ndef _lot"),
    "an unknown state is trading": (
        "    return InstrumentStatus.UNKNOWN\n\n\ndef _lot",
        "    return InstrumentStatus.TRADING\n\n\ndef _lot",
    ),
    "a test pair is halted": (
        '    if state in ("suspend", "rebase", "post_only", "preopen"):',
        '    if state in ("suspend", "rebase", "post_only", "preopen", "test"):',
    ),
    "the size step may be missing": ('    if "lotSz" not in row:\n        return None\n', ""),
    "the size step is not read": ('    step = decimal_text(row["lotSz"], "lotSz")', '    step = "1"'),
    "a spot size step is multiplied": ("    if kind is InstrumentKind.SPOT:\n        return step", "    if False:\n        return step"),
    "a spot size step is the contract value": (
        "    if kind is InstrumentKind.SPOT:\n        return step",
        '    if kind is InstrumentKind.SPOT:\n        return format(Decimal(step) * Decimal("2"), "f")',
    ),
    "an inverse contract has a step": (
        '    if contract == "inverse":\n        return None',
        '    if contract == "inverse":\n        return step',
    ),
    "an inverse contract is not recognised": ('    if contract == "inverse":', '    if contract == "never":'),
    "a contract type may be anything": (
        '    if contract != "linear":\n        raise VenueDataError(f"the contract type {contract!r} is neither linear nor inverse")\n',
        "",
    ),
    "the contract value is ignored": (
        'return format(Decimal(step) * Decimal(decimal_text(row.get("ctVal"), "ctVal")), "f")',
        "return step",
    ),
    "the contract value is added": (
        'return format(Decimal(step) * Decimal(decimal_text(row.get("ctVal"), "ctVal")), "f")',
        'return format(Decimal(step) + Decimal(decimal_text(row.get("ctVal"), "ctVal")), "f")',
    ),
    "the step is written in scientific notation": (
        'return format(Decimal(step) * Decimal(decimal_text(row.get("ctVal"), "ctVal")), "f")',
        'return str(Decimal(step) * Decimal(decimal_text(row.get("ctVal"), "ctVal")))',
    ),
    "the contract type is not required": ('    contract = _text(row, "ctType")', '    contract = str(row.get("ctType", "linear"))'),
    "the venue is named otherwise": ('    venue: ClassVar[str] = "okx"', '    venue: ClassVar[str] = "okx-eea"'),
    "the kinds are shifted": ('("spot", "perpetual", "future")', '("spot", "perpetual")'),
    "an option kind is offered": ('("spot", "perpetual", "future")', '("spot", "perpetual", "future", "option")'),
    "a kind may be left out": ('        if "kind" not in args:', "        if False:"),
    "a missing kind is the wrong error": (
        "            raise ConnectorError(\n                ErrorClass.INVALID_ARGS,",
        "            raise ConnectorError(\n                ErrorClass.UPSTREAM,",
    ),
    "the base class is not asked": ("        return super()._normalise_instruments(args)", '        return {"kind": args["kind"]}'),
    "the time path is wrong": (
        '            return VenueRequest("GET", "/api/v5/public/time")',
        '            return VenueRequest("GET", "/api/v5/public/status")',
    ),
    "the time is a POST": (
        '            return VenueRequest("GET", "/api/v5/public/time")',
        '            return VenueRequest("POST", "/api/v5/public/time")',
    ),
    "the candles are asked from the recent endpoint": ("/api/v5/market/history-candles?", "/api/v5/market/candles?"),
    "the candles are a POST": (
        '            return VenueRequest("GET", f"/api/v5/market/history-candles?{urlencode(query)}")',
        '            return VenueRequest("POST", f"/api/v5/market/history-candles?{urlencode(query)}")',
    ),
    "the end is inclusive": ('"after": to_ms(parse_rfc3339(str(args["end"]))),', '"after": to_ms(parse_rfc3339(str(args["end"]))) + 1,'),
    "the end is early": ('"after": to_ms(parse_rfc3339(str(args["end"]))),', '"after": to_ms(parse_rfc3339(str(args["end"]))) - 1,'),
    "the end is not read": ('"after": to_ms(parse_rfc3339(str(args["end"]))),', '"after": to_ms(parse_rfc3339(str(args["start"]))),'),
    "the end is in seconds": (
        '"after": to_ms(parse_rfc3339(str(args["end"]))),',
        '"after": to_ms(parse_rfc3339(str(args["end"]))) // 1000,',
    ),
    "the start is exclusive": (
        '"before": to_ms(parse_rfc3339(str(args["start"]))) - 1,',
        '"before": to_ms(parse_rfc3339(str(args["start"]))),',
    ),
    "the start is two milliseconds early": (
        '"before": to_ms(parse_rfc3339(str(args["start"]))) - 1,',
        '"before": to_ms(parse_rfc3339(str(args["start"]))) - 2,',
    ),
    "the start is not read": (
        '"before": to_ms(parse_rfc3339(str(args["start"]))) - 1,',
        '"before": to_ms(parse_rfc3339(str(args["end"]))) - 1,',
    ),
    "the start is in seconds": (
        '"before": to_ms(parse_rfc3339(str(args["start"]))) - 1,',
        '"before": to_ms(parse_rfc3339(str(args["start"]))) // 1000 - 1,',
    ),
    "the bar is a constant": ('"bar": BARS[int(args["interval_s"])],', '"bar": "1m",'),
    "the bar is the seconds": ('"bar": BARS[int(args["interval_s"])],', '"bar": str(args["interval_s"]),'),
    "the instrument is a constant": ('"instId": args["instrument"],', '"instId": "BTC-USDT",'),
    "the limit is not sent": ('                "limit": MAX_BARS_PER_CALL,\n', ""),
    "the instrument type is a constant": (
        "f\"/api/v5/public/instruments?instType={INST_TYPES[args['kind']]}\"",
        '"/api/v5/public/instruments?instType=SPOT"',
    ),
    "the instrument list is a POST": (
        '            return VenueRequest("GET", f"/api/v5/public/instruments?instType=',
        '            return VenueRequest("POST", f"/api/v5/public/instruments?instType=',
    ),
    "an unknown operation is a request": (
        "f\"/api/v5/public/instruments?instType={INST_TYPES[args['kind']]}\")\n        raise KeyError(operation)",
        'f"/api/v5/public/instruments?instType={INST_TYPES[args[\'kind\']]}")\n        return VenueRequest("GET", "/")',
    ),
    "a request costs the time weight": ('        return COST_TIME if operation == "time" else COST_REQUEST', "        return COST_TIME"),
    "the cost grows with the window": (
        '        return COST_TIME if operation == "time" else COST_REQUEST',
        '        return COST_TIME if operation == "time" else COST_REQUEST * (2 if operation == "candles" else 1)',
    ),
    "the time need not be one object": ("        if len(data) != 1 or not isinstance(data[0], dict):", "        if len(data) < 1:"),
    "the time may be many objects": (
        "        if len(data) != 1 or not isinstance(data[0], dict):",
        "        if len(data) < 1 or not isinstance(data[0], dict):",
    ),
    "the time item need not be an object": ("        if len(data) != 1 or not isinstance(data[0], dict):", "        if len(data) != 1:"),
    "the time is in seconds": (
        'ms_to_datetime(whole_number(data[0].get("ts"), "ts", text_ok=True))',
        'ms_to_datetime(whole_number(data[0].get("ts"), "ts", text_ok=True) * 1000)',
    ),
    "the time may not be text": ('whole_number(data[0].get("ts"), "ts", text_ok=True)', 'whole_number(data[0].get("ts"), "ts")'),
    "the time is read from another member": ('data[0].get("ts")', 'data[0].get("time")'),
    "a row may be short": (
        "            if not isinstance(row, list) or len(row) < 9:",
        "            if not isinstance(row, list) or len(row) < 8:",
    ),
    "a row must be exactly nine": (
        "            if not isinstance(row, list) or len(row) < 9:",
        "            if not isinstance(row, list) or len(row) != 9:",
    ),
    "a row need not be a list": ("            if not isinstance(row, list) or len(row) < 9:", "            if len(row) < 9:"),
    "the bar time may not be text": ('whole_number(row[0], "bar open", text_ok=True)', 'whole_number(row[0], "bar open")'),
    "the bar time is in seconds": (
        'ms_to_datetime(whole_number(row[0], "bar open", text_ok=True))',
        'ms_to_datetime(whole_number(row[0], "bar open", text_ok=True) * 1000)',
    ),
    "confirm may be anything": (
        '            if row[8] not in ("0", "1"):\n                raise VenueDataError("confirm is neither 0 nor 1")\n',
        "",
    ),
    "confirm may be a number": ('            if row[8] not in ("0", "1"):', '            if row[8] not in ("0", "1", 0, 1):'),
    "confirm may be two": ('            if row[8] not in ("0", "1"):', '            if row[8] not in ("0", "1", "2"):'),
    "the volume in currency is not read": (
        'contracts_or_base, currency = _volume(row[5], "volume"), _volume(row[6], "volume in currency")',
        'contracts_or_base, currency = _volume(row[5], "volume"), "0"',
    ),
    "the volume is not read": (
        'contracts_or_base, currency = _volume(row[5], "volume"), _volume(row[6], "volume in currency")',
        'contracts_or_base, currency = "0", _volume(row[6], "volume in currency")',
    ),
    "a derivative carries the contracts": (
        "base_volume = currency if derivative else contracts_or_base",
        "base_volume = contracts_or_base",
    ),
    "a spot pair carries the currency": ("base_volume = currency if derivative else contracts_or_base", "base_volume = currency"),
    "the derivative test is turned round": (
        "base_volume = currency if derivative else contracts_or_base",
        "base_volume = contracts_or_base if derivative else currency",
    ),
    "the open is the high": ('open=decimal_text(row[1], "open"),', 'open=decimal_text(row[2], "open"),'),
    "the high is the low": ('high=decimal_text(row[2], "high"),', 'high=decimal_text(row[3], "high"),'),
    "the low is the high": ('low=decimal_text(row[3], "low"),', 'low=decimal_text(row[2], "low"),'),
    "the close is the open": ('close=decimal_text(row[4], "close"),', 'close=decimal_text(row[1], "close"),'),
    "the quote volume is the volume": (
        'quote_volume=decimal_text(row[7], "quote volume"),',
        'quote_volume=decimal_text(row[5], "quote volume"),',
    ),
    "the quote volume is the currency volume": (
        'quote_volume=decimal_text(row[7], "quote volume"),',
        'quote_volume=decimal_text(row[6], "quote volume"),',
    ),
    "the quote volume is not read": ('quote_volume=decimal_text(row[7], "quote volume"),', "quote_volume=None,"),
    "a bar is final when confirm is zero": ('confirmed=row[8] == "1",', 'confirmed=row[8] == "0",'),
    "a bar is always final": ('confirmed=row[8] == "1",', "confirmed=True,"),
    "a bar is never final": ('confirmed=row[8] == "1",', "confirmed=False,"),
    "the bar closes after a minute": ("close_time=opened + step,", "close_time=opened + timedelta(seconds=60),"),
    "the bars are not sorted": ("        return sorted(bars, key=lambda bar: bar.open_time)", "        return bars"),
    "the bars are sorted newest first": (
        "        return sorted(bars, key=lambda bar: bar.open_time)",
        "        return sorted(bars, key=lambda bar: bar.open_time, reverse=True)",
    ),
    "the kind is not read": ('        kind = InstrumentKind(args["kind"])', "        kind = InstrumentKind.SPOT"),
    "the spot pattern is used for perpetuals": (
        "{InstrumentKind.SPOT: SPOT_ID, InstrumentKind.PERPETUAL: SWAP_ID, InstrumentKind.FUTURE: FUTURE_ID}",
        "{InstrumentKind.SPOT: SPOT_ID, InstrumentKind.PERPETUAL: SPOT_ID, InstrumentKind.FUTURE: FUTURE_ID}",
    ),
    "the swap pattern is used for futures": (
        "{InstrumentKind.SPOT: SPOT_ID, InstrumentKind.PERPETUAL: SWAP_ID, InstrumentKind.FUTURE: FUTURE_ID}",
        "{InstrumentKind.SPOT: SPOT_ID, InstrumentKind.PERPETUAL: SWAP_ID, InstrumentKind.FUTURE: SWAP_ID}",
    ),
    "a row need not be an object": (
        '            raw = row.get("instId") if isinstance(row, dict) else None',
        '            raw = row.get("instId")',
    ),
    "an id may be anything": (
        "                if not isinstance(raw, str) or pattern.fullmatch(raw) is None:",
        "                if not isinstance(raw, str):",
    ),
    "an id may be a prefix": ("pattern.fullmatch(raw) is None:", "pattern.match(raw) is None:"),
    "a repeated instrument is accepted": (
        '                if raw in found:\n                    raise VenueDataError("the instrument is listed twice")\n',
        "",
    ),
    "the instrument type need not match": (
        '                if row.get("instType") != INST_TYPES[kind.value]:\n                    raise VenueDataError("instType is not the one asked for")\n',
        "",
    ),
    "the instrument type is anything but missing": (
        '                if row.get("instType") != INST_TYPES[kind.value]:',
        '                if row.get("instType") == INST_TYPES[kind.value]:',
    ),
    "the state is not read": ('                state = _text(row, "state")', '                state = "live"'),
    "the spot base is not read": (
        'base, quote, settle = _text(row, "baseCcy"), _text(row, "quoteCcy"), None',
        'base, quote, settle = "X", _text(row, "quoteCcy"), None',
    ),
    "the spot quote is not read": (
        'base, quote, settle = _text(row, "baseCcy"), _text(row, "quoteCcy"), None',
        'base, quote, settle = _text(row, "baseCcy"), "USDT", None',
    ),
    "the spot id need not name the currencies": ('                    if raw != f"{base}-{quote}":', "                    if False:"),
    "the spot id may name them backwards": (
        '                    if raw != f"{base}-{quote}":',
        '                    if raw != f"{quote}-{base}":',
    ),
    "the spot settle is the quote": (
        'base, quote, settle = _text(row, "baseCcy"), _text(row, "quoteCcy"), None',
        'base, quote, settle = _text(row, "baseCcy"), _text(row, "quoteCcy"), _text(row, "quoteCcy")',
    ),
    "the underlying is not read": (
        '                    underlying = _text(row, "uly").split("-")',
        '                    underlying = ["BTC", "USDT"]',
    ),
    "the underlying may have three parts": (
        '                    if len(underlying) != 2 or not raw.startswith(f"{underlying[0]}-"):',
        '                    if len(underlying) < 2 or not raw.startswith(f"{underlying[0]}-"):',
    ),
    "the underlying may have one part": (
        '                    if len(underlying) != 2 or not raw.startswith(f"{underlying[0]}-"):',
        '                    if len(underlying) > 2 or not raw.startswith(f"{underlying[0]}-"):',
    ),
    "the underlying need not match the id": (
        '                    if len(underlying) != 2 or not raw.startswith(f"{underlying[0]}-"):',
        "                    if len(underlying) != 2:",
    ),
    "the underlying is matched in part of the id": ('not raw.startswith(f"{underlying[0]}-"):', "not raw.startswith(underlying[0]):"),
    "the derivative base is the quote": (
        'base, quote, settle = underlying[0], underlying[1], _text(row, "settleCcy")',
        'base, quote, settle = underlying[1], underlying[0], _text(row, "settleCcy")',
    ),
    "the settle currency is not read": (
        'base, quote, settle = underlying[0], underlying[1], _text(row, "settleCcy")',
        "base, quote, settle = underlying[0], underlying[1], underlying[1]",
    ),
    "the settle currency may be missing": (
        'base, quote, settle = underlying[0], underlying[1], _text(row, "settleCcy")',
        'base, quote, settle = underlying[0], underlying[1], str(row.get("settleCcy"))',
    ),
    "a future may have no expiry": (
        '                if kind is InstrumentKind.FUTURE and not stated_expiry:\n                    raise VenueDataError("a future has no expTime")\n',
        "",
    ),
    "a perpetual must have an expiry": (
        "                if kind is InstrumentKind.FUTURE and not stated_expiry:",
        "                if kind is not InstrumentKind.SPOT and not stated_expiry:",
    ),
    "a spot pair must have an expiry": (
        "                if kind is InstrumentKind.FUTURE and not stated_expiry:",
        "                if kind is not InstrumentKind.PERPETUAL and not stated_expiry:",
    ),
    "the expiry is not read": (
        'expiry = ms_to_datetime(whole_number(stated_expiry, "expTime", text_ok=True)) if stated_expiry else None',
        "expiry = None",
    ),
    "the expiry may not be text": ('whole_number(stated_expiry, "expTime", text_ok=True)', 'whole_number(stated_expiry, "expTime")'),
    "the expiry is in seconds": (
        'ms_to_datetime(whole_number(stated_expiry, "expTime", text_ok=True))',
        'ms_to_datetime(whole_number(stated_expiry, "expTime", text_ok=True) * 1000)',
    ),
    "the kind is wrong": ("                    kind=kind,", "                    kind=InstrumentKind.PERPETUAL,"),
    "the multiplier is not read": ("multiplier=multiplier_of(base),", 'multiplier="1",'),
    "the quote class is the base": ("quote_class=quote_class(quote),", "quote_class=quote_class(base),"),
    "the tick is not read": ('tick_size=decimal_text(row["tickSz"], "tickSz") if "tickSz" in row else None,', "tick_size=None,"),
    "the tick is required": (
        'tick_size=decimal_text(row["tickSz"], "tickSz") if "tickSz" in row else None,',
        'tick_size=decimal_text(row["tickSz"], "tickSz"),',
    ),
    "the tick is the lot": (
        'tick_size=decimal_text(row["tickSz"], "tickSz") if "tickSz" in row else None,',
        'tick_size=decimal_text(row["lotSz"], "lotSz") if "lotSz" in row else None,',
    ),
    "the lot is not read": ("lot_size=_lot(row, kind),", "lot_size=None,"),
    "the lot is the minimum size": (
        "lot_size=_lot(row, kind),",
        'lot_size=decimal_text(row.get("minSz"), "minSz") if "minSz" in row else None,',
    ),
    "the notional is the minimum size": (
        "min_notional=None,",
        'min_notional=decimal_text(row.get("minSz"), "minSz") if "minSz" in row else None,',
    ),
    "an unreadable instrument is dropped": (
        "            except VenueDataError as exc:\n                unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))",
        "            except VenueDataError:\n                pass",
    ),
    "an unreadable instrument loses its id": (
        "unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))",
        "unmapped.append(Unmapped(None, str(exc)))",
    ),
    "an unreadable instrument loses its reason": (
        "unmapped.append(Unmapped(raw if isinstance(raw, str) else None, str(exc)))",
        'unmapped.append(Unmapped(raw if isinstance(raw, str) else None, "unreadable"))',
    ),
    "the list is of another venue": (
        "        return InstrumentList(self.venue, tuple(found.values()), tuple(unmapped))",
        '        return InstrumentList("okx-eea", tuple(found.values()), tuple(unmapped))',
    ),
    "the unmapped instruments are not returned": ("tuple(found.values()), tuple(unmapped))", "tuple(found.values()), ())"),
    "an error body bound is not applied": ("data = jsonnum.parse(body, max_bytes=_ERROR_BODY_BYTES)", "data = jsonnum.parse(body)"),
    "a bad error body is a crash": (
        "        except jsonnum.JsonError:\n            return None",
        "        except ZeroDivisionError:\n            return None",
    ),
    "an error body need not be an object": (
        '            not isinstance(data, dict)\n            or not isinstance(data.get("code"), str)',
        '            not isinstance(data.get("code"), str)',
    ),
    "a code may be a number": ('            or not isinstance(data.get("code"), str)\n', ""),
    "a message may be missing": ('            or not isinstance(data.get("msg"), str)\n', ""),
    "a success code is an error": ('            or data["code"] == "0"\n', ""),
    "the status class is not consulted": (
        "error_class = classify_status(status) or ErrorClass.UNCLASSIFIED",
        "error_class = ErrorClass.UNCLASSIFIED",
    ),
    "the status class is the only decider": (
        "error_class = classify_status(status) or ErrorClass.UNCLASSIFIED",
        "error_class = classify_status(status) or ErrorClass.INVALID_ARGS",
    ),
    "the status class is consulted for a known code": ("        if error_class is None:", "        if True:"),
    "the message loses the code": ("f\"{data['code']} {data['msg']}\"[:200]", "f\"{data['msg']}\"[:200]"),
    "the message is not cut": ("f\"{data['code']} {data['msg']}\"[:200]", "f\"{data['code']} {data['msg']}\""),
    "the message is cut short": ("f\"{data['code']} {data['msg']}\"[:200]", "f\"{data['code']} {data['msg']}\"[:100]"),
    "the wait is not read": ('retry_after_s=parse_retry_after(headers.get("retry-after"), now)', "retry_after_s=None"),
    "the contract multiplier is ignored": (
        '    multiplier = row.get("ctMult")\n    if multiplier not in (None, "") and Decimal(decimal_text(multiplier, "ctMult")) != 1:\n        raise VenueDataError("ctMult is not 1: the size of a contract in base units is not known")\n',
        "",
    ),
    "a contract multiplier of any size is accepted": (
        'Decimal(decimal_text(multiplier, "ctMult")) != 1:',
        'Decimal(decimal_text(multiplier, "ctMult")) != 99:',
    ),
    "a contract multiplier must be present": ('    if multiplier not in (None, "") and', '    if multiplier not in ("",) and'),
    "an empty contract multiplier is refused": ('    if multiplier not in (None, "") and', "    if multiplier not in (None,) and"),
    "a contract multiplier of one is refused": (
        'Decimal(decimal_text(multiplier, "ctMult")) != 1:',
        'Decimal(decimal_text(multiplier, "ctMult")) != 2:',
    ),
    "a contract multiplier may be text": ('Decimal(decimal_text(multiplier, "ctMult")) != 1:', 'str(multiplier) != "1":'),
    "the declared model is a window": (
        "        RateModel.TOKEN_BUCKET, BUDGET_PER_WINDOW, WINDOW_S",
        "        RateModel.FIXED_WINDOW, BUDGET_PER_WINDOW, WINDOW_S",
    ),
    "the declared capacity is the window": (
        "        RateModel.TOKEN_BUCKET, BUDGET_PER_WINDOW, WINDOW_S",
        "        RateModel.TOKEN_BUCKET, WINDOW_S, WINDOW_S",
    ),
    "the declared window is the budget": (
        "        RateModel.TOKEN_BUCKET, BUDGET_PER_WINDOW, WINDOW_S",
        "        RateModel.TOKEN_BUCKET, BUDGET_PER_WINDOW, BUDGET_PER_WINDOW",
    ),
}


def member_removals(constant: str, label: str) -> dict[str, tuple[str, str]]:
    """One mutant per member of a set constant of the module: the set without that member."""
    source = inspect.getsource(venue_okx)
    match = re.search(rf"^{constant}: Final = frozenset\(\{{(?P<members>[^}}]*)\}}\)", source, flags=re.M)
    assert match is not None, constant
    raw = match.group("members")
    members = [m.strip() for m in raw.split(",") if m.strip()]
    return {
        f"{label}: {member} is not a member": ("{" + raw + "}", "{" + ", ".join(m for m in members if m != member) + "}")
        for member in members
    }


for _constant, _label in (
    ("_RATE_CODES", "rate codes"),
    ("_TIMEOUT_CODES", "timeout codes"),
    ("_UPSTREAM_CODES", "upstream codes"),
    ("_NOT_FOUND_CODES", "not-found codes"),
    ("_INVALID_CODES", "invalid codes"),
    ("_AUTH_CODES", "auth codes"),
    ("_REGION_CODES", "region codes"),
):
    MUTATIONS.update(member_removals(_constant, _label))


# Redundant guards without mutants: none known yet (every guard of the adapter has a profile case that fails without it).


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.venue_okx", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
