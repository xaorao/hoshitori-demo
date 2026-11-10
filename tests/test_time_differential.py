"""Python and Node read every record time the same way: the same instant to the millisecond, or both refuse it (including the edges of
the calendar and of the offset, and a fraction of a millisecond, which the verifiers cut and never round)."""

from __future__ import annotations

import json
import subprocess

import pytest

from hoshitori import verify
from hoshitori.timeline import TimelineError, to_ms
from hoshitori.timeutil import TimeError, parse_rfc3339
from tests.support import NODE, VERIFIER_JS

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for the cross-language tests")

TIMES = [
    "2026-01-01T10:00:00Z",
    "2026-01-01T10:00:00.000Z",
    "2026-01-01T10:00:01.0005Z",
    "2026-01-01T10:00:01.0009999Z",
    "2026-01-01T10:00:00.0001Z",
    "2026-01-01T10:00:00.123456789Z",
    "2026-01-01T10:00:00+01:00",
    "2026-01-01T10:00:00-23:59",
    "2026-01-01T10:00:00+24:00",
    "2026-01-01T10:00:00+00:60",
    "9999-12-31T23:59:59-23:59",
    "9999-12-31T23:59:59Z",
    "0001-01-01T00:00:00+01:00",
    "0001-01-01T00:00:00Z",
    "0999-01-01T00:00:00Z",
    "1000-01-01T00:00:00+01:00",
    "1000-01-01T00:00:00-01:00",
    "9999-12-31T23:59:59-00:01",
    "9999-12-31T23:58:59-00:01",
    "1000-01-01T00:00:00Z",
    "1969-12-31T23:59:59Z",
    "1970-01-01T00:00:00Z",
    "1970-01-01T00:00:00+01:00",
    "\u0662\u0660\u0662\u0666-10-29T11:59:00Z",
    "2026-10-29T11:59:00.\u0660\u0660\u0661Z",
    "2026-02-30T00:00:00Z",
    "2026-13-01T00:00:00Z",
    "2026-01-01T24:00:00Z",
    "2026-01-01T10:00:60Z",
    "2026-01-01T10:00:00",
    "2026-01-01 10:00:00Z",
    "",
    "not a time",
]

NODE_SCRIPT = """
import { parseRfc3339 } from "%s/src/time.mjs";
let text = "";
for await (const chunk of process.stdin) text += chunk;
const out = JSON.parse(text).map((t) => { try { return String(parseRfc3339(t)); } catch (e) { return "ERR"; } });
process.stdout.write(JSON.stringify(out));
"""


def python_reads(text: str) -> str:
    try:
        return str(to_ms(verify._to_the_millisecond(parse_rfc3339(text))))
    except (TimeError, TimelineError):
        return "ERR"


def test_every_time_is_read_to_the_same_millisecond_or_refused_by_both() -> None:
    assert NODE is not None
    done = subprocess.run(
        [NODE, "--input-type=module", "-e", NODE_SCRIPT % VERIFIER_JS],
        input=json.dumps(TIMES),
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
        timeout=60,
    )
    node = json.loads(done.stdout)
    differences = [(t, python_reads(t), n) for t, n in zip(TIMES, node, strict=True) if python_reads(t) != n]
    assert differences == []
