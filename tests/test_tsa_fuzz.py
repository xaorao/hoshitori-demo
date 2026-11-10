"""Random damage to tokens, replayed through both verifiers: they must agree on acceptance every time (slow)."""

from __future__ import annotations

import base64
import json
import os
import random
from typing import Any

import pytest

from hoshitori import tsa
from tests import tsa_pki as P
from tests.test_tsa_differential import NODE, run_node
from tests.tsa_checks import DIGEST, NONCE, VECTORS, standard

ROUNDS = int(os.environ.get("HOSHITORI_FUZZ_ROUNDS", "20000"))  # raise it for a long run; the default keeps the slow suite short

pytestmark = [pytest.mark.slow, pytest.mark.skipif(NODE is None, reason="Node.js is not installed")]


def mutate(rng: random.Random, blob: bytes) -> bytes:
    data = bytearray(blob)
    for _ in range(rng.choice([1, 1, 2, 3])):
        kind = rng.choice(["flip", "flip", "byte", "delete", "insert", "truncate", "dup"])
        at = rng.randrange(len(data)) if data else 0
        if kind == "flip" and data:
            data[at] ^= 1 << rng.randrange(8)
        elif kind == "byte" and data:
            data[at] = rng.randrange(256)
        elif kind == "delete" and data:
            del data[at : at + rng.choice([1, 1, 2, 5])]
        elif kind == "insert":
            data[at:at] = bytes(rng.randrange(256) for _ in range(rng.choice([1, 2, 4])))
        elif kind == "truncate" and data:
            del data[at:]
        elif kind == "dup" and data:
            data[at:at] = data[at : at + rng.choice([2, 8, 30])]
    return bytes(data)


def test_damaged_tokens_are_judged_alike() -> None:
    rng = random.Random(20261029)
    seeds: list[tuple[bytes, list[Any], bytes, int | None]] = []
    chain = standard("A")
    pins = [{"sha256": chain.root.fingerprint, "operator": "a", "der": base64.b64encode(chain.root.der).decode()}]
    for options in (
        P.TokenOptions(nonce=NONCE),
        P.TokenOptions(nonce=NONCE, sid="subject_key_identifier"),
        P.TokenOptions(nonce=NONCE, bare=True),
        P.TokenOptions(nonce=NONCE, include_ess="v1"),
    ):
        seeds.append((P.build_token(DIGEST, chain, options), pins, DIGEST, NONCE))
    rsa_chain = P.Chain.build("R", key=P.rsa_key(2048))
    rsa_pins = [{"sha256": rsa_chain.root.fingerprint, "operator": "r", "der": base64.b64encode(rsa_chain.root.der).decode()}]
    seeds.append((P.build_token(DIGEST, rsa_chain, P.TokenOptions(nonce=NONCE, signature_algorithm="sha256_rsa")), rsa_pins, DIGEST, NONCE))
    for entry in json.loads((VECTORS / "tsa_live.json").read_text())["entries"]:
        if entry["authority"] == "apple":
            continue
        live_pins = [{"sha256": entry["pinned_root_sha256"], "operator": entry["authority"], "der": entry["pinned_root_der_base64"]}]
        seeds.append((base64.b64decode(entry["token_base64"]), live_pins, bytes.fromhex(entry["digest_sha256_hex"]), None))

    queries: list[dict[str, Any]] = []
    python_ok: list[bool] = []
    for round_number in range(ROUNDS):
        blob, pin_list, digest, nonce = seeds[round_number % len(seeds)]
        damaged = mutate(rng, blob)
        query = {
            "token": base64.b64encode(damaged).decode(),
            "digest": digest.hex(),
            "pins": pin_list,
            "nonce": None if nonce is None else str(nonce),
        }
        policy = tsa.TokenPolicy(tuple(tsa.PinnedRoot(p["sha256"], p["operator"], base64.b64decode(p["der"])) for p in pin_list))
        try:
            tsa.verify_token(damaged, digest, policy, nonce=nonce)
            python_ok.append(True)
        except tsa.TokenError:
            python_ok.append(False)
        queries.append(query)
    answers = run_node(queries)
    different = [i for i, (py, node) in enumerate(zip(python_ok, answers, strict=True)) if py != node["ok"]]
    assert sum(python_ok) > 100, "the stream should contain tokens that survive their damage"
    if different:
        # the seed tokens use fresh keys every run, so keep the exact inputs of a disagreement for analysis
        import tempfile
        from pathlib import Path

        folder = Path(tempfile.mkdtemp(prefix="hoshitori-fuzz-"))
        for i in different[:20]:
            (folder / f"case-{i}.json").write_text(json.dumps({**queries[i], "python": python_ok[i], "node": answers[i]}))
        raise AssertionError(f"{len(different)} disagreements; inputs saved in {folder}")
