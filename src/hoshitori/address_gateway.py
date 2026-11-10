"""The address-flag skill as a gateway provider: a screen of one address is a call like any other, paid for, queued, stored and classified.

The skill (``skills/address-flag-check``) is a standalone tool: it reads OFAC's list and the issuers' own contracts and answers with an
envelope. Here it is one provider with one operation, ``check``, whose arguments are the chain and the address. The transport runs the skill
on the worker that the gateway's runner gives it and keeps the skill's answer as the capture's body, exactly as it came: the skill's own
record of what it read (``source_calls``) travels inside it. What the skill says when it fails never reaches a capture or a log: a refusal
of the input, a time out and any other failure each end the attempt with a fixed sentence.

The league does not import the skill to read its answer (``address_screen`` reads the envelope contract). It imports it only to bind it
(``skill_runner``), and then only when it is installed, so a league without the skill simply has no such provider. The skill's own requests
go out through its own bounded client (no redirect, bounded size and time); they are limited here by the attempt's deadline, by a rate
prior that is far under what OFAC and the public nodes allow, and by a breaker.
"""

from __future__ import annotations

import importlib
import importlib.util
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from hoshitori import address_screen, canonical
from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.capture import RawCapture
from hoshitori.clock import Clock
from hoshitori.connector import ConnectorError, ErrorClass, classify_status, parse_retry_after
from hoshitori.gateway import Gateway, RateObservation, TransportFailure
from hoshitori.governor import Governor, RateModel

PROVIDER: Final = address_screen.PROVIDER
OPERATION: Final = "check"
CHAINS: Final = address_screen.CHAINS
RATE_LIMIT: Final = 20.0  # screens a minute: a prior, far under what the list host and the public nodes allow
RATE_WINDOW_S: Final = 60.0
ATTEMPT_CAP_S: Final = 30.0  # the skill's own budget is 25 seconds
MIN_ATTEMPT_S: Final = 5.0
_ADDRESS: Final = re.compile(r"[A-Za-z0-9]{1,128}")

type SkillRunner = Callable[[Mapping[str, Any], datetime], Mapping[str, Any]]


class AddressFlagAdapter:
    """What the gateway needs of the screen."""

    provider = PROVIDER

    def operations(self) -> frozenset[str]:
        return frozenset({OPERATION})

    def normalise(self, operation: str, args: Mapping[str, Any]) -> dict[str, Any]:
        if operation != OPERATION:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"{PROVIDER} has no operation {operation!r}")
        if set(args) != {"address", "chain"}:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "check takes an address and a chain")
        chain, address = args["chain"], args["address"]
        if not isinstance(chain, str) or chain not in CHAINS:
            raise ConnectorError(ErrorClass.INVALID_ARGS, f"the chain is one of {', '.join(CHAINS)}")
        if not isinstance(address, str) or _ADDRESS.fullmatch(address) is None:
            raise ConnectorError(ErrorClass.INVALID_ARGS, "the address is plain text of letters and digits, at most 128 characters")
        return {"address": address, "chain": chain}

    def plan(self, operation: str, args: Mapping[str, Any]) -> float:
        return 1.0

    def attempt_cap_s(self, operation: str) -> float:
        return ATTEMPT_CAP_S

    def min_attempt_s(self, operation: str) -> float:
        return MIN_ATTEMPT_S

    def classify(self, capture: RawCapture, now: datetime) -> ConnectorError | None:
        if capture.status is None:
            return ConnectorError(ErrorClass.UPSTREAM, "no answer")
        failed = classify_status(capture.status)
        if failed is not None:
            wait = parse_retry_after(capture.headers.get("retry-after"), now)
            return ConnectorError(failed, f"HTTP {capture.status}", retry_after_s=wait)
        asked = capture.request.get("args")
        if not isinstance(asked, Mapping) or not isinstance(asked.get("chain"), str) or not isinstance(asked.get("address"), str):
            return ConnectorError(ErrorClass.MALFORMED, f"{PROVIDER}: the capture does not say what was asked")
        try:
            screen = address_screen.read_screen(capture.body, chain=str(asked["chain"]), address=str(asked["address"]))
        except address_screen.ScreenError as exc:
            return ConnectorError(ErrorClass.MALFORMED, f"{PROVIDER}: the answer is not a screen of what was asked: {exc}")
        if screen.status == "unavailable":
            return ConnectorError(ErrorClass.UPSTREAM, f"{PROVIDER}: no source answered")
        return None

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        return None


class SkillTransport:
    """Runs the skill and keeps its answer as the capture."""

    def __init__(self, runner: SkillRunner, clock: Callable[[], datetime]) -> None:
        self._runner = runner
        self._clock = clock

    def fetch(self, operation: str, args: Mapping[str, Any], timeout_s: float, capture_id: str, sent_at: datetime) -> RawCapture:
        if operation != OPERATION:
            raise TransportFailure(ErrorClass.INVALID_ARGS, f"{PROVIDER} has no operation {operation!r}")
        try:
            answer = self._runner(dict(args), sent_at)
            body = json.dumps(dict(answer), sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        except ValueError:
            raise TransportFailure(ErrorClass.INVALID_ARGS, "the screen refused the arguments") from None
        except TimeoutError:
            raise TransportFailure(ErrorClass.TIMEOUT, "the screen ran out of time") from None
        except Exception:  # a bug or a failure inside the skill: its text is not passed on
            raise TransportFailure(ErrorClass.UPSTREAM, "the screen failed") from None
        request: dict[str, canonical.JsonValue] = {"operation": operation, "args": dict(args), "surface": "skill"}
        return RawCapture(capture_id, PROVIDER, request, 200, {}, body, sent_at, max(self._clock(), sent_at))


@dataclass(frozen=True)
class AddressAttached:
    adapter: AddressFlagAdapter
    governor: Governor
    breakers: BreakerSet
    transport: SkillTransport


def attach_address(gateway: Gateway, runner: SkillRunner, clock: Clock, *, breaker: BreakerConfig | None = None) -> AddressAttached:
    """Register the screen with ``gateway`` and return its parts; the transport goes in the runner's table under its provider."""
    adapter = AddressFlagAdapter()
    now = clock.now()
    governor = Governor(PROVIDER, RateModel.FIXED_WINDOW, RATE_LIMIT, RATE_WINDOW_S, now)
    settings = breaker or BreakerConfig()
    breakers = BreakerSet(Breaker(PROVIDER, settings, now), settings)
    gateway.register(adapter, governor, breakers, concurrency=1)
    return AddressAttached(adapter, governor, breakers, SkillTransport(runner, clock.now))


def skill_runner(list_dir: Path) -> SkillRunner | None:
    """The real skill as a runner, keeping its copy of OFAC's list in ``list_dir``, or ``None`` when the skill is not installed."""
    if importlib.util.find_spec("address_flag_check") is None:
        return None
    skill = importlib.import_module("address_flag_check.skill")

    def run(args: Mapping[str, Any], now: datetime) -> Mapping[str, Any]:
        answer: Mapping[str, Any] = skill.run(dict(args), now=now, list_dir=list_dir)
        return answer

    return run
