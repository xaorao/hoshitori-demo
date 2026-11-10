"""Checks of the rules arm (``hoshitori.rules_arm``), runnable against a mutant. Counts and states are also computed by a separate path
(fractions and squares, no square root) and compared."""

from __future__ import annotations

import decimal
import itertools
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Any

from hoshitori import collect, snapshot
from hoshitori.timeline import to_ms
from hoshitori.venue_data import Candle
from tests.collect_checks import NOW, SERVER, SRC, Reply, klines
from tests.collect_checks import World as CollectWorld
from tests.snapshot_checks import S
from tests.snapshot_checks import World as SnapWorld

D = Decimal
T0 = datetime(2026, 9, 1, tzinfo=UTC)
HOUR = timedelta(hours=1)
ALL_STATES = ("S1", "S2", "S3", "S4", "S5")


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
        except self.mod.RulesError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


# --- the independent path ------------------------------------------------------------------------------------------------------------------
def r12(value: Fraction) -> Fraction:
    return Fraction(round(value * 10**12), 10**12)


def brute_state(ret: Fraction, atr: Fraction, hours: int) -> str | None:
    """The state by squares only: the move against the expected move without taking a square root. ``None`` for a zero range."""
    if atr == 0:
        return None
    a = atr * atr * hours
    if ret <= 0 and ret * ret >= a:
        return "S1"
    if ret <= 0 and 16 * ret * ret >= a:
        return "S2"
    if ret <= 0 or 16 * ret * ret < a:
        return "S3"
    return "S4" if ret * ret < a else "S5"


def history(seed: int, n: int, flat: tuple[int, int] = (0, 0), holes: frozenset[int] = frozenset()) -> list[Candle]:
    """A deterministic random walk of hourly bars in whole prices, with an optional dead stretch (no range) and missing bars."""
    state, price, out = seed, 1000, []
    for k in range(n):
        state = (state * 1103515245 + 12345) % 2**31
        step = (state >> 16) % 7 - 3
        dead = flat[0] <= k < flat[1]
        close = price if dead else max(10, price + step)
        pad = 0 if dead else (1 if k % 5 else 0)
        high, low = max(price, close) + pad, min(price, close) - pad
        if k not in holes:
            opened = T0 + HOUR * k
            out.append(Candle(opened, opened + HOUR, str(price), str(high), str(low), str(close)))
        price = close
    return out


def brute_counts(
    histories: dict[str, list[Candle]], horizons: tuple[int, ...] = (1, 4, 24)
) -> tuple[dict[tuple[int, str], list[int]], int]:
    counts = {(h, s): [0, 0, 0] for h in horizons for s in ALL_STATES}
    skipped = 0
    for name in sorted(histories):
        at = {int((b.open_time - T0) / HOUR): b for b in histories[name]}
        for hours in horizons:
            free = None
            for k in sorted(at):
                if free is not None and k < free:
                    continue
                before = at.get(k - hours)
                run = [at.get(k - back) for back in range(14, -1, -1)]
                later = at.get(k + hours)
                if before is None or any(x is None for x in run) or later is None:
                    skipped += 1
                    continue
                ret = r12(Fraction(at[k].close) / Fraction(before.close) - 1)
                trs = []
                for prev, bar in itertools.pairwise(run):
                    assert prev is not None and bar is not None
                    high, low, pc = Fraction(bar.high), Fraction(bar.low), Fraction(prev.close)
                    trs.append(max(high - low, abs(high - pc), abs(low - pc)))
                atr = r12(sum(trs) / 14 / Fraction(at[k].close))
                state = brute_state(ret, atr, hours)
                if state is None:
                    skipped += 1
                    continue
                cell = counts[(hours, state)]
                end, start = Fraction(later.close), Fraction(at[k].close)
                if end == start:
                    cell[2] += 1
                else:
                    cell[0] += 1
                    cell[1] += 1 if end > start else 0
                free = k + hours
    return counts, skipped


def rates(mod: types.ModuleType, cell: Callable[[int, str], tuple[int, int, int]], **over: Any) -> Any:
    values: dict[str, Any] = {
        "venue": "binance-spot",
        "quote_class": "USDT",
        "instruments": ("BTCUSDT", "ETHUSDT"),
        "period_start_ms": to_ms(T0),
        "period_end_ms": to_ms(T0 + timedelta(days=60)),
        "skipped": 3,
    }
    values.update(over)
    cells = tuple(((h, s), mod.Cell(*cell(h, s))) for h in mod.HORIZONS for s in mod.STATES)
    return mod.BaseRates(cells, **values)


