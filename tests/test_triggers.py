"""The registry triggers: every check passes on the real module, every small change is noticed, and a model of lists gives the same triggers."""

from __future__ import annotations

from collections import Counter

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import triggers
from hoshitori.registry import Registry
from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339
from tests.mutation_sweep import survivors
from tests.registry_checks import DELISTED, HALTED, TRADING, inst, lst, t
from tests.triggers_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(triggers) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|Impact = Callable[[Change], Decimal]|0": "a type alias that only the type checker reads",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.triggers", "tests.triggers_checks")
    assert total > 90, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


POOL = [f"X{n:02d}" for n in range(8)]
STATUSES = [TRADING, HALTED, DELISTED]


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.lists(st.tuples(st.sampled_from(POOL), st.sampled_from(STATUSES)), max_size=8, unique_by=lambda p: p[0]),
        min_size=1,
        max_size=7,
    ),
    st.integers(min_value=0, max_value=6),
    st.integers(min_value=0, max_value=7),
)
def test_a_model_of_the_lists_gives_the_same_triggers(lists: list[list[tuple[str, object]]], first: int, last: int) -> None:
    reg = Registry()
    for step, entries in enumerate(lists):
        listing = lst("binance-spot", *[inst("binance-spot", f"{n}USDT", n, status=s) for n, s in entries])  # type: ignore[arg-type]
        reg.admit(listing, scope="spot", known_at=t(60 * (step + 1)), capture_id=f"cap-{step}")
    # the model works straight from the lists: the accepted ones in order, the first of them the baseline
    expected: Counter[tuple[str, str, int]] = Counter()
    last_accepted: dict[str, str] | None = None
    accepted_times = {
        to_ms(parse_rfc3339(r["known_at"])): next(e for e in r["events"] if e["t"] == "snapshot")["outcome"] == "accepted"
        for r in reg.records()
    }
    for step, entries in enumerate(lists):
        at = to_ms(t(60 * (step + 1)))
        if not accepted_times.get(at, False):
            continue
        now = {n: s.value for n, s in entries}  # type: ignore[attr-defined]
        if last_accepted is not None:
            for name in sorted(now.keys() - last_accepted.keys()):
                expected[(triggers.NEW, name, at)] += 1
            for name in sorted(last_accepted.keys() - now.keys()):
                expected[(triggers.REMOVED, name, at)] += 1
            for name in sorted(now.keys() & last_accepted.keys()):
                if now[name] != last_accepted[name]:
                    expected[(triggers.STATUS, name, at)] += 1
        last_accepted = now
    after, until = to_ms(t(60 * first)) if first else 0, to_ms(t(60 * last)) if last else 0
    if after > until:
        return
    want = Counter(k for k in expected.elements() if after < k[2] <= until)
    got = Counter(
        (x.rule, x.symbol, x.first_seen_at_ms)
        for x in triggers.registry_triggers(reg, after_ms=after, until_ms=until, impact=triggers.equal_impact)
    )
    assert got == want
