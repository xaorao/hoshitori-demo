"""Checks of ``hoshitori.desk_baselines``, runnable against a mutant: the plain tests of ``tests/test_desk_baselines.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_desk_baselines


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_desk_baselines, "db", mod)
