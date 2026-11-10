"""RFC 3161 token verification against its scenarios."""

from __future__ import annotations

import pytest

from hoshitori import tsa
from tests.tsa_checks import SCENARIOS


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](tsa) == []
