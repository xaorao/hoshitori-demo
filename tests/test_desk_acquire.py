"""The Desk's way of running one menu check: the checks pass on the real module and every small change of its code is noticed or harmless."""

from __future__ import annotations

from hoshitori import desk_acquire
from tests.desk_acquire_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(desk_acquire) == []


EQUIVALENT: dict[str, str] = {
    "_Stop.__init__|statement dropped|super().__init__(text)|0": "the stop's text is read from its own attribute, and an exception keeps its arguments without the call",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.desk_acquire", "tests.desk_acquire_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
