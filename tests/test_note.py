"""Signed notes: the specification's example, round trips, and the rejection rules."""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from hoshitori import note as N

SPEC = json.loads((Path(__file__).parent / "vectors" / "signed_note_spec_example.json").read_text(encoding="utf-8"))
SEED_A = bytes(range(32))
SEED_B = bytes(range(32, 64))
TEXT = b"hello\nworld\n"


def signer(name: str = "example.org/log", seed: bytes = SEED_A) -> N.Signer:
    return N.Signer.from_seed(name, seed)


def test_specification_example_verifies() -> None:
    key = N.VerifierKey.from_vkey(SPEC["vkey"])
    text, names = N.verify_note(SPEC["note"].encode(), [key])
    assert text == b"This is an example message.\n"
    assert names == ["example.com/foo"]
    assert key.vkey() == SPEC["vkey"]


def test_tampering_with_the_specification_example_fails() -> None:
    key = N.VerifierKey.from_vkey(SPEC["vkey"])
    with pytest.raises(N.NoteError):
        N.verify_note(SPEC["note"].replace("example message", "example massage").encode(), [key])


def test_round_trip_and_two_signers() -> None:
    a, b = signer("a.example/log", SEED_A), signer("b.example/log", SEED_B)
    note = N.sign_note(TEXT, [a, b])
    text, names = N.verify_note(note, [a.verifier, b.verifier])
    assert text == TEXT and names == ["a.example/log", "b.example/log"]
    # one trusted key is enough; the other signature is ignored as unknown
    assert N.verify_note(note, [b.verifier])[1] == ["b.example/log"]


def test_unknown_keys_are_ignored_but_a_known_bad_signature_rejects_the_note() -> None:
    a, b = signer("a.example/log", SEED_A), signer("b.example/log", SEED_B)
    good = N.sign_note(TEXT, [a])
    # same name, different key: a different id, so unknown, so no trusted signature
    impostor = N.sign_note(TEXT, [N.Signer.from_seed("a.example/log", SEED_B)])
    with pytest.raises(N.NoteError, match="no signature from a trusted key"):
        N.verify_note(impostor, [a.verifier])
    # a known key whose signature does not verify rejects the whole note even if another signature is fine
    lines = good.split(b"\n")
    sig_line = lines[-2].decode()
    name, _, encoded = sig_line[2:].rpartition(" ")
    blob = bytearray(base64.b64decode(encoded))
    blob[10] ^= 1
    broken = b"\n".join([*lines[:-2], f"{N.EM_DASH} {name} {base64.b64encode(bytes(blob)).decode()}".encode(), b""])
    with pytest.raises(N.NoteError, match="does not verify"):
        N.verify_note(broken, [a.verifier])
    two = N.sign_note(TEXT, [b])
    mixed = broken.rstrip(b"\n") + b"\n" + two.split(b"\n\n", 1)[1]
    with pytest.raises(N.NoteError, match="does not verify"):
        N.verify_note(mixed, [a.verifier, b.verifier])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda n: n.replace(b"\n\n", b"\n", 1),  # no blank line
        lambda n: n.rstrip(b"\n"),  # no trailing newline
        lambda n: n + b"\n",  # blank line, no signature after it
        lambda n: n.replace("— ".encode(), b"- ", 1),  # ascii hyphen for the em dash
        lambda n: n.replace(b"hello", b"hel\x01lo", 1),  # control character in the text
        lambda n: n.replace(b"hello", b"hel\xc3lo", 1),  # invalid UTF-8
        lambda n: n.replace(b"=\n", b"\n", 1),  # padding removed (non-canonical base64)
        lambda n: n[: n.index("—".encode())] + "— \n".encode(),  # empty signature line
        lambda n: b"",
    ],
)
def test_malformed_notes_are_rejected(mutate) -> None:  # type: ignore[no-untyped-def]
    a = signer()
    with pytest.raises(N.NoteError):
        N.verify_note(mutate(N.sign_note(TEXT, [a])), [a.verifier])


def test_duplicate_name_and_id_is_rejected_and_same_name_other_id_is_allowed() -> None:
    a = signer()
    note = N.sign_note(TEXT, [a])
    line = note.split(b"\n\n", 1)[1]
    with pytest.raises(N.NoteError, match="duplicate"):
        N.verify_note(note + line, [a.verifier])
    other = N.Signer.from_seed("example.org/log", SEED_B)
    both = N.sign_note(TEXT, [a, other])
    assert N.verify_note(both, [a.verifier, other.verifier])[1] == ["example.org/log", "example.org/log"]


def test_signature_count_is_limited() -> None:
    signers = [N.Signer.from_seed(f"s{i}.example/log", bytes([i]) * 32) for i in range(N.MAX_SIGNATURES + 1)]
    with pytest.raises(N.NoteError, match="too many"):
        N.verify_note(N.sign_note(TEXT, signers), [signers[0].verifier])
    ok = N.sign_note(TEXT, signers[: N.MAX_SIGNATURES])
    assert N.verify_note(ok, [signers[0].verifier])[1] == ["s0.example/log"]


@pytest.mark.parametrize("name", ["", "has space", "tab\tname", "plus+name", "new\nline", "no" + chr(0xA0) + "break"])
def test_key_names_are_restricted(name: str) -> None:
    with pytest.raises(N.NoteError):
        N.check_key_name(name)


def test_vkey_parsing_is_strict() -> None:
    good = signer().verifier.vkey()
    assert N.VerifierKey.from_vkey(good) == signer().verifier
    name, hex_id, material = good.split("+", 2)  # the base64 material may itself contain '+'
    assert "+" in material, "the fixture key must exercise a '+' inside the base64 part"
    wrong_type = base64.b64encode(b"\x02" + b"\x00" * 32).decode()
    for bad in (
        f"{name}+{hex_id}",
        f"{name}+00000000+{material}",
        f"{name}+{hex_id}+{material[:-4]}",
        f"{name}+{hex_id}+{wrong_type}",
        f"+{hex_id}+{material}",
        f"{name}+{hex_id[:-1]}+{material}",
    ):
        with pytest.raises(N.NoteError):
            N.VerifierKey.from_vkey(bad)


def test_text_must_end_in_newline_and_contain_no_controls() -> None:
    for bad in (b"no newline", b"tab\there\n", b"\xff\n"):
        with pytest.raises(N.NoteError):
            N.sign_note(bad, [signer()])
