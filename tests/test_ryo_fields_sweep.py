"""Every small change of ``hoshitori.ryo_fields`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

EQUIVALENT: dict[str, str] = {
    "table_sha256|sorted removed|sorted(SPECS)|0": "the listing is a canonical record, whose keys are sorted when it is written, so the order the tools are visited in cannot reach the hash",
    "|statement dropped|Spec = Leaf | Obj | Seq | Keyed | Opaque|0": "the alias is used only in annotations, which this module does not evaluate (from __future__ import annotations)",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ryo_fields", "tests.ryo_fields_checks")
    assert total > 150, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
