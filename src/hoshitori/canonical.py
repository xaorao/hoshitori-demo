"""Canonical record form: RFC 8785 (JCS) restricted to the I-JSON subset the records use.

This is the *record profile* of ``LEDGER_SPEC`` section 2:

* the input is UTF-8 without a byte-order mark; invalid UTF-8 is rejected;
* the top-level value is an object; members are strings, booleans, ``null``, arrays, objects and integers within
  +/-(2**53 - 1); a number with a fraction or exponent, ``-0``, a leading zero or an integer outside the safe range
  is rejected (quantities are decimal *strings*, never numbers);
* object keys are unique after unescaping and sorted by UTF-16 code unit when serialised;
* every string, keys included, is free of lone surrogates, is made only of code points assigned in Unicode 15.0
  (``hoshitori.unicode15``), and is NFC; a string that breaks one of these is rejected, never repaired, so the bytes a
  producer hashed are the bytes it signed;
* nesting depth is at most 64;
* serialisation has no insignificant whitespace and RFC 8785 string escapes.

The repertoire is pinned because NFC is only as stable as the Unicode tables behind it. Unicode's normalization stability
policy keeps the NFC form of a string made only of characters assigned in a version the same in every later version, so two
runtimes on Unicode 15.0 or later agree on such a string; on a string with a code point assigned later they need not (finding
D-NFC: U+105D2 U+0307 is NFC under Python's Unicode 15.0 tables and not under Node's 17.0). The table is fixed in the
repository, never read from the running ``unicodedata``, and a runtime whose Unicode is older than 15.0 refuses to check a
string at all (``UnicodeVersionError``): its NFC could differ on the characters it does not know.

The module has its own strict parser. A runtime's ordinary JSON parser must never be used on text that is hashed
or compared: it cannot report duplicate keys or lone-surrogate escapes, and a runtime defect once returned wrong
property names for some documents (contract section 30).
"""

from __future__ import annotations

import bisect
import unicodedata
from typing import Final

from hoshitori import unicode15

JsonValue = None | bool | int | str | list["JsonValue"] | dict[str, "JsonValue"]

MAX_DEPTH: Final = 64
MAX_SAFE_INTEGER: Final = 2**53 - 1
RUNTIME_UNICODE: Final = unicodedata.unidata_version  # the version this runtime's NFC is built from
RUNTIME_TOO_OLD: Final = "Unicode 15.0 or later is needed to check NFC; this runtime has an older version"

_STARTS: Final = tuple(first for first, _ in unicode15.ASSIGNED)
_WHITESPACE: Final = " \t\n\r"
_DIGITS: Final = "0123456789"
_HEX: Final = "0123456789abcdefABCDEF"
_SHORT_ESCAPES: Final = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}
_OUTPUT_ESCAPES: Final = {
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0C: "\\f",
    0x0D: "\\r",
    0x22: '\\"',
    0x5C: "\\\\",
}


class CanonicalError(ValueError):
    """The input is not a valid record under the profile."""


class UnicodeVersionError(RuntimeError):
    """This runtime's Unicode is older than 15.0, so it cannot tell whether a string is NFC; no string is checked.

    Not a ``CanonicalError``: the record is not at fault, and no caller may report it as a bad record.
    """


def require_runtime_unicode() -> None:
    """Refuse to go on when the runtime's Unicode (``RUNTIME_UNICODE``, such as ``15.0.0``) is older than 15.0."""
    if int(RUNTIME_UNICODE.split(".")[0]) < 15:
        raise UnicodeVersionError(RUNTIME_TOO_OLD)


def decode_utf8(raw: bytes) -> str:
    """Strict UTF-8 decoding; a byte-order mark is an error."""
    if raw.startswith(b"\xef\xbb\xbf"):
        raise CanonicalError("byte-order mark")
    try:
        return raw.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise CanonicalError(f"invalid UTF-8: {exc.reason}") from exc


def _assigned(code: int) -> bool:
    """Whether ``code`` is assigned in Unicode 15.0: it lies in one of the ranges of ``hoshitori.unicode15``."""
    index = bisect.bisect_right(_STARTS, code) - 1
    return index >= 0 and code <= unicode15.ASSIGNED[index][1]


