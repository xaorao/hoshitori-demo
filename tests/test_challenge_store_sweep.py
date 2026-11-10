"""Every small change of ``hoshitori.challenge_store`` is noticed by a check or is listed here with the reason it changes nothing."""

from __future__ import annotations

import pytest

from tests.mutation_sweep import survivors

CODEC = "Python reads the codec name 'utf-8~' as 'utf-8' (checked with codecs.lookup)"
EQUIVALENT: dict[str, str] = {
    "triage|str+~|'replace'|0": "an unknown error handler raises LookupError, which the next lines catch and read the page again with the real handler",
    "triage|str+~|'utf-8'|0": CODEC,
    "triage|str+~|'utf-8'|1": CODEC,
    "ChallengeStore.file|str+~|'utf-8'|0": CODEC,
    "ChallengeStore.file|str+~|'utf-8'|1": CODEC,
    "ChallengeStore.records|str+~|'utf-8'|0": CODEC,
    "ChallengeStore.file|statement dropped|handle.seek(0, 2)|0": "the file is opened for appending, so every write goes to the end whatever the position",
    "ChallengeStore.file|statement dropped|handle.flush()|0": "closing the file at the end of the block writes what is waiting",
    "ChallengeStore.file|statement dropped|fcntl.flock(handle, fcntl.LOCK_UN)|0": "closing the file releases the lock",
    "read_submission|str+~|'\\n\\t'|0": "the allowed characters are control characters; a tilde added to the list is not one, so no input changes its meaning",
    "ChallengeStore._known|statement dropped|fcntl.flock(handle, fcntl.LOCK_UN)|0": "closing the file releases the lock",
    "ChallengeStore._known|str+~|'utf-8'|0": CODEC,
    "ChallengeStore._rows|statement dropped|continue|0": "a line that does not start with a brace fails to parse or parses to something that is not a record, and both are skipped below",
    "ChallengeStore.file|str+~|'\\n'|2": "a body tested for a different ending is taken as torn: one more newline is written, and a blank line is skipped by every reader",
    "_strip_tags|Lt->LtE|at < best|0": "two closers never start at one position, so the comparison is never equal",
    "_strip_tags|Lt->LtE|at < i|0": "a closer starts with a slash and the position i holds an opening bracket, so a closer never starts at i",
    "_strip_tags|Lt->LtE|end < 0|0": "an end is at least one after a start, never zero",
    "_strip_tags|Lt->LtE|end < 0|1": "an end is at least one after a start, never zero",
    "_strip_tags|Lt->LtE|gt < i + 1|0": "a bracket found again from the same place is the same bracket",
    "_strip_tags|int+1|0|2": "an end is at least one after a start, never zero",
    "_strip_tags|int+1|0|3": "an end is at least one after a start, never zero",
    "_strip_tags|int+1|1|0": "the value of no end only has to be below zero",
    "_strip_tags|int+1|1|5": "a bracket found again from the same place is the same bracket",
    "_strip_tags|int+1|2|0": "the value that means not looked for yet only has to differ from minus one and from every position",
    "_strip_tags|int+1|2|1": "the value that means not looked for yet only has to differ from minus one and from every position",
    "_strip_tags|int+1|2|2": "the value that means not looked for yet only has to differ from minus one and from every position",
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.challenge_store", "tests.challenge_store_checks")
    assert total > 120, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
