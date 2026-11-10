"""The RYO provider for the gateway: which operations exist, how arguments are made safe, and how every answer is classified.

``RYO_API_COMPATIBILITY`` is the contract and ``ryo_catalogue`` / ``ryo_envelope`` / ``ryo_fields`` are the readers. This module is the
adapter in the sense of ``gateway.Adapter``: it holds no socket and never retries (``ryo_transport`` owns the socket, the gateway owns the
retries). It answers four questions.

* **What may be called.** The six tools, as far as the live catalogue still has them in the structure the product was built against
  (``ryo_pin``), and three free reads (``_catalogue``, ``_whoami``, ``_health``). A tool whose structure changed or that disappeared is
  unavailable until a person has looked (``Drift.blocks``); a tool the product has no field specification for is never called.
* **What may be sent.** Arguments are checked against the catalogue's schema and then made canonical: symbols, never addresses (the guide:
  "token analysis accepts asset symbols, not wallet addresses"), a chain slug, two to four distinct comparison symbols, a count in a sane
  range. A request RYO would reject, or one that is not what the caller meant, costs no attempt.
* **What an answer is.** An HTTP status and a body become either nothing (the answer is usable evidence, whatever its ``status``) or one
  classified ``ConnectorError``. A 200 whose envelope or data does not match the contract is ``CONTRACT_DRIFT`` and is never partly used.
* **What the headers said about the allowance**, for the governor.

The first live pass showed attempts of 40 to 161 seconds and RYO's own errors arriving at 121 and 161 seconds, so an attempt may not be cut
short of 165 seconds without losing the classified error; the default is 170.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Final

from hoshitori import jsonnum
from hoshitori.capture import RawCapture
from hoshitori.connector import MAX_RETRY_AFTER_S, ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import RateObservation
from hoshitori.ryo_catalogue import Catalogue, Drift, check_arguments, diff, parse_catalogue
from hoshitori.ryo_envelope import read_error_body, read_health, read_rest_success, read_whoami
from hoshitori.ryo_fields import SPECS, walk
from hoshitori.timeutil import TimeError, parse_rfc3339

PROVIDER: Final = "ryo"
RECORDED_PROVIDER: Final = "ryo-recorded"
RYO_SERVER_TIMEOUT_S: Final = 165.0  # RYO's own 504 arrives at about 161 s
META_OPERATIONS: Final = {"_catalogue": "/tools", "_whoami": "/whoami", "_health": "/health"}
SYMBOL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,19}")
CHAIN: Final = re.compile(r"[a-z0-9][a-z0-9_-]{0,31}")
DIRECTIONS: Final = frozenset({"positive", "negative", "all"})
MAX_THEME: Final = 64


@dataclass(frozen=True)
class RyoConfig:
    attempt_cap_s: float = 170.0
    min_attempt_s: float = 30.0
    max_top_n: int = 50  # a limit of ours; RYO's own cap is not documented
    testing: bool = False  # allows attempts shorter than RYO's own timeout, for tests against a fake server

    def __post_init__(self) -> None:
        if not 0 < self.min_attempt_s <= self.attempt_cap_s:
            raise ValueError("the minimum attempt must be positive and no longer than the cap")
        if self.attempt_cap_s <= RYO_SERVER_TIMEOUT_S and not self.testing:
            raise ValueError(f"an attempt of {self.attempt_cap_s} s would cut off RYO's own error at about {RYO_SERVER_TIMEOUT_S} s")
        if self.max_top_n < 1:
            raise ValueError("max_top_n must be positive")


def _invalid(message: str) -> ConnectorError:
    return ConnectorError(ErrorClass.INVALID_ARGS, message)


def _symbol(value: str, name: str) -> str:
    text = value.strip()
    if SYMBOL.fullmatch(text) is None:
        if len(text) > 20 and text.isalnum():
            raise _invalid(f"{name} looks like an address; RYO takes token symbols, not addresses")
        raise _invalid(f"{name} is not a token symbol: letters and digits, up to 20 characters")
    return text


def _symbols(value: str) -> str:
    seen: dict[str, str] = {}
    for part in re.split(r"[,\s]+", value.strip()):
        if part:
            seen.setdefault(_symbol(part, "symbols").lower(), part)
    if not 2 <= len(seen) <= 4:
        raise _invalid("compare_tokens needs two to four distinct symbols")
    return ", ".join(seen.values())


def _chain(value: str) -> str:
    text = value.strip().lower()
    if CHAIN.fullmatch(text) is None:
        raise _invalid("chain is not a chain slug such as eth or bsc")
    return text


def _theme(value: str) -> str:
    text = value.strip()
    if not 1 <= len(text) <= MAX_THEME or not text.isprintable():
        raise _invalid(f"theme must be 1 to {MAX_THEME} printable characters")
    return text


def _top_n(value: int | float, limit: int) -> int:
    if not float(value).is_integer() or not 1 <= value <= limit:
        raise _invalid(f"top_n must be a whole number from 1 to {limit}")
    return int(value)


def _epoch(text: str) -> datetime | None:
    if not re.fullmatch(r"[0-9]{1,12}", text):
        return None
    return datetime.fromtimestamp(int(text), UTC)


def read_answer(operation: str, body: bytes) -> None:
    """Accept the body of a 2xx answer to ``operation`` or raise the ``ConnectorError`` that classifies it."""
    if operation == "_catalogue":
        parse_catalogue(body)
    elif operation == "_whoami":
        read_whoami(body)
    elif operation == "_health":
        read_health(body)
    elif operation in SPECS:
        envelope = read_rest_success(body, operation)
        walk(operation, envelope.data)
    else:
        raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"{operation} is not an operation of this provider")


class RyoAdapter:
    """``gateway.Adapter`` for RYO. Mutable only in what it knows of the live catalogue (``update_catalogue``)."""

    def __init__(self, pinned: Catalogue, config: RyoConfig | None = None, provider: str = PROVIDER) -> None:
        self.provider = provider
        self.config = config or RyoConfig()
        self._pinned = pinned
        self._live = pinned
        self._drift: tuple[Drift, ...] = ()
        self._blocked: frozenset[str] = frozenset()

    # --- what is known of the live catalogue ------------------------------------------------------------------------------------
    def update_catalogue(self, live: Catalogue) -> tuple[Drift, ...]:
        """Take a catalogue just read from RYO. Returns its drift against the pin; tools whose drift blocks become unavailable."""
        drift = tuple(diff(self._pinned, live))
        self._live = live
        self._drift = drift
        self._blocked = frozenset(d.tool for d in drift if d.blocks)
        return drift

    @property
    def drift(self) -> tuple[Drift, ...]:
        return self._drift

    @property
    def catalogue_sha256(self) -> str:
        """The hash of the catalogue in force, so that a record can say which one it was read under."""
        return self._live.sha256

    @property
    def blocked(self) -> frozenset[str]:
        return self._blocked

    # --- gateway.Adapter ----------------------------------------------------------------------------------------------------------
    def operations(self) -> frozenset[str]:
        tools = {name for name in self._live.tools if name in SPECS and name in self._pinned.tools and name not in self._blocked}
        return frozenset(tools | META_OPERATIONS.keys())

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation in META_OPERATIONS:
            if args:
                raise _invalid(f"{operation} takes no arguments")
            return {}
        spec = self._live.tools.get(operation)
        if spec is None:
            raise ConnectorError(ErrorClass.CONTRACT_DRIFT, f"{operation} is not in the live catalogue")
        out = check_arguments(spec, args)
        if "symbol" in out:
            out["symbol"] = _symbol(out["symbol"], "symbol")
        if "symbols" in out:
            out["symbols"] = _symbols(out["symbols"])
        if "chain" in out:
            out["chain"] = _chain(out["chain"])
        if "theme" in out:
            out["theme"] = _theme(out["theme"])
        if "top_n" in out:
            out["top_n"] = _top_n(out["top_n"], self.config.max_top_n)
        if "filter_direction" in out and out["filter_direction"] not in DIRECTIONS:
            raise _invalid("filter_direction must be positive, negative or all")
        return out

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def attempt_cap_s(self, operation: str) -> float:
        return self.config.attempt_cap_s

    def min_attempt_s(self, operation: str) -> float:
        return self.config.min_attempt_s

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no response", trace_id=capture.trace_id)
        trace = capture.trace_id
        error_class = classify_status(capture.status)
        if error_class is not None:
            return self._failure(capture, error_class, now)
        operation = capture.request.get("operation")
        if not isinstance(operation, str):
            return ConnectorError(ErrorClass.MALFORMED, "the capture does not name its operation", trace_id=trace)
        try:
            read_answer(operation, capture.body)
        except ConnectorError as exc:
            return ConnectorError(exc.error_class, exc.message, retry_after_s=exc.retry_after_s, trace_id=exc.trace_id or trace)
        return None

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        headers = capture.headers

        def number(name: str) -> float | None:
            value = headers.get(name)
            if value is None or not re.fullmatch(r"[0-9]{1,12}(\.[0-9]{1,6})?", value.strip()):
                return None
            return float(value)

        limit, remaining, reset = number("x-ratelimit-limit"), number("x-ratelimit-remaining"), number("x-ratelimit-reset")
        reset_at = datetime.fromtimestamp(reset, UTC) if reset is not None and reset < 4e10 else None
        response_date = None
        if "date" in headers:
            try:
                parsed = parsedate_to_datetime(headers["date"])
                response_date = parsed.astimezone(UTC) if parsed.tzinfo is not None else None
            except (TypeError, ValueError):
                response_date = None
        if limit is None and remaining is None and reset_at is None:
            return None
        return RateObservation(limit=limit, remaining=remaining, reset_at=reset_at, response_date=response_date)

    # --- reading an answer ----------------------------------------------------------------------------------------------------------
    def _failure(self, capture: RawCapture, error_class: ErrorClass, now: datetime) -> ConnectorError:
        body = read_error_body(capture.body)
        trace = (body.trace_id if body is not None else None) or capture.trace_id
        message = body.message if body is not None else f"HTTP {capture.status}"
        wait = parse_retry_after(capture.headers.get("retry-after"), now)
        if error_class is ErrorClass.NOT_FOUND and body is not None and body.message.startswith("Unknown tool"):
            error_class = ErrorClass.CONTRACT_DRIFT
        if error_class is ErrorClass.INVALID_ARGS and body is not None and body.message.startswith("couldn't analyze"):
            error_class = ErrorClass.NOT_FOUND  # the service could not read this token: not a defect of our request, as on the MCP path
        if wait is None and error_class is ErrorClass.RATE_LIMIT and body is not None:
            wait = self._reset_wait(body.details, now)
        return ConnectorError(error_class, f"HTTP {capture.status}: {message}", retry_after_s=wait, trace_id=trace)

    @staticmethod
    def _reset_wait(details: Any, now: datetime) -> float | None:
        if not isinstance(details, dict):
            return None
        reset = details.get("reset_at")
        moment: datetime | None = None
        if isinstance(reset, jsonnum.Num):
            moment = _epoch(reset.text)
        elif isinstance(reset, str):
            try:
                moment = parse_rfc3339(reset)
            except TimeError:
                moment = None
        if moment is None:
            return None
        return float(min(max((moment - now).total_seconds(), 0.0), MAX_RETRY_AFTER_S))
