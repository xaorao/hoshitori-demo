"""The rate governor: scenarios, properties on simulated time, and mutants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import governor
from hoshitori.governor import Governor, Priority, RateModel
from tests.governor_checks import failures
from tests.mutation import mutant

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def test_real_module_passes_every_scenario() -> None:
    assert failures(governor) == []


operations = st.lists(
    st.tuples(
        st.sampled_from(["spend", "spend", "spend", "wait", "pause", "ban", "earmark"]),
        st.floats(0, 40),
        st.sampled_from(list(Priority)),
        st.integers(1, 3),
    ),
    min_size=1,
    max_size=60,
)


@settings(max_examples=300, deadline=None)
@given(st.sampled_from(list(RateModel)), st.integers(1, 12), st.sampled_from([10, 30, 60]), operations)
def test_the_governor_never_overspends_and_never_serves_a_blackout(model: RateModel, limit: int, window: int, steps: list) -> None:  # type: ignore[type-arg]
    g = Governor("p", model, limit, window, T0)
    now = T0
    spent: list[tuple[datetime, float]] = []
    for op, seconds, priority, cost in steps:
        now += timedelta(seconds=seconds)
        if op == "wait":
            g.advance(now)
        elif op == "pause":
            g.pause(now, seconds)
        elif op == "ban":
            g.ban(now, seconds * 2)
        elif op == "earmark":
            g.advance(now)
            if cost <= limit:
                g.add_earmark(now + timedelta(seconds=seconds), cost, now + timedelta(seconds=seconds + 30))
        else:
            before_marks = list(g.earmarks)
            verdict = g.try_spend(now, cost, priority)
            if verdict.ok:
                spent.append((now, cost))
                # invariants at the moment of a spend
                assert g.banned_until is None or now >= g.banned_until
                assert g.paused_until is None or now >= g.paused_until
                if priority in (Priority.P2, Priority.P3):
                    period = timedelta(seconds=g.refill_period_s())
                    assert not any(m.start - period <= now <= m.deadline for m in before_marks)
        assert g.available(now) >= -1e-9
        assert g.available(now) <= limit + 1e-9
    # conservation: in any interval, what was spent is at most the capacity plus what refilled (token bucket), or the limit per window
    for i, (start, _) in enumerate(spent):
        total = 0.0
        for when, cost in spent[i:]:
            total += cost
            interval = (when - start).total_seconds()
            if model is RateModel.TOKEN_BUCKET:
                assert total <= limit + limit / window * interval + 1e-6
            else:
                assert total <= limit * (interval // window + 2) + 1e-6  # a fixed window allows a full limit on each side of a roll


@settings(max_examples=200, deadline=None)
@given(st.sampled_from(list(RateModel)), st.lists(st.floats(0.01, 25), min_size=1, max_size=40))
def test_earliest_is_exact_when_nothing_else_happens(model: RateModel, gaps: list[float]) -> None:
    g = Governor("p", model, 6, 60, T0)
    now = T0
    for gap in gaps:
        now += timedelta(seconds=gap)
        g.try_spend(now, 1, Priority.P0)
    when = g.earliest(now, 1, Priority.P0)
    assert when is not None and when >= now
    probe = Governor("p", model, 6, 60, T0)
    # replaying the same spends and then waiting until `when` allows the request, and a moment earlier does not (unless it is allowed now)
    now2 = T0
    for gap in gaps:
        now2 += timedelta(seconds=gap)
        probe.try_spend(now2, 1, Priority.P0)
    assert probe.check(when, 1, Priority.P0).ok
    if when > now + timedelta(milliseconds=50):
        assert not probe.check(when - timedelta(milliseconds=50), 1, Priority.P0).ok


# Redundant guards without a mutant: the check that a question is not about the past (the projection refuses to move backwards with the
# same error), the guard that a projection moves only forward (nothing asks it to go back), the cap on a bucket after a smaller limit is observed (the next advance applies it), and the end of the first window set
# at construction (the first advance rolls it).
MUTATIONS: dict[str, tuple[str, str]] = {
    # time
    "the refill period of a bucket is the whole window": (
        "return 1.0 / self.refill_per_s if self.model is RateModel.TOKEN_BUCKET else self.window_s",
        "return self.window_s",
    ),
    "the refill period of a window is one unit": (
        "return 1.0 / self.refill_per_s if self.model is RateModel.TOKEN_BUCKET else self.window_s",
        "return 1.0 / self.refill_per_s",
    ),
    "time may go backwards": ('        if now < self.now:\n            raise GovernorError("time went backwards")\n', ""),
    "a window rolls one period late": (
        "            if now >= self.window_end:\n                periods",
        "            if now > self.window_end:\n                periods",
    ),
    "a long gap rolls only one window": (
        "                periods = int((now - self.window_end).total_seconds() // self.window_s) + 1",
        "                periods = 1",
    ),
    "a rolled window keeps what was spent": ("                self.used = 0.0\n", "                pass\n"),
    "a bucket refills beyond its capacity": (
        "            self.tokens = min(self.capacity, self.tokens + self.refill_per_s * (now - self.last_refill).total_seconds())",
        "            self.tokens = self.tokens + self.refill_per_s * (now - self.last_refill).total_seconds()",
    ),
    "a bucket does not refill": (
        "            self.tokens = min(self.capacity, self.tokens + self.refill_per_s * (now - self.last_refill).total_seconds())",
        "            self.tokens = self.tokens",
    ),
    "a bucket refills twice as fast": (
        "self.tokens + self.refill_per_s * (now - self.last_refill).total_seconds())",
        "self.tokens + 2 * self.refill_per_s * (now - self.last_refill).total_seconds())",
    ),
    "the refill clock is not advanced": ("            self.last_refill = now\n", ""),
    "old spends are never forgotten": (
        "        self.spends = [s for s in self.spends if (now - s).total_seconds() <= self.window_s]\n",
        "",
    ),
    "the governor's clock is not advanced": ("        self.now = now\n\n    def available", "        pass\n\n    def available"),
    "available does not advance time": (
        "    def available(self, now: datetime) -> float:\n        self.advance(now)\n",
        "    def available(self, now: datetime) -> float:\n",
    ),
    # pauses and bans
    "a ban does not block": (
        '        if self.banned_until is not None and when < self.banned_until:\n            return "BANNED", self.banned_until\n',
        "",
    ),
    "a ban ends one tick late": (
        "if self.banned_until is not None and when < self.banned_until:",
        "if self.banned_until is not None and when <= self.banned_until:",
    ),
    "a pause does not block": (
        '        if self.paused_until is not None and when < self.paused_until:\n            return "PAUSED", self.paused_until\n',
        "",
    ),
    "a pause ends one tick late": (
        "if self.paused_until is not None and when < self.paused_until:",
        "if self.paused_until is not None and when <= self.paused_until:",
    ),
    "a pause may be shortened": (
        "        if self.paused_until is None or until > self.paused_until:\n            self.paused_until = until",
        "        self.paused_until = until",
    ),
    "a ban may be shortened": (
        "        if self.banned_until is None or until > self.banned_until:\n            self.banned_until = until",
        "        self.banned_until = until",
    ),
    "a negative pause reaches into the past": (
        "        until = self.now + timedelta(seconds=max(0.0, seconds))\n        if self.paused_until",
        "        until = self.now + timedelta(seconds=seconds)\n        if self.paused_until",
    ),
    "a negative ban reaches into the past": (
        "        until = self.now + timedelta(seconds=max(0.0, seconds))\n        if self.banned_until",
        "        until = self.now + timedelta(seconds=seconds)\n        if self.banned_until",
    ),
    "a pause is counted from the wrong instant": (
        "        until = self.now + timedelta(seconds=max(0.0, seconds))\n        if self.paused_until",
        "        until = datetime(2020, 1, 1, tzinfo=UTC) + timedelta(seconds=max(0.0, seconds))\n        if self.paused_until",
    ),
    # costs and tokens
    "a free request is accepted": (
        '        if cost <= 0:\n            raise GovernorError("a request costs something")\n        why = self._why_not(when, cost, priority)',
        "        why = self._why_not(when, cost, priority)",
    ),
    "a request above the limit is possible": (
        '        if cost > (self.capacity if self.model is RateModel.TOKEN_BUCKET else self.limit):\n            return "TOO_EXPENSIVE", None\n',
        "",
    ),
    "a request of exactly the limit is refused": (
        '        if cost > (self.capacity if self.model is RateModel.TOKEN_BUCKET else self.limit):\n            return "TOO_EXPENSIVE"',
        '        if cost >= (self.capacity if self.model is RateModel.TOKEN_BUCKET else self.limit):\n            return "TOO_EXPENSIVE"',
    ),
    "an exact balance is refused": (
        "        if self._available_at(when) >= cost - 1e-9:\n            return None",
        "        if self._available_at(when) > cost:\n            return None",
    ),
    "a request needs less than it costs": (
        "        if self._available_at(when) >= cost - 1e-9:\n            return None",
        "        if self._available_at(when) >= cost - 1.5:\n            return None",
    ),
    "a fixed window is next available at the wrong time": (
        "        if self.model is RateModel.FIXED_WINDOW:\n            return self.window_end\n        return self.now + timedelta",
        "        if self.model is RateModel.FIXED_WINDOW:\n            return self.now\n        return self.now + timedelta",
    ),
    "a bucket's wait ignores the cost": ("(cost - self.tokens) / self.refill_per_s", "(1 - self.tokens) / self.refill_per_s"),
    "a bucket's wait ignores the balance": ("(cost - self.tokens) / self.refill_per_s", "cost / self.refill_per_s"),
    "spending does not use up a window": (
        "            if self.model is RateModel.FIXED_WINDOW:\n                self.used += cost",
        "            if self.model is RateModel.FIXED_WINDOW:\n                self.used += 0",
    ),
    "spending does not use up a bucket": (
        "            else:\n                self.tokens -= cost",
        "            else:\n                self.tokens -= 0",
    ),
    "a spend is not remembered": ("            self.spends.append(self.now)\n", ""),
    "a refused request is paid for": (
        "        verdict = self.check(self.now, cost, priority)\n        if verdict.ok:\n            if earmark_id",
        "        verdict = self.check(self.now, cost, priority)\n        if True:\n            if earmark_id",
    ),
    # earliest
    "earliest gives up too early": ("        for _ in range(64):  # every step", "        for _ in range(1):  # every step"),
    "earliest answers for an impossible request": (
        "            if verdict.available_at is None:\n                return None\n",
        "            if verdict.available_at is None:\n                return self.now\n",
    ),
    "earliest does not advance": (
        "            when = max(verdict.available_at, when + timedelta(milliseconds=1))",
        "            when = when",
    ),
    # headers
    "stale headers are believed": (
        "        if reset_at is not None and response_date is not None and ensure_utc(reset_at) <= ensure_utc(response_date):\n            return False\n",
        "",
    ),
    "a reset equal to the date is believed": (
        "ensure_utc(reset_at) <= ensure_utc(response_date):",
        "ensure_utc(reset_at) < ensure_utc(response_date):",
    ),
    "a reset after the date is stale": (
        "ensure_utc(reset_at) <= ensure_utc(response_date):",
        "ensure_utc(reset_at) >= ensure_utc(response_date):",
    ),
    "unseen spends are not subtracted": (
        "            unseen = sum(1 for s in self.spends if s >= ensure_utc(response_date))",
        "            unseen = 0",
    ),
    "spends at the response date are not subtracted": ("if s >= ensure_utc(response_date))", "if s > ensure_utc(response_date))"),
    "all spends are subtracted": (
        "            unseen = sum(1 for s in self.spends if s >= ensure_utc(response_date))",
        "            unseen = len(self.spends)",
    ),
    "a remaining count above the limit is believed": ("min(float(self.limit), float(remaining) - unseen)", "float(remaining) - unseen"),
    "a negative remaining count is believed": ("believed = max(0.0, min(", "believed = (min("),
    "headers do not update a window": ("            self.used = self.limit - believed\n", "            pass\n"),
    "headers do not update a bucket": ("        else:\n            self.tokens = believed\n", "        else:\n            pass\n"),
    "the header reset does not set the window end": (
        "            if reset_at is not None and ensure_utc(reset_at) > self.now:\n                self.window_end = ensure_utc(reset_at)\n",
        "",
    ),
    "a header reset in the past sets the window end": (
        "            if reset_at is not None and ensure_utc(reset_at) > self.now:",
        "            if reset_at is not None:",
    ),
    "a new limit is not taken": (
        "        if limit is not None and limit > 0 and limit != self.limit:\n            self.limit = float(limit)\n",
        "",
    ),
    "a zero limit is taken": (
        "        if limit is not None and limit > 0 and limit != self.limit:",
        "        if limit is not None and limit != self.limit:",
    ),
    "observation without a count claims success": (
        "        if remaining is None:\n            return False\n",
        "        if remaining is None:\n            return True\n",
    ),
    "used headers are reported unused": (
        "            self.tokens = believed\n        return True",
        "            self.tokens = believed\n        return False",
    ),
    # classes and windows
    "a window is ignored by nobody": (
        "IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.P0, Priority.PJ})",
        "IGNORES_EARMARKS: Final = frozenset()",
    ),
    "league cycles respect windows": (
        "IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.P0, Priority.PJ})",
        "IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.PJ})",
    ),
    "judges respect windows": (
        "IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.P0, Priority.PJ})",
        "IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.P0})",
    ),
    "attestation respects windows": (
        "IGNORES_EARMARKS: Final = frozenset({Priority.PA, Priority.P0, Priority.PJ})",
        "IGNORES_EARMARKS: Final = frozenset({Priority.P0, Priority.PJ})",
    ),
    "follow refresh is blacked out": (
        "BLACKED_OUT: Final = frozenset({Priority.P2, Priority.P3})",
        "BLACKED_OUT: Final = frozenset({Priority.P1, Priority.P2, Priority.P3})",
    ),
    "investigations are not blacked out": (
        "BLACKED_OUT: Final = frozenset({Priority.P2, Priority.P3})",
        "BLACKED_OUT: Final = frozenset({Priority.P3})",
    ),
    "background work is not blacked out": (
        "BLACKED_OUT: Final = frozenset({Priority.P2, Priority.P3})",
        "BLACKED_OUT: Final = frozenset({Priority.P2})",
    ),
    "the blackout starts when the window does": (
        "                if mark.start - period <= when <= mark.deadline:",
        "                if mark.start <= when <= mark.deadline:",
    ),
    "the blackout starts too early": (
        "                if mark.start - period <= when <= mark.deadline:",
        "                if mark.start - 2 * period <= when <= mark.deadline:",
    ),
    "the blackout ends at the window start": (
        "                if mark.start - period <= when <= mark.deadline:",
        "                if mark.start - period <= when <= mark.start:",
    ),
    "the blackout ends one tick early": (
        "                if mark.start - period <= when <= mark.deadline:",
        "                if mark.start - period <= when < mark.deadline:",
    ),
    "the blackout uses the wrong refill period": (
        "        period = timedelta(seconds=self.refill_period_s())\n        if priority in BLACKED_OUT",
        "        period = timedelta(seconds=0)\n        if priority in BLACKED_OUT",
    ),
    "the blackout reports the wrong end": (
        '                    return "BLACKOUT", mark.deadline',
        '                    return "BLACKOUT", mark.start',
    ),
    "a window's calls do not pay its need down": (
        "                        mark.n = max(0.0, mark.n - cost)",
        "                        mark.n = mark.n",
    ),
    "a window's need goes negative": (
        "                        mark.n = max(0.0, mark.n - cost)",
        "                        mark.n = mark.n - cost",
    ),
    "any earmark is paid down": ("                    if mark.earmark_id == earmark_id:", "                    if True:"),
    "first-fit never finds an earlier time": (
        "        if self._why_not(high, cost, priority) is not None:\n            return high\n",
        "        return high\n",
    ),
    "first-fit stops at once": ("        for _ in range(24):", "        for _ in range(0):"),
    "first-fit is coarse": ("            if (high - low).total_seconds() < 0.01:", "            if (high - low).total_seconds() < 100:"),
    "first-fit moves the wrong end": (
        "            if self._why_not(middle, cost, priority) is None:\n                high = middle\n            else:\n                low = middle",
        "            if self._why_not(middle, cost, priority) is None:\n                low = middle\n            else:\n                high = middle",
    ),
    # the projection
    "a window's projection ignores the used count": (
        "return self.tokens if self.model is RateModel.TOKEN_BUCKET else self.limit - self.used",
        "return self.tokens if self.model is RateModel.TOKEN_BUCKET else self.limit",
    ),
    "a rolled window keeps its used count in a projection": (
        "            self.window_end += timedelta(seconds=self.window_s * periods)\n            self.used = 0.0",
        "            self.window_end += timedelta(seconds=self.window_s * periods)",
    ),
    "a projection past a window's end keeps the window": (
        "        elif t >= self.window_end:\n            periods",
        "        elif t > self.window_end:\n            periods",
    ),
    "a projection rolls only one window": (
        "            periods = int((t - self.window_end).total_seconds() // self.window_s) + 1\n            self.window_end +=",
        "            periods = 1\n            self.window_end +=",
    ),
    "a projected spend uses up no tokens": (
        "        if self.model is RateModel.TOKEN_BUCKET:\n            self.tokens -= amount\n        else:\n            self.used += amount",
        "        if self.model is RateModel.TOKEN_BUCKET:\n            pass\n        else:\n            self.used += amount",
    ),
    "a projected spend uses up no window": (
        "        if self.model is RateModel.TOKEN_BUCKET:\n            self.tokens -= amount\n        else:\n            self.used += amount",
        "        if self.model is RateModel.TOKEN_BUCKET:\n            self.tokens -= amount\n        else:\n            pass",
    ),
    "a bucket projection ignores refill": (
        "            self.tokens = min(self.capacity, self.tokens + self.limit / self.window_s * (t - self.cursor).total_seconds())",
        "            self.tokens = self.tokens",
    ),
    "a bucket projection exceeds the capacity": (
        "            self.tokens = min(self.capacity, self.tokens + self.limit / self.window_s * (t - self.cursor).total_seconds())",
        "            self.tokens = self.tokens + self.limit / self.window_s * (t - self.cursor).total_seconds()",
    ),
    "the burst is ignored": ("        return float(self.limit if self.burst is None else self.burst)", "        return float(self.limit)"),
    "a burst larger than the limit is accepted": ("if self.burst is not None and not 0 < self.burst <= self.limit:", "if False:"),
    # the reservation
    "windows are not reserved by arithmetic": (
        '            if projection.advance_to(max(mark.start, when)) < mark.n - 1e-9:\n                return "EARMARK", mark.deadline',
        '            if projection.advance_to(max(mark.start, when)) < -1:\n                return "EARMARK", mark.deadline',
    ),
    "the reservation ignores the spend": ("        projection.take(cost)\n", ""),
    "the reservation ignores what earlier windows take": ("            projection.take(mark.n)\n", ""),
    "the reservation looks at the wrong time": (
        "projection.advance_to(max(mark.start, when)) < mark.n",
        "projection.advance_to(when) < mark.n",
    ),
    "the reservation counts finished windows": (
        "            if mark.deadline < when:\n                continue\n            if projection",
        "            if projection",
    ),
    "the reservation forgets a window one tick early": (
        "            if mark.deadline < when:\n                continue\n            if projection",
        "            if mark.deadline <= when:\n                continue\n            if projection",
    ),
    "the reservation replays windows out of order": (
        "for mark in sorted(self.earmarks, key=lambda m: m.start):",
        "for mark in sorted(self.earmarks, key=lambda m: -m.start.timestamp()):",
    ),
    "the reservation replays windows as declared": (
        "for mark in sorted(self.earmarks, key=lambda m: m.start):",
        "for mark in self.earmarks:",
    ),
    "the reservation is off by one token": (
        "projection.advance_to(max(mark.start, when)) < mark.n - 1e-9",
        "projection.advance_to(max(mark.start, when)) < mark.n - 1",
    ),
    "the wait for a window is its whole length": (
        "            look_again = self._first_fit(when, look_again, cost, priority)",
        "            pass",
    ),
    # earmark management
    "an earmark needs no tokens": (
        "        if n <= 0 or n > self.limit or deadline < start:",
        "        if n > self.limit or deadline < start:",
    ),
    "an earmark may exceed the limit": (
        "        if n <= 0 or n > self.limit or deadline < start:",
        "        if n <= 0 or deadline < start:",
    ),
    "an earmark may end before it starts": (
        "        if n <= 0 or n > self.limit or deadline < start:",
        "        if n <= 0 or n > self.limit:",
    ),
    "an earmark may end the moment it starts": (
        "        if n <= 0 or n > self.limit or deadline < start:",
        "        if n <= 0 or n > self.limit or deadline <= start:",
    ),
    "earmark ids repeat": ("        self._next_earmark += 1\n", ""),
    "removing an earmark removes all": (
        "        self.earmarks = [m for m in self.earmarks if m.earmark_id != earmark_id]",
        "        self.earmarks = []",
    ),
    "removing an earmark removes none": (
        "        self.earmarks = [m for m in self.earmarks if m.earmark_id != earmark_id]",
        "        self.earmarks = list(self.earmarks)",
    ),
    # charging a request that was sent without asking
    "a charge is not counted in a window": (
        "        if self.model is RateModel.FIXED_WINDOW:\n            self.used += cost\n        else:\n            self.tokens -= cost\n        self.spends.append(self.now)\n\n    def pause",
        "        if self.model is RateModel.FIXED_WINDOW:\n            pass\n        else:\n            self.tokens -= cost\n        self.spends.append(self.now)\n\n    def pause",
    ),
    "a charge is not counted in a bucket": (
        "        if self.model is RateModel.FIXED_WINDOW:\n            self.used += cost\n        else:\n            self.tokens -= cost\n        self.spends.append(self.now)\n\n    def pause",
        "        if self.model is RateModel.FIXED_WINDOW:\n            self.used += cost\n        else:\n            pass\n        self.spends.append(self.now)\n\n    def pause",
    ),
    "a charge is not remembered": (
        "            self.tokens -= cost\n        self.spends.append(self.now)\n\n    def pause",
        "            self.tokens -= cost\n\n    def pause",
    ),
    "a charge is refused when it does not fit": (
        '        self.advance(now)\n        if cost <= 0:\n            raise GovernorError("a request costs something")\n        if self.model',
        '        self.advance(now)\n        if cost <= 0 or self.available(now) < cost:\n            raise GovernorError("a request costs something")\n        if self.model',
    ),
    "a free charge is accepted": (
        '        self.advance(now)\n        if cost <= 0:\n            raise GovernorError("a request costs something")\n        if self.model',
        "        self.advance(now)\n        if self.model",
    ),
    "a charge does not advance time": (
        '        self.advance(now)\n        if cost <= 0:\n            raise GovernorError("a request costs something")\n        if self.model',
        '        if cost <= 0:\n            raise GovernorError("a request costs something")\n        if self.model',
    ),
    # construction
    "a zero limit is accepted": ("        if self.limit <= 0 or self.window_s <= 0:", "        if self.window_s <= 0:"),
    "a zero window is accepted": ("        if self.limit <= 0 or self.window_s <= 0:", "        if self.limit <= 0:"),
    "a naive start is accepted": ("        self.now = ensure_utc(self.now)\n        self.window_end", "        self.window_end"),
    "a bucket starts empty": (
        "        self.tokens = float(self.capacity)\n        self.last_refill",
        "        self.tokens = 0.0\n        self.last_refill",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.governor", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
