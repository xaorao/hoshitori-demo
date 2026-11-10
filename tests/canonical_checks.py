"""Checks of the canonical form that can be run against the real module or against a mutant of it."""

from __future__ import annotations

import json
import types
from pathlib import Path

VECTORS = json.loads((Path(__file__).parent / "vectors" / "canonical_record.json").read_text())["vectors"]


def failures(mod: types.ModuleType) -> list[str]:
    """Names of the checks that fail for ``mod``; an empty list means every check passed."""
    bad: list[str] = []
    error = mod.CanonicalError

    def expect_reject(label: str, func, *args) -> None:  # type: ignore[no-untyped-def]
        try:
            func(*args)
        except error:
            return
        except Exception as exc:  # any other exception is also a failure of the contract
            bad.append(f"{label}: raised {type(exc).__name__}")
            return
        bad.append(f"{label}: accepted")

    for v in VECTORS:
        raw = bytes.fromhex(v["input_hex"])
        try:
            out = mod.canonicalize(raw)
        except error:
            if v["expect_hex"] is not None:
                bad.append(f"vector {v['id']}: rejected")
        except Exception as exc:
            bad.append(f"vector {v['id']}: raised {type(exc).__name__}")
        else:
            if v["expect_hex"] is None:
                bad.append(f"vector {v['id']}: accepted")
            elif out.hex() != v["expect_hex"]:
                bad.append(f"vector {v['id']}: wrong output")

    bad_values: dict[str, object] = {
        "float": 1.5,
        "unsafe-int": 2**53,
        "tuple": (1,),
        "int-key": {1: "a"},
        "non-nfc": "e" + chr(0x301),
        "lone-surrogate": chr(0xD800),
    }
    for label, value in bad_values.items():
        expect_reject(f"in-memory {label}", mod.dumps, {"v": value})
    expect_reject("in-memory lone surrogate key", mod.dumps, {"k" + chr(0xD800): 1})
    expect_reject("list at top", mod.dumps, [])

    def nested(levels: int) -> dict:  # type: ignore[type-arg]
        value: dict = {}  # type: ignore[type-arg]
        for _ in range(levels - 1):
            value = {"a": value}
        return value

    try:
        mod.dumps(nested(64))
    except Exception:
        bad.append("depth 64 rejected")
    expect_reject("depth 65", mod.dumps, nested(65))
    # UTF-16 order puts the surrogate pair of U+1F600 (0xD83D...) before U+E000; code point order puts it after.
    if mod.dumps({"\ue000": 1, "\U0001f600": 2}) != '{"\U0001f600":2,"\ue000":1}'.encode():
        bad.append("utf-16 key order")
    expect_reject("parse_record of an array", mod.parse_record, b"[]")
    if mod.dumps({"s": "\b\x01"}) != b'{"s":"\\b\\u0001"}':
        bad.append("escapes")
    bad += repertoire(mod)
    bad += runtime_version(mod)
    return bad


# The record profile admits only code points assigned in Unicode 15.0 (finding D-NFC): U+105D2 U+0307 is NFC under Python's Unicode 15.0
# tables, but Node on Unicode 17.0 composes it to U+105C9, so the two verifiers once disagreed about the same record.
EXOTIC = "\U000105d2\u0307"
UNASSIGNED = "code point U+{:04X} is not assigned in Unicode 15.0"
RUNTIME_TOO_OLD = "Unicode 15.0 or later is needed to check NFC; this runtime has an older version"


def _refusal(mod: types.ModuleType, func, *args) -> str:  # type: ignore[no-untyped-def]
    """The message of the CanonicalError ``func`` raises, ``<accepted>`` or the name of any other exception."""
    try:
        func(*args)
    except mod.CanonicalError as exc:
        return str(exc)
    except Exception as exc:
        return f"<raised {type(exc).__name__}>"
    return "<accepted>"