def _check_string(text: str) -> str:
    """A string must be free of lone surrogates, made of code points assigned in Unicode 15.0, and in NFC, checked in that order."""
    for char in text:
        if 0xD800 <= ord(char) <= 0xDFFF:
            raise CanonicalError("lone surrogate")
    if not text.isascii():  # every ASCII code point is assigned
        for char in text:
            if not _assigned(ord(char)):
                raise CanonicalError(f"code point U+{ord(char):04X} is not assigned in Unicode 15.0")
    if unicodedata.normalize("NFC", text) != text:
        raise CanonicalError("string is not NFC")
    return text


class _Parser:
    def __init__(self, text: str) -> None:
        self.text = text
        self.pos = 0

    def fail(self, message: str) -> CanonicalError:
        return CanonicalError(f"{message} at offset {self.pos}")

    def skip_space(self) -> None:
        while self.pos < len(self.text) and self.text[self.pos] in _WHITESPACE:
            self.pos += 1

    def parse_document(self) -> JsonValue:
        self.skip_space()
        value = self.value(0)
        self.skip_space()
        if self.pos != len(self.text):
            raise self.fail("trailing content")
        return value

    def value(self, depth: int) -> JsonValue:
        if self.pos >= len(self.text):
            raise self.fail("unexpected end of input")
        char = self.text[self.pos]
        if char == "{":
            return self.object(depth + 1)
        if char == "[":
            return self.array(depth + 1)
        if char == '"':
            return self.string()
        if char == "-" or char in _DIGITS:
            return self.integer()
        for literal, result in (("true", True), ("false", False), ("null", None)):
            if self.text.startswith(literal, self.pos):
                self.pos += len(literal)
                return result
        raise self.fail("unexpected character")

    def object(self, depth: int) -> dict[str, JsonValue]:
        if depth > MAX_DEPTH:
            raise self.fail("nesting too deep")
        self.pos += 1  # "{"
        result: dict[str, JsonValue] = {}
        self.skip_space()
        if self.text.startswith("}", self.pos):
            self.pos += 1
            return result
        while True:
            self.skip_space()
            if not self.text.startswith('"', self.pos):
                raise self.fail("object key must be a string")
            key = self.string()
            if key in result:
                raise self.fail("duplicate key")
            self.skip_space()
            if not self.text.startswith(":", self.pos):
                raise self.fail("expected ':'")
            self.pos += 1
            self.skip_space()
            result[key] = self.value(depth)
            self.skip_space()
            if self.text.startswith(",", self.pos):
                self.pos += 1
                continue
            if self.text.startswith("}", self.pos):
                self.pos += 1
                return result
            raise self.fail("expected ',' or '}'")

    def array(self, depth: int) -> list[JsonValue]:
        if depth > MAX_DEPTH:
            raise self.fail("nesting too deep")
        self.pos += 1  # "["
        result: list[JsonValue] = []
        self.skip_space()
        if self.text.startswith("]", self.pos):
            self.pos += 1
            return result
        while True:
            self.skip_space()
            result.append(self.value(depth))
            self.skip_space()
            if self.text.startswith(",", self.pos):
                self.pos += 1
                continue
            if self.text.startswith("]", self.pos):
                self.pos += 1
                return result
            raise self.fail("expected ',' or ']'")

    def hex4(self) -> int:
        digits = self.text[self.pos : self.pos + 4]
        if len(digits) != 4 or any(d not in _HEX for d in digits):
            raise self.fail("bad \\u escape")
        self.pos += 4
        return int(digits, 16)

    def string(self) -> str:
        self.pos += 1  # opening quote
        out: list[str] = []
        while True:
            if self.pos >= len(self.text):
                raise self.fail("unterminated string")
            char = self.text[self.pos]
            if char == '"':
                self.pos += 1
                return _check_string("".join(out))
            if ord(char) < 0x20:
                raise self.fail("raw control character in string")
            if char != "\\":
                out.append(char)
                self.pos += 1
                continue
            self.pos += 1
            if self.pos >= len(self.text):
                raise self.fail("unterminated escape")
            escape = self.text[self.pos]
            self.pos += 1
            if escape in _SHORT_ESCAPES:
                out.append(_SHORT_ESCAPES[escape])
            elif escape == "u":
                unit = self.hex4()
                if 0xD800 <= unit <= 0xDBFF:
                    if not self.text.startswith("\\u", self.pos):
                        raise self.fail("lone high surrogate")
                    self.pos += 2
                    low = self.hex4()
                    if not 0xDC00 <= low <= 0xDFFF:
                        raise self.fail("high surrogate not followed by a low surrogate")
                    out.append(chr(0x10000 + ((unit - 0xD800) << 10) + (low - 0xDC00)))
                elif 0xDC00 <= unit <= 0xDFFF:
                    raise self.fail("lone low surrogate")
                else:
                    out.append(chr(unit))
            else:
                raise self.fail("bad escape")

    def integer(self) -> int:
        start = self.pos
        if self.text[self.pos] == "-":
            self.pos += 1
        if self.pos >= len(self.text) or self.text[self.pos] not in _DIGITS:
            raise self.fail("malformed number")
        if self.text[self.pos] == "0":
            self.pos += 1
            if self.pos < len(self.text) and self.text[self.pos] in _DIGITS:
                raise self.fail("leading zero")
        else:
            while self.pos < len(self.text) and self.text[self.pos] in _DIGITS:
                self.pos += 1
        if self.pos < len(self.text) and self.text[self.pos] in ".eE":
            raise self.fail("fractional or exponent number; quantities are decimal strings")
        spelled = self.text[start : self.pos]
        if spelled == "-0":
            raise self.fail("negative zero")
        if len(spelled.lstrip("-")) > len(str(MAX_SAFE_INTEGER)):  # bound the work before converting a long digit run
            raise self.fail("integer outside +/-(2**53 - 1)")
        number = int(spelled)
        if abs(number) > MAX_SAFE_INTEGER:
            raise self.fail("integer outside +/-(2**53 - 1)")
        return number


