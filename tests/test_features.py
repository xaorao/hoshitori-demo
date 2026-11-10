"""The features: every check passes on the real module, every small change is noticed, and random histories keep the laws."""

from __future__ import annotations

from fractions import Fraction

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from hoshitori import features
from tests.features_checks import H, M, failures, series, text
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(features) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_newest|Gt->GtE|slot > last.open_time|0": "a slot that has a bar is never listed as missing, so the slot of the last bar is never in the list",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.features", "tests.features_checks")
    assert total > 90, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


@settings(max_examples=250, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(st.integers(min_value=50, max_value=100_000), min_size=H, max_size=H),
    st.lists(st.integers(min_value=50, max_value=100_000), min_size=M, max_size=M),
    st.sets(st.integers(min_value=0, max_value=H - 1), max_size=6),
    st.sets(st.integers(min_value=0, max_value=M - 1), max_size=6),
)
def test_random_histories_give_the_fraction_path_values_and_the_predicted_unknowns(
    hours: list[int], minutes: list[int], hour_holes: set[int], minute_holes: set[int]
) -> None:
    h, m = [Fraction(x) for x in hours], [Fraction(x) for x in minutes]
    got = features.compute(series(60, m, Fraction(1, 4), hole=minute_holes), series(3600, h, Fraction(5), hole=hour_holes))
    shown = {name: format(value, "f") for name, value in got.known().items()}
    want: dict[str, str] = {}
    last_m, last_h = M - 1, H - 1
    if last_m not in minute_holes:
        want["close"] = text(m[last_m])
        if last_m - 60 not in minute_holes:
            want["ret_1h"] = text(m[last_m] / m[last_m - 60] - 1)
    if last_h not in hour_holes:
        for name, back in (("ret_4h", 4), ("ret_24h", 24)):
            if last_h - back not in hour_holes:
                want[name] = text(h[last_h] / h[last_h - back] - 1)
        if not any(i in hour_holes for i in range(last_h - 14, last_h + 1)):
            trs = []
            for i in range(last_h - 13, last_h + 1):
                high, low = max(h[i - 1], h[i]) + 5, min(h[i - 1], h[i]) - 5
                trs.append(max(high - low, abs(high - h[i - 1]), abs(low - h[i - 1])))
            want["atr14_share"] = text(sum(trs) / 14 / h[last_h])
            want["atr14"] = text(sum(trs) / 14)
    assert shown == want
