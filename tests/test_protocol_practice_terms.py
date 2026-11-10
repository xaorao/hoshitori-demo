"""The terms a practice book runs under (fees, slippage, latency, sizing, risk limits) are part of the protocol and so of its hash."""

from __future__ import annotations

from decimal import Decimal

import pytest

from hoshitori import canonical, fills, practice, protocol, risk_kernel

ARGS = {
    "name": "t",
    "start_ms": 0,
    "every_ms": 3_600_000,
    "root_version": 1,
    "root_sha256": "a" * 64,
    "mappings": {"L0": "b" * 64},
    "watchlist": ("BTC",),
}


def test_the_standard_protocol_records_the_terms_and_reads_them_back() -> None:
    made = protocol.standard(**ARGS)
    assert made.practice_terms == protocol.PRACTICE_DEFAULTS
    read = protocol.load(protocol.dump(made))
    assert read.practice_terms == made.practice_terms and read.sha256 == made.sha256


def test_a_protocol_that_records_none_keeps_the_body_and_the_hash_it_had_before() -> None:
    old = protocol.standard(**ARGS, practice_terms=())
    assert "practice" not in old.body()
    assert protocol.load(protocol.dump(old)).practice_terms == ()
    assert old.sha256 != protocol.standard(**ARGS).sha256  # recording the terms is another protocol, never an edit of the first
    assert old.practice_values() == {k: Decimal(v) for k, v in protocol.PRACTICE_DEFAULTS}  # and its books ran under the defaults


def test_a_term_changed_in_the_file_is_not_the_protocol_whose_hash_it_carries() -> None:
    raw = protocol.dump(protocol.standard(**ARGS))
    record = canonical.parse_record(raw)
    record["practice"]["terms"]["fee_bps"] = "1"  # type: ignore[index]
    with pytest.raises(protocol.ProtocolError):
        protocol.load(canonical.dumps(record))


@pytest.mark.parametrize(
    "key, value",
    [
        ("fee_bps", "9000"),
        ("equity", "0"),
        ("request_weight", "1.5"),
        ("latency_ms", "1.5"),
        ("halt_at", "2"),
        ("slip_bps", "-1"),
        ("gross", "NaN"),
    ],
)
def test_terms_the_books_would_refuse_are_refused_by_the_protocol(key: str, value: str) -> None:
    terms = dict(protocol.PRACTICE_DEFAULTS)
    terms[key] = value
    with pytest.raises(protocol.ProtocolError):
        protocol.standard(**ARGS, practice_terms=tuple(terms.items()))


def test_missing_extra_or_misordered_terms_are_refused() -> None:
    terms = list(protocol.PRACTICE_DEFAULTS)
    for bad in (tuple(terms[:-1]), (*terms, ("extra", "1")), tuple(reversed(terms))):
        with pytest.raises(protocol.ProtocolError):
            protocol.standard(**ARGS, practice_terms=bad)


def test_the_books_run_under_the_terms_of_the_protocol() -> None:
    terms = dict(protocol.PRACTICE_DEFAULTS)
    terms.update(fee_bps="20", slip_bps="7", latency_ms="30000", per_token="0.04", equity="50000")
    made = protocol.standard(**ARGS, practice_terms=tuple(terms.items()))
    cfg = practice.config_for("L0", made.practice_values())
    assert cfg.costs == fills.Costs(Decimal(20), Decimal(7), Decimal(25), 30_000)
    assert cfg.limits.per_token == Decimal("0.04") and cfg.equity == Decimal(50000)


def test_the_defaults_are_the_values_the_books_always_used() -> None:
    cfg = practice.config_for("L0", protocol.standard(**ARGS).practice_values())
    assert (cfg.costs, cfg.limits, cfg.equity, cfg.request_weight) == (
        fills.Costs(),
        risk_kernel.Limits(),
        practice.OPENING_EQUITY,
        practice.REQUEST_WEIGHT,
    )
