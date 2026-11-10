"""Canonical record form: shared vectors, hostile inputs and properties.

The vector file holds the hand-built vectors (RFC 8785 samples, number spellings, string cases,
80 hostile inputs). ``expect_hex`` null means the input must be rejected under the record profile.
"""

from __future__ import annotations

import bisect
import json
import unicodedata
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import canonical as C
from hoshitori import unicode15
from tests.canonical_checks import failures
from tests.mutation import mutant

VECTORS = json.loads((Path(__file__).parent / "vectors" / "canonical_record.json").read_text())["vectors"]

# The rules of finding D-NFC, each shown by a mutant (the older rules are in test_canonical_mutants.py).
MUTATIONS = {
    "no repertoire check": ("            if not _assigned(ord(char)):", "            if False:"),
    "the check runs on ASCII text only": ("    if not text.isascii():  # every ASCII code point is assigned", "    if text.isascii():"),
    "the end of a range is not assigned": (
        "return index >= 0 and code <= unicode15.ASSIGNED[index][1]",
        "return index >= 0 and code < unicode15.ASSIGNED[index][1]",
    ),
    "a range starts one later": ("index = bisect.bisect_right(_STARTS, code) - 1", "index = bisect.bisect_left(_STARTS, code) - 1"),
    "the repertoire is the running Python's": (
        "            if not _assigned(ord(char)):",
        '            if unicodedata.category(char) == "Cn":',
    ),
    "the runtime is not checked": ('    if int(RUNTIME_UNICODE.split(".")[0]) < 15:', "    if False:"),
    "a runtime of Unicode 14 is accepted": (
        '    if int(RUNTIME_UNICODE.split(".")[0]) < 15:',
        '    if int(RUNTIME_UNICODE.split(".")[0]) < 14:',
    ),
    "the writer does not check the runtime": ("    require_runtime_unicode()\n    _validate(record, 1)", "    _validate(record, 1)"),
    "the parser does not check the runtime": (
        "    require_runtime_unicode()\n    value = _Parser(decode_utf8(raw)).parse_document()",
        "    value = _Parser(decode_utf8(raw)).parse_document()",
    ),
    "an old runtime is reported as a bad record": (
        "class UnicodeVersionError(RuntimeError):",
        "class UnicodeVersionError(CanonicalError):",
    ),
}


def test_real_module_passes_every_check() -> None:
    assert failures(C) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.canonical", old, new)), f"mutant not detected: {name}"


@pytest.mark.parametrize("vector", VECTORS, ids=[v["id"] for v in VECTORS])
def test_shared_vectors(vector: dict[str, str | None]) -> None:
    raw = bytes.fromhex(str(vector["input_hex"]))
    if vector["expect_hex"] is None:
        with pytest.raises(C.CanonicalError):
            C.canonicalize(raw)
    else:
        assert C.canonicalize(raw).hex() == vector["expect_hex"]


def test_vector_file_is_complete() -> None:
    assert len(VECTORS) >= 146
    assert sum(v["expect_hex"] is not None for v in VECTORS) >= 20
    assert sum(v["expect_hex"] is None for v in VECTORS) >= 100


def test_key_order_is_utf16_code_unit_order() -> None:
    # U+1F600 is the surrogate pair 0xD83D 0xDE00 in UTF-16, so it sorts before U+E000; by code point it sorts after.
    # Both keys are NFC-stable. (U+20AC against U+1F600 would not distinguish the two orders.)
    raw = json.dumps({"\ue000": 1, "\U0001f600": 2}, ensure_ascii=False).encode()
    assert C.canonicalize(raw) == '{"\U0001f600":2,"\ue000":1}'.encode()


def test_decimal_strings_are_preserved_exactly() -> None:
    record: dict[str, C.JsonValue] = {"price": "64000.10", "size": "0.00010000", "n": 7}
    assert C.dumps(record) == b'{"n":7,"price":"64000.10","size":"0.00010000"}'


