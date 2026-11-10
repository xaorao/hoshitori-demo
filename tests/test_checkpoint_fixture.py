"""The cross-language checkpoint fixture still matches this implementation's verdicts (guards regeneration)."""

from __future__ import annotations

import json
from pathlib import Path

from hoshitori import checkpoint as K
from hoshitori import note as N

FIXTURE = json.loads((Path(__file__).parent / "vectors" / "checkpoint_cases.json").read_text())


def test_every_case_matches() -> None:
    accepted = 0
    for case in FIXTURE["cases"]:
        keys = [N.VerifierKey.from_vkey(v) for v in case["trusted"]]
        try:
            cp = K.verify_checkpoint(bytes.fromhex(case["note_hex"]), keys, case["origin"])
        except (K.CheckpointError, N.NoteError):
            assert not case["ok"], case["label"]
            continue
        assert case["ok"], case["label"]
        assert (str(cp.size), cp.root.hex()) == (case["size"], case["root"]), case["label"]
        accepted += 1
    assert accepted > 90
    assert any(len(v.split("+")) > 3 for v in FIXTURE["vkeys"]), "the fixture must contain a key with '+' in its base64 part"
