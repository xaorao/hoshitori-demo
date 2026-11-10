"""Checks of the practice-portfolio view (``hoshitori.practice_view``), runnable against a mutant.

The view turns the ledger's ``practice`` records into what a person reads: for each book (an arm at a cost level) where it stands, what it
holds, the round trips it has made with what each cost, and the path of its equity. Every number is exact decimal text. The expected
figures below are worked by hand from the events in each case.
"""

from __future__ import annotations

import types
from decimal import Decimal
from typing import Any

from hoshitori import book, fills, practice_record
from hoshitori.ids import natural_id

URN = "urn:hoshitori:league:test"
FID_A = natural_id("fc", {"n": 1})
FID_B = natural_id("fc", {"n": 2})
DIGEST = "a" * 64
T0 = 1_793_000_000_000
MIN = 60_000


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Any) -> None:
        try:
            call()
        except self.mod.PracticeViewError:
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def D(text: str) -> Decimal:
    return Decimal(text)


def rec(
    name: str, event: book.Event, at: int, forecast: str | None = None, details: practice_record.Details | None = None
) -> dict[str, Any]:
    ref = None if forecast is None else (forecast, DIGEST)
    out = practice_record.to_record(name, event, ref, URN, at, details)
    return {**out, "committed_at": out["created_at"]}


def sale(kind: str, bar: int) -> practice_record.Details:
    return practice_record.Details(kind, bar, bar - MIN, 15_000, T0)


def buy_details(bar: int) -> practice_record.Details:
    return practice_record.Details(None, bar, None, 15_000, None)


