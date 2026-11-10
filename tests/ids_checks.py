"""Checks of the natural-key identifiers, runnable against a mutant of ``hoshitori.ids``."""

from __future__ import annotations

import hashlib
import re
import types

from hoshitori import canonical


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    a = mod.natural_id("tk", {"symbol": "BTC", "series": 1})
    if not re.fullmatch(r"tk[a-z2-7]{26}", a):
        bad.append(f"the shape of an id: {a!r}")
    if a != mod.natural_id("tk", {"series": 1, "symbol": "BTC"}):
        bad.append("the order of the key's members changes the id")
    if a != mod.natural_id("tk", {"symbol": "BTC", "series": 1}):
        bad.append("the same key gives two ids")
    for label, other in (
        ("another prefix", mod.natural_id("as", {"symbol": "BTC", "series": 1})),
        ("another value", mod.natural_id("tk", {"symbol": "ETH", "series": 1})),
        ("another number", mod.natural_id("tk", {"symbol": "BTC", "series": 2})),
        ("another member", mod.natural_id("tk", {"symbol": "BTC", "series": 1, "x": 1})),
        ("a key with a member less", mod.natural_id("tk", {"symbol": "BTC"})),
    ):
        if other == a:
            bad.append(f"{label} gives the same id")
    expected = hashlib.sha256(canonical.dumps({"symbol": "BTC", "series": 1})).digest()
    import base64

    if a[2:] != base64.b32encode(expected).decode().lower().rstrip("=")[:26]:
        bad.append("the id is not the base32 of the SHA-256 of the canonical key")
    if mod.natural_id("tk", {})[:2] != "tk" or len(mod.natural_id("tk", {})) != 28:
        bad.append("an empty key")
    try:
        if mod.natural_id("abcd", {"k": "v"})[:4] != "abcd":
            bad.append("a four-letter prefix")
    except ValueError:
        bad.append("a four-letter prefix is refused")
    for prefix in ("", "a", "abcde", "TK", "t1", "t-", "tk ", "t k"):
        try:
            mod.natural_id(prefix, {"k": "v"})
            bad.append(f"the prefix {prefix!r} is accepted")
        except ValueError:
            pass
    for prefix in ("ab", "abc", "abcd"):
        try:
            mod.natural_id(prefix, {"k": "v"})
        except ValueError:
            bad.append(f"the prefix {prefix!r} is refused")
    try:
        mod.natural_id("tk", {"k": 1.5})  # type: ignore[dict-item]
        bad.append("a float in a key is accepted")
    except ValueError:
        pass
    if mod.natural_id("tk", {"k": "é"}) != mod.natural_id("tk", {"k": "é"}):
        bad.append("a non-ASCII key")
    return bad
