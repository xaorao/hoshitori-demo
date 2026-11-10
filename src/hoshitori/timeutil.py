"""Time values: every instant is UTC with an explicit offset; naive values are rejected.

The verdict of a verifier must not depend on the machine's time zone. A spike verifier changed its answer
with the local zone because it accepted a timestamp without an offset, so this
module refuses to guess: a string without ``Z`` or a numeric offset is an error, and a naive ``datetime`` is
an error.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_RFC3339 = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})T(?P<h>\d{2}):(?P<mi>\d{2}):(?P<s>\d{2})"
    r"(?:\.(?P<frac>\d{1,9}))?(?P<tz>Z|[+-]\d{2}:\d{2})$",
    re.ASCII,  # \d must not match other scripts' digits: the Node verifier reads ASCII only
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


class TimeError(ValueError):
    """A time value was malformed, naive or out of range."""


def ensure_utc(value: datetime) -> datetime:
    """Return ``value`` converted to UTC; refuse a naive datetime."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise TimeError("naive datetime is not allowed; attach a time zone")
    return value.astimezone(UTC)


def parse_rfc3339(text: str) -> datetime:
    """Parse an RFC 3339 timestamp with an explicit offset into an aware UTC datetime."""
    match = _RFC3339.fullmatch(text)
    if match is None:
        raise TimeError(f"not an RFC 3339 timestamp with an explicit offset: {text!r}")
    parts = match.groupdict()
    frac = (parts["frac"] or "0").ljust(9, "0")[:9]
    micro = int(frac[:6])
    try:
        moment = datetime(
            int(parts["y"]),
            int(parts["mo"]),
            int(parts["d"]),
            int(parts["h"]),
            int(parts["mi"]),
            int(parts["s"]),
            micro,
            tzinfo=UTC,
        )
    except ValueError as exc:  # month 13, day 31 in a 30-day month, second 60, ...
        raise TimeError(f"out-of-range timestamp: {text!r}") from exc
    if moment < _EPOCH:  # the Node verifier reads no instant before the epoch, so neither does this one
        raise TimeError(f"out-of-range timestamp: {text!r}")
    tz = parts["tz"]
    if tz != "Z":
        sign = 1 if tz[0] == "+" else -1
        hours, minutes = int(tz[1:3]), int(tz[4:6])
        if hours > 23 or minutes > 59:
            raise TimeError(f"out-of-range offset: {text!r}")
        try:
            moment -= sign * timedelta(hours=hours, minutes=minutes)
        except OverflowError as exc:  # an offset that carries the instant out of the years the datetime type holds
            raise TimeError(f"out-of-range timestamp: {text!r}") from exc
        if moment < _EPOCH:  # the instant, not only the written date, lies from the epoch to the year 9999
            raise TimeError(f"out-of-range timestamp: {text!r}")
    return moment


def format_rfc3339_ms(value: datetime) -> str:
    """Format an aware datetime as ``YYYY-MM-DDTHH:MM:SS.mmmZ`` (millisecond precision, truncated)."""
    moment = ensure_utc(value)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"
