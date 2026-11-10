"""A transport that answers from recorded replies, and a caller that never sleeps."""

from __future__ import annotations

import datetime as dt
import json
import time
from pathlib import Path
from typing import Any

from exit_liquidity_check.transport import Caller, HttpReply

FIX = Path(__file__).parent / "fixtures"
NOW = dt.datetime(2026, 11, 9, 12, 0, tzinfo=dt.UTC)


def recorded_depth() -> Any:
    return json.loads((FIX / "dexscreener_bonk.json").read_text())["payload"]


def recorded_quotes() -> tuple[Any, Any]:
    rec = json.loads((FIX / "jupiter_bonk.json").read_text())
    return rec["reference"], rec["sized"]


class FakeTransport:
    """Answers by host. ``dex`` and ``jup`` are lists of replies used in order (the last repeats); a reply is a payload, an int status, or an
    exception to raise."""

    live = True

    def __init__(self, dex: list[Any] | None = None, jup: list[Any] | None = None) -> None:
        self.dex = dex if dex is not None else [recorded_depth()]
        self.jup = jup if jup is not None else list(recorded_quotes())
        self.urls: list[str] = []
        self.used = {"dex": 0, "jup": 0}

    def __call__(self, method: str, url: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpReply:
        self.urls.append(url)
        kind = "dex" if "dexscreener" in url else "jup"
        replies = self.dex if kind == "dex" else self.jup
        reply = replies[min(self.used[kind], len(replies) - 1)]
        self.used[kind] += 1
        if isinstance(reply, BaseException):
            raise reply
        if isinstance(reply, int):
            return HttpReply(reply, {}, b"{}")
        if isinstance(reply, bytes):
            return HttpReply(200, {}, reply)
        return HttpReply(200, {}, json.dumps(reply).encode())


def caller_for(transport: FakeTransport, *, cooldowns: dict[str, float] | None = None) -> Caller:
    return Caller(
        transport,
        now=NOW,
        deadline=time.monotonic() + 600,
        sleep=lambda _s: None,
        jitter=lambda cap: 0.0,
        cooldowns={} if cooldowns is None else cooldowns,
    )
