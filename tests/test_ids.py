"""Natural-key identifiers: scenarios and mutants."""

from __future__ import annotations

import pytest

from hoshitori import ids
from tests.ids_checks import failures
from tests.mutation import mutant


def test_real_module_passes_every_check() -> None:
    assert failures(ids) == []


MUTATIONS: dict[str, tuple[str, str]] = {
    "the prefix is not checked": (
        '    if PREFIX.fullmatch(prefix) is None:\n        raise ValueError("an id prefix is two to four lower-case letters")\n',
        "",
    ),
    "a prefix may be matched in part": ("PREFIX.fullmatch(prefix)", "PREFIX.match(prefix)"),
    "a prefix may be one letter": ('PREFIX: Final = re.compile(r"[a-z]{2,4}")', 'PREFIX: Final = re.compile(r"[a-z]{1,4}")'),
    "a prefix may be five letters": ('PREFIX: Final = re.compile(r"[a-z]{2,4}")', 'PREFIX: Final = re.compile(r"[a-z]{2,5}")'),
    "a prefix may be three letters at most": ('PREFIX: Final = re.compile(r"[a-z]{2,4}")', 'PREFIX: Final = re.compile(r"[a-z]{2,3}")'),
    "a prefix may be upper case": ('PREFIX: Final = re.compile(r"[a-z]{2,4}")', 'PREFIX: Final = re.compile(r"[a-zA-Z]{2,4}")'),
    "a prefix may hold digits": ('PREFIX: Final = re.compile(r"[a-z]{2,4}")', 'PREFIX: Final = re.compile(r"[a-z0-9]{2,4}")'),
    "the digest is shorter": ("[:26]", "[:25]"),
    "the digest is longer": ("[:26]", "[:27]"),
    "the digest is not lower case": ('.decode("ascii").lower()', '.decode("ascii")'),
    "the prefix is dropped": ("    return prefix + base64", "    return base64"),
    "the prefix is not part of the key": ("    return prefix + base64", "    return prefix[::-1] + base64"),
    "the key is hashed as text": ("canonical.dumps(key)", "str(key).encode()"),
    "the digest is of a different hash": ("hashlib.sha256(", "hashlib.sha1("),
    "the digest is hex": ("base64.b32encode(digest)", "base64.b16encode(digest)"),
}


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert failures(mutant("hoshitori.ids", old, new)), f"nothing noticed: {name}"
