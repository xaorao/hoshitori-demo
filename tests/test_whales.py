"""Whales: wallet-state parsing and the cohort summary."""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from hoshitori import whales as w

A, B, C = "0x" + "a" * 40, "0x" + "B" * 40, "0x" + "c" * 40


def state(positions: list[tuple[str, str, str]], value: str = "1000000") -> bytes:
    return json.dumps(
        {
            "marginSummary": {"accountValue": value},
            "assetPositions": [{"position": {"coin": c, "szi": s, "positionValue": n}} for c, s, n in positions],
        }
    ).encode()


def test_parse_signs_notional_by_size() -> None:
    wallet = w.parse_state(A, state([("SOL", "-100", "15000"), ("BTC", "1", "79000")]))
    assert wallet.positions == {"SOL": Decimal(-15000), "BTC": Decimal(79000)} and wallet.account_value == Decimal(1000000)


def test_address_is_lowercased_and_checked() -> None:
    assert w.parse_state(B, state([])).address == B.lower()
    with pytest.raises(w.WhalesError):
        w.parse_state("0x12", state([]))


@pytest.mark.parametrize(
    "body",
    [b"x", b"{}", json.dumps({"marginSummary": {"accountValue": "NaN"}, "assetPositions": []}).encode(), state([("SOL", "x", "1")])],
)
def test_other_shapes_are_refused(body: bytes) -> None:
    with pytest.raises(w.WhalesError):
        w.parse_state(A, body)


def test_cohort_counts_and_notional() -> None:
    ws = [
        w.parse_state(A, state([("SOL", "-100", "15000")])),
        w.parse_state(B, state([("SOL", "200", "30000")])),
        w.parse_state(C, state([("ETH", "5", "11000")])),
    ]
    c = w.cohort(ws, "SOL")
    assert (c.wallets, c.holding, c.long, c.short) == (3, 2, 1, 1)
    assert (c.gross_long, c.gross_short, c.net) == (Decimal(30000), Decimal(15000), Decimal(15000))
    assert c.top_share == Decimal(30000) / Decimal(45000)


def test_empty_cohort_is_zero_not_error() -> None:
    c = w.cohort([], "SOL")
    assert (c.wallets, c.holding, c.net, c.top_share) == (0, 0, Decimal(0), Decimal(0))


def test_the_position_list_is_bounded_and_must_be_a_list() -> None:
    def many(n: int) -> bytes:
        return json.dumps(
            {
                "marginSummary": {"accountValue": "1"},
                "assetPositions": [{"position": {"coin": f"C{i}", "szi": "1", "positionValue": "1"}} for i in range(n)],
            }
        ).encode()

    assert len(w.parse_state(A, many(500)).positions) == 500
    for bad in (many(501), json.dumps({"marginSummary": {"accountValue": "1"}, "assetPositions": 5}).encode()):
        with pytest.raises(w.WhalesError, match="list of at most 500"):
            w.parse_state(A, bad)


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        ({}, "a position has a coin"),
        ({"position": {"coin": "SOL", "szi": "1"}}, "a position has a coin"),
        ({"position": {"coin": 5, "szi": "1", "positionValue": "1"}}, "a coin is a name"),
        ({"position": {"coin": "SOL", "szi": "x", "positionValue": "1"}}, "a size is a decimal"),
        ({"position": {"coin": "SOL", "szi": "1", "positionValue": "Infinity"}}, "a position value is a finite"),
    ],
)
def test_a_position_that_is_not_one_is_refused_with_what_is_wrong(entry: dict, message: str) -> None:
    body = json.dumps({"marginSummary": {"accountValue": "1"}, "assetPositions": [entry]}).encode()
    with pytest.raises(w.WhalesError, match=message):
        w.parse_state(A, body)


def test_the_account_value_must_be_a_number_and_non_utf8_is_refused() -> None:
    with pytest.raises(w.WhalesError, match="the account value is a decimal"):
        w.parse_state(A, json.dumps({"marginSummary": {"accountValue": "lots"}, "assetPositions": []}).encode())
    with pytest.raises(w.WhalesError, match="not a Hyperliquid"):
        w.parse_state(A, b"\xff\xfe")


def test_a_coin_held_twice_adds_up_and_the_names_of_the_coins_are_fixed() -> None:
    body = json.dumps(
        {
            "marginSummary": {"accountValue": "1"},
            "assetPositions": [
                {"position": {"coin": "SOL", "szi": "1", "positionValue": "10"}},
                {"position": {"coin": "SOL", "szi": "-1", "positionValue": "4"}},
            ],
        }
    ).encode()
    assert w.parse_state(A, body).positions == {"SOL": Decimal(6)}
    assert w.COINS == {"BTC": "BTC", "ETH": "ETH", "SOL": "SOL"}


def test_a_flat_account_holds_nothing_and_the_records_are_frozen() -> None:
    wallet = w.parse_state(A, state([("SOL", "0", "0")]))
    assert w.cohort([wallet], "SOL").holding == 0 and w.cohort([wallet], "SOL").top_share == Decimal(0)
    with pytest.raises(AttributeError):
        wallet.address = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        w.cohort([wallet], "SOL").long = 3  # type: ignore[misc]


def test_a_gross_of_zero_has_no_top_share_and_the_start_values_are_zero() -> None:
    c = w.cohort([w.parse_state(A, state([("SOL", "1", "100")]))], "SOL")
    assert (c.gross_long, c.gross_short, c.net, c.top_share) == (Decimal(100), Decimal(0), Decimal(100), Decimal(1))
    c = w.cohort([w.parse_state(A, state([("SOL", "-1", "100")]))], "SOL")
    assert (c.gross_long, c.gross_short, c.net) == (Decimal(0), Decimal(100), Decimal(-100))
    empty = w.cohort([w.parse_state(A, state([("ETH", "1", "5")]))], "SOL")
    assert (empty.gross_long, empty.gross_short, empty.net) == (Decimal(0), Decimal(0), Decimal(0))


def test_a_small_position_is_a_position_of_its_side() -> None:
    longs = w.cohort([w.parse_state(A, state([("SOL", "0.5", "0.5")]))], "SOL")
    assert (longs.long, longs.short, longs.gross_long, longs.top_share) == (1, 0, Decimal("0.5"), Decimal(1))
    shorts = w.cohort([w.parse_state(A, state([("SOL", "-0.5", "0.5")]))], "SOL")
    assert (shorts.long, shorts.short, shorts.gross_short, shorts.top_share) == (0, 1, Decimal("0.5"), Decimal(1))
    assert w.parse_state(A, state([("SOL", "0.5", "10")])).positions == {"SOL": Decimal(10)}
    assert w.parse_state(A, state([("SOL", "-0.5", "10")])).positions == {"SOL": Decimal(-10)}
