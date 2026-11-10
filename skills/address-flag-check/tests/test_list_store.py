"""Local copy of the OFAC list: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from address_flag_check import list_store
from tests.list_store_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(list_store) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "load|str+~|'utf-8'|0": "Python's codec lookup normalises the name 'utf-8~' to 'utf-8' (codecs.lookup('utf-8~').name == 'utf-8')",
    "save|str+~|'utf-8'|0": "Python's codec lookup normalises the name 'utf-8~' to 'utf-8' (codecs.lookup('utf-8~').name == 'utf-8')",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.list_store", "tests.list_store_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
