"""The signed-note and checkpoint rules are each protected by a check that fails when the rule is broken.

One guard is deliberately redundant and has no mutant: the canonical-text comparison in ``verify_checkpoint`` repeats
what the strict ``parse_text`` already enforces.
"""

from __future__ import annotations

import pytest

from hoshitori import checkpoint, note
from tests.mutation import mutant
from tests.note_checks import checkpoint_failures, note_failures

NOTE_MUTATIONS = {
    "an untrusted signature is enough": ('        raise NoteError("no signature from a trusted key")', "        pass"),
    "a bad known signature is ignored": ('raise NoteError(f"signature of {item.name} does not verify") from exc', "continue"),
    "keys matched by name only": (
        "trusted = {(key.name, key.key_id): key for key in keys}",
        "trusted = {(key.name, key.key_id[:0]): key for key in keys}",
    ),
    "non-canonical base64 accepted": ("if b64encode(data) != text:", "if False:"),
    "duplicate signature accepted": ("if (item.name, item.key_id) in seen:", "if False:"),
    "em dash not required": ("if not line.startswith(prefix):", "if False:"),
    "plus allowed in key names": ('or "+" in name', ""),
    "key id without the newline byte": ('name.encode("utf-8") + b"\\n" + bytes', 'name.encode("utf-8") + bytes'),
    "control characters allowed in notes": (
        'if any(ord(ch) < 0x20 and ch != "\\n" for ch in decoded):\n        raise NoteError("note contains',
        'if False:\n        raise NoteError("note contains',
    ),
    "no limit on signatures": ("if len(signatures) > MAX_SIGNATURES:", "if False:"),
    "vkey id not checked": ("if hex_id != key.key_id.hex():", "if False:"),
    "text without newline accepted": (
        'if not decoded.endswith("\\n"):\n        raise NoteError("note text must end',
        'if False:\n        raise NoteError("note text must end',
    ),
}

CHECKPOINT_MUTATIONS = {
    "leading zeros accepted": ('or (size_text != "0" and size_text.startswith("0"))', ""),
    "extension lines accepted": ("if len(lines) != 3 or not all(lines):", "if len(lines) < 3 or not all(lines):"),
    "origin not checked": ("if checkpoint.origin != origin:", "if False:"),
    "root length not checked": ("if type(self.root) is not bytes or len(self.root) != HASH_LENGTH:", "if False:"),
    "size limit removed": ("not 0 <= self.size <= MAX_TREE_SIZE", "not 0 <= self.size"),
    "origin may contain spaces": ("any(ord(ch) < 0x20 or ch.isspace() for ch in self.origin)", "any(ord(ch) < 0x20 for ch in self.origin)"),
    "signature not required": ("text, _ = verify_note(note, keys)", 'text = note.split(b"\\n\\n", 1)[0] + b"\\n"'),
}


def test_real_modules_pass_every_check() -> None:
    assert note_failures(note) == []
    assert checkpoint_failures(checkpoint, note) == []


@pytest.mark.parametrize("name", NOTE_MUTATIONS)
def test_each_broken_note_rule_is_detected(name: str) -> None:
    old, new = NOTE_MUTATIONS[name]
    try:
        found = note_failures(mutant("hoshitori.note", old, new))
    except Exception:
        return
    assert found, f"mutant not detected: {name}"


@pytest.mark.parametrize("name", CHECKPOINT_MUTATIONS)
def test_each_broken_checkpoint_rule_is_detected(name: str) -> None:
    old, new = CHECKPOINT_MUTATIONS[name]
    try:
        found = checkpoint_failures(mutant("hoshitori.checkpoint", old, new), note)
    except Exception:
        return
    assert found, f"mutant not detected: {name}"
