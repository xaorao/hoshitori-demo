"""Scenarios for the C2SP tlog-proof reader and writer."""

from __future__ import annotations

import types
from collections.abc import Callable

from hoshitori import checkpoint, merkle
from hoshitori.note import Signer, b64encode

NOTE_SIGNER = Signer.from_seed("example.org/log", bytes(range(32)))
WITNESS = Signer.from_seed("witness.example", bytes(range(1, 33)))
NOTE = checkpoint.sign_checkpoint(checkpoint.Checkpoint("example.org/log", 7, merkle.sha256(b"root")), [NOTE_SIGNER, WITNESS])
HASHES = [merkle.sha256(bytes([i])) for i in range(5)]
HEADER = "c2sp.org/tlog-proof@v1"


def text(
    *,
    header: str = HEADER,
    extra: str | None = None,
    index: str = "5",
    path: list[str] | None = None,
    blank: bool = True,
    note: bytes = NOTE,
) -> bytes:
    lines = [header]
    if extra is not None:
        lines.append(f"extra {extra}")
    lines.append(f"index {index}")
    lines.extend(path if path is not None else [b64encode(h) for h in HASHES])
    body = "\n".join(lines) + ("\n\n" if blank else "\n")
    return body.encode() + note


def rejects(bad: list[str], mod: types.ModuleType, label: str, data: bytes, why: str) -> None:
    try:
        mod.parse_proof(data)
    except mod.ProofError as exc:
        if why not in str(exc):
            bad.append(f"{label}: rejected for another reason ({exc})")
        return
    except Exception as exc:
        bad.append(f"{label}: raised {type(exc).__name__} instead of ProofError: {exc}")
        return
    bad.append(f"{label}: accepted")


def reads_valid_proofs(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    parsed = mod.parse_proof(text())
    if (parsed.index, parsed.path, parsed.note, parsed.extra) != (5, tuple(HASHES), NOTE, None):
        bad.append("a plain proof was misread")
    with_extra = mod.parse_proof(text(extra=b64encode(b"app data")))
    if with_extra.extra != b"app data" or with_extra.index != 5:
        bad.append("extra data was misread")
    empty_extra = mod.parse_proof(text(extra=""))
    if empty_extra.extra != b"":
        bad.append("empty extra data was misread")
    first = mod.parse_proof(text(index="0", path=[]))
    if first.index != 0 or first.path != ():
        bad.append("index 0 with an empty path was misread")
    big = mod.parse_proof(text(index=str(2**63 - 1)))
    if big.index != 2**63 - 1:
        bad.append("the largest index was misread")
    if len(mod.parse_proof(text(path=[b64encode(HASHES[0])] * mod.MAX_PATH)).path) != mod.MAX_PATH:
        bad.append("a path of the maximum length was refused")
    for proof in (parsed, with_extra, empty_extra, first, big, mod.TlogProof(3, (), NOTE, None)):
        if mod.parse_proof(mod.format_proof(proof)) != proof:
            bad.append(f"round trip changed {proof.index}")
    if mod.format_proof(parsed) != text():
        bad.append("the formatter is not byte-exact for a plain proof")
    # the checkpoint note is returned verbatim, signatures and all
    if mod.parse_proof(text()).note.count(b"\xe2\x80\x94 ") != 2:
        bad.append("the note lost a signature line")
    return bad


def rejects_malformed_proofs(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    good_hash = b64encode(HASHES[0])
    rejects(bad, mod, "empty input", b"", "first line")
    rejects(bad, mod, "another version", text(header="c2sp.org/tlog-proof@v2"), "first line")
    rejects(bad, mod, "no header", text()[len(HEADER) + 1 :], "first line")
    rejects(bad, mod, "no index line", (HEADER + "\n\n").encode() + NOTE, "index line")
    rejects(bad, mod, "two extra lines", (HEADER + "\nextra AA==\nextra AA==\nindex 1\n\n").encode() + NOTE, "index line")
    rejects(bad, mod, "extra after the index", (HEADER + "\nindex 1\nextra AA==\n\n").encode() + NOTE, "bad base64")
    rejects(bad, mod, "extra that is not base64", text(extra="***"), "extra data")
    rejects(bad, mod, "extra in non-canonical base64", text(extra="AB=="), "extra data")
    rejects(bad, mod, "leading zero in the index", text(index="05"), "leading zeros")
    rejects(bad, mod, "negative index", text(index="-1"), "decimal")
    rejects(bad, mod, "index with a sign", text(index="+1"), "decimal")
    rejects(bad, mod, "index in another script", text(index=chr(0x665)), "decimal")
    rejects(bad, mod, "index with a space", text(index="5 "), "decimal")
    rejects(bad, mod, "empty index", text(index=""), "decimal")
    rejects(bad, mod, "index beyond the largest tree", text(index=str(2**63)), "out of range")
    rejects(bad, mod, "path hash not base64", text(path=["***"]), "bad base64")
    rejects(bad, mod, "path hash in non-canonical base64", text(path=[good_hash[:-2] + "B="]), "bad base64")
    rejects(bad, mod, "short path hash", text(path=[b64encode(HASHES[0][:31])]), "32 bytes")
    rejects(bad, mod, "long path hash", text(path=[b64encode(HASHES[0] + b"x")]), "32 bytes")
    rejects(bad, mod, "path longer than any tree", text(path=[good_hash] * (mod.MAX_PATH + 1)), "too long")
    rejects(bad, mod, "no blank line", text(blank=False, note=b"")[:-1], "blank line")
    rejects(bad, mod, "the checkpoint runs on from the path", text(blank=False), "bad base64")
    rejects(bad, mod, "no checkpoint", text(note=b""), "checkpoint is missing")
    rejects(bad, mod, "carriage returns", text().replace(b"\n", b"\r\n"), "bare newline")
    rejects(bad, mod, "a carriage return only inside the checkpoint", text(note=NOTE[:5] + b"\r" + NOTE[5:]), "bare newline")
    rejects(bad, mod, "not UTF-8", text() + b"\xff", "UTF-8")
    rejects(bad, mod, "too large", text() + b" " * mod.MAX_PROOF_BYTES, "too large")
    return bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "reads_valid_proofs": reads_valid_proofs,
    "rejects_malformed_proofs": rejects_malformed_proofs,
}


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
