"""The Desk's menu of checks: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import desk_menu
from tests.desk_menu_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk_menu) == []


EQUIVALENT: dict[str, str] = {
    "build_menu|sorted removed|sorted(NOT_SERVABLE)|0": "the omissions are sorted again before the menu is built, so the order they were added in is not seen",
    "build_menu|sorted removed|sorted(rule.extra_args + (token_args if rule.needs_asset else ()))|0": "the only rule with both kinds of argument already lists them in sorted order",
    "build_menu|statement dropped|continue|2": "with no provider the list of names is empty, so the loop that follows adds nothing either way",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_menu", "tests.desk_menu_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
