"""RYO answers as evidence: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import ryo_evidence
from tests.mutation_sweep import survivors
from tests.ryo_evidence_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](ryo_evidence) == []


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_evidence) == []


_UNREACHABLE = (
    "a number field reaches here as plain decimal text the reader has already checked (ryo_fields.plain), so Decimal cannot refuse it; "
    "the branch is a guard for a value that cannot arrive"
)
_FALSY = "the caller only asks whether the result is true; None and False are the same answer to that"
EQUIVALENT: dict[str, str] = {
    "_is_zero|True<->False|False|1": _UNREACHABLE,
    "_is_zero|return None|return False|1": _UNREACHABLE,
    "_is_zero|return None|return False|0": _FALSY,
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ryo_evidence", "tests.ryo_evidence_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
