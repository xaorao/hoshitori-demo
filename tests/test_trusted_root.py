"""The trusted-root parser and update client against their scenarios."""

from __future__ import annotations

import pytest

from hoshitori import canonical, trusted_root
from tests import root_support as R
from tests.root_checks import SCENARIOS


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](trusted_root) == []


def test_a_root_with_a_code_point_outside_unicode_15_is_malformed() -> None:
    # the same canonical rules read the root as the records (finding D-NFC): U+105D2 U+0307 composes only under Unicode 16.0 and later
    document = R.make_document(1)
    document["time_stamp"]["operators"]["operator-a"]["parent"] = "@placeholder@"  # type: ignore[index]
    payload = canonical.dumps(document).replace(b"@placeholder@", "\U000105d2\u0307".encode())  # type: ignore[arg-type]
    with pytest.raises(trusted_root.RootError) as caught:
        trusted_root.parse_root(payload)
    assert (caught.value.code, str(caught.value)) == (
        "MALFORMED",
        "MALFORMED: document: code point U+105D2 is not assigned in Unicode 15.0",
    )
