"""Checks of ``hoshitori.challenge_store``, runnable against a mutant: the plain tests of ``tests/test_challenge_store.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_challenge_store, test_challenge_store_strip


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_challenge_store, "cs", mod) + pytest_checks.run(test_challenge_store_strip, "cs", mod)