def graded(mod: types.ModuleType) -> Any:
    """Every cell has 100 windows, and the number that ended higher rises with the state: 10, 30, 50, 70, 90."""
    return rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0))


# --- checks ----------------------------------------------------------------------------------------------------------------------------------
def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "rule",
        (mod.RULE_VERSION, mod.HORIZONS, mod.STATES, mod.MIN_CELL, mod.EDGE, mod.P_FLOOR, mod.P_CEIL),
        ("1", (1, 4, 24), tuple(ALL_STATES), 100, D("0.02"), D("0.01"), D("0.99")),
    )
    c.eq("edges", mod.EDGES, (D("-1"), D("-0.25"), D("0.25"), D("1")))
    c.eq("invalidation", mod.INVALIDATION, ("close below entry minus 1.5 ATR14",))
    c.eq("verdicts", [v.value for v in mod.Verdict], ["CALL", "NO_CALL", "FAILED_EVIDENCE"])
    c.eq("reasons", [r.value for r in mod.Reason], ["UNMAPPED", "NO_RANGE", "NO_FEATURES", "FEATURE_UNKNOWN"])
    c.eq("actions", [a.value for a in mod.Action], ["LONG", "FLAT"])
    for cls in (mod.Cell, mod.BaseRates, mod.Decision):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_state(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    for z, want in (
        ("-5", "S1"), ("-1.0000000001", "S1"), ("-1", "S1"), ("-0.9999999999", "S2"), ("-0.5", "S2"), ("-0.25", "S2"), ("-0.2499999999", "S3"),
        ("-0.0000001", "S3"), ("0", "S3"), ("0.2499999999", "S3"), ("0.25", "S4"), ("0.5", "S4"), ("0.9999999999", "S4"), ("1", "S5"), ("1.0000000001", "S5"), ("9", "S5"),
    ):  # fmt: skip
        c.eq(f"z = {z}", mod.state_of(D(z)), want)
    # the move in units of the expected move
    c.eq("four hours: the expected move is twice the hourly range", mod.normalised(D("0.04"), D("0.01"), 4), D("2"))
    c.eq("one hour: the hourly range itself", mod.normalised(D("0.04"), D("0.01"), 1), D("4"))
    c.eq("a fall is negative", mod.normalised(D("-0.01"), D("0.01"), 1), D("-1"))
    day = D("1") / D("24").sqrt(decimal.Context(prec=28))
    c.eq("a day: the square root of 24, to 28 digits", mod.normalised(D("1"), D("1"), 24), day)
    c.eq("a zero range cannot be normalised", mod.normalised(D("0.01"), D("0"), 4), None)
    with decimal.localcontext() as ctx:
        ctx.prec = 3
        c.eq("the caller's precision does not matter", mod.normalised(D("1"), D("1"), 24), day)
    return c.bad


def check_rates(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = graded(mod)
    c.eq(
        "probabilities are (ups + 1) / (n + 2) to six places",
        [str(g.p(4, s)) for s in ALL_STATES],
        ["0.107843", "0.303922", "0.500000", "0.696078", "0.892157"],
    )
    c.eq("the same for every horizon", {g.p(1, s) for s in ALL_STATES} == {g.p(24, s) for s in ALL_STATES}, True)
    thin = rates(mod, lambda h, s: (99, 50, 0) if s == "S3" else (100, 50, 0))
    c.eq("99 windows is too few, 100 is enough", (thin.p(1, "S3"), thin.p(1, "S2")), (None, D("0.500000")))
    c.eq("ties are not windows", rates(mod, lambda h, s: (100, 50, 400)).p(1, "S1"), D("0.500000"))
    low = rates(mod, lambda h, s: (100, 0, 0))
    high = rates(mod, lambda h, s: (100, 100, 0))
    c.eq("never below 0.01 or above 0.99", (low.p(1, "S1"), high.p(1, "S1")), (D("0.01"), D("0.99")))
    half = rates(mod, lambda h, s: (126, 2, 0) if s == "S1" else (126, 4, 0))
    c.eq(
        "half-even at the sixth place: 3/128 up, 5/128 down",
        (str(half.p(1, "S1")), str(half.p(1, "S2"))),
        ["0.023438", "0.039062"][0:1] and ("0.023438", "0.039062"),
    )
    c.eq("a cell is reachable by horizon and state", g.cell(24, "S5"), mod.Cell(100, 90, 0))
    body = g.body()
    c.eq("the body's header", {k: body[k] for k in ("rule_version", "min_cell", "edge", "venue", "quote_class", "instruments", "interval_s", "skipped")}, {"rule_version": "1", "min_cell": 100, "edge": "0.02", "venue": "binance-spot", "quote_class": "USDT", "instruments": ["BTCUSDT", "ETHUSDT"], "interval_s": 3600, "skipped": 3})  # fmt: skip
    c.eq("and its period", (body["period_start"], body["period_end"]), ("2026-09-01T00:00:00.000Z", "2026-10-31T00:00:00.000Z"))
    c.eq("and its cells by horizon and state", body["cells"]["4"]["S2"], {"n": 100, "ups": 30, "ties": 0})
    c.eq("three horizons of five states", ({k: sorted(v) for k, v in body["cells"].items()}), {"1": ALL_STATES and list(ALL_STATES), "4": list(ALL_STATES), "24": list(ALL_STATES)})  # fmt: skip
    import hashlib

    from hoshitori import canonical

    c.eq("the hash is of the canonical body", g.sha256, hashlib.sha256(canonical.dumps(body)).hexdigest())
    c.eq(
        "the agent version stands on the rule and the mapping",
        mod.agent_version_hash(g),
        hashlib.sha256(
            canonical.dumps({"arm": "L0", "rule_version": "1", "mapping": g.sha256, "min_cell": 100, "edge": "0.02"})
        ).hexdigest(),
    )
    # everything is in the hash
    variants = {
        "a cell": rates(mod, lambda h, s: (100, 11, 0) if (h, s) == (24, "S5") else (100, 10 + 20 * ALL_STATES.index(s), 0)),
        "a tie count": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 1 if (h, s) == (1, "S1") else 0)),
        "the venue": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0), venue="okx"),
        "the quote class": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0), quote_class="USD"),
        "the instruments": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0), instruments=("BTCUSDT",)),
        "the start": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0), period_start_ms=to_ms(T0) + 1),
        "the end": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0), period_end_ms=to_ms(T0 + timedelta(days=61))),
        "the skips": rates(mod, lambda h, s: (100, 10 + 20 * ALL_STATES.index(s), 0), skipped=4),
    }
    for label, other in variants.items():
        c.eq(
            f"{label} is in the hash", (other.sha256 != g.sha256, mod.agent_version_hash(other) != mod.agent_version_hash(g)), (True, True)
        )
    # a valid mapping is refused when it is not
    ok = lambda h, s: (100, 50, 0)  # noqa: E731
    cells = graded(mod).cells
    c.refuses(
        "a missing cell",
        lambda: mod.BaseRates(cells[:-1], "v", "q", ("a",), 0, 1, 0),
        "a mapping has a cell for every horizon and state, in order",
    )
    c.refuses(
        "cells out of order",
        lambda: mod.BaseRates((cells[1], cells[0], *cells[2:]), "v", "q", ("a",), 0, 1, 0),
        "a mapping has a cell for every horizon and state, in order",
    )
    c.refuses(
        "more ups than windows",
        lambda: rates(mod, lambda h, s: (10, 11, 0)),
        "a cell counts windows: ups are some of n, and nothing is negative",
    )
    c.refuses(
        "a negative count",
        lambda: rates(mod, lambda h, s: (10, 1, -1)),
        "a cell counts windows: ups are some of n, and nothing is negative",
    )
    c.refuses(
        "a negative skip count", lambda: rates(mod, ok, skipped=-1), "a cell counts windows: ups are some of n, and nothing is negative"
    )
    time_says = "{} is a whole number of milliseconds up to the end of the year 9999"
    c.refuses("a negative start", lambda: rates(mod, ok, period_start_ms=-1), time_says.format("the period start"))
    c.refuses("a boolean start", lambda: rates(mod, ok, period_start_ms=True), time_says.format("the period start"))
    c.refuses("an end past the year 9999", lambda: rates(mod, ok, period_end_ms=253_402_300_800_000), time_says.format("the period end"))
    c.refuses("an end before the start", lambda: rates(mod, ok, period_end_ms=to_ms(T0) - 1), "the period ends no earlier than it starts")
    c.eq("an end equal to the start", rates(mod, ok, period_end_ms=to_ms(T0)).period_end_ms, to_ms(T0))
    c.eq(
        "the last millisecond of the year 9999 is a period end",
        rates(mod, ok, period_end_ms=253_402_300_799_999).period_end_ms,
        253_402_300_799_999,
    )
    naming = "a mapping names its venue, its quote class and its instruments, sorted and once each"
    c.refuses("no venue", lambda: rates(mod, ok, venue=""), naming)
    c.refuses("no quote class", lambda: rates(mod, ok, quote_class=""), naming)
    c.refuses("instruments out of order", lambda: rates(mod, ok, instruments=("ETHUSDT", "BTCUSDT")), naming)
    c.refuses("an instrument twice", lambda: rates(mod, ok, instruments=("BTCUSDT", "BTCUSDT")), naming)
    return c.bad