def repertoire(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []

    def refused(label: str, got: str, code: int) -> None:
        if got != UNASSIGNED.format(code):
            bad.append(f"{label}: {got!r}, expected {UNASSIGNED.format(code)!r}")

    refused("dumps of U+105D2 U+0307", _refusal(mod, mod.dumps, {"v": EXOTIC}), 0x105D2)
    refused("parse of U+105D2 U+0307", _refusal(mod, mod.parse_record, ('{"v":"' + EXOTIC + '"}').encode()), 0x105D2)
    refused("parse of U+105D2 U+0307 spelled as escapes", _refusal(mod, mod.parse_record, b'{"v":"\\ud801\\uddd2\\u0307"}'), 0x105D2)
    refused("a key of U+105D2 U+0307", _refusal(mod, mod.dumps, {EXOTIC: 1}), 0x105D2)
    # code points assigned only after 15.0, alone: U+2FFC (15.1) and U+1FAE9 (16.0); noncharacters, which are never assigned
    for code in (0x2FFC, 0x1FAE9, 0x0378, 0x323B0, 0xFDD0, 0xFFFE, 0xFFFF, 0x10FFFE, 0x10FFFF):
        refused(f"dumps of U+{code:04X} alone", _refusal(mod, mod.dumps, {"v": chr(code)}), code)
        refused(f"parse of U+{code:04X} alone", _refusal(mod, mod.parse_record, ('{"v":"' + chr(code) + '"}').encode()), code)
    refused("the first unassigned code point of a string is named", _refusal(mod, mod.dumps, {"v": "ok\u2ffc\U0001fae9"}), 0x2FFC)
    # code points assigned in 15.0 at the edges of their ranges, and characters new in 15.0, are accepted
    for code in (0x0377, 0x037A, 0xE000, 0x1E030, 0x1E08F, 0x31350, 0x323AF, 0x1F600, 0xE01EF, 0x10FFFD):
        text = ("a" if code == 0x1E08F else "") + chr(code)
        if _refusal(mod, mod.dumps, {"v": text}) != "<accepted>":
            bad.append(f"U+{code:04X} (assigned in 15.0) is refused: {_refusal(mod, mod.dumps, {'v': text})}")
    # the rules apply in a fixed order, the same in both verifiers: lone surrogates, then the repertoire, then NFC
    if _refusal(mod, mod.dumps, {"v": "\u2ffc" + chr(0xD800)}) != "lone surrogate":
        bad.append("a lone surrogate is not reported before an unassigned code point")
    refused("an unassigned code point is reported before a missing NFC", _refusal(mod, mod.dumps, {"v": "e\u0301\u2ffc"}), 0x2FFC)
    if _refusal(mod, mod.dumps, {"v": "e\u0301"}) != "string is not NFC":
        bad.append("a string of assigned code points that is not NFC is not reported as such")
    # the repertoire is the table, never the running unicodedata: a newer Python that has assigned U+2FFC must still refuse it
    running = mod.unicodedata
    mod.unicodedata = types.SimpleNamespace(
        normalize=running.normalize, category=lambda c: "So" if c == "\u2ffc" else running.category(c), unidata_version="15.1.0"
    )
    try:
        refused("U+2FFC on a runtime that has assigned it", _refusal(mod, mod.dumps, {"v": "\u2ffc"}), 0x2FFC)
    finally:
        mod.unicodedata = running
    return bad


def runtime_version(mod: types.ModuleType) -> list[str]:
    """A runtime whose Unicode is older than 15.0 refuses to check strings at all; 15.0 and later check them."""
    bad: list[str] = []
    if not hasattr(mod, "RUNTIME_UNICODE") or not hasattr(mod, "UnicodeVersionError"):
        return ["the module does not check the Unicode version of the runtime it runs on"]
    saved = mod.RUNTIME_UNICODE
    try:
        for version, refuses in (("14.0.0", True), ("9.0.0", True), ("15.0.0", False), ("15.1.0", False), ("16.0.0", False)):
            mod.RUNTIME_UNICODE = version
            for label, func, args in (("dumps", mod.dumps, ({"a": "b"},)), ("parse_record", mod.parse_record, (b'{"a":"b"}',))):
                try:
                    func(*args)
                    got = "<accepted>"
                except mod.UnicodeVersionError as exc:
                    got = str(exc)
                except Exception as exc:
                    got = f"<raised {type(exc).__name__}: {exc}>"
                wanted = RUNTIME_TOO_OLD if refuses else "<accepted>"
                if got != wanted:
                    bad.append(f"{label} on a runtime with Unicode {version}: {got!r}, expected {wanted!r}")
    finally:
        mod.RUNTIME_UNICODE = saved
    if issubclass(mod.UnicodeVersionError, mod.CanonicalError):
        bad.append("an old runtime is reported as a bad record")
    return bad
