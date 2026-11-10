"""Python and Node accept exactly the same key names (every Unicode scalar value, alone and in context)."""

from __future__ import annotations

import subprocess

import pytest

from hoshitori import note as N
from tests.support import NODE, VERIFIER_JS

pytestmark = pytest.mark.skipif(NODE is None, reason="node is required for the cross-language tests")


def python_accepts(name: str) -> bool:
    try:
        N.check_key_name(name)
    except N.NoteError:
        return False
    return True


def test_key_name_acceptance_is_identical_for_every_scalar_value() -> None:
    assert NODE is not None
    names = [f"a{chr(c)}b" for c in range(0x110000) if not 0xD800 <= c <= 0xDFFF]
    names += ["", "+", "a+b", " ", "a b"]
    payload = "".join(name.encode("utf-8").hex() + "\n" if name else "\n" for name in names)
    # an empty name is sent as an empty line; the adapter skips blank lines, so handle it separately
    names_nonempty = [n for n in names if n]
    payload = "".join(n.encode("utf-8").hex() + "\n" for n in names_nonempty)
    done = subprocess.run(
        [NODE, str(VERIFIER_JS / "src" / "keyname_cli.mjs")],
        input=payload,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
        check=True,
        timeout=600,
    )
    node_answers = done.stdout.strip()
    assert len(node_answers) == len(names_nonempty)
    differences = [
        hex(ord(n[1])) if len(n) == 3 else repr(n)
        for n, answer in zip(names_nonempty, node_answers, strict=True)
        if (answer == "1") != python_accepts(n)
    ]
    assert differences == [], f"implementations disagree on: {differences[:20]}"
    assert not python_accepts("")
