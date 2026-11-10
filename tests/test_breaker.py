"""Circuit breakers: scenarios, a property test of the state machine, and mutants."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import breaker
from hoshitori.breaker import Breaker, BreakerConfig, State
from hoshitori.connector import ErrorClass
from tests.breaker_checks import failures
from tests.mutation import mutant

T0 = datetime(2026, 10, 29, 12, 0, 0, tzinfo=UTC)


def test_real_module_passes_every_scenario() -> None:
    assert failures(breaker) == []


steps = st.lists(
    st.tuples(st.sampled_from(["allow", "ok", "fail", "ban", "limit", "probe"]), st.floats(0, 30), st.sampled_from(list(ErrorClass))),
    min_size=1,
    max_size=80,
)


@settings(max_examples=300, deadline=None)
@given(st.integers(1, 5), steps)
def test_the_state_machine_never_lets_a_call_through_while_open_or_busy(consecutive: int, ops: list) -> None:  # type: ignore[type-arg]
    b = Breaker("p", BreakerConfig(consecutive_failures=consecutive, cooldown_s=10, cooldown_max_s=60), T0)
    now = T0
    trials_out = 0
    for op, gap, cls in ops:
        now += timedelta(seconds=gap)
        if op == "allow":
            before = b.state(now)
            decision = b.allow(now)
            if before is State.OPEN:
                assert not decision.allowed and decision.retry_at is not None and decision.retry_at > now
            if decision.allowed and decision.state is State.HALF_OPEN:
                trials_out += 1
                assert trials_out == 1, "two trials at once"
        elif op == "ok":
            b.record_success(now)
            trials_out = 0
        elif op == "fail":
            b.record_failure(now, cls)
            trials_out = 0
        elif op == "ban":
            b.record_failure(now, ErrorClass.BANNED, ban_s=gap)
            trials_out = 0
        elif op == "limit":
            b.record_failure(now, ErrorClass.RATE_LIMIT)
            trials_out = 0
        else:
            b.probe_allowed(now)
        if b.state(now) is not State.HALF_OPEN:
            trials_out = 0
    # every transition is a legal one
    legal = {
        (State.CLOSED, State.OPEN),
        (State.OPEN, State.HALF_OPEN),
        (State.HALF_OPEN, State.CLOSED),
        (State.HALF_OPEN, State.OPEN),
        (State.OPEN, State.OPEN),
    }
    assert all((t.before, t.after) in legal for t in b.transitions)


# Redundant guards without a mutant: the streak reset when the breaker opens (the only way back to CLOSED is a success, which resets it), a
# negative ban length clamped to zero (a ban in the past ends at once either way), and the clearing of the trial flag when the cool-down ends
# (opening already clears it).
MUTATIONS: dict[str, tuple[str, str]] = {
    # configuration
    "zero consecutive failures are allowed": (
        "if self.consecutive_failures < 1 or self.window_calls < 1 or not 0 < self.error_rate < 1:",
        "if self.window_calls < 1 or not 0 < self.error_rate < 1:",
    ),
    "a zero window is allowed": (
        "if self.consecutive_failures < 1 or self.window_calls < 1 or not 0 < self.error_rate < 1:",
        "if self.consecutive_failures < 1 or not 0 < self.error_rate < 1:",
    ),
    "an error rate of zero is allowed": ("not 0 < self.error_rate < 1:", "not 0 <= self.error_rate < 1:"),
    "an error rate of one is allowed": ("not 0 < self.error_rate < 1:", "not 0 < self.error_rate <= 1:"),
    "a zero cool-down is allowed": (
        "if self.cooldown_s <= 0 or self.cooldown_max_s < self.cooldown_s",
        "if self.cooldown_max_s < self.cooldown_s",
    ),
    "a maximum below the start is allowed": ("or self.cooldown_max_s < self.cooldown_s or", "or"),
    "a shrinking cool-down is allowed": ("or self.cooldown_factor < 1 or", "or"),
    "a zero probe interval is allowed": ("or self.probe_interval_s <= 0:", ":"),
    "a zero trial lifetime is allowed": (
        '        if self.trial_ttl_s <= 0:\n            raise BreakerError("the trial time-to-live must be positive")\n',
        "",
    ),
    "a naive start is accepted": (
        "    def __post_init__(self) -> None:\n        self.now = ensure_utc(self.now)",
        "    def __post_init__(self) -> None:\n        pass",
    ),
    "time may go backwards": ('        if now < self.now:\n            raise BreakerError("time went backwards")\n', ""),
    # opening
    "failures in a row do not open it": ("            if self._streak >= self.config.consecutive_failures:", "            if False:"),
    "it opens one failure late": (
        "            if self._streak >= self.config.consecutive_failures:",
        "            if self._streak > self.config.consecutive_failures:",
    ),
    "it opens one failure early": (
        "            if self._streak >= self.config.consecutive_failures:",
        "            if self._streak >= self.config.consecutive_failures - 1:",
    ),
    "a success does not reset the streak": (
        "        self._push(False)\n        self._streak = 0\n        if self._state is State.HALF_OPEN:",
        "        self._push(False)\n        if self._state is State.HALF_OPEN:",
    ),
    "the error rate never opens it": (
        "            elif len(self._recent) >= self.config.window_calls and sum(self._recent) > self.config.error_rate * len(self._recent):",
        "            elif False:",
    ),
    "exactly half opens it": (
        "sum(self._recent) > self.config.error_rate * len(self._recent):",
        "sum(self._recent) >= self.config.error_rate * len(self._recent):",
    ),
    "a partial window can open it by rate": ("len(self._recent) >= self.config.window_calls and sum", "len(self._recent) >= 1 and sum"),
    "the window is not bounded": ("        while len(self._recent) > self.config.window_calls:\n            self._recent.popleft()\n", ""),
    "successes are not remembered": (
        "    def record_success(self, now: datetime) -> None:\n        self._advance(now)\n        self._push(False)\n",
        "    def record_success(self, now: datetime) -> None:\n        self._advance(now)\n",
    ),
    "failures are not remembered": ("        self._push(True)\n        self._streak += 1", "        self._streak += 1"),
    "the streak is not counted": ("        self._push(True)\n        self._streak += 1", "        self._push(True)"),
    "classes that do not count are counted": ("        if error_class not in COUNTING:", "        if False:"),
    "classes that count are ignored": ("        if error_class not in COUNTING:", "        if error_class in COUNTING:"),
    "back-pressure does not free a trial": (
        "        if error_class not in COUNTING:\n            self._trial_in_flight = False\n            return",
        "        if error_class not in COUNTING:\n            return",
    ),
    "a close forgets nothing": ('            self.reason = ""\n            self._recent.clear()', '            self.reason = ""'),
    "a close keeps the reason": ('            self.reason = ""\n            self._recent.clear()', "            self._recent.clear()"),
    "a close keeps the escalation": (
        "            self._trips = 0\n            self._trial_in_flight = False\n            self._move(State.CLOSED",
        "            self._trial_in_flight = False\n            self._move(State.CLOSED",
    ),
    # open and half-open
    "an open breaker lets calls through": (
        '        if self._state is State.OPEN:\n            return Decision(False, State.OPEN, "OPEN", self._open_until)',
        '        if self._state is State.OPEN:\n            return Decision(True, State.OPEN, "OPEN", self._open_until)',
    ),
    "an open breaker does not say when to come back": (
        '            return Decision(False, State.OPEN, "OPEN", self._open_until)',
        '            return Decision(False, State.OPEN, "OPEN", None)',
    ),
    "the cool-down never ends": (
        "        if self._state is State.OPEN and self._open_until is not None and now >= self._open_until:",
        "        if False:",
    ),
    "the cool-down ends one tick late": ("and now >= self._open_until:", "and now > self._open_until:"),
    "several trials at once": (
        '        if self._trial_in_flight:\n            return Decision(False, State.HALF_OPEN, "TRIAL_IN_FLIGHT", None)\n',
        "",
    ),
    "a trial is never marked": (
        "        self._trial_in_flight = True\n        self._trial_started = self.now\n",
        "        self._trial_started = self.now\n",
    ),
    "a trial has no start time": (
        "        self._trial_in_flight = True\n        self._trial_started = self.now\n",
        "        self._trial_in_flight = True\n",
    ),
    "a lost trial blocks for ever": (
        "        if self._trial_in_flight and started is not None and (now - started).total_seconds() >= self.config.trial_ttl_s:\n            self._trial_in_flight = False  # nobody reported back: the slot is free again",
        "        if False:\n            pass",
    ),
    "a lost trial expires one tick late": (
        "(now - started).total_seconds() >= self.config.trial_ttl_s:",
        "(now - started).total_seconds() > self.config.trial_ttl_s:",
    ),
    "a lost trial expires at once": (
        "(now - started).total_seconds() >= self.config.trial_ttl_s:",
        "(now - started).total_seconds() >= 0:",
    ),
    "a trial cannot be released": (
        '        """Give back a trial slot taken by ``allow`` that was not used (another breaker refused the call)."""\n        self._trial_in_flight = False',
        '        """Give back a trial slot taken by ``allow`` that was not used (another breaker refused the call)."""\n        pass',
    ),
    "a successful trial does not close it": ('            self._move(State.CLOSED, "trial succeeded")', "            pass"),
    "a failed trial does not open it": (
        '        if self._state is State.HALF_OPEN:\n            self._trip(f"trial failed: {error_class.value}")',
        "        if self._state is State.HALF_OPEN:\n            pass",
    ),
    "a late success closes an open breaker": (
        "        if self._state is State.HALF_OPEN:\n            self._trips = 0",
        "        if self._state is not State.CLOSED:\n            self._trips = 0",
    ),
    # the cool-down
    "the cool-down does not grow": ("self.config.cooldown_s * self.config.cooldown_factor**self._trips)", "self.config.cooldown_s)"),
    "the cool-down grows without bound": (
        "            wait_s = min(self.config.cooldown_max_s, self.config.cooldown_s * self.config.cooldown_factor**self._trips)",
        "            wait_s = self.config.cooldown_s * self.config.cooldown_factor**self._trips",
    ),
    "the cool-down grows too fast": (
        "self.config.cooldown_s * self.config.cooldown_factor**self._trips)",
        "self.config.cooldown_s * self.config.cooldown_factor ** (self._trips + 1))",
    ),
    "the escalation is not counted": ("        if escalate:\n            self._trips += 1\n", ""),
    "a ban escalates": ("        if escalate:\n            self._trips += 1\n", "        self._trips += 1\n"),
    "a ban is an ordinary failure": ("        if error_class is ErrorClass.BANNED and ban_s is not None:", "        if False:"),
    "a ban keeps the ordinary cool-down": (
        '            self._trip(f"banned for {ban_s:g} s", wait_s=max(0.0, ban_s), escalate=False)',
        '            self._trip(f"banned for {ban_s:g} s", escalate=False)',
    ),
    "a shorter ban shortens": (
        "        if self._state is State.OPEN and self._open_until is not None and self._open_until > until:\n            until = self._open_until  # a later, shorter verdict never shortens what is already in force\n",
        "",
    ),
    "an opening keeps the trial": (
        "        self._open_until = until\n        self._trial_in_flight = False\n",
        "        self._open_until = until\n",
    ),
    "the reason is lost": (
        "        self.reason = why\n        if self._state is not State.OPEN:",
        "        if self._state is not State.OPEN:",
    ),
    "transitions are not recorded": (
        "        self.transitions.append(Transition(self.now, self._state, after, why))\n        self._state = after",
        "        self._state = after",
    ),
    "an extension of an open breaker is not recorded": (
        "        else:\n            self.transitions.append(Transition(self.now, State.OPEN, State.OPEN, why))",
        "        else:\n            pass",
    ),
    # probes
    "probes are never allowed while open": (
        "        if self._last_probe is not None and (self.now - self._last_probe).total_seconds() < self.config.probe_interval_s:\n            return False\n        self._last_probe = self.now\n        return True",
        "        return False",
    ),
    "probes are not limited": (
        "        if self._last_probe is not None and (self.now - self._last_probe).total_seconds() < self.config.probe_interval_s:\n            return False\n",
        "",
    ),
    "probes are limited one tick too long": (
        "(self.now - self._last_probe).total_seconds() < self.config.probe_interval_s:",
        "(self.now - self._last_probe).total_seconds() <= self.config.probe_interval_s:",
    ),
    "probes are not remembered": ("        self._last_probe = self.now\n        return True", "        return True"),
    "a closed breaker refuses probes": (
        "        if self._state is State.CLOSED:\n            return True\n        if self._last_probe",
        "        if self._state is State.CLOSED:\n            return False\n        if self._last_probe",
    ),
    # sets
    "the provider breaker is ignored": (
        "        decision = self.provider.allow(now)\n        if not decision.allowed:\n            return decision\n",
        '        decision = Decision(True, State.CLOSED, "OK", None)\n',
    ),
    "the tool breaker is ignored": (
        "        tool_decision = self._tool(tool, now).allow(now)\n",
        '        tool_decision = Decision(True, State.CLOSED, "OK", None)\n',
    ),
    "a refused tool call keeps the provider's trial": (
        "            if decision.state is State.HALF_OPEN:\n                self.provider.release_trial()\n",
        "",
    ),
    "a refusal hides the tool's reason": (
        "                self.provider.release_trial()\n            return tool_decision",
        "                self.provider.release_trial()\n            return decision",
    ),
    "the provider's trial is not reported": (
        "        return tool_decision if tool_decision.state is State.HALF_OPEN else decision",
        "        return tool_decision",
    ),
    "the tool's own trial is not reported": (
        "        return tool_decision if tool_decision.state is State.HALF_OPEN else decision",
        "        return decision",
    ),
    "a success closes only the provider": (
        "        self.provider.record_success(now)\n        self._tool(tool, now).record_success(now)",
        "        self.provider.record_success(now)",
    ),
    "a success closes only the tool": (
        "        self.provider.record_success(now)\n        self._tool(tool, now).record_success(now)",
        "        self._tool(tool, now).record_success(now)",
    ),
    "a changed schema opens the provider": (
        "        if error_class in TOOL_SCOPED:\n            self._tool(tool, now).record_failure(now, error_class)\n            self.provider.release_trial()",
        "        if error_class in TOOL_SCOPED:\n            self.provider.record_failure(now, error_class)\n            self._tool(tool, now).release_trial()",
    ),
    "a shared failure opens only the tool": (
        "        else:\n            self.provider.record_failure(now, error_class, ban_s=ban_s)\n            self._tool(tool, now).release_trial()",
        "        else:\n            self._tool(tool, now).record_failure(now, error_class)\n            self.provider.release_trial()",
    ),
    "a tool failure keeps the provider's trial": (
        "            self._tool(tool, now).record_failure(now, error_class)\n            self.provider.release_trial()",
        "            self._tool(tool, now).record_failure(now, error_class)",
    ),
    "a provider failure keeps the tool's trial": (
        "            self.provider.record_failure(now, error_class, ban_s=ban_s)\n            self._tool(tool, now).release_trial()",
        "            self.provider.record_failure(now, error_class, ban_s=ban_s)",
    ),
    "bans lose their length": (
        "            self.provider.record_failure(now, error_class, ban_s=ban_s)",
        "            self.provider.record_failure(now, error_class)",
    ),
    "malformed bodies are not tool-scoped": (
        "TOOL_SCOPED: Final = frozenset({ErrorClass.CONTRACT_DRIFT, ErrorClass.MALFORMED})",
        "TOOL_SCOPED: Final = frozenset({ErrorClass.CONTRACT_DRIFT})",
    ),
    "drift is not tool-scoped": (
        "TOOL_SCOPED: Final = frozenset({ErrorClass.CONTRACT_DRIFT, ErrorClass.MALFORMED})",
        "TOOL_SCOPED: Final = frozenset({ErrorClass.MALFORMED})",
    ),
    "everything is tool-scoped": (
        "TOOL_SCOPED: Final = frozenset({ErrorClass.CONTRACT_DRIFT, ErrorClass.MALFORMED})",
        "TOOL_SCOPED: Final = frozenset(ErrorClass)",
    ),
    "tools share one breaker": (
        '        if tool not in self.tools:\n            self.tools[tool] = Breaker(f"{self.provider.name}:{tool}", self.tool_config, now)\n        return self.tools[tool]',
        '        if "*" not in self.tools:\n            self.tools["*"] = Breaker(f"{self.provider.name}:*", self.tool_config, now)\n        return self.tools["*"]',
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.breaker", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