@pytest.mark.parametrize(
    "bad",
    [
        1.5,
        float("nan"),
        2**53,
        -(2**53),
        (1, 2),
        {1: "a"},
        b"bytes",
        {"s": "e" + chr(0x301)},
        {"s": chr(0xD800)},
        {"k" + chr(0xD800): 1},
    ],
)
def test_in_memory_values_outside_the_profile_are_rejected(bad: object) -> None:
    with pytest.raises(C.CanonicalError):
        C.dumps({"v": bad})  # type: ignore[dict-item]


def test_top_level_must_be_object() -> None:
    for raw in (b"[]", b'"x"', b"1", b"null"):
        with pytest.raises(C.CanonicalError):
            C.canonicalize(raw)
    with pytest.raises(C.CanonicalError):
        C.dumps([])  # type: ignore[arg-type]


def test_nesting_limit_is_exact_for_parser_and_serialiser() -> None:
    def nested(levels: int) -> dict[str, C.JsonValue]:
        value: dict[str, C.JsonValue] = {}
        for _ in range(levels - 1):
            value = {"a": value}
        return value

    assert C.dumps(nested(64))
    with pytest.raises(C.CanonicalError):
        C.dumps(nested(65))
    ok = b'{"a":' * 63 + b"{}" + b"}" * 63
    assert C.canonicalize(ok) == ok
    with pytest.raises(C.CanonicalError):
        C.canonicalize(b'{"a":' * 64 + b"{}" + b"}" * 64)
    # a scalar inside the deepest allowed object does not add a level
    deepest = nested(64)
    node = deepest
    for _ in range(62):
        node = node["a"]  # type: ignore[assignment]
    node["a"] = {"leaf": 1}
    assert C.dumps(deepest)


# ---- properties ----------------------------------------------------------------------------------------------
_STARTS_15 = [first for first, _ in unicode15.ASSIGNED]


def _in_unicode_15(char: str) -> bool:
    index = bisect.bisect_right(_STARTS_15, ord(char)) - 1
    return index >= 0 and ord(char) <= unicode15.ASSIGNED[index][1]


# texts of code points assigned in Unicode 15.0 only (the record profile's repertoire), in NFC
_texts = st.text(alphabet=st.characters(exclude_categories=["Cs"]), max_size=12).map(
    lambda s: unicodedata.normalize("NFC", "".join(c for c in s if _in_unicode_15(c)))
)
_values = st.recursive(
    st.none() | st.booleans() | st.integers(-(2**53 - 1), 2**53 - 1) | _texts,
    lambda children: st.lists(children, max_size=4) | st.dictionaries(_texts, children, max_size=4),
    max_leaves=12,
)
_records = st.dictionaries(_texts, _values, max_size=5)


@settings(max_examples=400, deadline=None)
@given(_records)
def test_round_trip_and_idempotence(record: dict[str, C.JsonValue]) -> None:
    once = C.dumps(record)
    assert C.parse_record(once) == record
    assert C.canonicalize(once) == once


@settings(max_examples=300, deadline=None)
@given(_records, st.randoms(use_true_random=False))
def test_key_order_in_the_input_does_not_change_the_output(record: dict[str, C.JsonValue], rng) -> None:  # type: ignore[no-untyped-def]
    items = list(record.items())
    rng.shuffle(items)
    reordered = dict(items)
    assert C.dumps(reordered) == C.dumps(record)


@settings(max_examples=300, deadline=None)
@given(_records, st.sampled_from([",", ", ", " , "]), st.booleans())
def test_spelling_of_the_input_does_not_change_the_output(record, sep, ascii_escapes) -> None:  # type: ignore[no-untyped-def]
    spelled = json.dumps(record, separators=(sep, ":"), ensure_ascii=ascii_escapes, sort_keys=False).encode()
    assert C.canonicalize(spelled) == C.dumps(record)
