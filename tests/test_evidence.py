"""The evidence item: every check passes on the real module, and every small change of its code is noticed."""

from __future__ import annotations

from hoshitori import evidence
from tests.evidence_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(evidence) == []


# Changes that no check can notice because the code does the same thing either way. Each one was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_check_text|str+~|'v'|0": "the name of the one member of the throw-away record that the canonical profile checks the text with",
    "EvidenceItem._check_content|str+~|'request'|0": "the name of the one member of the throw-away record that the canonical profile checks the request with",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.evidence", "tests.evidence_checks")
    assert total > 450, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
