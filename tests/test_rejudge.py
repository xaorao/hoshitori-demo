"""Judging the stored time-stamp answers again: the checks pass on the real module and every small change of its code is noticed or listed
as harmless."""

from __future__ import annotations

from hoshitori import rejudge
from tests.mutation_sweep import survivors
from tests.rejudge_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(rejudge) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_is_registered|And->Or|entry['size'] == size and entry['root'] == root.hex()|0": "two checkpoints of one log never share a size or a root, so a match on either is a match on both",
    "_is_registered|return None|return False|0": "None and False are both false where the answer is used",
    "_is_registered|str+~|'utf-8'|0": "Python reads the changed name as the same encoding (punctuation in a codec name is dropped)",
    "_signed_notes|return None|return []|0": "an empty list and None both mean there are no checkpoints, and the loop over either does nothing",
    "_signed_notes|str+~|'utf-8'|0": "Python reads the changed name as the same encoding (punctuation in a codec name is dropped)",
    "Rejudger._keep|sorted removed|sorted(commitment.attestation.timing.chosen)|0": "the names only make the evidence text of an attested entry, which is read in an error message and nowhere else, so their order changes no result",
    "|str+~|'https://rejudge.invalid/'|0": "the address only gives the adapter a valid authority to judge a capture with; it is never contacted, so any valid address does",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.rejudge", "tests.rejudge_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
