"""Scenario checks of the circuit breakers, runnable against the real module or a mutant."""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

from hoshitori.connector import ErrorClass as E

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    S = mod.State

    def make(**kwargs: object):  # type: ignore[no-untyped-def]
        return mod.Breaker("p", mod.BreakerConfig(**kwargs), T0)  # type: ignore[arg-type]

    def check(label: str, got: object, expected: object) -> None:
        if got != expected:
            bad.append(f"{label}: {got!r}, expected {expected!r}")

    # --- consecutive failures open the breaker ---------------------------------------------------------------------------------
    b = make(consecutive_failures=3)
    for i in range(2):
        check(f"allowed before failure {i}", b.allow(at(i)).allowed, True)
        b.record_failure(at(i), E.UPSTREAM)
    check("still closed after two", b.state(at(2)), S.CLOSED)
    b.allow(at(2))
    b.record_failure(at(2), E.TIMEOUT)
    check("open after three", b.state(at(2)), S.OPEN)
    refused = b.allow(at(3))
    check("refused while open", (refused.allowed, refused.state, refused.reason, refused.retry_at), (False, S.OPEN, "OPEN", at(17)))
    check("a refusal while open at the very end", b.allow(at(16.999)).allowed, False)
    check("the reason is kept", "3 failures in a row" in b.reason and "TIMEOUT" in b.reason, True)

    # a success in between resets the streak
    b = make(consecutive_failures=3)
    for i, ok in enumerate([False, False, True, False, False]):
        b.allow(at(i))
        b.record_success(at(i)) if ok else b.record_failure(at(i), E.UPSTREAM)
    check("a success resets the streak", b.state(at(5)), S.CLOSED)

    # --- classes that do not count -----------------------------------------------------------------------------------------------
    b = make(consecutive_failures=2)
    tick = 0
    for cls in (E.RATE_LIMIT, E.INVALID_ARGS, E.NOT_FOUND):
        for _ in range(5):
            b.record_failure(at(tick), cls)
            tick += 1
    check("back-pressure and our own errors never open it", b.state(at(tick)), S.CLOSED)
    b.record_failure(at(tick), E.UPSTREAM)
    b.record_failure(at(tick + 1), E.RATE_LIMIT)  # does not reset the streak either
    b.record_failure(at(tick + 2), E.UPSTREAM)
    check("a non-counting class neither adds nor resets", b.state(at(tick + 2)), S.OPEN)
    for cls in (E.BANNED, E.AUTH, E.GEO_BLOCKED, E.TIMEOUT, E.UPSTREAM, E.MAINTENANCE, E.CONTRACT_DRIFT, E.MALFORMED, E.UNCLASSIFIED):
        c = make(consecutive_failures=1)
        c.record_failure(at(0), cls)
        check(f"{cls.value} counts", c.state(at(0)), S.OPEN)

    # --- the error rate over a window -----------------------------------------------------------------------------------------------
    b = make(consecutive_failures=100, window_calls=20, error_rate=0.5)
    pattern = [False, True] * 10  # 10 failures of 20, the last event a failure: exactly half, not above
    for i, failed in enumerate(pattern):
        b.record_failure(at(i), E.UPSTREAM) if failed else b.record_success(at(i))
    check("exactly half does not open it", b.state(at(20)), S.CLOSED)
    b.record_failure(at(20), E.UPSTREAM)  # the window slides: one more failure replaces a success
    check("more than half opens it", b.state(at(20)), S.OPEN)
    # only the last window counts, however long the history
    b = make(consecutive_failures=100, window_calls=20, error_rate=0.5)
    for i in range(100):
        b.record_success(at(i))
    for i in range(10):
        b.record_failure(at(100 + i), E.UPSTREAM)
    check("ten failures of the last twenty do not open it", b.state(at(110)), S.CLOSED)
    b.record_failure(at(110), E.UPSTREAM)
    check("eleven of the last twenty open it", b.state(at(110)), S.OPEN)
    # a close forgets the failures that led to it
    b = make(consecutive_failures=100, window_calls=4, error_rate=0.5, cooldown_s=10)
    for i in range(4):
        b.record_failure(at(i), E.UPSTREAM)
    check("opened by the rate", b.state(at(4)), S.OPEN)
    b.allow(at(14))
    b.record_success(at(14))
    check("a close clears the reason", b.reason, "")
    b.record_failure(at(15), E.UPSTREAM)
    check("a close clears the window", b.state(at(15)), S.CLOSED)
    b = make(consecutive_failures=100, window_calls=20, error_rate=0.5)
    for i in range(19):
        b.record_failure(at(i), E.UPSTREAM) if i % 4 else b.record_success(at(i))
    check("fewer than a full window never opens by rate", b.state(at(19)), S.CLOSED)
    b = make(consecutive_failures=100, window_calls=4, error_rate=0.5)
    for i, failed in enumerate([True, True, True, False]):
        b.record_failure(at(i), E.UPSTREAM) if failed else b.record_success(at(i))
    check("the rate counts the full window", b.state(at(4)), S.CLOSED)
    b.record_failure(at(4), E.UPSTREAM)
    check("three of the last four", b.state(at(4)), S.OPEN)

    # --- half-open: one trial at a time ----------------------------------------------------------------------------------------------
    b = make(consecutive_failures=1, cooldown_s=10, cooldown_max_s=100)
    b.allow(at(0))
    b.record_failure(at(0), E.UPSTREAM)
    check("open", b.state(at(9.9)), S.OPEN)
    check("half-open when the cool-down ends", b.state(at(10)), S.HALF_OPEN)
    trial = b.allow(at(10))
    check("the trial", (trial.allowed, trial.state, trial.reason), (True, S.HALF_OPEN, "TRIAL"))
    second = b.allow(at(10.5))
    check("a second call during the trial", (second.allowed, second.reason), (False, "TRIAL_IN_FLIGHT"))
    b.record_success(at(11))
    check("a successful trial closes it", b.state(at(11)), S.CLOSED)
    check("and calls flow again", (b.allow(at(11)).allowed, b.allow(at(11)).allowed), (True, True))
    check(
        "a close is remembered",
        [(t.before.value, t.after.value) for t in b.transitions],
        [("CLOSED", "OPEN"), ("OPEN", "HALF_OPEN"), ("HALF_OPEN", "CLOSED")],
    )

    # a failed trial opens it again with a longer cool-down, doubling to a cap
    b = make(consecutive_failures=1, cooldown_s=10, cooldown_max_s=35)
    b.record_failure(at(0), E.UPSTREAM)  # open until 10
    now = 10.0
    for expected_wait in (20.0, 35.0, 35.0):
        check(f"half-open at {now}", b.state(at(now)), S.HALF_OPEN)
        check("a trial is allowed", b.allow(at(now)).allowed, True)
        b.record_failure(at(now), E.UPSTREAM)
        check(f"open again at {now}", b.state(at(now)), S.OPEN)
        check(f"cool-down {expected_wait}", b.allow(at(now + 1)).retry_at, at(now + expected_wait))
        now += expected_wait
    b.state(at(now))
    b.allow(at(now))
    b.record_success(at(now))
    b.record_failure(at(now + 1), E.UPSTREAM)
    check("a success resets the escalation", b.allow(at(now + 2)).retry_at, at(now + 1 + 10))

    # a trial that ends in a non-counting outcome frees the slot and stays half-open
    b = make(consecutive_failures=1, cooldown_s=10)
    b.record_failure(at(0), E.UPSTREAM)
    b.allow(at(10))
    b.record_failure(at(10), E.RATE_LIMIT)
    check("a rate limit during a trial leaves it half-open", b.state(at(11)), S.HALF_OPEN)
    check("and the next call is the trial", (b.allow(at(11)).allowed, b.allow(at(11)).allowed), (True, False))
    # a trial whose result never comes back expires
    b = make(consecutive_failures=1, cooldown_s=10, trial_ttl_s=60)
    b.record_failure(at(0), E.UPSTREAM)
    b.allow(at(10))
    check("a lost trial still blocks", b.allow(at(69)).allowed, False)
    check("and expires", b.allow(at(70)).allowed, True)
    b.release_trial()
    check("a released trial can be taken again", b.allow(at(71)).allowed, True)

    # --- bans -----------------------------------------------------------------------------------------------------------------------------
    b = make(consecutive_failures=5, cooldown_s=10)
    b.record_failure(at(0), E.BANNED, ban_s=120)
    check("a ban opens it at once", b.state(at(0)), S.OPEN)
    check("for exactly the ban", (b.allow(at(119)).allowed, b.allow(at(119)).retry_at), (False, at(120)))
    check("the ban ends", b.state(at(120)), S.HALF_OPEN)
    b.allow(at(120))
    b.record_failure(at(120), E.BANNED, ban_s=30)
    check("a ban is not escalated", b.allow(at(121)).retry_at, at(150))
    b = make(consecutive_failures=5)
    b.record_failure(at(0), E.BANNED, ban_s=300)
    b.record_failure(at(1), E.BANNED, ban_s=10)  # never shortens
    check("a shorter ban never shortens", b.allow(at(2)).retry_at, at(300))
    b.record_failure(at(2), E.BANNED, ban_s=600)
    check("a longer ban lengthens", b.allow(at(3)).retry_at, at(602))
    check("every extension is recorded", [tr.reason for tr in b.transitions][-1], "banned for 600 s")
    b = make(consecutive_failures=5, cooldown_s=10)
    b.record_failure(at(0), E.BANNED, ban_s=5)
    b.allow(at(5))
    b.record_failure(at(5), E.UPSTREAM)
    check("a ban does not make the next cool-down longer", b.allow(at(6)).retry_at, at(15))
    b = make(consecutive_failures=5)
    b.record_failure(at(0), E.BANNED, ban_s=-5)
    check("a negative ban is no ban", b.state(at(0)), S.HALF_OPEN)
    b = make(consecutive_failures=5)
    b.record_failure(at(0), E.BANNED)  # no stated length: an ordinary counted failure
    check("a ban without a length is one failure", b.state(at(0)), S.CLOSED)

    # --- late outcomes while open --------------------------------------------------------------------------------------------------
    b = make(consecutive_failures=1, cooldown_s=10)
    b.record_failure(at(0), E.UPSTREAM)
    b.record_success(at(1))  # a call that started before it opened
    check("a late success does not close it", b.state(at(2)), S.OPEN)
    b.record_failure(at(2), E.UPSTREAM)
    check("a late failure does not extend it", b.allow(at(3)).retry_at, at(10))

    # --- probes ---------------------------------------------------------------------------------------------------------------------------
    b = make(consecutive_failures=1, cooldown_s=100, probe_interval_s=15)
    check("a closed breaker allows probes", (b.probe_allowed(at(0)), b.probe_allowed(at(0))), (True, True))
    b.record_failure(at(0), E.UPSTREAM)
    check("one probe", b.probe_allowed(at(1)), True)
    check("not another within the interval", b.probe_allowed(at(15.9)), False)
    check("the next interval", b.probe_allowed(at(16)), True)

    # --- breaker sets ---------------------------------------------------------------------------------------------------------------------------
    def new_set() -> object:
        provider = mod.Breaker("ryo", mod.BreakerConfig(consecutive_failures=3, cooldown_s=10), T0)
        return mod.BreakerSet(provider, mod.BreakerConfig(consecutive_failures=2, cooldown_s=10))

    s = new_set()
    for i in range(2):
        s.allow(at(i), "scan_market")
        s.record_failure(at(i), "scan_market", E.MALFORMED)
    check("a tool that changed shape is refused", s.allow(at(3), "scan_market").allowed, False)
    check("while the other tools keep working", s.allow(at(3), "analyze_token").allowed, True)
    check("and the provider is closed", s.provider.state(at(3)), S.CLOSED)
    s = new_set()
    for i in range(3):
        s.allow(at(i), f"tool{i}")
        s.record_failure(at(i), f"tool{i}", E.UPSTREAM)  # each tool fails once: the shared stage is what broke
    check("failures of the shared stage open the provider", s.provider.state(at(3)), S.OPEN)
    check("and every tool is refused", (s.allow(at(4), "tool0").allowed, s.allow(at(4), "brand_new_tool").allowed), (False, False))
    check("the tools themselves stay closed", [t.state(at(4)) for t in s.tools.values()], [S.CLOSED] * 3)
    s = new_set()
    s.allow(at(0), "a")
    s.record_success(at(0), "a")
    s.record_failure(at(1), "a", E.UPSTREAM)
    s.record_failure(at(2), "a", E.UPSTREAM)
    s.record_success(at(3), "a")  # a success resets both streaks
    s.record_failure(at(4), "a", E.UPSTREAM)
    s.record_failure(at(5), "a", E.UPSTREAM)
    check("a success resets the provider streak", s.provider.state(at(5)), S.CLOSED)
    # the half-open trial slots of the two levels
    s = new_set()
    s.record_failure(at(0), "x", E.MALFORMED)
    s.record_failure(at(1), "x", E.MALFORMED)  # tool x open until 11
    for i in range(3):
        s.record_failure(at(i + 2), "y", E.UPSTREAM)  # provider open until 14
    check("the provider is open", s.allow(at(5), "z").allowed, False)
    first = s.allow(at(14), "z")
    check("the provider's trial", (first.allowed, first.state, first.reason), (True, S.HALF_OPEN, "TRIAL"))
    check("a second call is refused during the trial", s.allow(at(14.5), "z").allowed, False)
    check("on any tool", s.allow(at(14.6), "x").allowed, False)
    s.record_success(at(15), "z")
    check("a successful trial closes the provider", s.provider.state(at(15)), S.CLOSED)
    again = s.allow(at(15), "x")
    check("the tool's own trial", (again.allowed, again.state, again.reason), (True, S.HALF_OPEN, "TRIAL"))
    s.record_failure(at(15.5), "x", E.MALFORMED)
    check(
        "a failed tool trial opens the tool, not the provider",
        (s.tools["x"].state(at(15.5)), s.provider.state(at(15.5))),
        (S.OPEN, S.CLOSED),
    )
    check("and the provider's slot is not left taken", s.allow(at(16), "other").state, S.CLOSED)
    # one call can be the trial of both levels
    s = new_set()
    s.record_failure(at(0), "x", E.MALFORMED)
    s.record_failure(at(1), "x", E.MALFORMED)  # tool x open until 11
    for i in range(3):
        s.record_failure(at(i + 2), "y", E.UPSTREAM)  # provider open until 14
    both = s.allow(at(14), "x")
    check("one call is both trials", (both.allowed, both.state), (True, S.HALF_OPEN))
    s.record_success(at(15), "x")
    check("and one success closes both", (s.provider.state(at(15)), s.tools["x"].state(at(15))), (S.CLOSED, S.CLOSED))
    # a failure that belongs to the tool gives back the provider's trial slot, and the other way round
    s = new_set()
    for i in range(3):
        s.record_failure(at(i), "y", E.UPSTREAM)  # provider open until 12
    both = s.allow(at(12), "x")
    s.record_failure(at(12), "x", E.MALFORMED)
    check("a tool-scoped failure of the provider's trial", both.state, S.HALF_OPEN)
    check("leaves the provider's trial slot free", s.allow(at(12.5), "y").allowed, True)
    s = new_set()
    s.record_failure(at(0), "x", E.MALFORMED)
    s.record_failure(at(1), "x", E.MALFORMED)  # tool x open until 11
    for i in range(3):
        s.record_failure(at(i + 2), "y", E.UPSTREAM)  # provider open until 14
    s.allow(at(14), "x")
    s.record_failure(at(14), "x", E.UPSTREAM)  # a provider-scoped failure of a call that was both trials
    check("the provider opened again", s.provider.state(at(14)), S.OPEN)
    check("and the tool's trial slot is free", s.tools["x"].allow(at(14)).allowed, True)
    # bans reach the provider breaker with their length; schema drift belongs to the tool
    s = new_set()
    s.record_failure(at(0), "x", E.BANNED, ban_s=120)
    check("a ban through the set", (s.provider.state(at(60)), s.provider.allow(at(60)).retry_at), (S.OPEN, at(120)))
    s = new_set()
    for i in range(2):
        s.allow(at(i), "x")
        s.record_failure(at(i), "x", E.CONTRACT_DRIFT)
    check("drift opens the tool", s.allow(at(3), "x").allowed, False)
    check("but not the provider", (s.provider.state(at(3)), s.allow(at(3), "other").allowed), (S.CLOSED, True))
    # a refused tool call gives the provider's trial slot back
    s = new_set()
    for i in range(3):
        s.record_failure(at(i), "y", E.UPSTREAM)  # provider open until 12
    s.record_failure(at(10), "x", E.MALFORMED)
    s.record_failure(at(11), "x", E.MALFORMED)  # tool x open until 21
    check("the tool is refused in the provider's trial", s.allow(at(12), "x").allowed, False)
    check("and the provider's trial slot is free again", s.allow(at(12), "other").allowed, True)

    # --- validation ---------------------------------------------------------------------------------------------------------------------
    for label, build in [
        ("zero consecutive failures", lambda: mod.BreakerConfig(consecutive_failures=0)),
        ("zero window", lambda: mod.BreakerConfig(window_calls=0)),
        ("rate zero", lambda: mod.BreakerConfig(error_rate=0)),
        ("rate one", lambda: mod.BreakerConfig(error_rate=1)),
        ("zero cool-down", lambda: mod.BreakerConfig(cooldown_s=0)),
        ("maximum below start", lambda: mod.BreakerConfig(cooldown_s=10, cooldown_max_s=5)),
        ("factor below one", lambda: mod.BreakerConfig(cooldown_factor=0.5)),
        ("zero probe interval", lambda: mod.BreakerConfig(probe_interval_s=0)),
        ("zero trial ttl", lambda: mod.BreakerConfig(trial_ttl_s=0)),
        ("naive time", lambda: mod.Breaker("x", mod.BreakerConfig(), T0.replace(tzinfo=None))),
        ("time going backwards", lambda: (lambda x: (x.state(at(5)), x.state(at(1))))(make())),
    ]:
        try:
            build()
            bad.append(f"{label}: accepted")
        except (mod.BreakerError, ValueError):
            pass
    return bad
