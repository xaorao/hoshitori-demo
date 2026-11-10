"""Checks of ``hoshitori.claim_benchmark``, runnable against a mutant: the plain tests of ``tests/test_claim_benchmark.py`` with the module bound by name."""

from __future__ import annotations

import types

from tests import pytest_checks, test_claim_benchmark


def failures(mod: types.ModuleType) -> list[str]:
    return pytest_checks.run(test_claim_benchmark, "cb", mod)