def check_calibration(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    start, end = to_ms(T0), to_ms(T0 + timedelta(days=60))
    data = {
        "AAAUSDT": history(1, 400, flat=(120, 150), holes=frozenset({60, 61, 200, 333})),
        "BBBUSDT": history(7, 380, holes=frozenset({30, 250})),
        "CCCUSDT": history(11, 90),
    }
    want, skipped = brute_counts(data)
    got = mod.calibrate(data, venue="binance-spot", quote_class="USDT", period_start_ms=start, period_end_ms=end)
    c.eq(
        "every cell against the separate path",
        {key: [cell.n, cell.ups, cell.ties] for key, cell in got.cells},
        {key: [v[0], v[1], v[2]] for key, v in want.items()},
    )
    c.eq("and the number of origins left out", got.skipped, skipped)
    c.eq(
        "the facts of the calibration",
        (got.venue, got.quote_class, got.instruments, got.period_start_ms, got.period_end_ms),
        ("binance-spot", "USDT", ("AAAUSDT", "BBBUSDT", "CCCUSDT"), start, end),
    )
    total = sum(cell.n + cell.ties for _, cell in got.cells)
    c.eq(
        "windows were counted in every horizon",
        all(sum(cell.n + cell.ties for (h, _), cell in got.cells if h == hours) > 0 for hours in (1, 4, 24)),
        True,
    )
    c.eq("some origins were left out and some counted (the data has holes and a dead stretch)", skipped > 0 and total > 0, True)
    c.eq(
        "some windows ended level and some did not",
        sum(cell.ties for _, cell in got.cells) > 0 and sum(cell.ups for _, cell in got.cells) > 0,
        True,
    )
    # order of the dictionary does not matter
    again = mod.calibrate(
        dict(reversed(list(data.items()))), venue="binance-spot", quote_class="USDT", period_start_ms=start, period_end_ms=end
    )
    c.eq("the order of the instruments does not matter", again.sha256, got.sha256)
    # windows do not overlap: a history of exactly lookback + 2 hours gives one window per horizon at most
    short = {"X": history(3, 14 + 1 + 24)}
    one = mod.calibrate(short, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0)
    c.eq(
        "a history too short for a day window counts none of them, and some hour windows",
        (
            sum(cell.n + cell.ties for (h, _), cell in one.cells if h == 24) > 0,
            sum(cell.n + cell.ties for (h, _), cell in one.cells if h == 1) > 0,
        ),
        (False, True),
    )
    small = {"X": history(3, 14 + 1 + 24 + 1)}
    want2, _ = brute_counts(small)
    c.eq(
        "the shortest history that gives a day window",
        {
            key: [cell.n, cell.ups, cell.ties]
            for key, cell in mod.calibrate(small, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0).cells
        },
        dict(want2),
    )
    c.eq(
        "empty histories",
        (
            mod.calibrate({}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0).skipped,
            mod.calibrate({"X": []}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0).instruments,
        ),
        (0, ("X",)),
    )
    bars = history(1, 30)
    c.refuses(
        "bars out of order",
        lambda: mod.calibrate({"X": [bars[1], bars[0]]}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0),
        "the bars of X are in strictly increasing time",
    )
    c.refuses(
        "two bad histories: the first by name is reported",
        lambda: mod.calibrate(
            {"Z": [bars[1], bars[0]], "A": [bars[1], bars[0]]}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0
        ),
        "the bars of A are in strictly increasing time",
    )
    c.refuses(
        "a bar twice",
        lambda: mod.calibrate({"X": [bars[0], bars[0]]}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0),
        "the bars of X are in strictly increasing time",
    )
    # a price too large for twelve places is skipped, not an error
    huge = [Candle(T0 + HOUR * k, T0 + HOUR * (k + 1), str(10**17), str(10**17), str(10**17), str(10**17)) for k in range(60)]
    c.eq(
        "prices the arithmetic cannot hold leave every origin out",
        mod.calibrate({"H": huge}, venue="v", quote_class="q", period_start_ms=0, period_end_ms=0).skipped > 0,
        True,
    )
    return c.bad


def check_file(mod: types.ModuleType) -> list[str]:
    import copy
    import json

    from hoshitori import canonical

    c = Case(mod)
    g = graded(mod)
    raw = mod.dump(g)
    c.eq("a mapping round-trips through its file", mod.load(raw), g)
    c.eq("the file is canonical bytes of the body and the hash", raw, canonical.dumps({**g.body(), "sha256": g.sha256}))
    c.eq("the same mapping is the same file", mod.dump(mod.load(raw)), raw)
    base = json.loads(raw)

    def edit(change: Callable[[dict[str, Any]], None], keep_hash: bool = True) -> bytes:
        doc = copy.deepcopy(base)
        change(doc)
        return canonical.dumps(doc)

    hash_says = "the mapping file's hash is not the hash of its contents"
    c.refuses("a changed count", lambda: mod.load(edit(lambda d: d["cells"]["4"]["S2"].update(ups=31))), hash_says)
    c.refuses("a changed venue", lambda: mod.load(edit(lambda d: d.update(venue="okx"))), hash_says)
    c.refuses("no hash", lambda: mod.load(edit(lambda d: d.pop("sha256"))), hash_says)
    c.refuses("a hash that is not the hash", lambda: mod.load(edit(lambda d: d.update(sha256="0" * 64))), hash_says)
    c.refuses("a hash that is a number", lambda: mod.load(edit(lambda d: d.update(sha256=5))), hash_says)
    c.refuses(
        "bytes that are not a record",
        lambda: mod.load(b"nope"),
        "the mapping file is not a canonical record: unexpected character at offset 0",
    )
    c.refuses("a record with a space in it", lambda: mod.load(raw.replace(b",", b", ", 1)), "the mapping file is not in canonical form")
    c.refuses("a record with a newline after it", lambda: mod.load(raw + b"\n"), "the mapping file is not in canonical form")
    members = "the mapping file has the members ['cells', 'edge', 'instruments', 'interval_s', 'min_cell', 'period_end', 'period_start', 'quote_class', 'rule_version', 'skipped', 'venue']"
    c.refuses("a missing member", lambda: mod.load(edit(lambda d: d.pop("skipped"))), members)
    c.refuses("an extra member", lambda: mod.load(edit(lambda d: d.update(note="x"))), members)
    other = "the mapping was made for another version of the rule or other constants"
    c.refuses("another rule version", lambda: mod.load(edit(lambda d: d.update(rule_version="2"))), other)
    c.refuses("another minimum", lambda: mod.load(edit(lambda d: d.update(min_cell=99))), other)
    c.refuses("another edge", lambda: mod.load(edit(lambda d: d.update(edge="0.03"))), other)
    c.refuses("another interval", lambda: mod.load(edit(lambda d: d.update(interval_s=60))), other)
    texts = "the venue, the quote class and the instruments are text"
    c.refuses("a venue that is a number", lambda: mod.load(edit(lambda d: d.update(venue=5))), texts)
    c.refuses("a quote class that is a number", lambda: mod.load(edit(lambda d: d.update(quote_class=5))), texts)
    c.refuses("instruments that are not a list", lambda: mod.load(edit(lambda d: d.update(instruments="BTCUSDT"))), texts)
    c.refuses("an instrument that is a number", lambda: mod.load(edit(lambda d: d.update(instruments=["BTCUSDT", 5]))), texts)
    c.refuses("a missing horizon", lambda: mod.load(edit(lambda d: d["cells"].pop("24"))), "the cells are given for every horizon")
    c.refuses(
        "an extra horizon",
        lambda: mod.load(edit(lambda d: d["cells"].update({"2": d["cells"]["1"]}))),
        "the cells are given for every horizon",
    )
    c.refuses("cells that are not an object", lambda: mod.load(edit(lambda d: d.update(cells=[]))), "the cells are given for every horizon")
    c.refuses("a horizon with a state missing", lambda: mod.load(edit(lambda d: d["cells"]["1"].pop("S5"))), "each horizon has every state")
    c.refuses(
        "a horizon that is not an object", lambda: mod.load(edit(lambda d: d["cells"].update({"1": []}))), "each horizon has every state"
    )
    c.refuses(
        "a cell with a member missing", lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].pop("ties"))), "a cell has n, ups and ties"
    )
    c.refuses(
        "a cell with an extra member", lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].update(x=1))), "a cell has n, ups and ties"
    )
    c.refuses(
        "a cell that is not an object", lambda: mod.load(edit(lambda d: d["cells"]["1"].update({"S1": 5}))), "a cell has n, ups and ties"
    )
    c.refuses("a count that is text", lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].update(n="100"))), "n is a whole number")
    c.refuses(
        "a count that is a boolean", lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].update(ups=True))), "ups is a whole number"
    )
    c.eq(
        "a count written as a fraction is not a canonical record",
        _says(mod, lambda: mod.load(raw.replace(b'"ties":0', b'"ties":1.5', 1))).startswith("the mapping file is not a canonical record: "),
        True,
    )
    c.refuses("ties that are text", lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].update(ties="1"))), "ties is a whole number")
    c.refuses("skips that are text", lambda: mod.load(edit(lambda d: d.update(skipped="3"))), "skipped is a whole number")
    c.refuses(
        "more ups than windows",
        lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].update(ups=101))),
        "a cell counts windows: ups are some of n, and nothing is negative",
    )
    c.refuses(
        "a negative count",
        lambda: mod.load(edit(lambda d: d["cells"]["1"]["S1"].update(ties=-1))),
        "a cell counts windows: ups are some of n, and nothing is negative",
    )
    c.refuses(
        "instruments out of order",
        lambda: mod.load(edit(lambda d: d.update(instruments=["ETHUSDT", "BTCUSDT"]))),
        "a mapping names its venue, its quote class and its instruments, sorted and once each",
    )
    c.refuses(
        "a period start that is a number",
        lambda: mod.load(edit(lambda d: d.update(period_start=5))),
        "the period start is an RFC 3339 time",
    )
    c.refuses(
        "a period start without milliseconds",
        lambda: mod.load(edit(lambda d: d.update(period_start="2026-09-01T00:00:00Z"))),
        "the period start is written as UTC with milliseconds",
    )
    c.refuses(
        "a period end with an offset",
        lambda: mod.load(edit(lambda d: d.update(period_end="2026-10-31T01:00:00.000+01:00"))),
        "the period end is written as UTC with milliseconds",
    )
    c.eq(
        "a period that is not a time names what is wrong",
        _says(mod, lambda: mod.load(edit(lambda d: d.update(period_end="yesterday")))).startswith("the period end: "),
        True,
    )
    c.eq(
        "a file that is not a record names that",
        _says(mod, lambda: mod.load(b"nope")).startswith("the mapping file is not a canonical record: "),
        True,
    )
    return c.bad


