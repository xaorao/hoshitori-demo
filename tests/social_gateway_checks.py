"""Checks of ``hoshitori.social_gateway``, runnable against a mutant: the plain tests of ``tests/test_social_gateway.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_social_gateway


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_social_gateway, "sg", mod)
