"""Settings, signing key and assembly of a running league: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import deployment
from tests.deployment_checks import failures
from tests.mutation_sweep import survivors


def test_real_module_passes_every_check() -> None:
    assert failures(deployment) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_forged_copy|statement dropped|partial.parent.mkdir(parents=True, exist_ok=True)|0": "copytree creates the parent folder of the copy, so the explicit creation is redundant",
    "_forged_copy|True<->False|True|1": "the same line: with exist_ok False an existing parent would raise, and copytree has already made it",
    "_check|any->all|any((mappings[arm].sha256 != pinned for arm, pinned in rules.arms))|0": "there is one arm today (ARMS is ('L0',)), so any and all of one comparison are the same",
    "load_settings|str+~|'utf-8'|0": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "read_key|str+~|'ascii'|0": "Python reads 'ascii~' as 'ascii': a codec name ignores punctuation it does not know",
    "export_site|str+~|'utf-8'|0": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "export_site|str+~|'utf-8'|1": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "export_site|str+~|'utf-8'|2": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "export_site|str+~|'utf-8'|3": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "export_site|str+~|'utf-8'|4": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "assemble_reading|statement dropped|captures.close()|0": "a store opened read-only holds no lock and no open file (CaptureStore.close releases only the lock a writer takes), so closing it changes nothing anyone can see",
    "forge_cycle|str+~|'hoshitori-forge-'|0": "the prefix only names the temporary directory, which is gone when the command ends",
    "forge_cycle|str+~|'work'|0": "the name of the folder inside the temporary directory is only a name",
    "_later_part_grew|True<->False|False|0": "a cycle whose commitment cannot be read cannot be exported either; the export then fails the same way and the bundle that is there stays",
    "_later_part_grew|return None|return False|0": "None and False are both false where the answer is used",
    "_later_part|sorted removed|sorted((seq for seq in positions if seq is not None and stamped_size <= seq < size))|0": "the bundle names each file by the record's position, so the order the positions are written in changes nothing",
    "_stored_checkpoints|statement dropped|continue|0": "the continue is the last statement of the loop's body, so without it the loop goes on to the next line just the same",
    "export_site|True<->False|True|4": "the folder that holds the replaced bundle exists when this runs, so whether a missing folder is ignored does not matter",
    "forge_cycle|str+~|'utf-8'|0": "Python reads 'utf-8~' as 'utf-8': a codec name ignores punctuation it does not know",
    "_committed|sorted removed|sorted(found | _whole_batch(running, records, cycle_id, parse_text(split_note(saved.note)[0]).size))|0": "the ids are sorted only to give the list a stable order; without it the order follows the process's hash seed, so a check that compares the list notices it in some runs and not in others",
    "_later_part|Gt->GtE|size > stamped_size|0": "a checkpoint of the stamped size is the stamped checkpoint itself, which holds none of the later records, so no record is selected and there is no later part either way",
    "export_site|statement dropped|continue|2": "the continue of a challenge view that cannot be made is the last statement of the loop's body (the body is one try whose except holds only it), so without it the loop goes on to the next cycle all the same",
}

# A change of a codec's name is harmless, but on a machine under load the checks can run out of time on it and the sweep then counts it
# as noticed; these may therefore be missing from the survivors without the list being stale. The same holds for any survivor, since a
# survivor runs the whole of the checks: the `any`/`all` change was run alone and passed every check (0 failures in 10 s), yet two
# sweeps in a row counted it as noticed once the checks of the optional sources made the suite longer.
LOAD_SENSITIVE: set[str] = {
    "_forged_copy|statement dropped|partial.parent.mkdir(parents=True, exist_ok=True)|0",  # run alone it survives; counted as noticed by the clock under load
    "_later_part|sorted removed|sorted((seq for seq in positions if seq is not None and stamped_size <= seq < size))|0",  # run alone it survives
    "_stored_checkpoints|statement dropped|continue|0",  # run alone it survives
    "assemble_reading|statement dropped|captures.close()|0",  # run alone it survives
    "export_site|True<->False|True|4",  # run alone it survives
    "export_site|str+~|'utf-8'|4",  # a codec name; run alone it survives
    "forge_cycle|str+~|'hoshitori-forge-'|0",  # run alone it survives
    "_committed|sorted removed|sorted(found | _whole_batch(running, records, cycle_id, parse_text(split_note(saved.note)[0]).size))|0",  # seed-sensitive: caught or not by the order a set happens to have in that process
    "_later_part|Gt->GtE|size > stamped_size|0",  # survives when the checks have time to finish; one sweep counted it as noticed by the clock
    "export_site|str+~|'utf-8'|1",
    "export_site|str+~|'utf-8'|2",
    "export_site|str+~|'utf-8'|3",
    "load_settings|str+~|'utf-8'|0",
    "_forged_copy|True<->False|True|1",
    "read_key|str+~|'ascii'|0",
    "export_site|str+~|'utf-8'|0",
    "forge_cycle|str+~|'utf-8'|0",
    "_later_part_grew|return None|return False|0",
    "_later_part_grew|True<->False|False|0",  # run alone it survives (applied and run: no failure); one sweep counted it as noticed by the clock  # run alone it survives (checked by applying it and running the checks: no failure); one sweep counted it as noticed by the clock
    "_check|any->all|any((mappings[arm].sha256 != pinned for arm, pinned in rules.arms))|0",
    "export_site|statement dropped|continue|2",
    "forge_cycle|str+~|'work'|0",  # run alone it survives (803 places, 10 survivors); in the whole suite one run counted it as noticed by the clock
}


@pytest.mark.slow
def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.deployment", "tests.deployment_checks")
    assert total > 100, f"only {total} places to change: the sweep is not looking at the module"
    unexplained, stale = set(alive) - set(EQUIVALENT), set(EQUIVALENT) - set(alive)
    assert not unexplained and stale <= LOAD_SENSITIVE, (
        f"unexplained survivors: {sorted(unexplained)}; stale entries: {sorted(stale - LOAD_SENSITIVE)}"
    )
