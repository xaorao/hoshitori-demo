"""The risk kernel: the worked checks pass on the real module, every small change of its code is noticed or listed as harmless, and the
properties of the spec hold for any targets: nothing is raised or turned round, every limit holds after, a second pass changes nothing, and
the order the caller listed the targets in does not matter."""

from __future__ import annotations

from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import risk_kernel
from tests.mutation_sweep import survivors
from tests.risk_kernel_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(risk_kernel) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "shrink|int+1|1|0": "a scale of two instead of one gives the ladder twice the request in room, so it never binds: the same result",
    "shrink|int+1|1|1": "a scale of two instead of one gives the ladder twice the request in room, so it never binds: the same result",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.risk_kernel", "tests.risk_kernel_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


SYMBOLS = [chr(ord("A") + n) for n in range(12)]
weight = st.integers(min_value=-300_000, max_value=300_000).map(lambda n: Decimal(n).scaleb(-6))
targets = st.dictionaries(st.sampled_from(SYMBOLS), weight, max_size=12)
groups = st.lists(st.sets(st.sampled_from(SYMBOLS), min_size=1, max_size=5), max_size=4).map(
    lambda sets: [s - set().union(*sets[:i]) for i, s in enumerate(sets)]
)
limit = st.integers(min_value=1, max_value=100).map(lambda n: Decimal(n).scaleb(-2))


@st.composite
def situations(draw: st.DrawFn) -> tuple[dict[str, Decimal], risk_kernel.Limits, list[set[str]]]:
    caps = [draw(limit) for _ in range(4)]
    rungs = sorted(draw(st.sets(st.integers(min_value=1, max_value=99), min_size=3, max_size=3)))
    ladder = [Decimal(n).scaleb(-2) for n in rungs]
    lim = risk_kernel.Limits(caps[0], caps[1], caps[2], caps[3], ladder[0], ladder[1], ladder[2])
    return draw(targets), lim, draw(groups)


@settings(max_examples=300, deadline=None)
@given(situations())
def test_nothing_is_raised_every_limit_holds_and_a_second_pass_changes_nothing(
    case: tuple[dict[str, Decimal], risk_kernel.Limits, list[set[str]]],
) -> None:
    wanted, lim, clusters = case
    got = risk_kernel.shrink(wanted, lim, clusters=clusters)
    assert set(got.weights) == set(wanted)
    for symbol, w in got.weights.items():
        assert abs(w) <= abs(wanted[symbol]) and (w == 0 or (w > 0) == (wanted[symbol] > 0))
        assert abs(w) <= lim.per_token
    assert sum(abs(w) for w in got.weights.values()) <= lim.gross
    assert abs(sum(got.weights.values())) <= lim.net
    for group in clusters:
        assert sum(abs(got.weights[s]) for s in group if s in got.weights) <= lim.cluster
    assert risk_kernel.shrink(got.weights, lim, clusters=clusters).weights == got.weights
    for shrink in got.shrinks:  # whatever was cut by more than rounding was cut by a named cap
        if abs(shrink.requested - shrink.applied) >= Decimal("0.000001"):
            assert shrink.bound


@settings(max_examples=200, deadline=None)
@given(situations(), st.randoms(use_true_random=False))
def test_the_order_the_targets_were_listed_in_does_not_matter(
    case: tuple[dict[str, Decimal], risk_kernel.Limits, list[set[str]]], rnd: object
) -> None:
    wanted, lim, clusters = case
    items = list(wanted.items())
    rnd.shuffle(items)  # type: ignore[attr-defined]
    assert risk_kernel.shrink(dict(items), lim, clusters=clusters) == risk_kernel.shrink(wanted, lim, clusters=clusters)


@settings(max_examples=200, deadline=None)
@given(situations(), st.integers(min_value=0, max_value=200).map(lambda n: Decimal(n).scaleb(-3)))
def test_the_ladder_only_ever_lowers_an_exposure(
    case: tuple[dict[str, Decimal], risk_kernel.Limits, list[set[str]]], drawdown: Decimal
) -> None:
    wanted, lim, clusters = case
    plain = risk_kernel.shrink(wanted, lim, clusters=clusters)
    got = risk_kernel.shrink(wanted, lim, drawdown=drawdown, clusters=clusters)
    assert got.mode is risk_kernel.mode_of(drawdown, lim)
    if got.mode in (risk_kernel.Mode.FLAT, risk_kernel.Mode.HALTED):
        assert all(w == 0 for w in got.weights.values())
    elif got.mode is risk_kernel.Mode.ACTIVE:
        assert got.weights == plain.weights
    else:  # halved: no token is above half its request, and every limit still holds
        assert all(abs(w) <= abs(wanted[symbol]) / 2 for symbol, w in got.weights.items())
        assert sum(abs(w) for w in got.weights.values()) <= lim.gross and abs(sum(got.weights.values())) <= lim.net
