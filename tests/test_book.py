"""Book: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import book
from tests.book_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(book) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "|statement dropped|Event = Open | Buy | Sell | Mark|0": "a type alias that only the type checker reads",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.book", "tests.book_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


from decimal import Decimal  # noqa: E402

from hypothesis import given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

_symbol = st.sampled_from(["AAA", "BBB", "CCC"])
_amount = st.integers(min_value=1, max_value=1_000_000).map(lambda n: Decimal(n).scaleb(-4))
_fee = st.integers(min_value=0, max_value=200_000_000).map(lambda n: Decimal(n).scaleb(-4))  # from nothing to twice the opening cash
_step = st.tuples(st.sampled_from(["buy", "sell", "mark"]), _symbol, _amount, _amount, _fee)


@settings(max_examples=200, deadline=None)
@given(st.lists(_step, max_size=40))
def test_any_run_of_events_keeps_cash_non_negative_and_the_fold_equals_the_steps(
    steps: list[tuple[str, str, Decimal, Decimal, Decimal]],
) -> None:
    taken: list[book.Event] = [book.Open("L0", "BASE", Decimal(10_000), 0)]
    current = book.replay(taken)
    for at, (kind, symbol, quantity, price, fee) in enumerate(steps, start=1):
        if kind == "buy":
            event: book.Event = book.Buy(symbol, quantity, price, fee, at)
        elif kind == "sell":
            event = book.Sell(symbol, quantity, price, fee, at)
        else:
            event = book.Mark(tuple((p.symbol, price) for p in current.positions), at)
        try:
            current = book.apply(current, event)
        except book.BookError:
            continue
        taken.append(event)
        assert current.cash >= 0 and all(p.quantity > 0 for p in current.positions)
        assert [p.symbol for p in current.positions] == sorted(p.symbol for p in current.positions)
        assert current.peak >= current.equity
    assert book.replay(taken) == current
    assert book.replay([book.from_body(book.to_body(e)) for e in taken]) == current