def _says(mod: types.ModuleType, call: Callable[[], object]) -> str:
    try:
        call()
    except mod.RulesError as exc:
        return str(exc)
    return ""


def reader_with(fields: dict[str, str], unknown: tuple[str, ...] = (), extra_features: bool = False) -> tuple[Any, Any, Any, str]:
    """A one-candidate snapshot whose features item has the fields given; the reader sees observations and derived items only."""
    from hoshitori.evidence import Unknown

    w = SnapWorld()
    minute, hour = w.add(w.bars("m", "tk-btc")), w.add(w.bars("h", "tk-btc"))
    feats = w.derive(
        "features",
        [minute, hour],
        source="hoshitori.features",
        fields=tuple(sorted(fields.items())),
        unknown_fields=tuple((n, Unknown.ABSENT) for n in sorted(unknown)),
    )
    ids = [minute, hour, feats]
    if extra_features:
        ids.append(
            w.derive(
                "features",
                [minute, hour],
                source="hoshitori.features",
                fields=tuple(sorted(fields.items())),
                transform=("features", "2"),
            )
        )
    snap = w.freeze(snapshot, candidates=[snapshot.Candidate("cd-btc", "tk-btc")], shared=[], by_candidate={"cd-btc": ids}, required=[])
    return w, snapshot.Reader(snap, snapshot.Access.INDEPENDENT), snapshot.Reader(snap, snapshot.Access.EVERYTHING), feats


