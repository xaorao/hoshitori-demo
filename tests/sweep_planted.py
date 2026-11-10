"""A module planted for the checks of the sweep harness itself: it is tiny so that every place the sweep can change, and what each change
does, is worked out by hand in ``mutation_sweep_checks``. It is not part of the product.

The seven places, in the order the sweep finds them (``ast.walk`` is breadth-first: a statement at the top of a function comes before the
inside of a statement above it):

1. ``WIDTH = 4`` dropped: ``WIDTH`` is then unknown at the assertion, so the module cannot be built.
2. ``4`` becomes ``5``: the assertion fails, so the module cannot be built.
3. ``!=`` becomes ``==``: the assertion fails, so the module cannot be built.
4. ``return n * 2`` becomes ``return None``.
5. ``5`` becomes ``6``: the module builds and behaves as before (``WIDTH`` is never used): a change nothing can notice.
6. ``n * 2`` becomes ``n + 2``.
7. ``2`` becomes ``3``.
"""

from __future__ import annotations

WIDTH = 4
assert WIDTH != 5


def double(n: int) -> int:
    return n * 2
