"""Static site documents: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from hoshitori import site_export
from tests.mutation_sweep import survivors
from tests.site_export_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(site_export) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "write|str+~|'.tmp'|0": "the name of the temporary file is only a name; it is renamed over the target at once",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.site_export", "tests.site_export_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