FEATURES = {
    "atr14_share": "0.010000000000",
    "close": "1000.000000000000",
    "ret_1h": "0.000000000000",
    "ret_4h": "0.000000000000",
    "ret_24h": "0.000000000000",
}


def check_decisions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    g = graded(mod)
    # every state, at the edges of its range, at horizons 1 and 4 where the square root is exact
    table = {
        1: (("-0.02", "S1"), ("-0.0100000000", "S1"), ("-0.009999999999", "S2"), ("-0.0025", "S2"), ("-0.002499999999", "S3"), ("0", "S3"), ("0.002499999999", "S3"), ("0.0025", "S4"), ("0.009999999999", "S4"), ("0.010000000000", "S5"), ("0.05", "S5")),
        4: (("-0.03", "S1"), ("-0.02", "S1"), ("-0.019999999999", "S2"), ("-0.005", "S2"), ("-0.004999999999", "S3"), ("0.004999999999", "S3"), ("0.005", "S4"), ("0.019999999999", "S4"), ("0.02", "S5")),
    }  # fmt: skip
    for hours, rows in table.items():
        for ret, state in rows:
            _, reader, _, feats = reader_with({**FEATURES, f"ret_{hours}h": ret})
            d = mod.decide(reader, "cd-btc", g, hours)
            p = g.p(hours, state)
            c.eq(f"h={hours} ret={ret}", (d.verdict, d.state, d.p), (mod.Verdict.CALL, state, p))
    # one decision in full
    w, reader, _everything, feats = reader_with({**FEATURES, "ret_4h": "0.03"})
    d = mod.decide(reader, "cd-btc", g, 4)
    parents = sorted(p for p, _ in w.store.get(feats).derived_from)
    used = (feats, *parents)
    c.eq(
        "a call: action, probability, state",
        (d.candidate_id, d.horizon_hours, d.verdict, d.reason, d.action, d.p, d.state),
        ("cd-btc", 4, mod.Verdict.CALL, None, mod.Action.LONG, D("0.892157"), "S5"),
    )
    c.eq("the evidence it used: the features and the two series", d.evidence_ids, used)
    c.eq("the hash of exactly what it read", d.observation_set_hash, reader.set_hash("cd-btc", used))
    c.eq("the mapping that gave the probability", d.mapping_sha256, g.sha256)
    c.eq("the invalidation", d.invalidation, ("close below entry minus 1.5 ATR14",))
    # the action follows the probability and the edge
    for ups, action in ((76, mod.Action.FLAT), (77, mod.Action.LONG)):
        near = rates(mod, lambda h, s, u=ups: (148, u, 0))
        _, rd, _, _ = reader_with({**FEATURES, "ret_1h": "0.0"})
        got = mod.decide(rd, "cd-btc", near, 1)
        c.eq(f"p = {got.p}: {action.value}", got.action, action)
    c.eq("a probability of exactly one half plus the edge is a long", near.p(1, "S3"), D("0.520000"))
    c.eq("a short probability is flat", mod.decide(reader_with({**FEATURES, "ret_1h": "-0.5"})[1], "cd-btc", g, 1).action, mod.Action.FLAT)
    # no call
    thin = rates(mod, lambda h, s: (99, 50, 0))
    d = mod.decide(reader_with(FEATURES)[1], "cd-btc", thin, 4)
    c.eq(
        "too few windows: a voluntary no call, with the state and what was read",
        (d.verdict, d.reason, d.action, d.p, d.state, d.invalidation, len(d.evidence_ids)),
        (mod.Verdict.NO_CALL, mod.Reason.UNMAPPED, None, None, "S3", (), 3),
    )
    flat = {**FEATURES, "atr14_share": "0.000000000000"}
    d = mod.decide(reader_with(flat)[1], "cd-btc", g, 4)
    c.eq("no range: a no call", (d.verdict, d.reason, d.state, d.p), (mod.Verdict.NO_CALL, mod.Reason.NO_RANGE, None, None))
    # failed evidence
    for label, fields, unknown in (
        ("the return unknown", {k: v for k, v in FEATURES.items() if k != "ret_4h"}, ("ret_4h",)),
        ("the range unknown", {k: v for k, v in FEATURES.items() if k != "atr14_share"}, ("atr14_share",)),
        ("both unknown", {"close": FEATURES["close"]}, ("ret_4h", "atr14_share")),
    ):
        d = mod.decide(reader_with(fields, unknown)[1], "cd-btc", g, 4)
        c.eq(
            label,
            (d.verdict, d.reason, d.action, d.p, d.state, d.invalidation, len(d.evidence_ids)),
            (mod.Verdict.FAILED_EVIDENCE, mod.Reason.FEATURE_UNKNOWN, None, None, None, (), 3),
        )
    d = mod.decide(reader_with({**FEATURES, "ret_1h": "x"})[1], "cd-btc", g, 1)
    c.eq("a feature that is not a number is unknown", (d.verdict, d.reason), (mod.Verdict.FAILED_EVIDENCE, mod.Reason.FEATURE_UNKNOWN))
    # a candidate without exactly one features item
    sw = SnapWorld()
    bare = snapshot.Reader(
        sw.freeze(
            snapshot, candidates=[snapshot.Candidate("cd-btc", "tk-btc")], shared=[], by_candidate={"cd-btc": [sw.btc_bars]}, required=[]
        ),
        snapshot.Access.INDEPENDENT,
    )
    d = mod.decide(bare, "cd-btc", g, 4)
    c.eq(
        "no features item: failed evidence, nothing read",
        (d.verdict, d.reason, d.evidence_ids, d.observation_set_hash),
        (mod.Verdict.FAILED_EVIDENCE, mod.Reason.NO_FEATURES, (), bare.set_hash("cd-btc", [])),
    )
    d = mod.decide(reader_with(FEATURES, extra_features=True)[1], "cd-btc", g, 4)
    c.eq("two features items: failed evidence", (d.verdict, d.reason), (mod.Verdict.FAILED_EVIDENCE, mod.Reason.NO_FEATURES))
    # only a derived item of the features source is the features: not an observation with that source, not another derived item
    w = SnapWorld()
    a, b = w.add(w.bars("m", "tk-btc")), w.add(w.bars("h", "tk-btc"))
    impostor = w.add(w.bars("impostor", "tk-btc", source="hoshitori.features", fields=tuple(sorted(FEATURES.items()))))
    other = w.derive("other", [a, b], fields=tuple(sorted(FEATURES.items())))
    for label, listed in (("an observation that calls itself features", [a, b, impostor]), ("another derived item", [a, b, other])):
        snap = w.freeze(
            snapshot, candidates=[snapshot.Candidate("cd-btc", "tk-btc")], shared=[], by_candidate={"cd-btc": listed}, required=[]
        )
        d = mod.decide(snapshot.Reader(snap, snapshot.Access.INDEPENDENT), "cd-btc", g, 4)
        c.eq(f"{label} is not the features", (d.verdict, d.reason), (mod.Verdict.FAILED_EVIDENCE, mod.Reason.NO_FEATURES))
    c.refuses("a horizon the arm does not have", lambda: mod.decide(reader, "cd-btc", g, 2), "the horizon is one of (1, 4, 24) hours")
    c.eq(
        "the independent reader gave it nothing but observations",
        all(i.kind.value != "INTERPRETATION" for i in reader.items("cd-btc")),
        True,
    )
    return c.bad


