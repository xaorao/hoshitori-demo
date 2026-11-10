"""Runs the plain pytest-style tests of a test file against a module that may be a mutant, so a mutation sweep can judge them.

The tests refer to the module under test by one global name (``v`` in ``test_voices``). ``run`` binds that name to the module it is given,
calls every ``test_*`` function (with its ``parametrize`` cases and a ``tmp_path``) and returns what failed. It reads only what these
test files use: ``parametrize`` and ``tmp_path``.
"""

from __future__ import annotations

import inspect
import itertools
import tempfile
import traceback
import types
from collections.abc import Callable
from pathlib import Path
from typing import Any


def _cases(fn: Callable[..., Any]) -> list[dict[str, Any]]:
    marks = [m for m in getattr(fn, "pytestmark", []) if m.name == "parametrize"]
    per_mark: list[list[dict[str, Any]]] = []
    for m in marks:
        names, values = m.args[0], m.args[1]
        keys = [n.strip() for n in names.split(",")] if isinstance(names, str) else list(names)
        per_mark.append([dict(zip(keys, v if len(keys) > 1 else (v,), strict=True)) for v in values])
    if not per_mark:
        return [{}]
    return [{k: v for part in combo for k, v in part.items()} for combo in itertools.product(*per_mark)]


def run(tests: types.ModuleType, name: str, mod: types.ModuleType) -> list[str]:
    """Every failed ``test_*`` of ``tests`` with ``tests.<name>`` bound to ``mod``."""
    previous = getattr(tests, name)
    setattr(tests, name, mod)
    bad: list[str] = []
    try:
        for fname, fn in sorted(vars(tests).items()):
            if not (fname.startswith("test_") and inspect.isfunction(fn)):
                continue
            wants = inspect.signature(fn).parameters
            for number, case in enumerate(_cases(fn)):
                kwargs = dict(case)
                with tempfile.TemporaryDirectory() as tmp:
                    if "tmp_path" in wants:
                        kwargs["tmp_path"] = Path(tmp)
                    try:
                        fn(**kwargs)
                    except Exception as exc:
                        bad.append(f"{fname}[{number}]: {type(exc).__name__}: {str(exc)[:200]}\n{traceback.format_exc()[-300:]}")
    finally:
        setattr(tests, name, previous)
    return bad
