"""All the checks of a cycle of the league: the rules arm's and the model arms'. The sweep of ``hoshitori.league`` runs both, so a change
of the code is noticed if either set sees it."""

from __future__ import annotations

import types

from tests import league_checks, league_models_checks


def failures(mod: types.ModuleType) -> list[str]:
    return [*league_checks.failures(mod), *league_models_checks.failures(mod)]
