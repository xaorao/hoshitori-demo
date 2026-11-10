"""Checks of ``hoshitori.adjudication_store``, runnable against a mutant: the plain tests of ``tests/test_adjudication_store.py``."""

from __future__ import annotations

import types

from tests import pytest_checks, test_adjudication_store


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_adjudication_store, "store_mod", mod)
