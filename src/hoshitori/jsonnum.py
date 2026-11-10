"""A strict JSON reader for provider responses that keeps every number exactly as it was written.

Providers send numbers as JSON floats (``100.44803097890649``, ``2.2e+91``). Parsing them into binary floats loses the spelling and
makes records depend on the language that read them, so this reader returns each number as ``Num``: the text of the number, checked
against the JSON grammar. ``Num.plain()`` gives the plain decimal notation (no exponent) through ``Decimal``, never through a float.

The reader refuses what a hostile or broken provider can send: a body that is too large, not UTF-8 (a byte order mark included), a
duplicated object key, ``NaN`` or ``Infinity``, a lone surrogate, nesting deeper than a limit, and a number whose plain notation would
need an absurd number of digits. Every refusal is a ``JsonError`` with a short code; nothing partial is returned.
"""

from __future__ import annotations

import json
from decimal import Decimal, InvalidOperation
from typing import Any, Final

MAX_BYTES: Final = 8 * 1024 * 1024
MAX_DEPTH: Final = 64
MAX_PLAIN_DIGITS: Final = 400


class JsonError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


class Num:
    """The text of a JSON number, exactly as written.

    Not a ``str``: a number must never pass an ``isinstance(value, str)`` check meant for text (a catalogue whose description is the
    number 5 is not a catalogue with a description).
    """

    __slots__ = ("text",)

    def __init__(self, text: str) -> None:
        self.text = text

    def __str__(self) -> str:
        return self.text

    def __repr__(self) -> str:
        return f"Num({self.text!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Num) and other.text == self.text

    def __hash__(self) -> int:
        return hash(("Num", self.text))

    def plain(self) -> str:
        """The number in plain decimal notation, without an exponent. ``-0`` becomes ``0``."""
        try:
            value = Decimal(self.text)
        except InvalidOperation as exc:  # cannot happen for text the JSON grammar accepted, kept as a guard
            raise JsonError("NUMBER", f"not a number: {self.text}") from exc
        _sign, digits, exponent = value.as_tuple()
        if not isinstance(exponent, int):
            raise JsonError("NUMBER", "not a finite number")
        if len(digits) + max(0, exponent) > MAX_PLAIN_DIGITS or len(digits) + max(0, -exponent) > MAX_PLAIN_DIGITS:
            raise JsonError("NUMBER", "the number needs too many digits in plain notation")
        text = format(value, "f")
        return "0" if value == 0 else text


def _reject_constant(name: str) -> Any:
    raise JsonError("CONSTANT", f"{name} is not JSON")


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    keys = [k for k, _ in pairs]
    if len(set(keys)) != len(keys):
        raise JsonError("DUPLICATE", "an object has the same key twice")
    return dict(pairs)


_DECODER = json.JSONDecoder(object_pairs_hook=_pairs, parse_float=Num, parse_int=Num, parse_constant=_reject_constant)


def _check(root: Any) -> None:
    """Walk the value once: depth, lone surrogates in strings and keys."""
    stack: list[tuple[Any, int]] = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > MAX_DEPTH:
            raise JsonError("DEPTH", f"nesting deeper than {MAX_DEPTH}")
        if isinstance(node, dict):
            for key, child in node.items():
                _no_surrogates(key)
                stack.append((child, depth + 1))
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
        elif isinstance(node, str):
            _no_surrogates(node)


def _no_surrogates(text: str) -> None:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise JsonError("SURROGATE", "a string holds a lone surrogate") from exc


def parse(raw: bytes, *, max_bytes: int = MAX_BYTES) -> Any:
    """The JSON value in ``raw``, numbers as ``Num``."""
    if len(raw) > max_bytes:
        raise JsonError("SIZE", f"{len(raw)} bytes is over the limit of {max_bytes}")
    if raw.startswith(b"\xef\xbb\xbf"):
        raise JsonError("ENCODING", "a byte order mark")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise JsonError("ENCODING", "not UTF-8") from exc
    try:
        value, end = _DECODER.raw_decode(text.lstrip(" \t\r\n"))
    except RecursionError as exc:
        raise JsonError("DEPTH", "nesting too deep to read") from exc
    except json.JSONDecodeError as exc:
        raise JsonError("SYNTAX", exc.msg) from exc
    stripped = text.lstrip(" \t\r\n")
    if stripped[end:].strip(" \t\r\n"):
        raise JsonError("SYNTAX", "data after the end of the value")
    _check(value)
    return value
