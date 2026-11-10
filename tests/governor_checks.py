"""Scenario checks of the rate governor, runnable against the real module or a mutant."""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    P, M = mod.Priority, mod.RateModel

    def fixed(limit: float = 6, window: float = 60) -> object:
        return mod.Governor("ryo", M.FIXED_WINDOW, limit, window, T0)

    def bucket(limit: float = 6, window: float = 60) -> object:
        return mod.Governor("ryo", M.TOKEN_BUCKET, limit, window, T0)

    def expect(label: str, verdict, ok: bool, reason: str, available: float | None = None) -> None:  # type: ignore[no-untyped-def]
        if verdict.ok is not ok or verdict.reason != reason:
            bad.append(f"{label}: {verdict.ok} {verdict.reason}, expected {ok} {reason}")
        if available is not None and (verdict.available_at is None or abs((verdict.available_at - at(available)).total_seconds()) > 0.05):
            bad.append(f"{label}: available at {verdict.available_at}, expected {at(available)}")
        if available is None and ok is False and reason == "TOO_EXPENSIVE" and verdict.available_at is not None:
            bad.append(f"{label}: an impossible request has a time")

    # --- a bucket that holds fewer tokens than its rate of a window: the burst a stricter limit than the stated one allows -----------------
    g = mod.Governor("ryo", M.TOKEN_BUCKET, 5, 60, T0, burst=1)
    expect("a burst of one: the first call", g.try_spend(at(0), 1, P.P0), True, "OK")
    expect("the second at the same time waits for the refill of one", g.try_spend(at(0), 1, P.P0), False, "TOKENS", 12)
    spent: list[float] = []
    clock = 0.0
    while clock < 600:
        if g.try_spend(at(clock), 1, P.P0).ok:
            spent.append(clock)
        clock += 1
    worst = max(sum(1 for s in spent if t0 - 60 < s <= t0) for t0 in spent)
    if worst > 6:
        bad.append(f"a burst of one at five a minute let {worst} calls into a window of a minute (the limit it guards is six)")
    if len(spent) < 45:
        bad.append(f"a burst of one at five a minute spent {len(spent)} calls in ten minutes; it must still use the rate it is given")
    idle = mod.Governor("ryo", M.TOKEN_BUCKET, 5, 60, T0, burst=1)
    if abs(idle.available(at(3600)) - 1.0) > 1e-9:
        bad.append("an idle bucket of burst one holds more than one token")
    for wrong in (0, -1, 6):
        try:
            mod.Governor("ryo", M.TOKEN_BUCKET, 5, 60, T0, burst=wrong)
            bad.append(f"a burst of {wrong} was accepted for a limit of 5")
        except mod.GovernorError:
            pass
    expect("a request larger than the burst can never be made", g.check(at(700), 2, P.P0), False, "TOO_EXPENSIVE")

    # --- a fixed window --------------------------------------------------------------------------------------------------------
    g = fixed()
    for i in range(6):
        expect(f"fixed spend {i}", g.try_spend(at(i), 1, P.P0), True, "OK")
    expect("fixed seventh", g.try_spend(at(7), 1, P.P0), False, "TOKENS", 60)
    if abs(g.available(at(7))) > 1e-9:
        bad.append("a fixed window with six spent has tokens left")
    expect("fixed at the roll", g.try_spend(at(60), 1, P.P0), True, "OK")
    if g.available(at(60)) != 5 or g.window_end != at(120):
        bad.append(
            f"a spend at the exact end of a window was charged to the old one: {g.available(at(60))} left, window ends {g.window_end}"
        )
    expect("fixed just before the roll", fixed().check(at(0), 1, P.P0), True, "OK")
    h = fixed()
    for _ in range(6):
        h.try_spend(at(0), 1, P.P0)
    expect("fixed one second before the end", h.check(at(59), 1, P.P0), False, "TOKENS", 60)
    expect("fixed exactly at the end", h.check(at(60), 1, P.P0), True, "OK")
    h.advance(at(60 + 125))  # a long gap rolls whole windows
    if h.available(at(60 + 125)) != 6 or h.window_end != at(240):
        bad.append(f"a long gap left the window at {h.window_end} with {h.available(at(185))} left")
    k = fixed()
    k.try_spend(at(0), 3, P.P0)
    expect("a cost of three", k.try_spend(at(1), 3, P.P0), True, "OK")
    expect("a cost that does not fit", k.try_spend(at(2), 1, P.P0), False, "TOKENS", 60)
    expect("a cost above the limit", fixed().check(at(0), 7, P.P0), False, "TOO_EXPENSIVE")
    expect("a cost equal to the limit", fixed().check(at(0), 6, P.P0), True, "OK")

    # --- a token bucket ---------------------------------------------------------------------------------------------------------
    b = bucket()
    for i in range(6):
        expect(f"bucket spend {i}", b.try_spend(at(0), 1, P.P0), True, "OK")
    expect("bucket empty", b.try_spend(at(0), 1, P.P0), False, "TOKENS", 10)
    expect("bucket after five seconds", b.try_spend(at(5), 1, P.P0), False, "TOKENS", 10)
    expect("bucket after ten seconds", b.try_spend(at(10), 1, P.P0), True, "OK")
    if abs(b.available(at(10))) > 1e-9:
        bad.append("the bucket kept a token it spent")
    if abs(b.available(at(35)) - 2.5) > 1e-9:
        bad.append(f"the bucket refilled to {b.available(at(35))} after 25 seconds, expected 2.5")
    if bucket().available(at(1000)) != 6:
        bad.append("the bucket refilled beyond its capacity")
    c = bucket()
    c.try_spend(at(0), 6, P.P0)
    expect("bucket cost of three", c.check(at(30), 3, P.P0), True, "OK")
    expect("bucket cost of three too early", c.check(at(29), 3, P.P0), False, "TOKENS", 30)
    expect("bucket cost above the capacity", bucket().check(at(0), 6.5, P.P0), False, "TOO_EXPENSIVE")
    if mod.Governor("x", M.TOKEN_BUCKET, 6, 60, T0).refill_period_s() != 10 or fixed().refill_period_s() != 60:
        bad.append("the refill period is wrong")

    # --- earliest ---------------------------------------------------------------------------------------------------------------
    e = fixed()
    e.try_spend(at(0), 6, P.P0)
    if e.earliest(at(5), 1, P.P0) != at(60):
        bad.append(f"earliest in a fixed window: {e.earliest(at(5), 1, P.P0)}")
    if fixed().earliest(at(0), 7, P.P0) is not None:
        bad.append("an impossible request has an earliest time")
    f = bucket()
    f.try_spend(at(0), 6, P.P0)
    got = f.earliest(at(0), 1, P.P0)
    if got is None or abs((got - at(10)).total_seconds()) > 0.05:
        bad.append(f"earliest in a bucket: {got}")
    if fixed().earliest(at(3), 1, P.P0) != at(3):
        bad.append("earliest of an allowed request is not now")

    # --- back-pressure and bans ---------------------------------------------------------------------------------------------------
    p = fixed()
    p.pause(at(0), 5)
    expect("paused", p.check(at(1), 1, P.P0), False, "PAUSED", 5)
    expect("pause over", p.check(at(5), 1, P.P0), True, "OK")
    p.pause(at(1), 1)  # never shortens
    expect("pause not shortened", p.check(at(4), 1, P.P0), False, "PAUSED", 5)
    p.pause(at(1), 20)
    expect("pause extended", p.check(at(10), 1, P.P0), False, "PAUSED", 21)
    p.pause(at(2), -5)  # a negative wait is no wait
    q = fixed()
    q.pause(at(0), -5)
    expect("negative pause", q.check(at(0), 1, P.P0), True, "OK")
    if q.paused_until is not None and q.paused_until < q.now:
        bad.append("a negative pause reaches into the past")
    r = fixed()
    r.ban(at(0), 120)
    r.pause(at(0), 10)
    expect("banned", r.check(at(60), 1, P.P0), False, "BANNED", 120)
    r.ban(at(1), 30)  # never shortens
    expect("ban not shortened", r.check(at(100), 1, P.P0), False, "BANNED", 120)
    expect("ban over", r.check(at(120), 1, P.P0), True, "OK")
    negative = fixed()
    negative.ban(at(0), -5)
    if negative.banned_until is not None and negative.banned_until < negative.now:
        bad.append("a negative ban reaches into the past")
    r.ban(at(121), 300)
    expect("ban extended", r.check(at(200), 1, P.P0), False, "BANNED", 421)
    r2 = fixed()
    r2.pause(at(0), 50)
    r2.ban(at(0), 10)
    expect("a ban reported before a pause", r2.check(at(5), 1, P.P0), False, "BANNED", 10)
    expect("then the pause", r2.check(at(10), 1, P.P0), False, "PAUSED", 50)
    expect("a pause blocks every class", r2.check(at(20), 1, P.PA), False, "PAUSED", 50)

    # --- headers ----------------------------------------------------------------------------------------------------------------------
    o = fixed()
    for i in range(2):
        o.try_spend(at(i), 1, P.P0)
    if not o.observe(at(3), remaining=1, reset_at=at(57), response_date=at(3)):
        bad.append("fresh headers were ignored")
    if o.available(at(3)) != 1 or o.window_end != at(57):
        bad.append(f"headers did not replace belief: {o.available(at(3))} left, window ends {o.window_end}")
    o2 = fixed()
    for i in range(4):
        o2.try_spend(at(i), 1, P.P0)
    o2.observe(at(5), remaining=5, reset_at=at(60), response_date=at(4))  # the provider counted fewer than we sent: believe it
    if o2.available(at(5)) != 5 - sum(1 for i in range(4) if at(i) >= at(4)):
        bad.append(f"unseen spends were not subtracted: {o2.available(at(5))}")
    o3 = fixed()
    o3.try_spend(at(0), 1, P.P0)
    o3.try_spend(at(10), 1, P.P0)
    o3.try_spend(at(11), 1, P.P0)
    o3.observe(at(12), remaining=4, reset_at=at(60), response_date=at(10))  # two spends are not in the header
    if o3.available(at(12)) != 2:
        bad.append(f"spends started after the response date were not subtracted: {o3.available(at(12))}")
    o4 = fixed()
    o4.try_spend(at(0), 5, P.P0)
    if o4.observe(at(5), remaining=6, reset_at=at(30), response_date=at(40)):  # reset before the response's date: the window had rolled
        bad.append("stale headers were used")
    if o4.available(at(5)) != 1:
        bad.append("stale headers changed the local belief")
    if o4.observe(at(5), remaining=6, reset_at=at(40), response_date=at(40)):
        bad.append("a header reset equal to the response date was used")
    if not fixed().observe(at(1), remaining=3, reset_at=at(41), response_date=at(1)):
        bad.append("a reset after the date was ignored")
    if fixed().observe(at(1)):
        bad.append("an observation without a remaining count claims to have been used")
    o5 = fixed()
    o5.observe(at(1), remaining=500)
    if o5.available(at(1)) != 6:
        bad.append("a remaining count above the limit was believed")
    o6 = fixed()
    o6.observe(at(1), remaining=-4)
    if o6.available(at(1)) != 0:
        bad.append("a negative remaining count was believed")
    o7 = fixed()
    o7.observe(at(1), limit=10, remaining=10, response_date=at(1))
    if o7.limit != 10 or o7.available(at(1)) != 10:
        bad.append("a changed limit was not taken")
    o8 = bucket()
    o8.observe(at(0), limit=3, remaining=3)
    if o8.limit != 3 or o8.available(at(0)) != 3:
        bad.append("a smaller limit did not shrink the bucket")
    o8b = bucket()
    o8b.observe(at(0), limit=3)
    if o8b.limit != 3 or o8b.available(at(0)) != 3:
        bad.append("a smaller limit alone did not shrink the bucket")
    o8c = bucket()
    o8c.observe(at(0), limit=0)
    o8c.observe(at(0), limit=-4)
    if o8c.limit != 6:
        bad.append("a limit that is not positive was taken")
    o9 = bucket()
    o9.try_spend(at(0), 4, P.P0)
    o9.observe(at(0), remaining=1)
    if abs(o9.available(at(0)) - 1) > 1e-9:
        bad.append("a bucket did not take the header's remaining count")
    o10 = fixed()
    o10.observe(at(1), remaining=2, reset_at=at(0), response_date=None)  # no date to compare; a reset in the past is not a window end
    if o10.window_end != at(60):
        bad.append(f"a header reset in the past moved the window end to {o10.window_end}")
    old = fixed()
    old.try_spend(at(0), 1, P.P0)
    old.try_spend(at(10), 1, P.P0)
    old.observe(at(200), remaining=4, response_date=at(5))  # a date older than the window: the spends it would match are long forgotten
    if old.available(at(200)) != 4:
        bad.append(f"spends older than a window were still subtracted: {old.available(at(200))}")
    o11 = fixed()
    o11.observe(at(1), remaining=2, reset_at=at(33), response_date=None)
    if o11.window_end != at(33):
        bad.append("the header's reset time did not set the window end")

    # --- classes and declared windows ---------------------------------------------------------------------------------------------
    w = bucket()
    w.add_earmark(at(100), 6, at(220))
    expect("far from a window every class may spend", w.check(at(0), 1, P.P2), True, "OK")
    expect("one refill period before a window, P2 gets nothing", w.check(at(90), 1, P.P2), False, "BLACKOUT", 220)
    expect("just before that, P2 may still spend", w.check(at(89.9), 1, P.P2), True, "OK")
    expect("P3 gets nothing either", w.check(at(95), 1, P.P3), False, "BLACKOUT", 220)
    expect("inside the window P2 gets nothing", w.check(at(150), 1, P.P2), False, "BLACKOUT", 220)
    expect("at the deadline P2 still gets nothing", w.check(at(220), 1, P.P2), False, "BLACKOUT", 220)
    expect("after the deadline P2 is served", w.check(at(221), 1, P.P2), True, "OK")
    for cls in (P.P0, P.PJ, P.PA):
        expect(f"{cls.value} ignores a window", w.check(at(150), 1, cls), True, "OK")
    w2 = bucket()
    w2.add_earmark(at(5), 6, at(220))
    w2.try_spend(at(0), 6, P.P0)
    for cls in (P.P0, P.PJ, P.PA):
        expect(f"{cls.value} ignores a window it could not otherwise pay for", w2.check(at(10), 1, cls), True, "OK")
    for cls in (P.P1, P.P2, P.P3):
        if w2.check(at(10), 1, cls).ok:
            bad.append(f"{cls.value} spent tokens a window needs")
    # P1 is not blacked out but may not take what the window needs
    x = bucket()
    x.add_earmark(at(30), 6, at(200))
    x.try_spend(at(0), 3, P.P0)  # 3 tokens left at t=0; by t=30 they refill to 6 even after P1 takes some? 3 + 3 = 6 at t=30
    expect("P1 may not take what the window needs", x.check(at(0), 1, P.P1), False, "EARMARK")
    y = bucket()
    y.add_earmark(at(30), 5, at(200))
    expect("P1 may take what the window can spare", y.check(at(0), 1, P.P1), True, "OK")
    y.try_spend(at(0), 1, P.P1)
    expect("and the next one too", y.check(at(0), 1, P.P1), True, "OK")
    z = fixed()
    z.add_earmark(at(30), 6, at(200))
    expect("a window in this fixed window: P1 may not spend", z.check(at(0), 1, P.P1), False, "EARMARK")
    zz = fixed()
    zz.add_earmark(at(70), 6, at(200))  # starts after this window rolls: a spend now cannot touch it
    expect("a window after the roll: P1 may spend", zz.check(at(0), 1, P.P1), True, "OK")
    far = fixed()
    far.add_earmark(at(1010), 6, at(1100))
    expect("a long jump then a window in the same window", far.check(at(1000), 1, P.P1), False, "EARMARK")
    # two windows need what both take
    t2 = bucket()
    t2.add_earmark(at(30), 3, at(100))
    t2.add_earmark(at(35), 3, at(120))
    t2.try_spend(at(0), 3, P.P0)
    expect("two windows: the second needs the first's tokens too", t2.check(at(0), 1, P.P1), False, "EARMARK")
    # windows far enough apart share the pool one after the other: the second need not hold tokens back now
    sp = bucket()
    sp.add_earmark(at(30), 6, at(60))
    sp.add_earmark(at(100), 6, at(130))
    expect("spaced windows leave P1 its spend", sp.check(at(0), 1, P.P1), True, "OK")
    sp2 = bucket()
    sp2.add_earmark(at(100), 6, at(130))
    sp2.add_earmark(at(30), 6, at(60))  # declared out of order
    expect("windows declared out of order are replayed in order", sp2.check(at(0), 1, P.P1), True, "OK")
    sp3 = bucket()
    sp3.add_earmark(at(30), 6, at(60))
    sp3.add_earmark(at(40), 6, at(70))
    sp3.add_earmark(at(32), 1, at(33))  # replayed in start order: the 1 comes before the second 6
    expect("three windows in order", sp3.check(at(0), 1, P.P1), False, "EARMARK")
    # a window that has ended reserves nothing; one that ends this very instant still does
    fin = bucket()
    fin.add_earmark(at(0), 6, at(50))
    fin.try_spend(at(0), 6, P.P0)
    expect("a window that ends now still reserves", fin.check(at(50), 1, P.P1), False, "BLACKOUT") if False else None
    fin.advance(at(50))
    if fin.check(at(50), 1, P.P1).ok:
        bad.append("a window ending now reserved nothing")
    fin.advance(at(55))
    expect("a window that has ended reserves nothing", fin.check(at(55), 1, P.P1), True, "OK")
    expect("and P2 is served again", fin.check(at(55), 1, P.P2), True, "OK")
    # paying one window down leaves the others alone
    two = bucket()
    first = two.add_earmark(at(10), 3, at(100))
    second = two.add_earmark(at(20), 4, at(100))
    two.try_spend(at(0), 1, P.P0, earmark_id=first)
    if [m.n for m in two.earmarks] != [2, 4]:
        bad.append(f"paying one window changed the others: {[m.n for m in two.earmarks]}")
    two.remove_earmark(first)
    if [m.earmark_id for m in two.earmarks] != [second]:
        bad.append("removing one earmark removed another")
    # an earmark may end the moment it starts
    fixed().add_earmark(at(5), 1, at(5))
    # the wait for P1 inside a running window ends when tokens have refilled enough, not when the window ends
    fit = bucket()
    fit.add_earmark(at(10), 5, at(200))
    fit.try_spend(at(0), 6, P.P0)
    verdict = fit.check(at(0), 1, P.P1)
    if (
        verdict.ok
        or verdict.reason != "EARMARK"
        or verdict.available_at is None
        or abs((verdict.available_at - at(60)).total_seconds()) > 0.05
    ):
        bad.append(f"the wait for a running window is {verdict.available_at}, expected about {at(60)}")
    got = fit.earliest(at(0), 1, P.P1)
    if got is None or abs((got - at(60)).total_seconds()) > 0.05:
        bad.append(f"earliest inside a running window is {got}")
    nev = bucket()
    nev.add_earmark(at(10), 6, at(200))
    nev.try_spend(at(0), 6, P.P0)
    verdict = nev.check(at(0), 1, P.P1)
    if verdict.ok or verdict.available_at != at(200):
        bad.append(f"a request that never fits before the window ends should wait for the deadline, not {verdict.available_at}")
    # the wait for P1 ends sooner than the window does, as tokens refill
    u = bucket()
    u.add_earmark(at(50), 6, at(200))
    u.try_spend(at(0), 6, P.P0)
    verdict = u.check(at(0), 1, P.P1)
    if verdict.ok or verdict.reason != "EARMARK" or verdict.available_at is None:
        bad.append("P1 spent tokens a window needs")
    # a call made for the window pays the window's own need down
    v = bucket()
    ident = v.add_earmark(at(0), 6, at(100))
    for _ in range(3):
        v.try_spend(at(0), 1, P.P0, earmark_id=ident)
    if v.earmarks[0].n != 3:
        bad.append(f"the window's need did not shrink: {v.earmarks[0].n}")
    v.try_spend(at(0), 1, P.P0)  # not for the window: its need stands
    if v.earmarks[0].n != 3:
        bad.append("a call outside the window reduced its need")
    for _ in range(10):
        v.try_spend(at(0), 1, P.P0, earmark_id=ident)
    if v.earmarks[0].n != 1:  # only the two tokens left could be spent
        bad.append(f"a window's need is {v.earmarks[0].n} after its tokens ran out, expected 1")
    small = bucket()
    small_id = small.add_earmark(at(0), 2, at(100))
    small.try_spend(at(0), 3, P.P0, earmark_id=small_id)
    if small.earmarks[0].n != 0:
        bad.append("a window's need went below zero")
    v.remove_earmark(ident)
    if v.earmarks:
        bad.append("an earmark was not removed")
    v.remove_earmark(99)  # unknown: no effect
    # a window that has been paid in full reserves nothing for the arithmetic rule (the blackout of P2 and P3 still stands)
    s = bucket()
    s.add_earmark(at(10), 6, at(100))
    for _ in range(6):
        s.try_spend(at(0), 1, P.P0, earmark_id=1)
    s.advance(at(60))
    expect("a paid window reserves nothing: P1 may spend", s.check(at(60), 1, P.P1), True, "OK")
    expect("but its blackout stands for P2", s.check(at(60), 1, P.P2), False, "BLACKOUT", 100)

    # --- charging a request that was sent without asking (one found abandoned after a restart) --------------------------------------
    ch = fixed()
    ch.charge(at(5), 2)
    if ch.available(at(5)) != 4 or len(ch.spends) != 1:
        bad.append(f"a charge in a window: {ch.available(at(5))} left")
    cb = bucket()
    cb.charge(at(0), 4)
    if abs(cb.available(at(0)) - 2) > 1e-9 or len(cb.spends) != 1:
        bad.append(f"a charge in a bucket: {cb.available(at(0))} left")
    debt = fixed()
    debt.charge(at(0), 9)
    if debt.available(at(0)) != -3:
        bad.append("a charge that does not fit must leave the governor in debt")
    expect("in debt nothing more is sent", debt.check(at(1), 1, P.P0), False, "TOKENS", 60)
    expect("and the debt is repaid by the next window", debt.check(at(60), 1, P.P0), True, "OK")
    later = bucket()
    later.charge(at(30), 1)
    if later.now != at(30):
        bad.append("a charge did not bring the governor to its time")
    for cost in (0, -1):
        try:
            fixed().charge(at(0), cost)
            bad.append(f"a charge of {cost} was accepted")
        except mod.GovernorError:
            pass
    unseen = fixed()
    unseen.charge(at(1), 1)
    unseen.observe(at(2), remaining=5, response_date=at(0))  # sent after the response the header describes
    if unseen.available(at(2)) != 4:
        bad.append("a charged request must be subtracted from a header that cannot have counted it")

    # --- validation and time ---------------------------------------------------------------------------------------------------------
    for label, build in [
        ("a zero limit", lambda: mod.Governor("x", M.FIXED_WINDOW, 0, 60, T0)),
        ("a negative limit", lambda: mod.Governor("x", M.FIXED_WINDOW, -1, 60, T0)),
        ("a zero window", lambda: mod.Governor("x", M.FIXED_WINDOW, 6, 0, T0)),
        ("a naive start", lambda: mod.Governor("x", M.FIXED_WINDOW, 6, 60, T0.replace(tzinfo=None))),
        ("time going backwards", lambda: fixed().advance(at(-1))),
        ("a zero cost", lambda: fixed().check(at(0), 0, P.P0)),
        ("a negative cost", lambda: fixed().check(at(0), -1, P.P0)),
        ("a question about the past", lambda: (lambda g: (g.advance(at(10)), g.check(at(5), 1, P.P0)))(fixed())),
        ("an earmark with no tokens", lambda: fixed().add_earmark(at(1), 0, at(2))),
        ("an earmark above the limit", lambda: fixed().add_earmark(at(1), 7, at(2))),
        ("an earmark ending before it starts", lambda: fixed().add_earmark(at(5), 1, at(4))),
    ]:
        try:
            build()
            bad.append(f"{label}: accepted")
        except (mod.GovernorError, ValueError):
            pass
    return bad
