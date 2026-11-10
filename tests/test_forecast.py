"""The forecast record: every check passes on the real module, every small change of its code is noticed, and no caller's decimal context
changes a record, what reading it gives or a refusal."""

from __future__ import annotations

import copy
from collections.abc import Callable
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Context,
    Decimal,
    Inexact,
    localcontext,
)

from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import canonical, forecast
from tests.forecast_checks import AS_OF, common, failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(forecast) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|Forecast = DirectionalForecast | BarrierForecast|0": "a type alias that only the type checker reads",
    "|int+1|86400000|0": "a horizon is whole minutes, so a cap 366 milliseconds longer admits the same horizons",
    "_text|str+~|'value'|0": "the name of the one member of the throw-away record that the canonical profile checks the text with",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.forecast", "tests.forecast_checks")
    assert total > 300, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


ROUNDINGS = (ROUND_05UP, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR, ROUND_HALF_DOWN, ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_UP)
NUMBERS = st.builds(lambda coefficient, scale: Decimal(f"{coefficient}E-{scale}"), st.integers(1, 10**30 - 1), st.integers(0, 40))


def _context(prec: int, rounding: str, trap_inexact: bool) -> Context:
    ctx = Context(prec=prec, rounding=rounding)
    ctx.traps[Inexact] = trap_inexact
    return ctx


CONTEXTS = st.builds(_context, st.integers(1, 70), st.sampled_from(ROUNDINGS), st.booleans())


@st.composite
def probabilities(draw: st.DrawFn) -> tuple[Decimal, Decimal, Decimal]:
    """Three probabilities that sum to exactly one, or that miss it by 1E-30, 1E-59, 1E-60 or 1E-61."""
    scale = draw(st.integers(1, 30))
    first = draw(st.integers(0, 10**scale))
    second = draw(st.integers(0, 10**scale - first))
    third = Decimal(f"{10**scale - first - second}E-{scale}")
    miss = draw(st.sampled_from((None, 30, 59, 60, 61)))
    if miss is not None:
        with localcontext(Context(prec=200)):
            third += Decimal(f"1E-{miss}")
    return Decimal(f"{first}E-{scale}"), Decimal(f"{second}E-{scale}"), third


def _outcome(call: Callable[[], object]) -> str:
    try:
        return f"gives {call()!r}"
    except Exception as exc:  # what happens, whatever it is, is what is compared
        return f"raises {type(exc).__name__}: {exc}"


@settings(max_examples=200, deadline=None)
@given(NUMBERS, NUMBERS, NUMBERS, probabilities(), CONTEXTS)
def test_no_callers_context_changes_a_record(
    atr: Decimal, k_target: Decimal, k_stop: Decimal, p: tuple[Decimal, Decimal, Decimal], ctx: Context
) -> None:
    def made() -> forecast.BarrierForecast:
        return forecast.BarrierForecast(common(forecast, contract="B"), forecast.Side.SHORT, atr, k_target, k_stop, *p)

    def written() -> bytes:
        return canonical.dumps(forecast.to_record(made(), "urn:x", AS_OF))

    with localcontext(Context()):
        base = _outcome(written)
        record = forecast.to_record(made(), "urn:x", AS_OF) if base.startswith("gives") else None
        read = None if record is None else _outcome(lambda: forecast.from_record(copy.deepcopy(record)))
    with localcontext(ctx):
        assert _outcome(written) == base
        if record is not None:
            assert _outcome(lambda: forecast.from_record(copy.deepcopy(record))) == read
