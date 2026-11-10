"""Checks of the typed answer (``hoshitori.typed``), runnable against a mutant: every kind of schema, every refusal with its exact words, the
strict reading of a raw answer, the exported JSON schema and the one-line description."""

from __future__ import annotations

import json
import types
from collections.abc import Callable
from decimal import Decimal
from typing import Any

from hoshitori import jsonnum

D = Decimal


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want or type(got) is not type(want):
            self.bad.append(f"{label}: got {got!r} ({type(got).__name__}), expected {want!r} ({type(want).__name__})")

    def refuses(self, label: str, call: Callable[[], object], says: str, error: str = "TypedError") -> None:
        want = getattr(self.mod, error)
        try:
            call()
        except want as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def j(text: str) -> Any:
    return jsonnum.parse(text.encode())


def check_definitions(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("limits", (mod.MAX_TEXT, mod.MAX_ITEMS, mod.MAX_FIELDS, mod.MAX_PLACES), (20_000, 200, 64, 12))
    for cls in (mod.Text, mod.Whole, mod.Number, mod.Flag, mod.Items, mod.Field, mod.Object):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    text = "a text has 0 <= min_len <= max_len <= 20000"
    closed = "a closed list of texts is not empty, has no repeat, and each text fits the length"
    for label, build, says in (
        ("a negative minimum", lambda: mod.Text(5, -1), text),
        ("a minimum above the maximum", lambda: mod.Text(5, 6), text),
        ("a maximum above the limit", lambda: mod.Text(20_001), text),
        ("a boolean length", lambda: mod.Text(True), text),
        ("a boolean minimum", lambda: mod.Text(5, True), text),
        ("an empty closed list", lambda: mod.Text(5, 1, ()), closed),
        ("a closed list with a repeat", lambda: mod.Text(5, 1, ("a", "a")), closed),
        ("a closed text that is too long", lambda: mod.Text(2, 1, ("abc",)), closed),
        ("a closed list where only one text fits", lambda: mod.Text(5, 1, ("a", "abcdef")), closed),
        ("a closed text that is too short", lambda: mod.Text(5, 2, ("a",)), closed),
        ("a closed entry that is not text", lambda: mod.Text(5, 1, (1,)), closed),
        ("a pattern that does not compile", lambda: mod.Text(5, 1, None, "("), "the pattern of a text does not compile"),
        ("a whole number with a float bound", lambda: mod.Whole(0.5, 2), "the bounds of a whole number are whole numbers"),
        ("a whole number with a boolean bound", lambda: mod.Whole(True, 2), "the bounds of a whole number are whole numbers"),
        ("a whole number with high below low", lambda: mod.Whole(3, 2), "the low bound of a whole number is at most the high bound"),
        ("a number with a float bound", lambda: mod.Number(0.5, D(1), 2), "the bounds of a number are finite decimals"),
        ("a number with a NaN bound", lambda: mod.Number(D("NaN"), D(1), 2), "the bounds of a number are finite decimals"),
        ("a number with an infinite bound", lambda: mod.Number(D(0), D("Infinity"), 2), "the bounds of a number are finite decimals"),
        ("a number with high below low", lambda: mod.Number(D(2), D(1), 2), "the low bound of a number is at most the high bound"),
        ("negative places", lambda: mod.Number(D(0), D(1), -1), "the places of a number are 0 to 12"),
        ("too many places", lambda: mod.Number(D(0), D(1), 13), "the places of a number are 0 to 12"),
        ("places that are a boolean", lambda: mod.Number(D(0), D(1), True), "the places of a number are 0 to 12"),
        ("a list with high below low", lambda: mod.Items(mod.Flag(), 3, 2), "a list has 0 <= low <= high <= 200 items"),
        ("a list with too many items", lambda: mod.Items(mod.Flag(), 0, 201), "a list has 0 <= low <= high <= 200 items"),
        ("a list with a negative low", lambda: mod.Items(mod.Flag(), -1, 2), "a list has 0 <= low <= high <= 200 items"),
        ("a unique list of flags", lambda: mod.Items(mod.Flag(), 0, 2, True), "only a list of texts or numbers can be unique"),
        (
            "a member name in capitals",
            lambda: mod.Field("Name", mod.Flag()),
            "a member's name is 1 to 64 characters of a-z, 0-9 and _, starting with a letter",
        ),
        (
            "a member name starting with a digit",
            lambda: mod.Field("1a", mod.Flag()),
            "a member's name is 1 to 64 characters of a-z, 0-9 and _, starting with a letter",
        ),
        (
            "a member name of 65 characters",
            lambda: mod.Field("a" * 65, mod.Flag()),
            "a member's name is 1 to 64 characters of a-z, 0-9 and _, starting with a letter",
        ),
        (
            "a member name that is not text",
            lambda: mod.Field(5, mod.Flag()),
            "a member's name is 1 to 64 characters of a-z, 0-9 and _, starting with a letter",
        ),
        ("an object with no members", lambda: mod.Object(()), "an object has 1 to 64 members, each named once"),
        (
            "an object naming a member twice",
            lambda: mod.Object((mod.Field("a", mod.Flag()), mod.Field("a", mod.Flag()))),
            "an object has 1 to 64 members, each named once",
        ),
        (
            "an object of 65 members",
            lambda: mod.Object(tuple(mod.Field(f"a{i}", mod.Flag()) for i in range(65))),
            "an object has 1 to 64 members, each named once",
        ),
    ):
        c.refuses(label, build, says, "SchemaError")
    mod.Text(20_000, 0)
    mod.Text(5, 5, ("abcde",), "[a-e]+")
    mod.Whole(2, 2)
    mod.Number(D(0), D(0), 0)
    mod.Number(D(0), D(1), 12)
    mod.Items(mod.Text(3), 0, 200, True)
    mod.Items(mod.Whole(0, 1), 0, 0, True)
    mod.Items(mod.Number(D(0), D(1), 1), 0, 1, True)
    mod.Field("a" * 64, mod.Flag())
    mod.Object(tuple(mod.Field(f"a{i}", mod.Flag()) for i in range(64)))
    c.eq("the boundaries are accepted", True, True)
    return c.bad


def check_scalars(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = mod.validate
    t = mod.Text(5)
    c.eq("a text", v(t, j('"abc"')), "abc")
    c.eq("a text is normalised to NFC", v(t, j('"e\\u0301"')), "é")
    c.eq("a text may hold a newline and a tab", v(t, j('"a\\n\\tb"')), "a\n\tb")
    c.eq("the longest text", v(t, j('"abcde"')), "abcde")
    for code in (0x200B, 0x2060, 0xFEFF, 0x00AD, 0xE0041, 0xE000, 0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800):
        c.refuses(
            f"a hidden character U+{code:04X} in model text",
            lambda code=code: v(mod.Text(20), j(json.dumps(f"ab{chr(code)}cd"))),
            f"$: contains a hidden formatting character (U+{code:04X})",
        )
    ordinary = "".join(chr(i) for i in range(0x20, 0x7F)) + "é日本語한국어😀"
    c.eq("no ordinary character is taken for a hidden one", mod.hidden_character(ordinary), None)
    c.eq("a joiner a script needs stays", v(mod.Text(5), j('"a\\u200db"')), "a\u200db")
    c.eq("and so does the non-joiner", v(mod.Text(5), j('"a\\u200cb"')), "a\u200cb")
    for label, text, says in (
        ("an empty text", '""', "$: the text is 0 characters, not 1 to 5"),
        ("a text of 6 characters", '"abcdef"', "$: the text is 6 characters, not 1 to 5"),
        ("a text that is a number", "5", "$: expected text"),
        ("a text that is a list", "[]", "$: expected text"),
        ("a text with a bell", '"a\\u0007"', "$: contains a control character"),
        ("a text with a delete", '"a\\u007f"', "$: contains a control character"),
        ("a text with a carriage return", '"a\\r"', "$: contains a control character"),
        ("null", "null", "$: null is not an answer"),
    ):
        c.refuses(label, lambda text=text: v(t, j(text)), says)
    c.eq("a minimum of zero allows the empty text", v(mod.Text(5, 0), j('""')), "")
    c.eq("a space is not a control character", v(mod.Text(5), j('" "')), " ")
    closed = mod.Text(5, 1, ("up", "down"))
    c.eq("a closed text", v(closed, j('"up"')), "up")
    c.refuses("not in the list", lambda: v(closed, j('"flat"')), "$: the text is not one of ['up', 'down']")
    c.refuses("not in the list, however long", lambda: v(closed, j('"flatflatflat"')), "$: the text is not one of ['up', 'down']")
    pattern = mod.Text(10, 1, None, "[A-Z]{2,5}")
    c.eq("a pattern", v(pattern, j('"BTC"')), "BTC")
    c.refuses("a pattern is matched whole, at the start", lambda: v(pattern, j('"xBTC"')), "$: the text does not match the pattern")
    c.refuses("and at the end", lambda: v(pattern, j('"BTCx"')), "$: the text does not match the pattern")
    w = mod.Whole(-3, 3)
    c.eq("a whole number", v(w, j("2")), 2)
    c.eq("the bounds", (v(w, j("-3")), v(w, j("3")), v(w, j("0")), v(w, j("-0"))), (-3, 3, 0, 0))
    for label, text, says in (
        ("above", "4", "$: the number is not from -3 to 3"),
        ("below", "-4", "$: the number is not from -3 to 3"),
        ("a number written with a point", "2.0", "$: expected a whole number"),
        ("a number written with an exponent", "2e0", "$: expected a whole number"),
        ("a list", "[2]", "$: expected a whole number"),
        ("text", '"2"', "$: expected a whole number"),
        ("true", "true", "$: expected a whole number"),
    ):
        c.refuses(label, lambda text=text: v(w, j(text)), says)
    n = mod.Number(D(0), D(1), 3)
    c.eq("a number", v(n, j("0.5")), D("0.5"))
    c.eq("with trailing zeros up to the places", v(n, j("0.500")), D("0.500"))
    c.eq("the bounds", (v(n, j("0")), v(n, j("1")), v(n, j("0.123")), v(n, j("1e-3"))), (D(0), D(1), D("0.123"), D("0.001")))
    c.eq("a whole number is a number", v(n, j("1")), D(1))
    for label, text, says in (
        ("above", "1.001", "$: the number is not from 0 to 1"),
        ("below", "-0.001", "$: the number is not from 0 to 1"),
        ("four places", "0.1234", "$: the number has more than 3 decimal places"),
        ("an exponent that makes four places", "1.234e-1", "$: the number has more than 3 decimal places"),
        ("a trailing zero is a place as written", "0.5000", "$: the number has more than 3 decimal places"),
        ("text", '"0.5"', "$: expected a number"),
        ("a boolean", "false", "$: expected a number"),
        ("a list", "[0.5]", "$: expected a number"),
    ):
        c.refuses(label, lambda text=text: v(n, j(text)), says)
    for label, num in (("text that is no number", "abc"), ("NaN", "NaN"), ("infinity", "Infinity")):
        c.refuses(f"a hand-made {label}", lambda num=num: v(n, jsonnum.Num(num)), "$: expected a number")
    zero = mod.Number(D(0), D(100), 0)
    c.eq("no places", (v(zero, j("5")), v(zero, j("5e0")), v(zero, j("5e1"))), (D(5), D("5"), D("5E+1")))
    c.refuses("a place where none is allowed", lambda: v(zero, j("5.5")), "$: the number has more than 0 decimal places")
    c.refuses("even a zero place", lambda: v(zero, j("5.0")), "$: the number has more than 0 decimal places")
    c.refuses("a huge number is out of bounds", lambda: v(n, j("1e400")), "$: the number is not from 0 to 1")
    c.eq("a flag", (v(mod.Flag(), j("true")), v(mod.Flag(), j("false"))), (True, False))
    for label, text in (("a number", "1"), ("text", '"true"'), ("a list", "[]")):
        c.refuses(label, lambda text=text: v(mod.Flag(), j(text)), "$: expected true or false")
    c.refuses("a path is carried", lambda: v(t, j("5"), "$.name"), "$.name: expected text")
    return c.bad


def check_structures(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = mod.validate
    items = mod.Items(mod.Whole(0, 9), 1, 3)
    c.eq("a list", v(items, j("[1, 2]")), [1, 2])
    c.eq("its bounds", (len(v(items, j("[1]"))), len(v(items, j("[1,2,3]")))), (1, 3))
    c.refuses("too few", lambda: v(items, j("[]")), "$: the list has 0 items, not 1 to 3")
    c.refuses("too many", lambda: v(items, j("[1,2,3,4]")), "$: the list has 4 items, not 1 to 3")
    c.refuses("not a list", lambda: v(items, j("{}")), "$: expected a list")
    c.refuses("an item that is wrong, with its place", lambda: v(items, j("[1, 2, 12]")), "$[2]: the number is not from 0 to 9")
    c.refuses("the first wrong item is reported", lambda: v(items, j("[10, 11]")), "$[0]: the number is not from 0 to 9")
    plain = mod.Items(mod.Flag())
    c.eq("a list without bounds takes none to twenty", (v(plain, j("[]")), len(v(plain, j("[" + ",".join(["true"] * 20) + "]")))), ([], 20))
    c.refuses("and not twenty-one", lambda: v(plain, j("[" + ",".join(["true"] * 21) + "]")), "$: the list has 21 items, not 0 to 20")
    unique = mod.Items(mod.Text(3), 0, 5, True)
    c.eq("a unique list", v(unique, j('["a","b"]')), ["a", "b"])
    c.refuses("a repeat", lambda: v(unique, j('["a","b","a"]')), "$: the list repeats an item")
    c.refuses("a repeat after normalisation", lambda: v(unique, j('["\\u00e9","e\\u0301"]')), "$: the list repeats an item")
    c.eq("a list that may repeat", v(mod.Items(mod.Text(3), 0, 5), j('["a","a"]')), ["a", "a"])
    nums = mod.Items(mod.Number(D(0), D(9), 1), 0, 5, True)
    c.refuses("a repeat of numbers written differently", lambda: v(nums, j("[1, 1.0]")), "$: the list repeats an item")
    obj = mod.Object((mod.Field("symbol", mod.Text(10)), mod.Field("p", mod.Number(D(0), D(1), 4)), mod.Field("note", mod.Text(20), False)))
    c.eq("an object, in the order of its schema", list(v(obj, j('{"p": 0.5, "symbol": "BTC"}'))), ["symbol", "p"])
    c.eq(
        "an optional member that is given", v(obj, j('{"symbol":"BTC","p":0.5,"note":"x"}')), {"symbol": "BTC", "p": D("0.5"), "note": "x"}
    )
    c.eq("and one that is not stays absent, not null", "note" in v(obj, j('{"symbol":"BTC","p":0.5}')), False)
    c.refuses("a missing member", lambda: v(obj, j('{"symbol":"BTC"}')), "$.p: is missing")
    c.refuses("an unknown member", lambda: v(obj, j('{"symbol":"BTC","p":0.5,"q":1}')), "$.q: is not a member of this answer")
    c.refuses("a wrong member, with its path", lambda: v(obj, j('{"symbol":"BTC","p":2}')), "$.p: the number is not from 0 to 1")
    c.refuses("a null member", lambda: v(obj, j('{"symbol":"BTC","p":null}')), "$.p: null is not an answer")
    c.refuses("a null optional member", lambda: v(obj, j('{"symbol":"BTC","p":0.5,"note":null}')), "$.note: null is not an answer")
    c.refuses("not an object", lambda: v(obj, j("[]")), "$: expected an object")
    c.refuses("a missing member is reported before an unknown one", lambda: v(obj, j('{"zz":1,"p":0.5}')), "$.symbol: is missing")
    c.refuses(
        "a wrong member is reported before an unknown one",
        lambda: v(obj, j('{"p":5,"zz":1,"symbol":"BTC"}')),
        "$.p: the number is not from 0 to 1",
    )
    nested = mod.Object((mod.Field("cites", mod.Items(mod.Object((mod.Field("id", mod.Text(5)),)), 0, 3)),))
    c.eq("nested answers", v(nested, j('{"cites":[{"id":"a"},{"id":"b"}]}')), {"cites": [{"id": "a"}, {"id": "b"}]})
    c.refuses("a nested path", lambda: v(nested, j('{"cites":[{"id":"a"},{"id":7}]}')), "$.cites[1].id: expected text")
    c.refuses("a nested missing member", lambda: v(nested, j('{"cites":[{}]}')), "$.cites[0].id: is missing")
    return c.bad


def check_parse(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    obj = mod.Object((mod.Field("p", mod.Number(D(0), D(1), 3)),))
    c.eq("a raw answer", mod.parse(obj, b' {"p": 0.25} \n'), {"p": D("0.25")})
    for label, raw, says in (
        ("text that is not JSON", b"p = 0.5", "$: not valid JSON (SYNTAX: Expecting value)"),
        ("a duplicate key", b'{"p": 0.5, "p": 0.6}', "$: not valid JSON (DUPLICATE: an object has the same key twice)"),
        ("NaN", b'{"p": NaN}', "$: not valid JSON (CONSTANT: NaN is not JSON)"),
        ("data after the value", b'{"p": 0.5} {}', "$: not valid JSON (SYNTAX: data after the end of the value)"),
        ("a byte order mark", b'\xef\xbb\xbf{"p": 0.5}', "$: not valid JSON (ENCODING: a byte order mark)"),
        ("bytes that are not UTF-8", b'{"p": "\xff"}', "$: not valid JSON (ENCODING: not UTF-8)"),
        ("an empty answer", b"", "$: not valid JSON (SYNTAX: Expecting value)"),
        ("a wrong shape", b'{"q": 0.5}', "$.p: is missing"),
        ("a list at the top", b"[]", "$: expected an object"),
    ):
        c.refuses(label, lambda raw=raw: mod.parse(obj, raw), says)
    return c.bad


def check_closed(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    words = mod.closed(("up", "down", "flat"))
    c.eq(
        "a closed text takes its bounds from its words",
        (words.max_len, words.min_len, words.one_of, words.pattern),
        (4, 2, ("up", "down", "flat"), None),
    )
    c.eq("one word", (mod.closed(("sideways",)).max_len, mod.closed(("sideways",)).min_len), (8, 8))
    c.eq("it reads a word of the list", mod.validate(words, j('"flat"')), "flat")
    c.refuses("and not another", lambda: mod.validate(words, j('"left"')), "$: the text is not one of ['up', 'down', 'flat']")
    return c.bad


def check_exports(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    obj = mod.Object(
        (
            mod.Field("action", mod.Text(8, 1, ("up", "down"))),
            mod.Field("p", mod.Number(D(0), D(1), 6)),
            mod.Field("size", mod.Number(D("0.5"), D("2.5"), 1)),
            mod.Field("n", mod.Whole(1, 5)),
            mod.Field("ok", mod.Flag(), False),
            mod.Field("ids", mod.Items(mod.Text(10, 1, None, "[a-z]+"), 0, 4, True)),
        )
    )
    c.eq(
        "the JSON schema",
        mod.json_schema(obj),
        {
            "type": "object",
            "properties": {
                "action": {"type": "string", "minLength": 1, "maxLength": 8, "enum": ["up", "down"]},
                "p": {"type": "number", "minimum": 0, "maximum": 1},
                "size": {"type": "number"},
                "n": {"type": "integer", "minimum": 1, "maximum": 5},
                "ok": {"type": "boolean"},
                "ids": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1, "maxLength": 10, "pattern": "[a-z]+"},
                    "minItems": 0,
                    "maxItems": 4,
                },
            },
            "required": ["action", "p", "size", "n", "ids"],
            "additionalProperties": False,
        },
    )
    c.eq("no list carries uniqueItems, which the endpoint's decoder refuses", "uniqueItems" in str(mod.json_schema(obj)), False)
    c.eq("not even one that must be unique, which the validator and the prompt still say", mod.describe(obj).count("all different"), 1)
    c.eq("half a bound", mod.json_schema(mod.Number(D("0.5"), D(2), 1)), {"type": "number", "maximum": 2})
    c.eq("and the other half", mod.json_schema(mod.Number(D(1), D("2.5"), 1)), {"type": "number", "minimum": 1})
    c.eq(
        "the description",
        mod.describe(obj),
        '{"action": "up" | "down", "p": number(0..1, at most 6 decimal places), "size": number(0.5..2.5, at most 1 decimal places), '
        '"n": whole number(1..5), "ok"?: true | false, "ids": [text(1..10 characters)] (0..4 items, all different)}',
    )
    c.eq("a list that may repeat is described without the phrase", mod.describe(mod.Items(mod.Flag(), 1, 2)), "[true | false] (1..2 items)")
    c.eq("a closed text with a quote and a backslash", mod.describe(mod.Text(9, 1, ('a"b', "c\\d"))), '"a\\"b" | "c\\\\d"')
    c.eq("a plain text", mod.describe(mod.Text(40, 0)), "text(0..40 characters)")
    return c.bad


def check_places_as_written(mod: types.ModuleType) -> list[str]:
    """the places of a number are those it is written with, a zero's included, so the number kept (and the text a record
    makes of it with ``format(value, "f")``) never has more places than the schema allows."""
    c = Case(mod)
    v = mod.validate
    p = mod.Number(D(0), D(1), 6)  # the shape of a probability (arm L1)
    six = "$: the number has more than 6 decimal places"
    for label, text in (
        ("a zero with a huge negative exponent", "0e-999999999"),
        ("written with a capital E", "0E-999999999"),
        ("a negative zero with one", "-0e-999999999"),
        ("a zero one place beyond", "0.0000000"),
        ("a zero by its exponent, one place beyond", "0e-7"),
        ("a probability with a trailing zero beyond the places", "0.6200000"),
        ("one written with a zero beyond the places", "1.0000000"),
        ("a nonzero by its exponent, one place beyond", "6200000e-7"),
        ("a tiny nonzero", "1e-999999999"),
    ):
        c.refuses(label, lambda text=text: v(p, j(text)), six)
    for label, text, want in (
        ("a zero at the places allowed", "0.000000", D("0.000000")),
        ("a zero by its exponent at the places allowed", "0e-6", D("0E-6")),
        ("a probability at the places allowed", "0.620000", D("0.620000")),
        ("one at the places allowed", "1.000000", D("1.000000")),
        ("a nonzero by its exponent at the places allowed", "620000e-6", D("0.620000")),
        ("an exponent that leaves fewer places", "6.2E-1", D("0.62")),
        ("a zero with a huge positive exponent has no places", "0E+999999999", D("0E+999999999")),
    ):
        c.eq(label, v(p, j(text)), want)
    c.eq("a zero with a huge positive exponent is written as a zero", format(v(p, j("0E+999999999")), "f"), "0")
    c.eq(
        "every number taken is written in at most eight characters",
        sorted({len(format(v(p, j(t)), "f")) for t in ("0", "1", "0.000000", "0e-6", "0.620000", "1.000000", "0E+999999999", "6.2E-1")}),
        [1, 4, 8],
    )
    level = mod.Number(D(-(10**12)), D(10**12), 8)
    c.eq("a large whole number by its exponent", v(level, j("1e12")), D("1E+12"))
    c.eq("a negative number at the places allowed", v(level, j("-0.12345678")), D("-0.12345678"))
    c.refuses("and one beyond", lambda: v(level, j("-0.123456780")), "$: the number has more than 8 decimal places")
    answer = mod.Object((mod.Field("p", p),))
    c.refuses("the finding's answer", lambda: mod.parse(answer, b'{"p": 0e-999999999}'), "$.p: the number has more than 6 decimal places")
    return c.bad


def check_canonical_text(mod: types.ModuleType) -> list[str]:
    """a text, and a member's name that an error would quote, is valid canonical text (the canonical module is asked), so whatever
    an answer puts into a record, a digest or the repair request is something the records can hold."""
    c = Case(mod)
    v = mod.validate
    c.eq(
        "the reasons are the canonical module's own",
        tuple(mod.not_canonical(t) for t in ("ok", "", "\u00e9", "e\u0301", "\ud800", "a\U0001fae8", "a\U0001fae9")),
        (None, None, None, "string is not NFC", "lone surrogate", None, "code point U+1FAE9 is not assigned in Unicode 15.0"),
    )
    t = mod.Text(10)
    c.eq("a code point assigned in Unicode 15.0", v(t, j('"a\\ud83e\\udee8"')), "a\U0001fae8")
    after = "$: is not valid canonical text: code point U+1FAE9 is not assigned in Unicode 15.0"
    c.refuses("a code point assigned only after Unicode 15.0", lambda: v(t, j('"a\\ud83e\\udee9"')), after)
    c.refuses("and handed in as it is", lambda: v(t, "a\U0001fae9"), after)
    c.refuses("a lone surrogate handed in", lambda: v(t, "a\ud800"), "$: is not valid canonical text: lone surrogate")
    c.refuses("a control character is still that", lambda: v(t, "\U0001fae9\u0007"), "$: contains a control character")
    c.eq("a text that is not NFC is still made NFC, not refused", v(t, "e\u0301"), "\u00e9")
    obj = mod.Object((mod.Field("p", mod.Number(D(0), D(1), 3)),))
    for label, raw, says in (
        (
            "a member whose name is not NFC",
            '{"p": 0.5, "note\\u0301": 1}',
            "$: a member's name is not valid canonical text: string is not NFC",
        ),
        (
            "one whose name holds a code point assigned after Unicode 15.0",
            '{"p": 0.5, "x\\ud83e\\udee9": 1}',
            "$: a member's name is not valid canonical text: code point U+1FAE9 is not assigned in Unicode 15.0",
        ),
        ("an unknown member in canonical text is named", '{"p": 0.5, "not\u00e9e": 1}', "$.not\u00e9e: is not a member of this answer"),
    ):
        c.refuses(label, lambda raw=raw: v(obj, j(raw)), says)
    c.refuses(
        "a name read from raw bytes",
        lambda: mod.parse(obj, '{"p": 0.5, "note\u0301": 1}'.encode()),
        "$: a member's name is not valid canonical text: string is not NFC",
    )
    nested = mod.Object((mod.Field("a", mod.Items(obj, 0, 2)),))
    c.refuses(
        "the path is that of the object holding it",
        lambda: v(nested, j('{"a": [{"p": 0.5}, {"p": 0.5, "e\\u0301": 1}]}')),
        "$.a[1]: a member's name is not valid canonical text: string is not NFC",
    )
    summary = mod.Object((mod.Field("summary", mod.Text(40)),))
    c.refuses(
        "a text of an answer",
        lambda: mod.parse(summary, '{"summary": "s \U0001fae9"}'.encode()),
        "$.summary: is not valid canonical text: code point U+1FAE9 is not assigned in Unicode 15.0",
    )
    return c.bad


def check_no_minus_sign(mod: types.ModuleType) -> list[str]:
    """the last answer of the model that ended a cycle: a number whose schema allows no negative value (a low bound of zero or more)
    is written without a minus sign, so a negative zero, which no record writes (they write decimals with no sign), is an invalid answer
    there. Where the low bound is negative a negative zero is a number like any other."""
    c = Case(mod)
    v = mod.validate
    p = mod.Number(D(0), D(1), 6)  # the shape of a probability (arm L1)
    says = "$: a number from 0 to 1 is written without a minus sign"
    for label, text in (
        ("a negative zero", "-0"),
        ("one with a place", "-0.0"),
        ("one with an exponent", "-0e5"),
        ("one with the places allowed", "-0.000000"),
        ("one with a negative exponent", "-0E-6"),
    ):
        c.refuses(label, lambda text=text: v(p, j(text)), says)
    c.refuses("the places are counted first", lambda: v(p, j("-0.0000000")), "$: the number has more than 6 decimal places")
    c.refuses("and the bounds before them", lambda: v(mod.Number(D("0.5"), D(2), 1), j("-0")), "$: the number is not from 0.5 to 2")
    c.refuses(
        "a low bound of exactly zero",
        lambda: v(mod.Number(D(0), D(0), 0), j("-0")),
        "$: a number from 0 to 0 is written without a minus sign",
    )
    c.refuses(
        "in an answer, with its path",
        lambda: mod.parse(mod.Object((mod.Field("p", p),)), b'{"p": -0}'),
        "$.p: a number from 0 to 1 is written without a minus sign",
    )
    signed = mod.Number(D(-1), D(1), 6)
    c.eq(
        "where the low bound is negative, a negative zero is taken as written",
        [str(v(signed, j(x))) for x in ("-0", "-0.0", "-0e5")],
        ["-0", "-0.0", "-0E+5"],
    )
    c.eq("and so it is just below zero", str(v(mod.Number(D("-0.000001"), D(1), 6), j("-0"))), "-0")
    c.eq("a zero without a sign is still taken", [str(v(p, j(x))) for x in ("0", "0.0", "0e5")], ["0", "0.0", "0E+5"])
    c.eq("and so is any other number in bounds", v(p, j("0.62")), D("0.62"))
    return c.bad


def check_tagged(mod: types.ModuleType) -> list[str]:
    """A tagged object: the check reads it as its base, a provider is told one closed shape per value of its tag (C1: the endpoint
    enforces anyOf, enum and additionalProperties, so a shape it is told is a shape it can emit)."""
    c = Case(mod)
    tag = mod.Field("kind", mod.Text(1, 1, ("A", "B")))
    base = mod.Object((tag, mod.Field("x", mod.Whole(0, 9), False), mod.Field("y", mod.Flag(), False)))
    a = mod.Object((mod.Field("kind", mod.Text(1, 1, ("A",))), mod.Field("x", mod.Whole(0, 9))))
    b = mod.Object((mod.Field("kind", mod.Text(1, 1, ("B",))), mod.Field("y", mod.Flag())))
    both = mod.Tagged(base, "kind", (a, b))
    c.eq("a tagged object is frozen", mod.Tagged.__dataclass_params__.frozen, True)
    c.eq(
        "the JSON schema is one closed object per value of the tag",
        mod.json_schema(both),
        {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string", "minLength": 1, "maxLength": 1, "enum": ["A"]},
                        "x": {"type": "integer", "minimum": 0, "maximum": 9},
                    },
                    "required": ["kind", "x"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"kind": {"type": "string", "minLength": 1, "maxLength": 1, "enum": ["B"]}, "y": {"type": "boolean"}},
                    "required": ["kind", "y"],
                    "additionalProperties": False,
                },
            ]
        },
    )
    c.eq(
        "the description names each shape", mod.describe(both), '{"kind": "A", "x": whole number(0..9)} | {"kind": "B", "y": true | false}'
    )
    c.eq(
        "inside a list",
        mod.describe(mod.Items(both, 0, 2)),
        '[{"kind": "A", "x": whole number(0..9)} | {"kind": "B", "y": true | false}] (0..2 items)',
    )
    c.eq("the check reads it as its base", mod.validate(both, j('{"kind": "A", "x": 3}')), {"kind": "A", "x": 3})
    c.eq("a member its own shape lacks is the base's to judge", mod.validate(both, j('{"kind": "A", "y": true}')), {"kind": "A", "y": True})
    c.refuses("and the base's refusals stand", lambda: mod.validate(both, j('{"kind": "C"}')), "$.kind: the text is not one of ['A', 'B']")
    c.refuses("a member no shape has", lambda: mod.validate(both, j('{"kind": "B", "z": 1}')), "$.z: is not a member of this answer")
    every = mod.Object((mod.Field("kind", mod.Text(1, 1, ("A",))), mod.Field("x", mod.Whole(0, 9)), mod.Field("y", mod.Flag())))
    c.eq("a variant may hold every member of its base", mod.Tagged(base, "kind", (every, b)).variants[0], every)
    say = "a tagged object's tag is a member of its base with a closed list of texts"
    each = "each variant of a tagged object has its tag, with one value, and only members of its base"
    order = "the variants of a tagged object give the tag's values once each, in the order of the base"
    for label, build, says in (
        ("a tag that is not a member", lambda: mod.Tagged(base, "w", (a, b)), say),
        ("a tag that is not a closed text", lambda: mod.Tagged(base, "x", (a, b)), say),
        ("an open text as the tag", lambda: mod.Tagged(mod.Object((mod.Field("kind", mod.Text(1)),)), "kind", (a,)), say),
        ("a variant without the tag", lambda: mod.Tagged(base, "kind", (mod.Object((mod.Field("x", mod.Whole(0, 9)),)), b)), each),
        ("a variant with two values of the tag", lambda: mod.Tagged(base, "kind", (mod.Object((tag,)), b)), each),
        (
            "a variant whose tag may be left out",
            lambda: mod.Tagged(base, "kind", (mod.Object((mod.Field("kind", mod.Text(1, 1, ("A",)), False),)), b)),
            each,
        ),
        (
            "a variant with a member the base lacks",
            lambda: mod.Tagged(base, "kind", (a, mod.Object((b.fields[0], mod.Field("w", mod.Flag()))))),
            each,
        ),
        ("a value missing", lambda: mod.Tagged(base, "kind", (a,)), order),
        ("the values in another order", lambda: mod.Tagged(base, "kind", (b, a)), order),
        ("a value twice", lambda: mod.Tagged(base, "kind", (a, a, b)), order),
    ):
        c.refuses(label, build, says, "SchemaError")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_definitions,
        check_scalars,
        check_structures,
        check_parse,
        check_closed,
        check_exports,
        check_places_as_written,
        check_canonical_text,
        check_no_minus_sign,
        check_tagged,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-800:]}")
    return bad
