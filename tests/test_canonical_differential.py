"""Two independent implementations (Python, Node) must agree on every input: accepted bytes and rejections.

Inputs: the shared vectors, valid records spelled in randomised ways, and many mutations of both. A disagreement
on any input is a defect in one of them. The random generators are seeded, so a failure reproduces.
"""

from __future__ import annotations

import json
import random
import unicodedata
from pathlib import Path

import pytest

from hoshitori import canonical as C
from tests.support import NODE, run_node_canonical

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for the cross-language tests")

VECTORS = json.loads((Path(__file__).parent / "vectors" / "canonical_record.json").read_text())["vectors"]
# Every code point here is assigned in Unicode 15.0 (the record profile's repertoire), so the generator mostly makes valid records;
# U+10FFFD (private use) stands where the noncharacter U+10FFFF stood, which the profile now refuses.
_POOL = ["a", "Z", " ", "0", '"', "\\", "/"] + [
    chr(code) for code in (0x7F, 0x80, 0xE9, 0x20AC, 0xFB33, 0x1F600, 0x10FFFD, 0x2028, 0xFFFD, 0x00, 0x1F, 0x301, 0xC5)
]


def python_verdict(raw: bytes) -> tuple[bool, str]:
    try:
        return True, C.canonicalize(raw).hex()
    except C.CanonicalError:
        return False, ""


def random_text(rng: random.Random) -> str:
    return unicodedata.normalize("NFC", "".join(rng.choice(_POOL) for _ in range(rng.randint(0, 8))))


def random_value(rng: random.Random, depth: int) -> C.JsonValue:
    pick = rng.random()
    if depth > 4 or pick < 0.3:
        kind = rng.random()
        if kind < 0.4:
            return random_text(rng)
        if kind < 0.7:
            return rng.choice([0, 1, -1, 9007199254740991, -9007199254740991, rng.randint(-(10**6), 10**6)])
        return rng.choice([True, False, None])
    if pick < 0.6:
        return [random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]
    return {random_text(rng): random_value(rng, depth + 1) for _ in range(rng.randint(0, 4))}


def spelled(value: C.JsonValue, rng: random.Random) -> str:
    return json.dumps(value, ensure_ascii=rng.random() < 0.5, separators=rng.choice([(",", ":"), (", ", " : ")]))


def mutate(raw: bytes, rng: random.Random) -> bytes:
    data = bytearray(raw)
    for _ in range(rng.randint(1, 3)):
        choice = rng.random()
        if choice < 0.4 and data:
            data[rng.randrange(len(data))] = rng.randrange(256)
        elif choice < 0.7:
            data.insert(rng.randrange(len(data) + 1), rng.choice(b'{}[]",:\\-0123456789eE.+tfnul \n'))
        elif choice < 0.9 and data:
            del data[rng.randrange(len(data))]
        else:
            data = data[: rng.randrange(len(data) + 1)]
    return bytes(data)


def compare(items: list[tuple[str, bytes]]) -> None:
    node = run_node_canonical(items)
    for ident, raw in items:
        ok, out = python_verdict(raw)
        other = node[ident]
        assert other["ok"] == ok, f"{ident}: python ok={ok}, node ok={other['ok']} ({other.get('error')}) for {raw!r}"
        if ok:
            assert other["hex"] == out, f"{ident}: outputs differ for {raw!r}"
        else:
            assert not str(other["error"]).startswith("INTERNAL"), f"{ident}: node internal error {other['error']}"


def test_shared_vectors_agree() -> None:
    compare([(v["id"], bytes.fromhex(v["input_hex"])) for v in VECTORS])


def test_random_valid_records_agree_and_hash_identically() -> None:
    rng = random.Random(20261029)
    items = []
    for n in range(1500):
        record = {random_text(rng): random_value(rng, 0) for _ in range(rng.randint(0, 4))}
        items.append((f"valid-{n}", spelled(record, rng).encode()))
    compare(items)
    node = run_node_canonical(items)
    accepted = sum(1 for ident, _ in items if node[ident]["ok"])
    assert accepted > 1000  # the generator mostly produces valid records, so the test is not vacuous


def test_mutated_inputs_agree() -> None:
    rng = random.Random(8020261029)
    seeds = [bytes.fromhex(v["input_hex"]) for v in VECTORS if v["expect_hex"] is not None]
    for _ in range(300):
        record = {random_text(rng): random_value(rng, 0) for _ in range(rng.randint(1, 3))}
        seeds.append(spelled(record, rng).encode())
    items = [(f"mut-{n}", mutate(rng.choice(seeds), rng)) for n in range(6000)]
    compare(items)
    node = run_node_canonical(items)
    assert 100 < sum(1 for ident, _ in items if node[ident]["ok"]) < 5900  # both outcomes are exercised
