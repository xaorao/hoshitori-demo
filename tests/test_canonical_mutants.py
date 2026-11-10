"""Every rule of the canonical form is protected by a check that fails when the rule is broken.

Three guards are deliberately redundant and are not listed as mutants because breaking one leaves the other
rejecting the same input: a leading zero (the digits that follow are rejected by the container grammar), a
byte-order mark (the decoded U+FEFF is rejected by the parser) and a non-object top level in ``dumps`` (the
parser guard is mutated instead).
"""

from __future__ import annotations

import pytest

from hoshitori import canonical
from tests.canonical_checks import failures
from tests.mutation import mutant

_OBJECT_GUARD = 'if not isinstance(value, dict):\n        raise CanonicalError("a record must be a JSON object")\n    return value'
_UNSUPPORTED = 'raise CanonicalError(f"unsupported type {type(value).__name__}; quantities are decimal strings")'

MUTATIONS = {
    "no NFC check": ('if unicodedata.normalize("NFC", text) != text:', "if False:"),
    "no duplicate-key check": ("if key in result:", "if False:"),
    "no lone-surrogate check": ("if 0xD800 <= ord(char) <= 0xDFFF:", "if False:"),
    "negative zero accepted": ('if spelled == "-0":', "if False:"),
    "safe integer limit one too high": ("MAX_SAFE_INTEGER: Final = 2**53 - 1", "MAX_SAFE_INTEGER: Final = 2**53"),
    "keys sorted by UTF-32 instead of UTF-16": ('key.encode("utf-16-be")', 'key.encode("utf-32-be")'),
    "depth limit 65": ("MAX_DEPTH: Final = 64", "MAX_DEPTH: Final = 65"),
    "depth limit 63": ("MAX_DEPTH: Final = 64", "MAX_DEPTH: Final = 63"),
    "backspace not escaped": ('0x08: "\\\\b",', ""),
    "raw control character accepted": ("if ord(char) < 0x20:", "if False:"),
    "trailing content accepted": ("if self.pos != len(self.text):", "if False:"),
    "non-object top level accepted by the parser": (_OBJECT_GUARD, "return value"),
    "digit-run bound removed": ('if len(spelled.lstrip("-")) > len(str(MAX_SAFE_INTEGER)):', "if False:"),
    "unsupported in-memory type accepted": (_UNSUPPORTED, "return"),
}


def test_real_module_passes_every_check() -> None:
    assert failures(canonical) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.canonical", old, new)), f"mutant not detected: {name}"
