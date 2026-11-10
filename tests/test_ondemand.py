"""The on-demand forecast: every check passes on the real module and every small change of its code is noticed or harmless (plan J-01)."""

from __future__ import annotations

from hoshitori import desk_plan, ondemand
from tests.mutation_sweep import survivors
from tests.ondemand_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(ondemand) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_already|int+1|0|0": "every step of a history names the same forecast, so the first and the second step give the same id",
    "_finish|int+1|0|0": "every step of a history names the same forecast, so the first and the second step give the same id",
    "commit|And->Or|r['type'] == forecast.TYPE and r['id'] == fid|0": (
        "a run's source holds one forecast record and it is the first record of the source, so the first record that is a forecast "
        "or has the forecast's id is that record either way"
    ),
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ondemand", "tests.ondemand_checks")
    assert total > 20, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


def test_the_invalidation_is_the_desks_own_sentence() -> None:
    assert ondemand.INVALIDATION == (desk_plan._INVALIDATION,)
