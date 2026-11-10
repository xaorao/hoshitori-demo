"""A module planted for the checks of the sweep harness: it imports its neighbour with a relative import, as the skills' modules do. A harness
that builds a mutant without telling it its package cannot build this one, and would count its only change as noticed (finding F1)."""

from __future__ import annotations

from . import sweep_planted


def twice(n: int) -> int:
    return sweep_planted.double(n)