def one_round_trip() -> list[dict[str, Any]]:
    name = "L0:BASE"
    return [
        rec(name, book.Open("L0", "BASE", D("100000"), T0), T0),
        rec(name, book.Buy("BTC", D("1"), D("50000"), D("50"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Mark((("BTC", D("51000")),), T0 + 2 * MIN), T0 + 2 * MIN),
        rec(name, book.Sell("BTC", D("1"), D("51500"), D("51.5"), T0 + 3 * MIN), T0 + 3 * MIN, FID_A, sale("TARGET", T0 + 3 * MIN)),
        rec(name, book.Mark((), T0 + 4 * MIN), T0 + 4 * MIN),
    ]


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("a curve has at most 120 points", mod.MAX_POINTS, 120)
    for cls in (mod.Position, mod.Trade, mod.Point, mod.PracticeBook):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_nothing(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("no records, no books", mod.build([]), ())
    other = [{"type": "forecast", "id": "x"}, {"type": "cycle_state", "id": "y"}]
    c.eq("records of other kinds are not read", mod.build(other), ())
    return c.bad


def check_one_round_trip(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    (view,) = mod.build(one_round_trip())
    c.eq("the book and its parts", (view.book, view.arm, view.level), ("L0:BASE", "L0", "BASE"))
    c.eq(
        "the figures",
        (view.opening_equity, view.equity, view.peak, view.cash, view.drawdown, view.fees, view.return_share),
        ("100000", "101398.5", "101398.5", "101398.5", "0", "101.5", "0.013985"),
    )
    c.eq("counts of what was done", (view.buys, view.sells, view.marks), (1, 1, 2))
    c.eq("nothing is held at the end", view.positions, ())
    c.eq("the state of the book", (view.mode, view.ruined), ("ACTIVE", False))
    c.eq("opened and last event", (view.opened_at, view.last_at), ("2026-10-26T07:33:20.000Z", "2026-10-26T07:37:20.000Z"))
    (trade,) = view.trades
    c.eq(
        "the round trip, with what it cost and how it ended",
        (trade.symbol, trade.quantity, trade.entry_price, trade.exit_price, trade.fees, trade.net, trade.exit_kind, trade.forecast_id),
        ("BTC", "1", "50000", "51500", "101.5", "1398.5", "TARGET", FID_A),
    )
    c.eq(
        "it opened and closed when the book says",
        (trade.opened_at, trade.closed_at),
        ("2026-10-26T07:33:20.000Z", "2026-10-26T07:36:20.000Z"),
    )
    c.eq(
        "the path of the equity is its marks",
        [(p.at, p.equity) for p in view.curve],
        [("2026-10-26T07:35:20.000Z", "100950"), ("2026-10-26T07:37:20.000Z", "101398.5")],
    )
    return c.bad


def check_open_position(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    events = one_round_trip()[:3]  # opened, bought, marked at 51000; not sold
    (view,) = mod.build(events)
    (held,) = view.positions
    c.eq(
        "what is held, what it cost and what it is worth at the last mark",
        (held.symbol, held.quantity, held.cost, held.mark, held.value),
        ("BTC", "1", "50050", "51000", "51000"),
    )
    c.eq("no round trip yet", view.trades, ())
    c.eq("the equity is cash and the marked value", (view.equity, view.cash), ("100950", "49950"))
    c.eq("the return is on the opening equity", view.return_share, "0.0095")
    unmarked = one_round_trip()[:2]
    (bare,) = mod.build(unmarked)
    (kept,) = bare.positions
    c.eq("a position with no mark has no value, never zero", (kept.mark, kept.value), (None, None))
    return c.bad


def check_average_cost(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    name = "L0:BASE"
    events = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("1"), D("100"), D("0.1"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Buy("BTC", D("1"), D("110"), D("0.11"), T0 + 2 * MIN), T0 + 2 * MIN, FID_B, buy_details(T0 + 2 * MIN)),
        rec(name, book.Sell("BTC", D("1.5"), D("120"), D("0.18"), T0 + 3 * MIN), T0 + 3 * MIN, FID_B, sale("TIME", T0 + 3 * MIN)),
    ]
    (view,) = mod.build(events)
    (trade,) = view.trades
    c.eq(
        "a sale more than its own call bought takes its units at the average the position cost, as the book does: the entry price is that average without the fees",
        (trade.quantity, trade.entry_price, trade.exit_price),
        ("1.5", "105", "120"),
    )
    c.eq("it carries its share of the purchase fees and the sale fee", trade.fees, "0.3375")
    c.eq("and its net is the proceeds after the sale fee, less the cost taken from the position", trade.net, "22.1625")
    (left,) = view.positions
    c.eq("what is left of the position has the cost the book keeps for it", (left.quantity, left.cost), ("0.5", "52.5525"))
    c.eq("the fees of the whole book", view.fees, "0.39")
    c.eq("a sale names the forecast it follows and the exit's kind", (trade.forecast_id, trade.exit_kind), (FID_B, "TIME"))
    return c.bad


def check_own_lot(mod: types.ModuleType) -> list[str]:
    """Two calls buy one token at different prices and each sale closes its own call's purchase: each is shown at the price that call paid,
    with that call's fees, not at the average of both (a receipt shows what this call did); the nets together are what the book made."""
    c = Case(mod)
    name = "L0:BASE"
    events = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("1"), D("100"), D("0.1"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Buy("BTC", D("1"), D("110"), D("0.11"), T0 + 2 * MIN), T0 + 2 * MIN, FID_B, buy_details(T0 + 2 * MIN)),
        rec(name, book.Sell("BTC", D("1"), D("120"), D("0.12"), T0 + 3 * MIN), T0 + 3 * MIN, FID_A, sale("TARGET", T0 + 3 * MIN)),
    ]
    (view,) = mod.build(events)
    (first,) = view.trades
    c.eq(
        "the first call's sale is at its own purchase",
        (first.forecast_id, first.entry_price, first.fees, first.net),
        (FID_A, "100", "0.22", "19.78"),
    )
    (left,) = view.positions
    c.eq("what is held is the second call's purchase, at what it cost", (left.quantity, left.cost), ("1", "110.11"))
    events.append(rec(name, book.Sell("BTC", D("1"), D("120"), D("0.12"), T0 + 4 * MIN), T0 + 4 * MIN, FID_B, sale("TIME", T0 + 4 * MIN)))
    events.append(rec(name, book.Mark((), T0 + 5 * MIN), T0 + 5 * MIN))
    (both,) = mod.build(events)
    second = both.trades[1]
    c.eq(
        "the second call's sale is at its own purchase",
        (second.forecast_id, second.entry_price, second.fees, second.net),
        (FID_B, "110", "0.23", "9.77"),
    )
    c.eq("together they are what the book made", D(first.net) + D(second.net), D(both.equity) - D("1000"))
    c.eq("and nothing is left", both.positions, ())
    return c.bad


def check_second_sale_and_old_records(mod: types.ModuleType) -> list[str]:
    """A second sale carries only the purchase fees that are left in the position; a record without fill details is shown without them."""
    c = Case(mod)
    name = "L0:BASE"
    events = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("2"), D("100"), D("1"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Sell("BTC", D("1"), D("110"), D("0.5"), T0 + 2 * MIN), T0 + 2 * MIN, FID_A, sale("TIME", T0 + 2 * MIN)),
        rec(name, book.Sell("BTC", D("1"), D("120"), D("0.5"), T0 + 3 * MIN), T0 + 3 * MIN, FID_B, sale("STOP", T0 + 3 * MIN)),
    ]
    (view,) = mod.build(events)
    first, second = view.trades
    c.eq("the first sale takes half the purchase fee and its own", (first.fees, first.net), ("1", "9"))
    c.eq("the second takes the half that was left, not the whole again", (second.fees, second.net), ("1", "19"))
    c.eq("together the fees are what was paid", view.fees, "2")
    bare = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("1"), D("100"), D("0"), T0 + MIN), T0 + MIN, FID_A),
        rec(name, book.Sell("BTC", D("1"), D("105"), D("0"), T0 + 2 * MIN), T0 + 2 * MIN, FID_A),
    ]
    (old,) = mod.build(bare)
    c.eq(
        "a sale recorded without details says neither when it began nor why it ended",
        (old.trades[0].opened_at, old.trades[0].exit_kind),
        (None, None),
    )
    c.eq("but still what it made", (old.trades[0].entry_price, old.trades[0].net), ("100", "5"))
    return c.bad


