"""Loader for the private venue recordings (``fixtures/venues_live``): byte-exact responses of the public market-data
endpoints, kept outside the public repository until the data rights are settled (contract section 16). Tests that need them skip, and say so.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

VENUES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "venues_live"


@dataclass(frozen=True)
class Recording:
    name: str
    url: str
    request_body: Any
    captured_at: datetime
    status: int
    headers: dict[str, str]
    body: bytes
    sha256: str


def recording(name: str) -> Recording:
    """The one recording named ``name`` (skips the test when the recordings are not on this machine)."""
    matches = sorted(VENUES_DIR.glob(f"{name}.*.json")) if VENUES_DIR.is_dir() else []
    if not matches:
        pytest.skip(f"the private venue recording {name!r} is not on this machine")
    assert len(matches) == 1, f"{name}: {len(matches)} recordings, expected one"
    raw = json.loads(matches[0].read_text())
    response = raw["response"]
    body = response["body"].encode()
    assert len(body) == response["bytes"], f"{name}: the stored body is not the recorded size"
    assert hashlib.sha256(body).hexdigest() == response["sha256"], f"{name}: the stored body is not the recorded bytes"
    return Recording(
        name=raw["name"],
        url=raw["request"]["url"],
        request_body=raw["request"]["body"],
        captured_at=datetime.fromtimestamp(raw["captured_at_ms"] / 1000, UTC),
        status=response["status"],
        headers=response["headers"],
        body=body,
        sha256=response["sha256"],
    )