def parse_record(raw: bytes) -> dict[str, JsonValue]:
    """Parse record bytes under the profile; the top level must be an object. Raises ``UnicodeVersionError`` on a runtime too old."""
    require_runtime_unicode()
    value = _Parser(decode_utf8(raw)).parse_document()
    if not isinstance(value, dict):
        raise CanonicalError("a record must be a JSON object")
    return value


def _validate(value: object, depth: int) -> None:
    """Check an in-memory value; only containers count towards the nesting depth (as in the parser)."""
    if value is None or isinstance(value, bool):
        return
    if isinstance(value, int):
        if abs(value) > MAX_SAFE_INTEGER:
            raise CanonicalError("integer outside +/-(2**53 - 1)")
        return
    if isinstance(value, str):
        _check_string(value)
        return
    if isinstance(value, list):
        if depth > MAX_DEPTH:
            raise CanonicalError("nesting too deep")
        for item in value:
            _validate(item, depth + 1)
        return
    if isinstance(value, dict):
        if depth > MAX_DEPTH:
            raise CanonicalError("nesting too deep")
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalError("object keys must be strings")
            _check_string(key)
            _validate(item, depth + 1)
        return
    raise CanonicalError(f"unsupported type {type(value).__name__}; quantities are decimal strings")


def _quote(text: str) -> str:
    out = ['"']
    for char in text:
        code = ord(char)
        if code in _OUTPUT_ESCAPES:
            out.append(_OUTPUT_ESCAPES[code])
        elif code < 0x20:
            out.append(f"\\u{code:04x}")
        else:
            out.append(char)
    out.append('"')
    return "".join(out)


def _serialise(value: JsonValue) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        return _quote(value)
    if isinstance(value, list):
        return "[" + ",".join(_serialise(item) for item in value) + "]"
    keys = sorted(value, key=lambda key: key.encode("utf-16-be"))  # UTF-16 code unit order (RFC 8785 3.2.3)
    return "{" + ",".join(_quote(key) + ":" + _serialise(value[key]) for key in keys) + "}"


def dumps(record: dict[str, JsonValue]) -> bytes:
    """Canonical bytes of a record built in memory. Validates first; raises ``CanonicalError`` or ``UnicodeVersionError``."""
    if not isinstance(record, dict):
        raise CanonicalError("a record must be a JSON object")
    require_runtime_unicode()
    _validate(record, 1)
    return _serialise(record).encode("utf-8")


def canonicalize(raw: bytes) -> bytes:
    """Canonical bytes of record text: strict parse, then serialise."""
    return dumps(parse_record(raw))