def check_derived_figures_keep_eight_places(mod: types.ModuleType) -> list[str]:
    """Two of three units bought at 100 with a fee of 1: the share of the fee is a quotient that never ends. A person reads it as money,
    which the book keeps to eight places, so the view states it to eight places and not to sixty digits."""
    c = Case(mod)
    name = "L0:BASE"
    events = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("3"), D("100"), D("1"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Sell("BTC", D("2"), D("110"), D("0.5"), T0 + 2 * MIN), T0 + 2 * MIN, FID_A, sale("TIME", T0 + 2 * MIN)),
    ]
    (view,) = mod.build(events)
    (trade,) = view.trades
    c.eq(
        "the entry price is the average price paid, which is exact here",
        (trade.quantity, trade.entry_price, trade.exit_price),
        ("2", "100", "110"),
    )
    c.eq(
        "the share of the purchase fee and the sale fee are money, to eight places", (trade.fees, trade.net), ("1.16666667", "18.83333333")
    )
    for label, text in (("fees", trade.fees), ("net", trade.net)):
        places = len(text.partition(".")[2])
        c.eq(f"{label} has at most eight places", places <= 8, True)
    (left,) = view.positions
    c.eq("what is left of the position is unchanged by that", (left.quantity, left.cost), ("1", "100.33333333"))
    return c.bad


