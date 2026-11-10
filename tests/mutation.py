"""A tiny mutation harness: build a copy of a module with one rule deliberately broken.

A test suite that passes proves little until it is shown to fail on a broken implementation. ``mutant`` replaces
exactly one occurrence of ``old`` with ``new`` in the module's source and returns the compiled copy; the caller runs
its checks against it and requires at least one failure. An ``old`` that does not occur exactly once is an error, so
a mutation cannot silently stop applying when the source changes.
"""

from __future__ import annotations

import importlib.util
import sys
import types


def mutant(module_name: str, old: str, new: str) -> types.ModuleType:
    spec = importlib.util.find_spec(module_name)
    assert spec is not None and spec.origin is not None
    with open(spec.origin, encoding="utf-8") as handle:
        source = handle.read()
    count = source.count(old)
    if count != 1:
        raise AssertionError(f"mutation target occurs {count} times in {module_name}: {old!r}")
    name = f"_mutant_{module_name.replace('.', '_')}_{abs(hash(old)) % 10**8}"
    module = types.ModuleType(name)
    module.__file__ = spec.origin
    sys.modules[name] = module  # dataclasses and typing look the module up by name
    try:
        exec(compile(source.replace(old, new), spec.origin, "exec"), module.__dict__)  # noqa: S102
    finally:
        sys.modules.pop(name, None)
    return module
