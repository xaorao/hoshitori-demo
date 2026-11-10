"""Checks of the signed-note and checkpoint modules, runnable against the real module or a mutant of it."""

from __future__ import annotations

import base64
import json
import types
from pathlib import Path

SPEC = json.loads((Path(__file__).parent / "vectors" / "signed_note_spec_example.json").read_text(encoding="utf-8"))
TEXT = b"hello\nworld\n"
ROOT = bytes(range(32))


def note_failures(note: types.ModuleType) -> list[str]:
    bad: list[str] = []
    error = note.NoteError

    def rejects(label: str, func, *args) -> None:  # type: ignore[no-untyped-def]
        try:
            func(*args)
        except error:
            return
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}")
            return
        bad.append(f"{label}: accepted")

    a = note.Signer.from_seed("a.example/log", bytes(range(32)))
    b = note.Signer.from_seed("b.example/log", bytes(range(32, 64)))
    good = note.sign_note(TEXT, [a])

    key = note.VerifierKey.from_vkey(SPEC["vkey"])  # exercises the key id formula (type byte and newline)
    try:
        text, names = note.verify_note(SPEC["note"].encode(), [key])
        if text != b"This is an example message.\n" or names != ["example.com/foo"]:
            bad.append("spec example: wrong result")
    except Exception as exc:
        bad.append(f"spec example: {type(exc).__name__}")
    rejects("tampered spec example", note.verify_note, SPEC["note"].replace("example message", "example massage").encode(), [key])
    rejects("no trusted key", note.verify_note, good, [b.verifier])
    impostor = note.sign_note(TEXT, [note.Signer.from_seed("a.example/log", bytes(range(32, 64)))])
    rejects("same name, other key", note.verify_note, impostor, [a.verifier])

    lines = good.split(b"\n")
    name, _, encoded = lines[-2].decode()[2:].rpartition(" ")
    blob = bytearray(base64.b64decode(encoded))
    blob[10] ^= 1
    broken_line = f"{note.EM_DASH} {name} {base64.b64encode(bytes(blob)).decode()}\n".encode()
    rejects("bad known signature", note.verify_note, good.replace(lines[-2] + b"\n", broken_line), [a.verifier])
    mixed = good.replace(lines[-2] + b"\n", broken_line) + note.sign_note(TEXT, [b]).split(b"\n\n", 1)[1]
    rejects("bad known signature next to a good one", note.verify_note, mixed, [a.verifier, b.verifier])
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
    sig_text = lines[-2].decode()
    assert sig_text.endswith("=")  # a 68-byte blob has one padding character and two spare bits
    tweaked_char = alphabet[alphabet.index(sig_text[-2]) ^ 1]
    tweaked = (sig_text[:-2] + tweaked_char + "=\n").encode()
    rejects("non-canonical base64 (spare bits set)", note.verify_note, good.replace(lines[-2] + b"\n", tweaked), [a.verifier])
    rejects("padding removed", note.verify_note, good.replace(b"=\n", b"\n", 1), [a.verifier])
    rejects("duplicate signature line", note.verify_note, good + good.split(b"\n\n", 1)[1], [a.verifier])
    rejects("no blank line", note.verify_note, good.replace(b"\n\n", b"\n", 1), [a.verifier])
    rejects("hyphen for em dash", note.verify_note, good.replace(note.EM_DASH.encode() + b" ", b"- ", 1), [a.verifier])
    ctl_text = b"hel\x01lo\n"
    ctl_line = f"{note.EM_DASH} a.example/log {base64.b64encode(a.verifier.key_id + a.sign(ctl_text)).decode()}\n".encode()
    rejects("control character under a valid signature", note.verify_note, ctl_text + b"\n" + ctl_line, [a.verifier])
    rejects("invalid UTF-8", note.verify_note, good.replace(b"hello", b"hel\xc3lo", 1), [a.verifier])
    rejects("plus in key name", note.check_key_name, "a+b")
    rejects("space in key name", note.check_key_name, "a b")
    rejects("empty key name", note.check_key_name, "")
    rejects("text without newline", note.sign_note, b"abc", [a])
    crowd = [a] + [note.Signer.from_seed(f"s{i}.example/log", bytes([i]) * 32) for i in range(note.MAX_SIGNATURES)]
    rejects("too many signatures", note.verify_note, note.sign_note(TEXT, crowd), [a.verifier])

    vkey = a.verifier.vkey()
    name_part, _hex_part, material = vkey.split("+", 2)
    rejects("vkey with wrong id", note.VerifierKey.from_vkey, f"{name_part}+00000000+{material}")
    try:
        if note.VerifierKey.from_vkey(vkey) != a.verifier:
            bad.append("vkey round trip")
    except Exception as exc:
        bad.append(f"vkey round trip: {type(exc).__name__}")
    return bad


def checkpoint_failures(cp: types.ModuleType, note: types.ModuleType) -> list[str]:
    bad: list[str] = []
    error = cp.CheckpointError

    def rejects(label: str, func, *args) -> None:  # type: ignore[no-untyped-def]
        try:
            func(*args)
        except error:
            return
        except Exception as exc:
            bad.append(f"{label}: raised {type(exc).__name__}")
            return
        bad.append(f"{label}: accepted")

    b64 = base64.b64encode(ROOT)
    signer = note.Signer.from_seed("example.org/log", bytes(range(32)))
    good = cp.sign_checkpoint(cp.Checkpoint("example.org/log", 7, ROOT), [signer])
    try:
        if cp.verify_checkpoint(good, [signer.verifier], "example.org/log") != cp.Checkpoint("example.org/log", 7, ROOT):
            bad.append("round trip: different checkpoint")
    except Exception as exc:
        bad.append(f"round trip: {type(exc).__name__}")
    impostor = note.Signer.from_seed("example.org/log", bytes(range(1, 33)))
    rejects(
        "untrusted signer",
        cp.verify_checkpoint,
        cp.sign_checkpoint(cp.Checkpoint("example.org/log", 7, ROOT), [impostor]),
        [signer.verifier],
        "example.org/log",
    )
    rejects("other origin", cp.verify_checkpoint, good, [signer.verifier], "example.org/other")
    for label, text in {
        "leading zero": b"o\n01\n" + b64 + b"\n",
        "sign": b"o\n+5\n" + b64 + b"\n",
        "trailing space in size": b"o\n5 \n" + b64 + b"\n",
        "short root": b"o\n5\n" + base64.b64encode(ROOT[:31]) + b"\n",
        "unpadded root": b"o\n5\n" + b64.rstrip(b"=") + b"\n",
        "extension line": b"o\n5\n" + b64 + b"\nx\n",
        "empty line": b"o\n5\n\n",
        "empty origin": b"\n5\n" + b64 + b"\n",
        "no final newline": b"o\n5\n" + b64,
        "size beyond 2**63 - 1": b"o\n9223372036854775808\n" + b64 + b"\n",
    }.items():
        rejects(label, cp.parse_text, text)
    rejects(
        "signed extension line",
        cp.verify_checkpoint,
        note.sign_note(b"example.org/log\n5\n" + b64 + b"\nx\n", [signer]),
        [signer.verifier],
        "example.org/log",
    )
    for label, args in {"origin with space": ("o x", 1, ROOT), "negative size": ("o", -1, ROOT), "short root": ("o", 1, ROOT[:5])}.items():
        rejects(f"constructor {label}", cp.Checkpoint, *args)
    return bad