def check_entry_price_is_the_price_paid(mod: types.ModuleType) -> list[str]:
    """The entry price is worked from the prices paid, not from the cost the book rounds to eight places: a position bought once is
    entered at the price it was bought at whatever its quantity, and the average of several purchases survives partial sales."""
    c = Case(mod)
    name = "L0:BASE"
    once = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("ETH", D("0.12345678"), D("2322.930885"), D("0.2868"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Sell("ETH", D("0.12345678"), D("2400"), D("0.3"), T0 + 2 * MIN), T0 + 2 * MIN, FID_A, sale("TARGET", T0 + 2 * MIN)),
    ]
    (view,) = mod.build(once)
    c.eq("bought once, entered at the price it was bought at", view.trades[0].entry_price, "2322.930885")
    several = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("0.3"), D("100.1"), D("0.03"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Buy("BTC", D("0.7"), D("100.3"), D("0.07"), T0 + 2 * MIN), T0 + 2 * MIN, FID_B, buy_details(T0 + 2 * MIN)),
        rec(name, book.Sell("BTC", D("0.5"), D("101"), D("0.05"), T0 + 3 * MIN), T0 + 3 * MIN, FID_A, sale("TIME", T0 + 3 * MIN)),
        rec(name, book.Sell("BTC", D("0.25"), D("101"), D("0.02"), T0 + 4 * MIN), T0 + 4 * MIN, FID_A, sale("TIME", T0 + 4 * MIN)),
        rec(name, book.Sell("BTC", D("0.25"), D("99"), D("0.02"), T0 + 5 * MIN), T0 + 5 * MIN, FID_B, sale("STOP", T0 + 5 * MIN)),
    ]
    (view,) = mod.build(several)
    c.eq(
        "the average price of two purchases is the same for every sale of the position",
        [t.entry_price for t in view.trades],
        ["100.24", "100.24", "100.24"],
    )
    again = [
        *several[:3],
        rec(name, book.Sell("BTC", D("1"), D("101"), D("0.1"), T0 + 3 * MIN), T0 + 3 * MIN, FID_A, sale("TIME", T0 + 3 * MIN)),
        rec(name, book.Buy("BTC", D("0.1"), D("90"), D("0"), T0 + 4 * MIN), T0 + 4 * MIN, FID_B, buy_details(T0 + 4 * MIN)),
        rec(name, book.Sell("BTC", D("0.1"), D("95"), D("0"), T0 + 5 * MIN), T0 + 5 * MIN, FID_B, sale("TARGET", T0 + 5 * MIN)),
    ]
    (view,) = mod.build(again)
    c.eq("a position sold out and bought again starts its average afresh", [t.entry_price for t in view.trades], ["100.24", "90"])
    tiny = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("PEPE", D("1000000"), D("0.0000123456789"), D("0.01"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(
            name,
            book.Sell("PEPE", D("1000000"), D("0.00001301"), D("0.01"), T0 + 2 * MIN),
            T0 + 2 * MIN,
            FID_A,
            sale("TARGET", T0 + 2 * MIN),
        ),
    ]
    (view,) = mod.build(tiny)
    c.eq(
        "a token priced in millionths keeps its price to its significant digits, not to eight places",
        view.trades[0].entry_price,
        "0.0000123456789",
    )
    odd = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("1"), D("100"), D("0"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Buy("BTC", D("2"), D("101"), D("0"), T0 + 2 * MIN), T0 + 2 * MIN, FID_B, buy_details(T0 + 2 * MIN)),
        rec(name, book.Sell("BTC", D("3"), D("102"), D("0"), T0 + 3 * MIN), T0 + 3 * MIN, FID_A, sale("TIME", T0 + 3 * MIN)),
    ]
    (view,) = mod.build(odd)
    c.eq("an average that never ends is stated to sixteen significant digits", view.trades[0].entry_price, "100.6666666666667")
    tie = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("ETH", D("1"), D("1.2345678901234565"), D("0"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Sell("ETH", D("1"), D("2"), D("0"), T0 + 2 * MIN), T0 + 2 * MIN, FID_A, sale("TIME", T0 + 2 * MIN)),
    ]
    (view,) = mod.build(tie)
    c.eq("a price exactly between two sixteen-digit prices goes to the even one", view.trades[0].entry_price, "1.234567890123456")
    return c.bad


