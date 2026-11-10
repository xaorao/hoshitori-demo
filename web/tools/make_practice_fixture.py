"""Writes ``practice_books.json``: practice books made by the real read-model from a small set of recorded events, for ``practice_test.py``.

Run with the project's Python from the repository root: ``PYTHONPATH=. .venv/bin/python web/tools/make_practice_fixture.py``. The events are
test events, not a league's: the browser test uses them only to look at every part of the page (a round trip, a position held and marked, a
position never marked, a book stopped after a fall, a long curve). Nothing here is shown as a result of a run.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

from tests.practice_view_checks import DIGEST, FID_A, FID_B, MIN, T0, buy_details, rec, sale  # noqa: F401

from hoshitori import book, practice_view
from hoshitori.site_export import canonical_bytes
from hoshitori.view_models import to_json


def d(text: str) -> Decimal:
    return Decimal(text)


def base_book() -> list[dict]:
    name = "L0:BASE"
    out = [rec(name, book.Open("L0", "BASE", d("100000"), T0), T0)]
    out.append(rec(name, book.Buy("BTC", d("1.5"), d("50000"), d("75"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)))
    # a long wander of marks, so the curve is thinned
    price = Decimal(50000)
    for k in range(2, 260):
        price += Decimal((k * 37) % 211) - Decimal(100)
        out.append(rec(name, book.Mark((("BTC", price),), T0 + k * MIN), T0 + k * MIN))
    out.append(
        rec(name, book.Sell("BTC", d("1.5"), d("50620"), d("75.93"), T0 + 261 * MIN), T0 + 261 * MIN, FID_A, sale("TARGET", T0 + 261 * MIN))
    )
    out.append(rec(name, book.Buy("ETH", d("20"), d("2400"), d("48"), T0 + 262 * MIN), T0 + 262 * MIN, FID_B, buy_details(T0 + 262 * MIN)))
    out.append(rec(name, book.Mark((("ETH", d("2412.5")),), T0 + 263 * MIN), T0 + 263 * MIN))
    return out


def stress_book() -> list[dict]:
    name = "L0:STRESS"
    return [
        rec(name, book.Open("L0", "STRESS", d("100000"), T0), T0),
        rec(name, book.Buy("SOL", d("100"), d("150"), d("37.5"), T0 + MIN), T0 + MIN, FID_B, buy_details(T0 + MIN)),
        # a second purchase at another price: the average never ends, and the page must show it cut, not in sixty digits
        rec(name, book.Buy("SOL", d("50"), d("151"), d("18.875"), T0 + 2 * MIN), T0 + 2 * MIN, FID_B, buy_details(T0 + 2 * MIN)),
        rec(name, book.Mark((("SOL", d("141")),), T0 + 3 * MIN), T0 + 3 * MIN),
        rec(name, book.Sell("SOL", d("150"), d("139.65"), d("52.36875"), T0 + 4 * MIN), T0 + 4 * MIN, FID_B, sale("STOP", T0 + 4 * MIN)),
        rec(name, book.Mark((), T0 + 5 * MIN), T0 + 5 * MIN),
    ]


def unmarked_book() -> list[dict]:
    name = "L0:DOUBLE_FEES"
    return [
        rec(name, book.Open("L0", "DOUBLE_FEES", d("100000"), T0), T0),
        rec(name, book.Buy("BTC", d("0.5"), d("50000"), d("50"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
    ]


def halted_book() -> list[dict]:
    name = "L1:BASE"
    return [
        rec(name, book.Open("L1", "BASE", d("1000"), T0), T0),
        rec(name, book.Buy("BTC", d("9"), d("100"), d("0"), T0 + MIN), T0 + MIN, FID_A, buy_details(T0 + MIN)),
        rec(name, book.Mark((("BTC", d("100")),), T0 + 2 * MIN), T0 + 2 * MIN),
        rec(name, book.Mark((("BTC", d("80")),), T0 + 3 * MIN), T0 + 3 * MIN),
    ]


def gapped_book() -> list[dict]:
    """Marks every minute that stop for two hours and start again: the curve must not join them as if the equity moved smoothly."""
    name = "L1x:BASE"
    out = [
        rec(name, book.Open("L1x", "BASE", d("100000"), T0), T0),
        rec(name, book.Buy("ETH", d("10"), d("2400"), d("24"), T0 + MIN), T0 + MIN, FID_B, buy_details(T0 + MIN)),
    ]
    for k, price in ((2, "2401"), (3, "2402.5"), (4, "2403"), (5, "2404.25"), (125, "2380")):
        out.append(rec(name, book.Mark((("ETH", d(price)),), T0 + k * MIN), T0 + k * MIN))
    return out


def main() -> None:
    records = [*base_book(), *stress_book(), *unmarked_book(), *halted_book(), *gapped_book()]
    books = practice_view.build(records)
    out = Path(__file__).with_name("practice_books.json")
    out.write_bytes(canonical_bytes(to_json(books)) + b"\n")
    print(f"{len(books)} books, {sum(len(b.trades) for b in books)} round trips, curves {[len(b.curve) for b in books]}: {out}")


if __name__ == "__main__":
    main()
