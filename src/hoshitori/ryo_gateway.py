"""RYO attached to the gateway, the way the exchange venues and the model are: one adapter on the pinned catalogue, a governor, a
breaker set and an HTTP transport, registered in one call.

``ryo`` (the adapter), ``ryo_transport`` (the socket) and ``ryo_fields`` (the evidence-class table) are the parts; this module is
where a league joins them. Three rules are kept here and nowhere else:

* **The key is the caller's, read at each request.** ``key`` is a function; nothing keeps the value, so a rotated key is used at the next
  call and the value is never in a record, a setting or a log. A missing key is an ``AUTH`` failure of the call, with nothing sent.
* **The catalogue is read off the cycle's path.** ``refresh_catalogue`` asks RYO for its tool list (a free read, no quota) and gives
  it to the adapter, which compares it with the pin: a tool whose structure changed is unavailable until a person has looked. A league
  does this once when it starts and not inside a cycle, because RYO's authenticated reads took 40 to 161 seconds in the first live pass.
* **An attempt is never shorter than RYO's own timeout** (``RyoConfig`` refuses it), because RYO's classified error arrives at about 161
  seconds and a shorter attempt would lose it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from hoshitori.breaker import Breaker, BreakerConfig, BreakerSet
from hoshitori.capture import CaptureStore, RawCapture
from hoshitori.clock import Clock
from hoshitori.gateway import Call, Gateway, Outcome, RateObservation
from hoshitori.governor import Governor, Priority, RateModel
from hoshitori.runner import Runner
from hoshitori.ryo import PROVIDER, RyoAdapter, RyoConfig
from hoshitori.ryo_catalogue import Drift, load_pin, parse_catalogue
from hoshitori.ryo_pin import PIN
from hoshitori.ryo_transport import RyoTransport

# RYO enforces two limits. `/whoami` and every response header describe 60 a minute. A second, stricter limit of six a minute for the
# fan-out of tool calls is in no header: passing it answers HTTP 429 with `retry-after: 60`. A burst of four calls at once was refused twice
# in the metered probe; calls started eleven seconds apart never were. The governor therefore holds one call under the stricter limit, as a
# bucket of one that refills steadily (at most six calls enter any window of a minute), and takes nothing from the headers, which
# describe the looser one.
RATE_LIMIT = 5.0  # calls a minute
RATE_WINDOW_S = 60.0
RATE_BURST = 1.0  # a bucket of one, so that no window of a minute holds more than six calls (the stricter limit) at a rate of five
BREAKER_FAILURES = 3  # the prior for RYO (5 for exchange reads): a failed call costs minutes, so fewer are worth making blind
CATALOGUE_WAIT_S = 200.0  # a little over the longest recorded read (161 s)


class RyoGatewayError(RuntimeError):
    """RYO could not be reached for what a league needs before a cycle."""


class FanoutAdapter(RyoAdapter):
    """RYO's adapter for a governor that must not believe RYO's rate headers: they count the outer limit of 60 a minute and would let the
    governor forget the calls it made against the binding limit of six."""

    def rate_observation(self, capture: RawCapture) -> RateObservation | None:
        return None


@dataclass(frozen=True)
class RyoAttached:
    adapter: RyoAdapter
    governor: Governor
    breakers: BreakerSet
    transport: RyoTransport


def attach_ryo(
    gateway: Gateway,
    base_url: str,
    key: Callable[[], str | None],
    clock: Clock,
    *,
    rate_limit: float = RATE_LIMIT,
    concurrency: int = 4,
    breaker: BreakerConfig | None = None,
    config: RyoConfig | None = None,
    allow_insecure_loopback: bool = False,
    ca_file: str | None = None,
) -> RyoAttached:
    """Register RYO with ``gateway`` and return its parts; the transport goes in the runner's table under its provider."""
    adapter = FanoutAdapter(load_pin(PIN), config or RyoConfig())
    now = clock.now()
    governor = Governor(adapter.provider, RateModel.TOKEN_BUCKET, rate_limit, RATE_WINDOW_S, now, burst=min(RATE_BURST, rate_limit))
    settings = breaker or BreakerConfig(consecutive_failures=BREAKER_FAILURES)
    breakers = BreakerSet(Breaker(adapter.provider, settings, now), settings)
    gateway.register(adapter, governor, breakers, concurrency=concurrency)
    transport = RyoTransport(
        base_url, key, clock.now, provider=adapter.provider, allow_insecure_loopback=allow_insecure_loopback, ca_file=ca_file
    )
    return RyoAttached(adapter, governor, breakers, transport)


def refresh_catalogue(
    runner: Runner, adapter: RyoAdapter, captures: CaptureStore, clock: Clock, wait_s: float = CATALOGUE_WAIT_S
) -> tuple[Drift, ...]:
    """Read RYO's live tool list and give it to the adapter; returns the drift against the pin (empty when the structure is as built)."""
    call = Call(PROVIDER, "_catalogue", {}, Priority.P0, clock.now() + timedelta(seconds=wait_s), "ryo-catalogue")
    result = runner.submit(call).result(timeout=wait_s + 5.0)
    if result.outcome is not Outcome.OK or result.capture_id is None:
        raise RyoGatewayError(f"RYO's tool list could not be read: {result.outcome.value} {result.reason or ''}".rstrip())
    return adapter.update_catalogue(parse_catalogue(captures.get(result.capture_id).body))
