"""Checks of the Unicode 15.0 repertoire table (``hoshitori.unicode15``) that can be run against the real module or a mutant of it.

The table must be exactly the code points whose general category is not Cn in the Unicode Character Database 15.0.0, the Node verifier
must carry the same ranges, and both files must be what ``scripts/make_unicode_table.py`` writes.
"""

from __future__ import annotations

import functools
import importlib.util
import itertools
import re
import types
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE_TABLE = ROOT / "verifier-js" / "src" / "unicode15.mjs"
PYTHON_TABLE = ROOT / "src" / "hoshitori" / "unicode15.py"
GENERATOR = ROOT / "scripts" / "make_unicode_table.py"
# Unicode 15.0 designates 288,767 code points: 149,186 characters, 65 controls, 137,468 private-use code points and 2,048 surrogates.
ASSIGNED_COUNT = 288_767
_PAIR = re.compile(r"0x([0-9A-F]{4,6}), 0x([0-9A-F]{4,6})")


@functools.cache
def _generator() -> types.ModuleType:
    spec = importlib.util.spec_from_file_location("_make_unicode_table", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@functools.cache
def _from_running_unicodedata() -> tuple[tuple[int, int], ...]:
    return tuple(_generator().assigned_ranges())


def _ranges_in(text: str) -> list[tuple[int, int]]:
    return [(int(first, 16), int(last, 16)) for first, last in _PAIR.findall(text)]


def failures(mod: types.ModuleType) -> list[str]:
    """Names of the checks that fail for ``mod``; an empty list means every check passed."""
    bad: list[str] = []
    ranges = [tuple(r) for r in mod.ASSIGNED]
    if mod.UNICODE_VERSION != "15.0.0":
        bad.append(f"version is {mod.UNICODE_VERSION!r}")
    if not ranges or any(len(r) != 2 for r in ranges):
        return [*bad, "the table is not a list of pairs"]
    if ranges[0][0] != 0 or ranges[-1][1] > 0x10FFFF:
        bad.append(f"the table runs from {ranges[0][0]:#x} to {ranges[-1][1]:#x}")
    if any(first > last for first, last in ranges):
        bad.append("a range ends before it starts")
    for (_, last), (after, _) in itertools.pairwise(ranges):
        if not last < after - 1:
            bad.append(f"ranges out of order, overlapping or adjacent at {last:#x} and {after:#x}")
            break
    if sum(last - first + 1 for first, last in ranges) != ASSIGNED_COUNT:
        bad.append(f"{sum(last - first + 1 for first, last in ranges)} code points, Unicode 15.0 assigns {ASSIGNED_COUNT}")
    # fixed facts of Unicode 15.0, true whatever Python runs the checks
    for code, assigned in (
        (0x0377, True),
        (0x0378, False),
        (0xD800, True),  # a surrogate is designated (Cs); the profile refuses lone surrogates by its own rule
        (0xFDD0, False),  # a noncharacter is Cn
        (0xFFFE, False),
        (0x1E08F, True),  # COMBINING CYRILLIC SMALL LETTER BYELORUSSIAN-UKRAINIAN I, new in 15.0
        (0x323AF, True),  # the last ideograph of CJK Extension H, new in 15.0
        (0x323B0, False),
        (0x105D2, False),  # Todhri, assigned only in 16.0 (the D-NFC case)
        (0x2FFC, False),  # assigned only in 15.1
        (0x10FFFD, True),
        (0x10FFFE, False),
    ):
        if any(first <= code <= last for first, last in ranges) != assigned:
            bad.append(f"U+{code:04X} should {'' if assigned else 'not '}be assigned")
    if unicodedata.unidata_version == "15.0.0" and tuple(ranges) != _from_running_unicodedata():
        bad.append("the table is not the Cn complement of this Python's unicodedata 15.0.0")
    if _ranges_in(NODE_TABLE.read_text(encoding="utf-8")) != ranges:
        bad.append("verifier-js/src/unicode15.mjs holds other ranges")
    if 'export const UNICODE_VERSION = "15.0.0";' not in NODE_TABLE.read_text(encoding="utf-8"):
        bad.append("verifier-js/src/unicode15.mjs names another version")
    if unicodedata.unidata_version == "15.0.0":
        generated = list(_from_running_unicodedata())
        if PYTHON_TABLE.read_text(encoding="utf-8") != _generator().render_python(generated):
            bad.append("src/hoshitori/unicode15.py is not what scripts/make_unicode_table.py writes")
        if NODE_TABLE.read_text(encoding="utf-8") != _generator().render_node(generated):
            bad.append("verifier-js/src/unicode15.mjs is not what scripts/make_unicode_table.py writes")
    return bad
