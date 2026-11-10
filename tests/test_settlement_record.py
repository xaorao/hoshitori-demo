"""The settlement record: the checks pass on the real module, every small change is noticed, and what the settlement rules give is
recorded and read back unchanged."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import settlement_record
from hoshitori.settlement import Bar, Directional, Oracle, OraclePair, settle_directional
from hoshitori.timeline import outcome_candle_open, reference_candle_open, to_ms
from tests.mutation_sweep import survivors
from tests.settlement_record_checks import DIGEST, SRC, failures


def test_real_module_passes_every_check() -> None:
    assert failures(settlement_record) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.settlement_record", "tests.settlement_record_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


ORIGIN = to_ms(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
HORIZON = 3_600_000


def pair(open_close: dict[int, Decimal], second: dict[int, Decimal]) -> OraclePair:
    def oracle(name: str, closes: dict[int, Decimal]) -> Oracle:
        bars = {k: Bar(k, v, v, v, v) for k, v in closes.items()}
        return Oracle(name, "BTCUSDT", "USDT", Decimal(1), None, ORIGIN + 2 * HORIZON, bars)

    return OraclePair("A", oracle("binance-spot", open_close), oracle("okx-spot", second))


@settings(max_examples=80, deadline=None)
@given(
    start=st.decimals(min_value="5", max_value="100000", places=2),
    move=st.integers(min_value=-300, max_value=300),
    drift=st.integers(min_value=-60, max_value=60),
)
def test_what_the_settlement_gives_is_recorded_and_read_back(start: Decimal, move: int, drift: int) -> None:
    """Whatever the rules answer for random prices (a rise, a fall, a tie, oracles that disagree), the record reads back to it."""
    end = start + Decimal(move) / 100
    opens = (reference_candle_open(ORIGIN), outcome_candle_open(ORIGIN, HORIZON))
    primary = {opens[0]: start, opens[1]: end}
    second = {opens[0]: start * (1 + Decimal(drift) / 1000), opens[1]: end * (1 + Decimal(drift) / 1000)}
    result = settle_directional(Directional("fc-1", ORIGIN, HORIZON, "USDT"), pair(primary, second), retries_exhausted=True)
    record = settlement_record.to_record(result, DIGEST, SRC, ORIGIN + 2 * HORIZON)
    assert settlement_record.from_record(record) == (result, DIGEST)
