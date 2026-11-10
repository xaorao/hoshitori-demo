from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest

from hoshitori.clock import ManualClock, SystemClock
from hoshitori.timeutil import TimeError, ensure_utc, format_rfc3339_ms, parse_rfc3339


def test_parse_z_and_offset_agree() -> None:
    assert parse_rfc3339("2026-10-29T12:00:00Z") == parse_rfc3339("2026-10-29T21:00:00+09:00")
    assert parse_rfc3339("2026-10-29T12:00:00.250Z").microsecond == 250_000


def test_naive_timestamp_is_rejected() -> None:
    # a verdict must not depend on the verifier's time zone.
    with pytest.raises(TimeError):
        parse_rfc3339("2026-10-29T12:00:00")
    with pytest.raises(TimeError):
        ensure_utc(datetime(2026, 10, 29, 12, 0, 0))


@pytest.mark.parametrize(
    "text",
    [
        "2026-13-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-10-29T24:00:00Z",
        "2026-10-29T12:00:60Z",
        "2026-10-29 12:00:00Z",
        "2026-10-29T12:00:00+24:00",
        "2026-10-29T12:00:00+09:60",
        "",
        "yesterday",
    ],
)
def test_malformed_or_out_of_range_is_rejected(text: str) -> None:
    with pytest.raises(TimeError):
        parse_rfc3339(text)


def test_format_is_millisecond_utc_and_truncates() -> None:
    moment = datetime(2026, 10, 29, 21, 0, 0, 999_999, tzinfo=timezone(timedelta(hours=9)))
    assert format_rfc3339_ms(moment) == "2026-10-29T12:00:00.999Z"


def test_round_trip() -> None:
    moment = datetime(2026, 10, 29, 12, 34, 56, 789_000, tzinfo=UTC)
    assert parse_rfc3339(format_rfc3339_ms(moment)) == moment


def test_manual_clock_only_moves_when_told() -> None:
    clock = ManualClock(datetime(2026, 10, 29, 12, 0, tzinfo=UTC))
    first = clock.now()
    assert clock.now() == first
    clock.advance(timedelta(seconds=90))
    assert clock.now() == first + timedelta(seconds=90)
    with pytest.raises(TimeError):
        ManualClock(datetime(2026, 10, 29))


def test_system_clock_is_aware_utc() -> None:
    assert SystemClock().now().utcoffset() == timedelta(0)
