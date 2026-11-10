"""Checks of the book (``hoshitori.book``), runnable against a mutant: worked sequences, every refusal, the terminal states, the fold, the
cash that never falls below zero, the ladder read from the exact drawdown, and results that do not depend on the caller's decimal context."""

from __future__ import annotations

import types
from collections.abc import Callable
from decimal import (
    ROUND_CEILING,
    ROUND_FLOOR,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Any

from hoshitori import canonical
from hoshitori.risk_kernel import Limits, Mode

D = Decimal


def ratio(top: Decimal, bottom: Decimal) -> Decimal:
    """A quotient at the book's own precision of sixty digits, so that equality with the book's drawdown is meaningful."""
    with localcontext() as ctx:
        ctx.prec = 60
        return top / bottom


def contexts() -> dict[str, Context]:
    """Callers' decimal contexts other than the default: six, nine and 28 digits, each rounding half-even, toward minus infinity and toward
    plus infinity, with and without a trap on an inexact result; and one that traps nothing."""
    out: dict[str, Context] = {}
    for prec in (6, 9, 28):
        for name, rounding in (
            ("half-even", ROUND_HALF_EVEN),
            ("toward minus infinity", ROUND_FLOOR),
            ("toward plus infinity", ROUND_CEILING),
        ):
            for inexact in (False, True):
                if prec == 28 and rounding == ROUND_HALF_EVEN and not inexact:
                    continue  # the default context itself
                traps = [InvalidOperation, DivisionByZero, Overflow, *([Inexact] if inexact else [])]
                out[f"{prec} digits rounding {name}{' trapping Inexact' if inexact else ''}"] = Context(
                    prec=prec, rounding=rounding, traps=traps
                )
    out["28 digits trapping nothing"] = Context(traps=[])
    return out


def outcome(call: Callable[[], object]) -> str:
    """What a call gives, written out exactly (a decimal's digits and exponent count), or what it raised."""
    try:
        return repr(call())
    except TimeoutError:
        raise
    except Exception as exc:
        return f"raises {type(exc).__name__}: {exc}"


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.BookError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def opened(mod: types.ModuleType, equity: str = "100000") -> Any:
    return mod.apply(None, mod.Open("L0", "BASE", D(equity), 1_000))


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for cls in (mod.Open, mod.Buy, mod.Sell, mod.Mark, mod.Position, mod.Book):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    c.eq("the default limits are the kernel's", mod.DEFAULT_LIMITS, Limits())
    own = getattr(mod, "_CTX", None)
    c.eq(
        "the book's own decimal context: sixty digits, half-even", None if own is None else (own.prec, own.rounding), (60, ROUND_HALF_EVEN)
    )
    return c.bad


def check_sequence(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    b = opened(mod)
    c.eq(
        "a new book",
        (b.arm, b.level, b.cash, b.positions, b.peak, b.equity, b.mode, b.ruined, b.last_ms),
        ("L0", "BASE", D("100000"), (), D("100000"), D("100000"), Mode.ACTIVE, False, 1_000),
    )
    c.eq("with no drawdown", (b.drawdown, b.halted), (D(0), False))
    b = mod.apply(b, mod.Buy("BTC", D("0.5"), D("10000"), D("5"), 2_000))
    c.eq(
        "a purchase pays price times quantity plus the fee",
        (b.cash, b.positions, b.last_ms),
        (D("94995.00000000"), (mod.Position("BTC", D("0.5"), D("5005.00000000")),), 2_000),
    )
    c.eq("and the quantity held is found by name", (b.held("BTC"), b.held("ETH")), (D("0.5"), D(0)))
    b = mod.apply(b, mod.Buy("ETH", D("2"), D("500"), D("1"), 2_500))
    b = mod.apply(b, mod.Buy("BTC", D("0.1"), D("10100"), D("1"), 3_000))
    c.eq(
        "positions are kept by symbol and add up, cost with them",
        b.positions,
        (mod.Position("BTC", D("0.6"), D("6016.00000000")), mod.Position("ETH", D("2"), D("1001.00000000"))),
    )
    c.eq("cash after three purchases", b.cash, D("100000") - D("5005") - D("1001") - D("1011"))
    marked = mod.apply(b, mod.Mark((("BTC", D("11000")), ("ETH", D("450"))), 4_000))
    want = marked.cash + D("0.6") * D("11000") + D("2") * D("450")
    c.eq("a mark gives the equity, cash plus what is held at the marks", marked.equity, want)
    c.eq("and a higher equity raises the peak", marked.peak, want)
    c.eq("with no drawdown, and the book active", (marked.drawdown, marked.mode), (D(0), Mode.ACTIVE))
    down = mod.apply(marked, mod.Mark((("BTC", D("9000")), ("ETH", D("400"))), 5_000))
    c.eq(
        "a lower mark keeps the peak and shows the drawdown from it, unrounded",
        (down.peak, down.equity, down.drawdown),
        (want, down.cash + D("5400") + D("800"), ratio(want - down.equity, want)),
    )
    c.eq("it is a drawdown of a few percent: still active", down.mode, Mode.ACTIVE)
    s = mod.apply(down, mod.Sell("BTC", D("0.2"), D("9000"), D("0.9"), 6_000))
    c.eq("a sale adds the proceeds less the fee to the cash", s.cash, down.cash + D("1799.10000000"))
    c.eq("and leaves the rest of the position at its share of the cost", s.positions[0], mod.Position("BTC", D("0.4"), D("4010.66666667")))
    out = mod.apply(s, mod.Sell("BTC", D("0.4"), D("9000"), D("0"), 7_000))
    c.eq("selling all of it removes the position", [p.symbol for p in out.positions], ["ETH"])
    c.eq(
        "the equity at given prices, without changing the book",
        (mod.equity_of(out, {"ETH": D("450")}), out.equity),
        (out.cash + D("900"), down.equity),
    )
    # the ladder through marks
    base = mod.apply(opened(mod, "1000"), mod.Buy("X", D("10"), D("100"), D("0"), 2_000))  # all in: cash 0
    for price, mode in (("99", Mode.ACTIVE), ("95", Mode.HALVED), ("92.5", Mode.FLAT), ("88", Mode.HALTED)):
        m = mod.apply(base, mod.Mark((("X", D(price)),), 3_000))
        c.eq(f"at a mark of {price}", (m.mode, m.ruined), (mode, False))
    # positions stay in symbol order whatever order they were bought in
    reverse = mod.apply(mod.apply(opened(mod), mod.Buy("ETH", D(1), D(10), D(0), 2_000)), mod.Buy("BTC", D(1), D(10), D(0), 3_000))
    c.eq("bought in the other order, still by symbol", [p.symbol for p in reverse.positions], ["BTC", "ETH"])
    c.eq(
        "and after a partial sale",
        [p.symbol for p in mod.apply(reverse, mod.Sell("ETH", D("0.5"), D(10), D(0), 4_000)).positions],
        ["BTC", "ETH"],
    )
    # a drawdown is measured from a peak of one as well
    one = mod.apply(mod.apply(opened(mod, "1"), mod.Buy("X", D(1), D(1), D(0), 2_000)), mod.Mark((("X", D("0.5")),), 3_000))
    c.eq("a peak of one", (one.peak, one.equity, one.drawdown), (D(1), D("0.5"), D("0.5")))
    return c.bad


def check_terminal(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    b = mod.apply(opened(mod, "1000"), mod.Buy("X", D("10"), D("100"), D("0"), 2_000))
    halted = mod.apply(b, mod.Mark((("X", D("80")),), 3_000))
    c.eq("a fifth lost is past the last rung: halted", (halted.mode, halted.halted, halted.ruined), (Mode.HALTED, True, False))
    recovered = mod.apply(halted, mod.Mark((("X", D("100")),), 4_000))
    c.eq("halted is never left, whatever the next mark", (recovered.mode, recovered.drawdown), (Mode.HALTED, D(0)))
    c.refuses("a halted book buys nothing", lambda: mod.apply(halted, mod.Buy("Y", D(1), D(1), D(0), 5_000)), "a halted book buys nothing")
    sold = mod.apply(halted, mod.Sell("X", D(10), D(80), D(0), 5_000))
    c.eq("but it may sell what it holds", (sold.cash, sold.positions), (D("800.00000000"), ()))
    ruin = mod.apply(b, mod.Mark((("X", D("0.0001")),), 3_000))
    c.eq("an equity of nearly nothing is a drawdown, not yet ruin", ruin.ruined, False)
    bought_all = mod.apply(opened(mod, "100"), mod.Buy("X", D(1), D(100), D(0), 2_000))
    dead = mod.apply(bought_all, mod.Mark((("X", D("0.00000001")),), 3_000))
    c.eq("ruin is an equity of zero or less after rounding, and halts the book as well", (dead.ruined, dead.halted), (False, True))
    c.eq("a book never ruined stays unruined at a positive equity", mod.apply(opened(mod), mod.Mark((), 2_000)).ruined, False)
    zero = mod.apply(mod.apply(opened(mod, "100"), mod.Buy("X", D(1), D(100), D(0), 2_000)), mod.Mark((("X", D("0.000000001")),), 3_000))
    c.eq("an equity that rounds to nothing is ruin, and halts the book", (zero.equity, zero.ruined, zero.halted), (D(0), True, True))
    back = mod.apply(zero, mod.Mark((("X", D(100)),), 4_000))
    c.eq("ruin is never left, whatever the next mark", (back.equity, back.ruined, back.mode), (D(100), True, Mode.HALTED))
    flat = mod.apply(opened(mod, "100"), mod.Mark((), 2_000))
    c.eq("a mark of nothing held is the cash", (flat.equity, flat.peak), (D("100"), D("100")))
    return c.bad


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    b = opened(mod)
    c.refuses("an event before a book", lambda: mod.apply(None, mod.Buy("A", D(1), D(1), D(0), 1)), "a book is opened first")
    c.refuses("an empty replay", lambda: mod.replay([]), "a book is opened first")
    c.refuses("opened twice", lambda: mod.apply(b, mod.Open("L0", "BASE", D(1), 2)), "a book is opened once")
    c.refuses("no arm", lambda: mod.apply(None, mod.Open("", "BASE", D(1), 1)), "a book names its arm and its cost level")
    c.refuses("no level", lambda: mod.apply(None, mod.Open("L0", "", D(1), 1)), "a book names its arm and its cost level")
    for bad in (D(0), D(-1), D("NaN"), 5):
        c.refuses(
            f"opening equity {bad!r}",
            lambda bad=bad: mod.apply(None, mod.Open("L0", "BASE", bad, 1)),
            "the opening equity is a positive decimal",
        )
    for bad in (-1, True, 1.5):
        c.refuses(
            f"time {bad!r}",
            lambda bad=bad: mod.apply(None, mod.Open("L0", "BASE", D(1), bad)),
            "a time is a whole number of milliseconds, zero or more",
        )
    c.eq("time zero is a time", mod.apply(None, mod.Open("L0", "BASE", D(1), 0)).last_ms, 0)
    c.refuses("time going back", lambda: mod.apply(b, mod.Buy("A", D(1), D(1), D(0), 999)), "time only moves forward")
    c.eq("the same moment is allowed", mod.apply(b, mod.Buy("A", D(1), D(1), D(0), 1_000)).last_ms, 1_000)
    qty = "a quantity is a positive decimal"
    for bad in (D(0), D(-1), D("NaN"), 1):
        c.refuses(f"buying {bad!r}", lambda bad=bad: mod.apply(b, mod.Buy("A", bad, D(1), D(0), 2_000)), qty)
        c.refuses(f"selling {bad!r}", lambda bad=bad: mod.apply(b, mod.Sell("A", bad, D(1), D(0), 2_000)), qty)
    px = "a price is a positive decimal"
    for bad in (D(0), D(-1), D("NaN")):
        c.refuses(f"price {bad!r}", lambda bad=bad: mod.apply(b, mod.Buy("A", D(1), bad, D(0), 2_000)), px)
        c.refuses(f"sell price {bad!r}", lambda bad=bad: mod.apply(b, mod.Sell("A", D(1), bad, D(0), 2_000)), px)
    for bad in (D(-1), D("NaN"), D("Infinity")):
        c.refuses(f"fee {bad!r}", lambda bad=bad: mod.apply(b, mod.Buy("A", D(1), D(1), bad, 2_000)), "a fee is zero or a positive decimal")
    for bad in (D(-1), D("NaN"), D("Infinity")):
        c.refuses(
            f"sell fee {bad!r}", lambda bad=bad: mod.apply(b, mod.Sell("A", D(1), D(1), bad, 2_000)), "a fee is zero or a positive decimal"
        )
    c.refuses("a token with no name", lambda: mod.apply(b, mod.Buy("", D(1), D(1), D(0), 2_000)), "a token is named")
    c.refuses("selling a token with no name", lambda: mod.apply(b, mod.Sell("", D(1), D(1), D(0), 2_000)), "a token is named")
    c.refuses(
        "no leverage",
        lambda: mod.apply(b, mod.Buy("A", D(1), D("100001"), D(0), 2_000)),
        "a purchase is paid in cash: there is no leverage",
    )
    c.refuses(
        "the fee counts towards what must be paid",
        lambda: mod.apply(b, mod.Buy("A", D(1), D("100000"), D("0.00000001"), 2_000)),
        "a purchase is paid in cash: there is no leverage",
    )
    c.eq("exactly all the cash is enough", mod.apply(b, mod.Buy("A", D(1), D("100000"), D(0), 2_000)).cash, D(0))
    held = mod.apply(b, mod.Buy("A", D(1), D(10), D(0), 2_000))
    sale = "a sale cannot be larger than what is held"
    c.refuses("selling what is not held", lambda: mod.apply(held, mod.Sell("B", D(1), D(1), D(0), 3_000)), sale)
    c.refuses("selling more than is held", lambda: mod.apply(held, mod.Sell("A", D("1.00000001"), D(1), D(0), 3_000)), sale)
    once = "a mark prices every position held, once each, by symbol"
    c.refuses("a mark that misses a position", lambda: mod.apply(held, mod.Mark((), 3_000)), once)
    c.refuses("a mark of something not held", lambda: mod.apply(held, mod.Mark((("A", D(1)), ("B", D(1))), 3_000)), once)
    two = mod.apply(held, mod.Buy("B", D(1), D(1), D(0), 2_500))
    c.refuses("a mark listed out of order", lambda: mod.apply(two, mod.Mark((("B", D(1)), ("A", D(1))), 3_000)), once)
    for bad in (D(0), D(-1), D("NaN")):
        c.refuses(f"a mark of {bad!r}", lambda bad=bad: mod.apply(held, mod.Mark((("A", bad),), 3_000)), "a mark is a positive decimal")
    return c.bad


def check_fold_and_bodies(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    events = [
        mod.Open("L0", "STRESS", D("5000"), 10),
        mod.Buy("BTC", D("0.01"), D("20000"), D("0.2"), 20),
        mod.Mark((("BTC", D("20500")),), 30),
        mod.Buy("ETH", D("1"), D("1500"), D("0.15"), 40),
        mod.Mark((("BTC", D("21000")), ("ETH", D("1400"))), 50),
        mod.Sell("BTC", D("0.01"), D("21000"), D("0.21"), 60),
    ]
    step = None
    for event in events:
        step = mod.apply(step, event)
    c.eq("the fold equals the step-by-step book", mod.replay(events), step)
    c.eq("and a prefix is the book at that point", mod.replay(events[:3]).equity, D("5000") - D("200.2") + D("205"))
    bodies = [mod.to_body(e) for e in events]
    back = [mod.from_body(canonical.parse_record(canonical.dumps(b))) for b in bodies]
    c.eq("events read back whole from canonical bodies", back, events)
    c.eq("and a replay of them is the same book", mod.replay(back), step)
    c.eq("the body of a buy", bodies[1], {"event": "buy", "symbol": "BTC", "quantity": "0.01", "price": "20000", "fee": "0.2", "at_ms": 20})
    c.eq(
        "the body of a mark lists the prices by symbol",
        mod.to_body(mod.Mark((("A", D("1.5")), ("B", D("2"))), 7)),
        {"event": "mark", "prices": {"A": "1.5", "B": "2"}, "at_ms": 7},
    )
    c.eq(
        "the body of a sell and of an open",
        (bodies[5]["event"], bodies[0]),
        ("sell", {"event": "open", "arm": "L0", "level": "STRESS", "equity": "5000", "at_ms": 10}),
    )
    says = "a book event is an open, a buy, a sell or a mark with its members"
    c.refuses("a body with no event", lambda: mod.from_body({"symbol": "A"}), says)
    c.refuses("an event that is not one", lambda: mod.from_body({"event": "gift"}), says)
    c.refuses("a body with a member short", lambda: mod.from_body({k: v for k, v in bodies[1].items() if k != "fee"}), says)
    c.refuses("a body with an extra member", lambda: mod.from_body({**bodies[1], "x": 1}), says)
    c.refuses(
        "a time that is not whole",
        lambda: mod.from_body({**bodies[1], "at_ms": "20"}),
        "a time is a whole number of milliseconds, zero or more",
    )
    c.refuses(
        "a quantity that is not text", lambda: mod.from_body({**bodies[1], "quantity": 0.01}), "a quantity is written as decimal text"
    )
    c.refuses(
        "a quantity that is not a number", lambda: mod.from_body({**bodies[1], "quantity": "lots"}), "a quantity is written as decimal text"
    )
    c.refuses(
        "a quantity written loosely", lambda: mod.from_body({**bodies[1], "quantity": "1E-2"}), "a quantity is written as a plain decimal"
    )
    c.refuses("a price that is not text", lambda: mod.from_body({**bodies[1], "price": 5}), "a price is written as decimal text")
    c.refuses("a fee that is not text", lambda: mod.from_body({**bodies[1], "fee": 5}), "a fee is written as decimal text")
    c.refuses("an equity that is not text", lambda: mod.from_body({**bodies[0], "equity": 5}), "the equity is written as decimal text")
    unsorted_mark = mod.from_body({"event": "mark", "prices": {"B": "2", "A": "1.5"}, "at_ms": 7})
    c.eq("prices are taken by symbol", unsorted_mark.prices, (("A", D("1.5")), ("B", D("2"))))
    c.refuses("a symbol that is not text", lambda: mod.from_body({**bodies[1], "symbol": 5}), "a token is named")
    c.refuses("an arm that is not text", lambda: mod.from_body({**bodies[0], "arm": 1}), "the arm and the level are text")
    c.refuses("a level that is not text", lambda: mod.from_body({**bodies[0], "level": 1}), "the arm and the level are text")
    c.refuses("prices that are not an object", lambda: mod.from_body({**bodies[2], "prices": []}), "the prices of a mark are an object")
    c.refuses(
        "a mark that is not decimal text", lambda: mod.from_body({**bodies[2], "prices": {"BTC": 5}}), "a mark is written as decimal text"
    )
    return c.bad


def check_cash_never_below_zero(mod: types.ModuleType) -> list[str]:
    """Contract section 4.1: cash "must stay at or above zero". A sale brings what it sells less its fee, so a fee larger than the sale
    takes cash away; the book refuses the sale that would leave the cash below zero."""
    c = Case(mod)
    says = "a sale cannot leave the cash below zero"
    all_in = mod.apply(opened(mod, "1000"), mod.Buy("X", D(10), D(100), D(0), 2_000))  # no cash left
    c.refuses(
        "a sale of 1000 for a fee of 1500, with no cash", lambda: mod.apply(all_in, mod.Sell("X", D(10), D(100), D(1500), 3_000)), says
    )
    c.refuses(
        "a fee a hundred-millionth above the sale, with no cash",
        lambda: mod.apply(all_in, mod.Sell("X", D(10), D(100), D("1000.00000001"), 3_000)),
        says,
    )
    c.refuses(
        "a part sold for more fee than it brings", lambda: mod.apply(all_in, mod.Sell("X", D(1), D(100), D("100.00000001"), 3_000)), says
    )
    nothing = mod.apply(all_in, mod.Sell("X", D(10), D(100), D(1000), 3_000))
    c.eq("a fee of the whole sale leaves the cash at zero, which is allowed", (nothing.cash, nothing.positions), (D(0), ()))
    part = mod.apply(opened(mod, "1000"), mod.Buy("X", D(4), D(100), D(0), 2_000))  # 600 left in cash
    c.eq(
        "a fee above the sale that the cash covers takes cash away, and is allowed",
        mod.apply(part, mod.Sell("X", D(4), D(100), D("999.5"), 3_000)).cash,
        D("0.5"),
    )
    c.refuses(
        "but not a hundred-millionth more than there is",
        lambda: mod.apply(part, mod.Sell("X", D(4), D(100), D("1000.00000001"), 3_000)),
        says,
    )
    # the fill model's purchase and sale at double fees of 6000 basis points (review A5, p06), as records
    held = mod.apply(opened(mod, "2500"), mod.Buy("X", D("9.99500249"), D("100.0500"), D("1199.99999895"), 2_000))
    c.refuses(
        "the review's sale: proceeds of -999.00049887 on a cash of 300.00000193",
        lambda: mod.apply(held, mod.Sell("X", D("9.99500249"), D("499.7500"), D("5994.00299325"), 3_000)),
        says,
    )
    c.refuses(
        "and in a replay of the records",
        lambda: mod.replay(
            [
                mod.Open("L0", "DOUBLE_FEES", D("2500"), 1_000),
                mod.Buy("X", D("9.99500249"), D("100.0500"), D("1199.99999895"), 2_000),
                mod.Sell("X", D("9.99500249"), D("499.7500"), D("5994.00299325"), 3_000),
            ]
        ),
        says,
    )
    return c.bad


def check_ladder_reads_the_exact_drawdown(mod: types.ModuleType) -> list[str]:
    """The ladder reads the drawdown as it is: ``SIMULATOR_SPEC`` section 5 rounds prices, quantities and money and nothing else, so a
    drawdown of 0.1199995 is below the halt at 0.12 and does not end the arm."""
    c = Case(mod)
    for mark, drawdown, mode in (
        ("95000.05", "0.0499995", Mode.ACTIVE),
        ("95000", "0.05", Mode.HALVED),
        ("92500.05", "0.0749995", Mode.HALVED),
        ("92500", "0.075", Mode.FLAT),
        ("88000.05", "0.1199995", Mode.FLAT),
        ("88000", "0.12", Mode.HALTED),
    ):
        marked = mod.apply(mod.apply(opened(mod), mod.Buy("X", D(1), D(100000), D(0), 2_000)), mod.Mark((("X", D(mark)),), 3_000))
        c.eq(
            f"a mark of {mark} on a peak of 100000: the drawdown, the rung and whether it halted",
            (marked.drawdown, marked.mode, marked.halted),
            (D(drawdown), mode, mode is Mode.HALTED),
        )
    third = mod.apply(mod.apply(opened(mod, "3"), mod.Buy("X", D(1), D(3), D(0), 2_000)), mod.Mark((("X", D(2)),), 3_000))
    c.eq("a drawdown that does not end is carried to sixty digits", third.drawdown, ratio(D(1), D(3)))
    return c.bad


def check_contexts(mod: types.ModuleType) -> list[str]:
    """The same events give the same book whatever decimal context the caller has (``SIMULATOR_SPEC`` section 1: every result can be
    recomputed from stored inputs): six or nine digits, rounding toward either infinity, a trap on an inexact result, or no trap at all,
    each against the default context."""
    c = Case(mod)

    def bought() -> Any:
        return mod.apply(opened(mod), mod.Buy("X", D("9.99500249"), D("100.05"), D("0.99999999"), 2_000))

    def all_in(mark: str, equity: str = "100000") -> Any:
        book = mod.apply(opened(mod, equity), mod.Buy("X", D(1), D(equity), D(0), 2_000))
        return mod.apply(book, mod.Mark((("X", D(mark)),), 3_000))

    def ladder(book: Any) -> tuple[Decimal, Decimal, Decimal, Mode]:
        return book.equity, book.peak, book.drawdown, book.mode

    def held(book: Any) -> tuple[Decimal, tuple[Any, ...]]:
        return book.cash, book.positions

    body = {"event": "buy", "symbol": "X", "quantity": "9.99500249", "price": "100.05", "fee": "0.99999999", "at_ms": 2_000}
    operations: dict[str, Callable[[], object]] = {
        "a purchase": lambda: held(bought()),
        "a mark after it": lambda: ladder(mod.apply(bought(), mod.Mark((("X", D("99.5")),), 3_000))),
        "a sale of all of it": lambda: held(mod.apply(bought(), mod.Sell("X", D("9.99500249"), D("99.45025"), D("0.99400498"), 3_000))),
        "a sale of a third of it": lambda: held(mod.apply(bought(), mod.Sell("X", D("3.33166750"), D("99.45025"), D("0.33133499"), 3_000))),
        "an opening of 10 marked with nothing held": lambda: ladder(mod.apply(opened(mod, "10"), mod.Mark((), 2_000))),
        "a drawdown of 0.0500001": lambda: ladder(all_in("94999.99")),
        "a drawdown of a third": lambda: ladder(all_in("2", "3")),
        "the equity at given prices": lambda: mod.equity_of(bought(), {"X": D("99.123456789")}),
        "a purchase the cash cannot pay": lambda: mod.apply(
            opened(mod, "1000"), mod.Buy("X", D("9.99500250"), D("100.05"), D("0.00000001"), 2_000)
        ),
        "a sale that would leave the cash below zero": lambda: mod.apply(
            all_in("100"), mod.Sell("X", D(1), D(100000), D("100000.00000001"), 4_000)
        ),
        "a replay": lambda: mod.replay(
            [mod.Open("L0", "BASE", D("100000"), 1_000), mod.from_body(body), mod.Mark((("X", D("99.5")),), 3_000)]
        ),
        "a body read back": lambda: mod.from_body(body),
        "a body whose quantity is not a number": lambda: mod.from_body({**body, "quantity": "lots"}),
    }

    def results() -> dict[str, str]:
        return {name: outcome(call) for name, call in operations.items()}

    with localcontext(Context()):
        want = results()
    anchors = {
        "a mark after it": (D("99993.50274865"), D("100000.00000000"), D("0.0000649725135"), Mode.ACTIVE),
        "a sale of all of it": (D("99992.01149229"), ()),
        "a sale of a third of it": (D("99330.00383169"), (mod.Position("X", D("6.66333499"), D("667.33333241")),)),
        "a drawdown of 0.0500001": (D("94999.99000000"), D("100000.00000000"), D("0.0500001"), Mode.HALVED),
        "the equity at given prices": D("99989.73919831"),
    }
    for name, value in anchors.items():
        c.eq(f"{name}, in the default context", want[name], repr(value))
    for label, ctx in contexts().items():
        with localcontext(ctx):
            got = results()
        for name, value in want.items():
            c.eq(f"{name}, in a caller's context of {label}", got[name], value)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_sequence,
        check_terminal,
        check_refusals,
        check_fold_and_bodies,
        check_cash_never_below_zero,
        check_ladder_reads_the_exact_drawdown,
        check_contexts,
    ):
        try:
            bad += [f"{check.__name__}: {m}" for m in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1000:]}")
    return bad