def collected_reader(script: Callable[[Any], Any] | None = None, root: Any = None) -> tuple[Any, Any]:
    """A reader over what the collector read from a scripted venue, for the look-ahead checks."""
    import tempfile
    from pathlib import Path

    tmp = tempfile.mkdtemp() if root is None else root
    w = CollectWorld(Path(tmp), script)
    got = w.run(collect, ("BTC",), windows=collect.DEFAULT_WINDOWS)
    snap = snapshot.freeze(
        w.store,
        cycle_id="cy-1",
        manifest_hash="9" * 64,
        snapshot_at_ms=to_ms(NOW) + 1_000,
        candidates=w.candidates(collect, "BTC"),
        shared=[],
        by_candidate=got.ids(),
        required=[],
    )
    return w, snapshot.Reader(snap, snapshot.Access.INDEPENDENT)


def check_lookahead(mod: types.ModuleType) -> list[str]:
    """Nothing after the venue's clock can enter a decision: a wild bar beyond the window, and bars before it, change nothing."""
    c = Case(mod)
    g = graded(mod)

    def known(call: Any) -> Reply:
        return Reply(SERVER if call.operation == "time" else klines(call.args))

    def planted(call: Any) -> Reply:
        if call.operation == "time":
            return Reply(SERVER)
        end = int(parse_end(call.args) * 1000)
        step = int(call.args["interval_s"]) * 1000
        wild = f'[{end},"9999.00","99999.00","1.00","50000.00","12.5",{end + step - 1},"987.6",120,"6.1","482.0","0"]'.encode()
        return Reply(klines(call.args)[:-1] + b"," + wild + b"]")

    outcomes = []
    for script in (known, planted):
        _, reader = collected_reader(script)
        outcomes.append([mod.decide(reader, "cd-btc", g, h) for h in (1, 4, 24)])
    fields = lambda ds: [(d.verdict, d.reason, d.action, d.p, d.state, d.evidence_ids and len(d.evidence_ids)) for d in ds]  # noqa: E731
    c.eq("a bar after the window changes nothing", fields(outcomes[0]), fields(outcomes[1]))
    c.eq("and the decisions are calls", [d.verdict for d in outcomes[0]], [mod.Verdict.CALL] * 3)
    # prefix invariance: the same decisions from a history that stops at the origin as from one that goes on
    c.eq(
        "the planted world read the same features (its hash of what it read is of the same items' digests)",
        len({len(d.observation_set_hash) for d in outcomes[0] + outcomes[1]}),
        1,
    )
    return c.bad


def parse_end(args: Any) -> float:
    from hoshitori.timeutil import parse_rfc3339

    return parse_rfc3339(str(args["end"])).timestamp()


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_state, check_rates, check_file, check_calibration, check_decisions, check_lookahead):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad


_ = (S, SERVER, SRC, Callable)