def check_no_figure_runs_long(mod: types.ModuleType) -> list[str]:
    """Whatever a book holds, every figure the view states is cut where what it measures is exact: money and quantities to the eight places
    the book keeps, a price per unit to sixteen significant digits. Nothing runs to the sixty digits of a quotient."""
    c = Case(mod)
    name = "L0:BASE"
    events = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("1"), D("100"), D("1"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Buy("BTC", D("2"), D("101"), D("0.03"), T0 + 2 * MIN), T0 + 2 * MIN, FID_B, buy_details(T0 + 2 * MIN)),
        rec(name, book.Buy("ETH", D("7"), D("3"), D("0.03"), T0 + 3 * MIN), T0 + 3 * MIN, FID_B, buy_details(T0 + 3 * MIN)),
        rec(name, book.Mark((("BTC", D("101")), ("ETH", D("3.1"))), T0 + 4 * MIN), T0 + 4 * MIN),
        rec(name, book.Sell("BTC", D("2"), D("110"), D("0.5"), T0 + 5 * MIN), T0 + 5 * MIN, FID_A, sale("TIME", T0 + 5 * MIN)),
        rec(name, book.Sell("ETH", D("3"), D("3.3"), D("0.01"), T0 + 6 * MIN), T0 + 6 * MIN, FID_B, sale("STOP", T0 + 6 * MIN)),
        rec(name, book.Mark((("BTC", D("102")), ("ETH", D("3.2"))), T0 + 7 * MIN), T0 + 7 * MIN),
    ]
    (view,) = mod.build(events)
    prices = {"entry_price", "exit_price", "mark"}
    long: list[tuple[str, str]] = []

    def walk(label: str, value: Any) -> None:
        if isinstance(value, str):
            plain = value.replace("-", "").replace(".", "")
            if not plain.isdigit():
                return  # a name, a time, a word
            if label.rsplit(".", 1)[-1] in prices:
                if len(plain.lstrip("0")) > 16:
                    long.append((label, value))
            elif len(value.partition(".")[2]) > 8:
                long.append((label, value))
        elif isinstance(value, tuple | list):
            for i, item in enumerate(value):
                walk(f"{label}[{i}]", item)
        elif hasattr(value, "__dataclass_fields__"):
            for field in value.__dataclass_fields__:
                walk(f"{label}.{field}", getattr(value, field))

    walk("book", view)
    c.eq("no figure of the book runs past the places or digits of what it measures", long, [])
    c.eq("the average of the purchases is among them, cut at sixteen digits", view.trades[0].entry_price, "100.6666666666667")
    c.eq("and so is the share of the purchase fees, cut at eight places", view.trades[0].fees, "1.18666667")
    return c.bad


def check_agent_names(mod: types.ModuleType) -> list[str]:
    """A page shows the agent as every other page does (``labels.ARM_NAMES``), and an agent with no name keeps its code, never a blank."""
    c = Case(mod)
    books = [rec(f"{arm}:BASE", book.Open(arm, "BASE", D("1000"), T0), T0) for arm in ("L0", "L1", "L1x", "L2", "L9")]
    got = {b.arm: b.arm_name for b in mod.build(books)}
    c.eq(
        "each agent is named as the other pages name it",
        got,
        {"L0": "Rules", "L1": "Single model", "L1x": "Matched control", "L2": "Debate", "L9": "L9"},
    )
    return c.bad


