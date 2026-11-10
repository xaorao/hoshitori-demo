"""The rules arm: every check passes on the real module, every small change is noticed, and random histories count as a separate path does."""

from __future__ import annotations

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import rules_arm
from tests.mutation_sweep import survivors
from tests.rules_arm_checks import brute_counts, failures, history


def test_real_module_passes_every_check() -> None:
    assert failures(rules_arm) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.rules_arm", "tests.rules_arm_checks")
    assert total > 200, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


@settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.tuples(
            st.integers(min_value=1, max_value=10**6),
            st.integers(min_value=40, max_value=160),
            st.integers(min_value=0, max_value=200),
            st.integers(min_value=0, max_value=60),
            st.sets(st.integers(min_value=0, max_value=159), max_size=4),
        ),
        min_size=1,
        max_size=3,
    )
)
def test_random_histories_count_as_the_separate_path_does(specs: list[tuple[int, int, int, int, set[int]]]) -> None:
    data = {
        f"I{n}USDT": history(seed, length, flat=(start, start + width), holes=frozenset(holes))
        for n, (seed, length, start, width, holes) in enumerate(specs)
    }
    want, skipped = brute_counts(data)
    got = rules_arm.calibrate(data, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0)
    assert {key: [cell.n, cell.ups, cell.ties] for key, cell in got.cells} == want
    assert got.skipped == skipped
    for key, cell in got.cells:
        assert cell.ups <= cell.n
        p = got.p(*key)
        assert p is None or rules_arm.P_FLOOR <= p <= rules_arm.P_CEIL
