"""Checks of the clocks (``hoshitori.timeline``), runnable against a mutant: golden values, brute-force differentials over every offset
inside a minute, and the contract's worked cases."""

from __future__ import annotations

import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone

MINUTE, HOUR, DAY = 60_000, 3_600_000, 86_400_000


def at(h: int, m: int, s: float = 0.0) -> int:
    """Milliseconds of a time of day on 2026-10-29 UTC."""
    base = int(datetime(2026, 10, 29, tzinfo=UTC).timestamp() * 1000)
    return base + h * HOUR + m * MINUTE + round(s * 1000)


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], text: str | None = None) -> None:
        try:
            call()
        except self.mod.TimelineError as exc:
            if text is not None and not str(exc).startswith(text):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected it to begin {text!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_instants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for label, moment, want in (
        ("the epoch", datetime(1970, 1, 1, tzinfo=UTC), 0),
        ("a millisecond", datetime(1970, 1, 1, 0, 0, 0, 1000, tzinfo=UTC), 1),
        ("2026-10-29 12:00", datetime(2026, 10, 29, 12, tzinfo=UTC), at(12, 0)),
        ("with milliseconds", datetime(2026, 10, 29, 12, 0, 0, 123000, tzinfo=UTC), at(12, 0) + 123),
        ("in another zone", datetime(2026, 10, 29, 14, tzinfo=timezone(timedelta(hours=2))), at(12, 0)),
        (
            "a year with a leap day",
            datetime(2028, 3, 1, tzinfo=UTC),
            int((datetime(2028, 3, 1, tzinfo=UTC) - datetime(1970, 1, 1, tzinfo=UTC)).total_seconds()) * 1000,
        ),
        (
            "the last millisecond of a day",
            datetime(2026, 12, 31, 23, 59, 59, 999000, tzinfo=UTC),
            int(datetime(2027, 1, 1, tzinfo=UTC).timestamp() * 1000) - 1,
        ),
    ):
        c.eq(f"to_ms({label})", mod.to_ms(moment), want)
        c.eq(f"from_ms(to_ms({label}))", mod.from_ms(mod.to_ms(moment)), moment.astimezone(UTC))
    c.refuses("a naive instant", lambda: mod.to_ms(datetime(2026, 10, 29, 12)))
    c.refuses("microseconds", lambda: mod.to_ms(datetime(2026, 10, 29, 12, 0, 0, 1, tzinfo=UTC)))
    c.refuses("half a millisecond", lambda: mod.to_ms(datetime(2026, 10, 29, 12, 0, 0, 1500, tzinfo=UTC)))
    c.refuses("before the epoch", lambda: mod.to_ms(datetime(1969, 12, 31, 23, 59, 59, 999000, tzinfo=UTC)))
    c.refuses("negative milliseconds", lambda: mod.from_ms(-1))
    for bad in (True, 1.5, "5", None):
        c.refuses(f"from_ms({bad!r})", lambda b=bad: mod.from_ms(b))
    c.eq("the clock constants", (mod.MINUTE_MS, mod.HOUR_MS, mod.DEFAULT_HORIZONS), (MINUTE, HOUR, (HOUR, 4 * HOUR, DAY)))
    p = mod.ClockParameters()
    c.eq("default parameters", (p.uncertainty_ms, p.latency_ms), (5_000, 15_000))
    c.eq("parameters are frozen", mod.ClockParameters.__dataclass_params__.frozen, True)
    c.eq("a parameter of zero is allowed", (mod.ClockParameters(0, 0).uncertainty_ms, mod.ClockParameters(0, 0).latency_ms), (0, 0))
    c.refuses("a negative uncertainty", lambda: mod.ClockParameters(uncertainty_ms=-1))
    c.refuses("a negative latency", lambda: mod.ClockParameters(latency_ms=-1))
    return c.bad


def check_origin(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    # the contract's worked example: tokens at 10:01:02 and 10:01:04, u = 5 s and a 2 s spread
    ea = mod.eligible_after(at(10, 1, 4), 5_000, 2_000)
    c.eq("the worked example: eligible_after", ea, at(10, 1, 11))
    c.eq("the worked example: T0", mod.origin(ea), at(10, 2, 0))
    c.eq(
        "the worked example: the candle that closed 10:01:00 is not the reference", mod.reference_candle_open(mod.origin(ea)), at(10, 1, 0)
    )
    c.eq("eligible_after is the sum", mod.eligible_after(1000, 20, 3), 1023)
    for label, args in (
        ("a negative attestation", (-1, 0, 0)),
        ("a negative u", (0, -1, 0)),
        ("a negative spread", (0, 0, -1)),
        ("a float", (1.5, 0, 0)),
        ("a bool", (True, 0, 0)),
    ):
        c.refuses(label, lambda a=args: mod.eligible_after(*a))
    for ea, want in (
        (0, MINUTE),
        (1, MINUTE),
        (MINUTE - 1, MINUTE),
        (MINUTE, 2 * MINUTE),
        (MINUTE + 1, 2 * MINUTE),
        (2 * MINUTE - 1, 2 * MINUTE),
        (at(10, 1, 11), at(10, 2, 0)),
        (at(10, 1, 0), at(10, 2, 0)),
        (at(10, 0, 59.999), at(10, 1, 0)),
        (DAY - 1, DAY),
        (DAY, DAY + MINUTE),
        (DAY + 1, DAY + MINUTE),
    ):
        c.eq(f"origin({ea})", mod.origin(ea), want)
    # every millisecond of three minutes, against a brute-force scan of candle closes
    closes = list(range(0, 8 * MINUTE, MINUTE))
    wrong = 0
    for start in (0, DAY - MINUTE, int(datetime(2026, 12, 31, 23, 58, tzinfo=UTC).timestamp() * 1000)):
        for offset in range(0, 3 * MINUTE, 7):
            ea = start + offset
            expected = min(k for k in (start - start % MINUTE + j * MINUTE for j in range(0, 8)) if k > ea)
            got = mod.origin(ea)
            if got != expected or got % MINUTE != 0 or not 0 < got - ea <= MINUTE:
                wrong += 1
    c.eq("origin against a scan of candle closes", wrong, 0)
    del closes
    c.eq(
        "a boundary and the millisecond after",
        (mod.origin(5 * MINUTE), mod.origin(5 * MINUTE + 1), mod.origin(5 * MINUTE - 1)),
        (6 * MINUTE, 6 * MINUTE, 5 * MINUTE),
    )
    for bad in (-1, 1.5, True, "5", None):
        c.refuses(f"origin({bad!r})", lambda b=bad: mod.origin(b))
    for t0 in (0, 1, MINUTE + 1, -MINUTE, 1.5 * MINUTE, True):
        c.refuses(f"the reference candle of {t0!r}", lambda t=t0: mod.reference_candle_open(t))
    c.eq("the first possible origin", mod.reference_candle_open(MINUTE), 0)
    # the outcome
    t0 = at(10, 2, 0)
    for h in (HOUR, 4 * HOUR, DAY, 7 * DAY, 90 * MINUTE, 61 * MINUTE):
        c.eq(f"outcome_close({h})", mod.outcome_close(t0, h), t0 + h)
        c.eq(f"outcome_candle_open({h})", mod.outcome_candle_open(t0, h), t0 + h - MINUTE)
    for h in (0, MINUTE, HOUR - MINUTE, HOUR - 1, HOUR + 1, HOUR + 30_000, -HOUR, 1.5 * HOUR, True, "1h", None):
        c.refuses(f"the horizon {h!r}", lambda x=h: mod.outcome_close(t0, x))
        c.refuses(f"horizon_ok({h!r})", lambda x=h: mod.horizon_ok(x))
    c.eq("a valid horizon is returned", (mod.horizon_ok(HOUR), mod.horizon_ok(61 * MINUTE)), (HOUR, 61 * MINUTE))
    c.refuses("an outcome from a bad origin", lambda: mod.outcome_close(t0 + 1, HOUR))
    return c.bad


def check_fill(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    t0 = at(10, 2, 0)
    c.eq("authorised long before the origin: the origin", mod.earliest_fill(t0 - 5 * MINUTE, 15_000, t0), t0)
    c.eq("authorised 10 s before the origin with 15 s latency: 5 s after it", mod.earliest_fill(t0 - 10_000, 15_000, t0), t0 + 5_000)
    c.eq("authorisation plus latency exactly at the origin", mod.earliest_fill(t0 - 15_000, 15_000, t0), t0)
    c.eq("one millisecond past", mod.earliest_fill(t0 - 14_999, 15_000, t0), t0 + 1)
    c.eq("authorised after the origin", mod.earliest_fill(t0 + 20_000, 15_000, t0), t0 + 35_000)
    c.eq("no latency", mod.earliest_fill(t0 - 1, 0, t0), t0)
    for fill, want in (
        (0, 0),
        (1, MINUTE),
        (MINUTE - 1, MINUTE),
        (MINUTE, MINUTE),
        (MINUTE + 1, 2 * MINUTE),
        (t0, t0),
        (t0 + 5_000, t0 + MINUTE),
        (t0 + 59_999, t0 + MINUTE),
    ):
        c.eq(f"fill_candle_open({fill})", mod.fill_candle_open(fill), want)
    # every offset of the authorisation inside two minutes, several latencies, against a scan of minute boundaries
    failures = 0
    for latency in (0, 1, 15_000, 59_999, 60_000, 75_000):
        for authorized in range(t0 - MINUTE, t0 + MINUTE, 997):
            fill = mod.earliest_fill(authorized, latency, t0)
            opened = mod.fill_candle_open(fill)
            expected = min(k for k in range(t0 - 2 * MINUTE, t0 + 4 * MINUTE, MINUTE) if k >= max(authorized + latency, t0))
            if (
                opened != expected
                or fill < t0
                or fill < authorized + latency
                or opened < fill
                or opened - fill >= MINUTE
                or opened == mod.reference_candle_open(t0)
            ):
                failures += 1
    c.eq(
        "the fill sweep: never before the origin, never the reference candle, the first minute boundary after the earliest fill",
        failures,
        0,
    )
    for args in ((-1, 0, MINUTE), (0, -1, MINUTE), (0, 0, 1), (0, 0, 0), (1.5, 0, MINUTE), (0, 0, True)):
        c.refuses(f"earliest_fill{args}", lambda a=args: mod.earliest_fill(*a))
    for bad in (-1, 1.5, True):
        c.refuses(f"fill_candle_open({bad!r})", lambda b=bad: mod.fill_candle_open(b))
    return c.bad


def check_attestation(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    committed = at(10, 1, 0)
    c.eq("the window", mod.token_window(committed), (committed - 1_000, committed + 60_000))
    c.eq("the window of a commit near the epoch", mod.token_window(500), (0, 60_500))
    c.eq("a window that starts exactly at zero", mod.token_window(1000), (0, 61_000))
    for gen, want in (
        (committed - 30_000, False),
        (committed - 1_001, False),
        (committed - 1_000, True),
        (committed, True),
        (committed + 1_000, True),
        (committed + 59_999, True),
        (committed + 60_000, True),
        (committed + 60_001, False),
        (committed + 90_000, False),
    ):
        c.eq(f"a token at {gen - committed:+d} ms", mod.token_counts(gen, committed), want)
    c.eq("the attestation closes", mod.attestation_close(committed), committed + 90_000)
    c.eq("the constants", (mod.TOKEN_BEFORE_MS, mod.TOKEN_AFTER_MS, mod.ATTESTATION_RETRY_MS), (1_000, 60_000, 30_000))
    c.refuses("a negative commit time", lambda: mod.token_window(-1))
    c.refuses("a negative generation time", lambda: mod.token_counts(-1, 1000))
    c.refuses("a float commit time", lambda: mod.attestation_close(1.5))
    return c.bad


def check_gap(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for h, want in (
        (HOUR, 6 * MINUTE),
        (2 * HOUR, 12 * MINUTE),
        (90 * MINUTE, 9 * MINUTE),
        (150 * MINUTE, 15 * MINUTE),
        (4 * HOUR, 15 * MINUTE),
        (DAY, 15 * MINUTE),
        (7 * DAY, 15 * MINUTE),
        (61 * MINUTE, 366_000),
    ):
        c.eq(f"league gap bound of {h} ms", mod.gap_bound(h), want)
    for h, want in ((HOUR, 150_000), (DAY, HOUR), (7 * DAY, 7 * HOUR), (4 * HOUR, 600_000), (2 * HOUR, 300_000)):
        c.eq(f"arena gap bound of {h} ms", mod.gap_bound(h, arena=True), want)
    c.refuses("a gap bound of a bad horizon", lambda: mod.gap_bound(MINUTE))
    snap = at(10, 0, 0)
    c.eq("the gap", mod.gap_ms(snap, snap + 5 * MINUTE), 5 * MINUTE)
    c.eq("no gap", mod.gap_ms(snap, snap), 0)
    c.refuses("an origin before the snapshot", lambda: mod.gap_ms(snap, snap - MINUTE))
    # the contract's test: a 7 minute gap voids the one-hour horizon and not the others; 5 minutes voids none
    gap7, gap5 = snap + 7 * MINUTE, snap + 5 * MINUTE
    c.eq("7 minutes", [mod.void_gap(snap, gap7, h) for h in mod.DEFAULT_HORIZONS], [True, False, False])
    c.eq("5 minutes", [mod.void_gap(snap, gap5, h) for h in mod.DEFAULT_HORIZONS], [False, False, False])
    c.eq("exactly the bound is not void", mod.void_gap(snap, snap + 6 * MINUTE, HOUR), False)
    c.eq("a millisecond over is", mod.void_gap(snap - 1, snap + 6 * MINUTE, HOUR), True)
    c.eq("a millisecond under is not", mod.void_gap(snap + 1, snap + 6 * MINUTE, HOUR), False)
    c.eq("exactly 15 minutes is not void for a day", mod.void_gap(snap, snap + 15 * MINUTE, DAY), False)
    c.eq("16 minutes is", mod.void_gap(snap, snap + 16 * MINUTE, DAY), True)
    c.eq(
        "the arena has its own bound",
        (
            mod.void_gap(snap, snap + 30 * MINUTE, DAY),
            mod.void_gap(snap, snap + 30 * MINUTE, DAY, arena=True),
            mod.void_gap(snap, snap + 61 * MINUTE, DAY, arena=True),
        ),
        (True, False, True),
    )
    # decision cutoffs
    c.eq("a fast arm at one hour", mod.decision_cutoff(100_000, HOUR), 150_000)
    c.eq("a slower arm is cut at the room", mod.decision_cutoff(200_000, HOUR), 240_000)
    c.eq("an arm that just fits", mod.decision_cutoff(240_000, HOUR), 240_000)
    c.eq("an arm that does not fit is not admitted", mod.decision_cutoff(240_001, HOUR), None)
    c.eq("a zero latency", mod.decision_cutoff(0, HOUR), 0)
    c.eq("one and a half rounds up", (mod.decision_cutoff(1, HOUR), mod.decision_cutoff(3, HOUR), mod.decision_cutoff(2, HOUR)), (2, 5, 3))
    c.eq(
        "four hours: room is 780 s",
        (
            mod.decision_cutoff(520_000, 4 * HOUR),
            mod.decision_cutoff(780_000, 4 * HOUR),
            mod.decision_cutoff(780_001, 4 * HOUR),
            mod.decision_cutoff(400_000, 4 * HOUR),
        ),
        (780_000, 780_000, None, 600_000),
    )
    c.eq(
        "the arena day",
        (
            mod.decision_cutoff(1_000_000, DAY, arena=True),
            mod.decision_cutoff(3_480_000, DAY, arena=True),
            mod.decision_cutoff(3_480_001, DAY, arena=True),
        ),
        (1_500_000, 3_480_000, None),
    )
    c.eq(
        "an arena hour leaves 30 s of room",
        (
            mod.decision_cutoff(0, HOUR, arena=True),
            mod.decision_cutoff(10_000, HOUR, arena=True),
            mod.decision_cutoff(30_000, HOUR, arena=True),
        ),
        (0, 15_000, 30_000),
    )
    c.eq("an arm slower than the room is not admitted", mod.decision_cutoff(31_000, HOUR, arena=True), None)
    c.refuses("a negative latency", lambda: mod.decision_cutoff(-1, HOUR))
    c.refuses("a bad horizon", lambda: mod.decision_cutoff(1000, 5))
    # the order of a cycle
    d = at(10, 0, 0)
    c.eq("a cycle in order", mod.cycle_order_ok(d, d + 1_000, d + 5 * MINUTE), True)
    c.eq("sealed with the discovery", mod.cycle_order_ok(d, d, d), True)
    c.eq("sealed before the discovery", mod.cycle_order_ok(d, d - 1, d + 1000), False)
    c.eq("a snapshot before the seal", mod.cycle_order_ok(d, d + 2_000, d + 1_000), False)
    c.eq("a snapshot exactly ten minutes after the discovery", mod.cycle_order_ok(d, d + 1_000, d + 10 * MINUTE), True)
    c.eq("a millisecond later", mod.cycle_order_ok(d, d + 1_000, d + 10 * MINUTE + 1), False)
    c.refuses("a negative discovery time", lambda: mod.cycle_order_ok(-1, 0, 0))
    c.eq("the constants", (mod.DISCOVERY_TO_SNAPSHOT_MS, mod.LEAGUE_GAP_CAP_MS, mod.CUTOFF_MARGIN_MS), (10 * MINUTE, 15 * MINUTE, 120_000))
    return c.bad


def check_messages(mod: types.ModuleType) -> list[str]:
    """A refusal names the input that was wrong."""
    c = Case(mod)
    t0 = at(10, 2, 0)
    for label, call, text in (
        ("to_ms", lambda: mod.to_ms(datetime(1960, 1, 1, tzinfo=UTC)), "the instant is "),
        ("from_ms", lambda: mod.from_ms(-1), "the instant is "),
        ("eligible_after attested", lambda: mod.eligible_after(-1, 0, 0), "the attestation time is "),
        ("eligible_after uncertainty", lambda: mod.eligible_after(0, -1, 0), "the uncertainty is "),
        ("eligible_after spread", lambda: mod.eligible_after(0, 0, -1), "the spread is "),
        ("origin", lambda: mod.origin(-1), "eligible_after is "),
        ("reference candle", lambda: mod.reference_candle_open(1), "the origin is "),
        ("outcome horizon", lambda: mod.outcome_close(t0, 5), "a horizon is "),
        ("earliest_fill authorised", lambda: mod.earliest_fill(-1, 0, t0), "the authorisation time is "),
        ("earliest_fill latency", lambda: mod.earliest_fill(0, -1, t0), "the latency is "),
        ("fill candle", lambda: mod.fill_candle_open(-1), "the earliest fill is "),
        ("token window", lambda: mod.token_window(-1), "the commit time is "),
        ("token counts", lambda: mod.token_counts(-1, 1000), "the generation time is "),
        ("attestation close", lambda: mod.attestation_close(-1), "the commit time is "),
        ("gap snapshot", lambda: mod.gap_ms(-1, t0), "the snapshot time is "),
        ("gap origin", lambda: mod.gap_ms(t0 + 1, t0), "the origin is not before "),
        ("cutoff latency", lambda: mod.decision_cutoff(-1, HOUR), "the latency is "),
        ("cycle discovery", lambda: mod.cycle_order_ok(-1, 0, 0), "the discovery time is "),
        ("cycle sealing", lambda: mod.cycle_order_ok(0, -1, 0), "the sealing time is "),
        ("cycle snapshot", lambda: mod.cycle_order_ok(0, 0, -1), "the snapshot time is "),
        ("parameters", lambda: mod.ClockParameters(-1, 0), "the clock constants are not negative"),
        ("an instant without a zone", lambda: mod.to_ms(datetime(2026, 1, 1)), "an instant has a zone"),
        (
            "an instant with a fraction",
            lambda: mod.to_ms(datetime(2026, 1, 1, 0, 0, 0, 1, tzinfo=UTC)),
            "an instant is a whole number of milliseconds",
        ),
        ("a negative origin", lambda: mod.reference_candle_open(-MINUTE), "the origin is a whole number of milliseconds"),
        ("an origin that is not a minute", lambda: mod.reference_candle_open(t0 + 1), "the origin is a whole minute after the epoch"),
    ):
        c.refuses(label, call, text)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_instants, check_origin, check_fill, check_attestation, check_gap, check_messages):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
