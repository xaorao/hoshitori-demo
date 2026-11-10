"""Checks of ``hoshitori.desk_live``, runnable against a mutant: the plain tests of ``tests/test_desk_live.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_desk_live


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_desk_live, "desk_live", mod)
