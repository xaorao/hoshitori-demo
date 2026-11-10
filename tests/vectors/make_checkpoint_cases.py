"""Regenerate ``checkpoint_cases.json`` (deterministic): signed checkpoints, crafted malformed ones and mutations.

Every case records the verdict of the Python implementation; the Node implementation must agree on all of them.
Crafted cases carry *valid signatures over malformed text*, so only the checkpoint rules (not the signature check)
can reject them.
"""

from __future__ import annotations

import base64
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from hoshitori import checkpoint as K
from hoshitori import note as N

ORIGIN = "example.org/hoshitori-test-log"
A = N.Signer.from_seed(ORIGIN, bytes(range(32)))
B = N.Signer.from_seed("example.org/witness", bytes(range(32, 64)))
KEYS = [A.verifier.vkey(), B.verifier.vkey()]
rng = random.Random(2026)
cases: list[dict] = []


def expect(note: bytes, trusted: list[str], origin: str) -> dict:
    try:
        cp = K.verify_checkpoint(note, [N.VerifierKey.from_vkey(v) for v in trusted], origin)
    except (K.CheckpointError, N.NoteError):
        return {"ok": False}
    return {"ok": True, "size": str(cp.size), "root": cp.root.hex()}


def add(label: str, note: bytes, trusted: list[str] | None = None, origin: str = ORIGIN) -> None:
    trusted = trusted or KEYS
    cases.append({"label": label, "note_hex": note.hex(), "trusted": trusted, "origin": origin, **expect(note, trusted, origin)})


def sign_text(text: bytes, signers: list[N.Signer] | None = None) -> bytes:
    return N.sign_note(text, signers or [A])


root = rng.randbytes(32)
b64 = base64.b64encode(root).decode()
o = ORIGIN
seeds: list[bytes] = []
for i in range(30):
    size = rng.choice([0, 1, 2, 7, 255, 256, 10**6, 2**40, 2**63 - 1])
    cp = K.Checkpoint(ORIGIN, size, rng.randbytes(32))
    for signers, trusted in (([A], KEYS[:1]), ([A, B], KEYS), ([B], KEYS[:1]), ([A, B], KEYS[1:])):
        note = K.sign_checkpoint(cp, signers)
        add(f"valid-{i}-{len(signers)}", note, trusted)
        seeds.append(note)

crafted = {
    "size-leading-zero": f"{o}\n05\n{b64}\n",
    "size-plus-sign": f"{o}\n+5\n{b64}\n",
    "size-negative": f"{o}\n-1\n{b64}\n",
    "size-too-large": f"{o}\n{2**63}\n{b64}\n",
    "size-max": f"{o}\n{2**63 - 1}\n{b64}\n",
    "size-zero": f"{o}\n0\n{b64}\n",
    "extension-line": f"{o}\n5\n{b64}\nextra\n",
    "empty-extension-line": f"{o}\n5\n{b64}\n\n",
    "root-short": f"{o}\n5\n{base64.b64encode(root[:31]).decode()}\n",
    "root-long": f"{o}\n5\n{base64.b64encode(root + b'x').decode()}\n",
    "root-unpadded": f"{o}\n5\n{b64.rstrip('=')}\n",
    "root-noncanonical": f"{o}\n5\n{b64[:-2]}{'B' if b64[-2] != 'B' else 'C'}=\n",
    "root-empty": f"{o}\n5\n\n",
    "origin-empty": f"\n5\n{b64}\n",
    "origin-space": f"example.org/log x\n5\n{b64}\n",
    "origin-other": f"example.org/other\n5\n{b64}\n",
    "two-lines": f"{o}\n5\n",
    "four-newlines": f"{o}\n5\n{b64}\n\n\n",
}
for label, text in crafted.items():
    add("crafted-" + label, sign_text(text.encode()))

good = K.sign_checkpoint(K.Checkpoint(ORIGIN, 9, root), [A])
sig_line = good.split(b"\n\n", 1)[1]
add("duplicate-signature-line", good + sig_line)
alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
text_part, _, line = good.partition(b"\n\n")
line_text = line.decode()
add("signature-noncanonical-base64", text_part + b"\n\n" + (line_text[:-3] + alphabet[alphabet.index(line_text[-3]) ^ 1] + "=\n").encode())
crowd = [A] + [N.Signer.from_seed(f"s{i}.example/log", bytes([i]) * 32) for i in range(N.MAX_SIGNATURES)]
add("too-many-signatures", sign_text(K.Checkpoint(ORIGIN, 3, root).text(), crowd))
add("max-signatures", sign_text(K.Checkpoint(ORIGIN, 3, root).text(), crowd[: N.MAX_SIGNATURES]))
add("control-char-valid-signature", sign_text(f"{o}\n5\n{b64}\n".encode()).replace(b"\n5\n", b"\n5\x01\n", 1))


def mutate(raw: bytes) -> bytes:
    data = bytearray(raw)
    for _ in range(rng.randint(1, 3)):
        choice = rng.random()
        if choice < 0.4 and data:
            data[rng.randrange(len(data))] = rng.randrange(256)
        elif choice < 0.7:
            data.insert(rng.randrange(len(data) + 1), rng.choice(b"\n +-=\x01A/0"))
        elif choice < 0.9 and data:
            del data[rng.randrange(len(data))]
        else:
            data = data[: rng.randrange(len(data) + 1)]
    return bytes(data)


for n in range(700):
    add(f"mutated-{n}", mutate(rng.choice(seeds)))

out = {"origin": ORIGIN, "vkeys": KEYS, "cases": cases}
Path(__file__).with_name("checkpoint_cases.json").write_text(json.dumps(out, separators=(",", ":")))
print(len(cases), "cases;", sum(c["ok"] for c in cases), "accepted")