def check_books_apart(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    base = one_round_trip()
    # a record of another book cannot be built by editing (its id names its book): build them again
    other = [
        rec("L0:STRESS", book.Open("L0", "STRESS", D("5000"), T0), T0),
        rec("L0:STRESS", book.Mark((), T0 + MIN), T0 + MIN),
    ]
    out = mod.build([*other, *base])
    c.eq("books are separate and listed by name", [b.book for b in out], ["L0:BASE", "L0:STRESS"])
    c.eq("each has its own opening", [b.opening_equity for b in out], ["100000", "5000"])
    c.eq("and each its own cost level", [(b.level, b.fee_bps, b.slippage_bps) for b in out], [("BASE", "10", "5"), ("STRESS", "10", "25")])
    return c.bad


def check_costs(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    books = [rec(f"L0:{level}", book.Open("L0", level, D("1000"), T0), T0) for level in ("DOUBLE_FEES", "BASE", "STRESS")]
    out = {b.level: (b.fee_bps, b.slippage_bps, b.adverse) for b in mod.build(books)}
    c.eq(
        "the cost of each level, as the fill model states it",
        out,
        {"BASE": ("10", "5", False), "STRESS": ("10", "25", True), "DOUBLE_FEES": ("20", "5", False)},
    )
    custom = fills.Costs(fee_bps=D("7"), slip_bps=D("3"), stress_slip_bps=D("40"))
    out = {b.level: (b.fee_bps, b.slippage_bps) for b in mod.build(books, custom)}
    c.eq("the costs given are the costs shown", out, {"BASE": ("7", "3"), "STRESS": ("7", "40"), "DOUBLE_FEES": ("14", "3")})
    return c.bad


def check_curve(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    name = "L0:BASE"
    events = [rec(name, book.Open("L0", "BASE", D("1000"), T0), T0)]
    for k in range(1, 1501):
        events.append(rec(name, book.Mark((), T0 + k * MIN), T0 + k * MIN))
    (view,) = mod.build(events)
    times = [p.at for p in view.curve]
    c.eq("a long record keeps at most the limit", len(view.curve), mod.MAX_POINTS)
    c.eq("and the first and the last mark", (times[0], times[-1]), ("2026-10-26T07:34:20.000Z", "2026-10-27T08:33:20.000Z"))
    c.eq("in time order, each once", times == sorted(set(times)), True)
    c.eq("every mark is still counted", view.marks, 1500)
    short = mod.build(events[:6])
    c.eq("a short record keeps every mark", len(short[0].curve), 5)
    return c.bad


def check_mode_and_ruin(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    name = "L0:BASE"
    events = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("10"), D("100"), D("0"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Mark((("BTC", D("85")),), T0 + 2 * MIN), T0 + 2 * MIN),
    ]
    (view,) = mod.build(events)
    replayed = book.replay([practice_record.from_record(e).event for e in events])
    c.eq("the mode and the drawdown are the book's own", (view.mode, view.drawdown), (replayed.mode.value, format(replayed.drawdown, "f")))
    c.eq("a fall of a seventh of the equity moves the ladder", view.mode != "ACTIVE", True)
    from hoshitori.timeline import from_ms
    from hoshitori.timeutil import format_rfc3339_ms

    steps = [
        rec(name, book.Open("L0", "BASE", D("1000"), T0), T0),
        rec(name, book.Buy("BTC", D("10"), D("100"), D("0"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Mark((("BTC", D("99")),), T0 + 2 * MIN), T0 + 2 * MIN),  # 1%: no rung
        rec(name, book.Mark((("BTC", D("94")),), T0 + 3 * MIN), T0 + 3 * MIN),  # 6%: sizes halved (5%)
        rec(name, book.Mark((("BTC", D("85")),), T0 + 4 * MIN), T0 + 4 * MIN),  # 15%: past the last rung (12%)
    ]
    (laddered,) = mod.build(steps)
    c.eq(
        "each move down the ladder is kept, with its time and the fall then",
        laddered.steps,
        (
            mod.Step(format_rfc3339_ms(from_ms(T0 + 3 * MIN)), "HALVED", "0.06"),
            mod.Step(format_rfc3339_ms(from_ms(T0 + 4 * MIN)), "HALTED", "0.15"),
        ),
    )
    c.eq("a book that never fell has none", mod.build(steps[:3])[0].steps, ())
    try:
        laddered.steps[0].mode = "ACTIVE"  # type: ignore[misc]
        c.bad.append("a step can be changed after it is made")
    except AttributeError:
        pass
    return c.bad


def check_strict(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    good = one_round_trip()
    tampered = [dict(r) for r in good]
    tampered[1] = {**tampered[1], "id": tampered[2]["id"]}
    c.refuses("a record whose id is not its content's is refused", lambda: mod.build(tampered))
    c.refuses("a book whose first event is not its opening is refused", lambda: mod.build(good[1:]))
    c.refuses(
        "a cost level that is not one of the model's is refused", lambda: mod.build([rec("L0:ODD", book.Open("L0", "ODD", D("1"), T0), T0)])
    )
    too_big = [
        rec("L0:BASE", book.Open("L0", "BASE", D("100"), T0), T0),
        rec("L0:BASE", book.Buy("BTC", D("10"), D("100"), D("0"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
    ]
    c.refuses("a purchase the cash could not pay is refused by the book's own rule", lambda: mod.build(too_big))
    not_a_dict = {**good[0], "body": "x"}
    c.refuses("a body that is not an object is refused", lambda: mod.build([not_a_dict]))
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_nothing,
        check_one_round_trip,
        check_open_position,
        check_average_cost,
        check_own_lot,
        check_second_sale_and_old_records,
        check_derived_figures_keep_eight_places,
        check_entry_price_is_the_price_paid,
        check_no_figure_runs_long,
        check_agent_names,
        check_books_apart,
        check_costs,
        check_curve,
        check_mode_and_ruin,
        check_strict,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
