"""Checks of the strict JSON reader, runnable against the real module or a mutant."""

from __future__ import annotations

import types


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    Num = mod.Num

    def reads(label: str, raw: bytes, expected: object) -> None:
        try:
            got = mod.parse(raw)
        except mod.JsonError as exc:
            bad.append(f"{label}: refused ({exc})")
            return
        if got != expected or type(got) is not type(expected):
            bad.append(f"{label}: {got!r}, expected {expected!r}")

    def refuses(label: str, raw: bytes, code: str, **kwargs: object) -> None:
        try:
            got = mod.parse(raw, **kwargs)
            bad.append(f"{label}: accepted as {got!r}")
        except mod.JsonError as exc:
            if exc.code != code:
                bad.append(f"{label}: refused with {exc.code}, expected {code}")
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}: {exc}")

    # --- numbers keep their spelling ------------------------------------------------------------------------------------------------
    value = mod.parse(b'{"a": 100.44803097890649, "b": 2.2e+91, "c": -0.0, "d": 7, "e": 1.50, "f": 1E5, "g": 0, "h": [1, 2.0]}')
    spelled = {k: str(v) for k, v in value.items() if k != "h"}
    if spelled != {"a": "100.44803097890649", "b": "2.2e+91", "c": "-0.0", "d": "7", "e": "1.50", "f": "1E5", "g": "0"}:
        bad.append(f"numbers lost their spelling: {spelled}")
    if not all(isinstance(value[k], Num) for k in "abcdefg") or [type(x) for x in value["h"]] != [Num, Num]:
        bad.append("a number is not a Num")
    for text, plain in [
        ("100.44803097890649", "100.44803097890649"),
        ("2.2e+91", "22" + "0" * 90),
        ("-0.0", "0"),
        ("1.50", "1.50"),
        ("1E5", "100000"),
        ("0", "0"),
        ("-0", "0"),
        ("0e10", "0"),
        ("1e-3", "0.001"),
        ("-12.5e1", "-125"),
        ("123456789012345678901234567890", "123456789012345678901234567890"),
        ("0.1", "0.1"),
        ("1.0e0", "1.0"),
        ("-7", "-7"),
        ("5e-324", "0." + "0" * 323 + "5"),
    ]:
        try:
            got = Num(text).plain()
        except mod.JsonError as exc:
            bad.append(f"plain({text}) refused: {exc}")
            continue
        if got != plain:
            bad.append(f"plain({text}) = {got!r}, expected {plain!r}")
    for text in ("1e400", "1e-500", "1" + "0" * 399 + "e1", "1e999999999", "1e-999999999", "0." + "0" * 399 + "1", "9" * 401):
        try:
            Num(text).plain()
            bad.append(f"plain({text[:20]}...) accepted")
        except mod.JsonError as exc:
            if exc.code != "NUMBER":
                bad.append(f"plain({text[:20]}...) refused with {exc.code}")
    Num("1" + "0" * 399).plain()  # exactly at the limit
    Num("1e399").plain()
    Num("1e-399").plain()

    if isinstance(Num("5"), str) or isinstance(value["d"], str):
        bad.append("a number must not be an instance of str")
    if Num("5") == "5" or Num("5") != Num("5") or Num("5") == Num("5.0") or hash(Num("5")) != hash(Num("5")):
        bad.append("numbers compare by their spelling and only with numbers")
    if repr(Num("5")) != "Num('5')":
        bad.append(f"repr: {Num('5')!r}")

    # --- ordinary documents ------------------------------------------------------------------------------------------------------------
    reads("empty object", b"{}", {})
    reads("empty list", b"[]", [])
    reads(
        "strings and literals",
        b'{"s": "caf\\u00e9 \\"x\\"", "t": true, "f": false, "n": null}',
        {"s": 'café "x"', "t": True, "f": False, "n": None},
    )
    reads("surrounding whitespace", b' \t\r\n{"a": 1}\n ', {"a": Num("1")})
    reads("nested", b'{"a": {"b": [1, {"c": null}]}}', {"a": {"b": [Num("1"), {"c": None}]}})
    reads("a bare string", b'"hello"', "hello")
    reads("a bare number", b"12.5", Num("12.5"))
    reads("a pair of surrogates", b'"\\ud83d\\ude00"', "\U0001f600")
    reads("unicode keys", '{"é": 1}'.encode(), {"é": Num("1")})
    reads("depth at the limit", b"[" * 64 + b"]" * 64, [[]] if False else _nested(64))

    # --- refusals ------------------------------------------------------------------------------------------------------------------------
    refuses("a body over the limit", b'{"a":' + b"1" * 100 + b"}", "SIZE", max_bytes=50)
    exactly = b"[" + b"1" * 48 + b"]"  # 50 bytes
    try:
        mod.parse(exactly, max_bytes=50)
    except mod.JsonError:
        bad.append("a body exactly at the limit was refused")
    refuses("a body one byte over the limit", b"[" + b"1" * 49 + b"]", "SIZE", max_bytes=50)
    refuses("a byte order mark", b'\xef\xbb\xbf{"a": 1}', "ENCODING")
    refuses("latin-1 bytes", b'{"a": "\xe9"}', "ENCODING")
    refuses("overlong utf-8", b'{"a": "\xc0\xaf"}', "ENCODING")
    refuses("empty", b"", "SYNTAX")
    refuses("whitespace only", b"  \n", "SYNTAX")
    refuses("trailing text", b'{"a": 1} x', "SYNTAX")
    refuses("a second value", b"{} {}", "SYNTAX")
    refuses("trailing comma", b'{"a": 1,}', "SYNTAX")
    refuses("single quotes", b"{'a': 1}", "SYNTAX")
    refuses("a comment", b'{"a": 1} // note', "SYNTAX")
    refuses("an unquoted key", b"{a: 1}", "SYNTAX")
    refuses("a leading zero", b'{"a": 01}', "SYNTAX")
    refuses("a bare plus", b'{"a": +1}', "SYNTAX")
    refuses("a bare dot", b'{"a": .5}', "SYNTAX")
    refuses("a raw control character in a string", b'{"a": "x\ty"}', "SYNTAX")
    refuses("a raw newline in a string", b'{"a": "x\ny"}', "SYNTAX")
    refuses("an html page", b"<html><body>Bad Gateway</body></html>", "SYNTAX")
    refuses("a truncated document", b'{"a": [1, 2', "SYNTAX")
    refuses("NaN", b'{"a": NaN}', "CONSTANT")
    refuses("Infinity", b'{"a": Infinity}', "CONSTANT")
    refuses("negative Infinity", b'{"a": -Infinity}', "CONSTANT")
    refuses("a duplicated key", b'{"a": 1, "a": 2}', "DUPLICATE")
    refuses("a duplicated key deeper in", b'{"x": [{"a": 1, "a": 1}]}', "DUPLICATE")
    refuses("a lone surrogate in a string", b'{"a": "\\ud800"}', "SURROGATE")
    refuses("a lone low surrogate in a string", b'"\\udc00"', "SURROGATE")
    refuses("a lone surrogate in a key", b'{"\\ud800": 1}', "SURROGATE")
    refuses("a lone surrogate in a list", b'["\\udfff"]', "SURROGATE")
    refuses("nesting over the limit", b"[" * 65 + b"]" * 65, "DEPTH")
    refuses("objects nested over the limit", b'{"a":' * 65 + b"1" + b"}" * 65, "DEPTH")
    refuses("nesting far over the recursion limit", b"[" * 100_000 + b"]" * 100_000, "DEPTH")
    return bad


def _nested(depth: int) -> object:
    value: object = []
    for _ in range(depth - 1):
        value = [value]
    return value
