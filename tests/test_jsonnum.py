"""The strict JSON reader: examples, properties, and mutants."""

from __future__ import annotations

import contextlib
import json

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import jsonnum
from tests.jsonnum_checks import failures
from tests.mutation import mutant


def test_real_module_passes_every_check() -> None:
    assert failures(jsonnum) == []


numbers = st.one_of(st.integers(-(10**30), 10**30), st.floats(allow_nan=False, allow_infinity=False, width=64))
values = st.recursive(
    st.one_of(st.none(), st.booleans(), numbers, st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=20)),
    lambda children: st.one_of(
        st.lists(children, max_size=4),
        st.dictionaries(st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=8), children, max_size=4),
    ),
    max_leaves=20,
)


def plain(value: object) -> object:
    if isinstance(value, jsonnum.Num):
        return json.loads(str(value))
    if isinstance(value, list):
        return [plain(v) for v in value]
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    return value


@settings(max_examples=300, deadline=None)
@given(values)
def test_what_the_standard_library_writes_this_reader_reads_back(value: object) -> None:
    raw = json.dumps(value, ensure_ascii=False).encode()
    assert plain(jsonnum.parse(raw)) == json.loads(raw)


@settings(max_examples=200, deadline=None)
@given(st.floats(allow_nan=False, allow_infinity=False, width=64))
def test_a_float_written_by_python_keeps_its_exact_spelling(x: float) -> None:
    raw = json.dumps({"x": x}).encode()
    assert (
        str(jsonnum.parse(raw)["x"]) == repr(x)
        if "e" not in repr(x) and "inf" not in repr(x)
        else str(jsonnum.parse(raw)["x"]) == json.dumps(x)
    )


@settings(max_examples=300, deadline=None)
@given(st.binary(max_size=60))
def test_nothing_but_a_json_error_escapes(raw: bytes) -> None:
    with contextlib.suppress(jsonnum.JsonError):
        jsonnum.parse(raw)


MUTATIONS: dict[str, tuple[str, str]] = {
    "numbers lose their spelling": ("parse_float=Num, parse_int=Num,", "parse_float=float, parse_int=int,"),
    "floats become floats": ("parse_float=Num, parse_int=Num,", "parse_float=float, parse_int=Num,"),
    "integers become integers": ("parse_float=Num, parse_int=Num,", "parse_float=Num, parse_int=int,"),
    "NaN is read": ("parse_constant=_reject_constant", "parse_constant=lambda name: float('nan')"),
    "duplicated keys are read": (
        '    if len(set(keys)) != len(keys):\n        raise JsonError("DUPLICATE", "an object has the same key twice")\n',
        "",
    ),
    "a body of any size is read": (
        '    if len(raw) > max_bytes:\n        raise JsonError("SIZE", f"{len(raw)} bytes is over the limit of {max_bytes}")\n',
        "",
    ),
    "a body one byte over the limit is read": ("    if len(raw) > max_bytes:", "    if len(raw) > max_bytes + 1:"),
    "a body exactly at the limit is refused": ("    if len(raw) > max_bytes:", "    if len(raw) >= max_bytes:"),
    "a byte order mark is read": (
        '    if raw.startswith(b"\\xef\\xbb\\xbf"):\n        raise JsonError("ENCODING", "a byte order mark")\n',
        "",
    ),
    "invalid UTF-8 is read": (
        '        text = raw.decode("utf-8")\n    except UnicodeDecodeError as exc:\n        raise JsonError("ENCODING", "not UTF-8") from exc',
        '        text = raw.decode("utf-8", errors="replace")\n    except UnicodeDecodeError as exc:\n        raise JsonError("ENCODING", "not UTF-8") from exc',
    ),
    "trailing data is ignored": (
        '    if stripped[end:].strip(" \\t\\r\\n"):\n        raise JsonError("SYNTAX", "data after the end of the value")\n',
        "",
    ),
    "leading whitespace is refused": (
        '        value, end = _DECODER.raw_decode(text.lstrip(" \\t\\r\\n"))',
        "        value, end = _DECODER.raw_decode(text)",
    ),
    "syntax errors escape": (
        '    except json.JSONDecodeError as exc:\n        raise JsonError("SYNTAX", exc.msg) from exc',
        '    except ZeroDivisionError as exc:\n        raise JsonError("SYNTAX", "") from exc',
    ),
    "deep nesting crashes the reader": (
        '    except RecursionError as exc:\n        raise JsonError("DEPTH", "nesting too deep to read") from exc\n',
        "",
    ),
    "nesting is not limited": (
        '        if depth > MAX_DEPTH:\n            raise JsonError("DEPTH", f"nesting deeper than {MAX_DEPTH}")\n',
        "",
    ),
    "nesting is limited one level early": ("        if depth > MAX_DEPTH:", "        if depth >= MAX_DEPTH:"),
    "nesting is limited one level late": ("        if depth > MAX_DEPTH:", "        if depth > MAX_DEPTH + 1:"),
    "objects are not walked": (
        "            for key, child in node.items():\n                _no_surrogates(key)\n                stack.append((child, depth + 1))",
        "            pass",
    ),
    "lists are not walked": ("            stack.extend((child, depth + 1) for child in node)", "            pass"),
    "keys may hold surrogates": ("                _no_surrogates(key)\n", ""),
    "strings may hold surrogates": (
        "        elif isinstance(node, str):\n            _no_surrogates(node)",
        "        elif False:\n            pass",
    ),
    "surrogates are not refused": (
        '    except UnicodeEncodeError as exc:\n        raise JsonError("SURROGATE", "a string holds a lone surrogate") from exc',
        "    except UnicodeEncodeError:\n        return",
    ),
    "plain notation keeps the exponent": ('        text = format(value, "f")', "        text = str(value)"),
    "negative zero stays": ('        return "0" if value == 0 else text', "        return text"),
    "the digit limit is not applied": (
        '        if len(digits) + max(0, exponent) > MAX_PLAIN_DIGITS or len(digits) + max(0, -exponent) > MAX_PLAIN_DIGITS:\n            raise JsonError("NUMBER", "the number needs too many digits in plain notation")\n',
        "",
    ),
    "large exponents are not refused": ("len(digits) + max(0, exponent) > MAX_PLAIN_DIGITS or ", ""),
    "small exponents are not refused": (" or len(digits) + max(0, -exponent) > MAX_PLAIN_DIGITS:", ":"),
    "the digit limit is one too small": (
        "len(digits) + max(0, exponent) > MAX_PLAIN_DIGITS or",
        "len(digits) + max(0, exponent) >= MAX_PLAIN_DIGITS or",
    ),
    "the small digit limit is one too small": (
        "len(digits) + max(0, -exponent) > MAX_PLAIN_DIGITS:",
        "len(digits) + max(0, -exponent) >= MAX_PLAIN_DIGITS:",
    ),
    "numbers are converted through floats": ("            value = Decimal(self.text)", "            value = Decimal(float(self.text))"),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    module = mutant("hoshitori.jsonnum", old, new)
    try:
        found = failures(module)
    except Exception:  # a mutant that crashes under the checks is detected as well
        return
    assert found, f"mutant not detected: {name}"
