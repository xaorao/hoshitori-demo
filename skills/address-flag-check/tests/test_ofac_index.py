"""OFAC list index: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import os
import pathlib

import pytest

from address_flag_check import ofac_index
from tests.checks_support import ofac_meta
from tests.mutation_sweep import survivors
from tests.ofac_index_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(ofac_index) == []


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "IndexBuilder.__init__|True<->False|True|0": "buffer_text off splits a text into several _text calls; _text appends each piece and _end joins them",
    "IndexBuilder.__init__|statement dropped|self._parser.buffer_text = True|0": "buffer_text off (the default) only splits texts, which _end joins",
    "IndexBuilder.__init__|statement dropped|self._entry_profile = ''|0": (
        "read only for a Program comment, which needs a Program measure in a SanctionsEntry, whose start set _entry_profile first"
    ),
    "IndexBuilder.__init__|statement dropped|self._ref_id = ''|0": (
        "read only at the end of FeatureType, PartyType, Reliability or SanctionsType, whose start always set _ref_id"
    ),
    "IndexBuilder.__init__|statement dropped|self._parser.EntityDeclHandler = self._refuse_declaration|0": (
        "entity declarations exist only inside a DOCTYPE, and StartDoctypeDeclHandler refuses every DOCTYPE before its first declaration"
    ),
    "IndexBuilder.__init__|statement dropped|self._parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)|0": (
        "parameter entities need a DOCTYPE, which StartDoctypeDeclHandler refuses (and NEVER is expat's default anyway)"
    ),
    "IndexBuilder._start|And->Or|local == 'Comment' and parent == 'SanctionsMeasure' and self._program_measure|0": (
        "it is the last branch, so it pre-empts none; it only starts captures that no _end reads, and the next end event drops them"
    ),
    "IndexBuilder._start|int+1|1|0": "name.rsplit('}', 2)[-1] is the same last piece as name.rsplit('}', 1)[-1]",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("address_flag_check.ofac_index", "tests.ofac_index_checks")
    assert total > 250, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )


@pytest.mark.skipif(
    not os.environ.get("ADDRESS_FLAG_CHECK_FULL_ZIP"), reason="set ADDRESS_FLAG_CHECK_FULL_ZIP to the recorded SDN_ADVANCED.ZIP"
)
def test_the_whole_recorded_file_gives_the_independently_counted_numbers() -> None:
    """Golden test on the whole recorded publication (5.7 MB, not bundled): the counts were made by a separate ElementTree analysis."""
    data = pathlib.Path(os.environ["ADDRESS_FLAG_CHECK_FULL_ZIP"]).read_bytes()
    meta = ofac_meta()
    import hashlib

    assert hashlib.sha256(data).hexdigest() == meta["full_reply"]["body_sha256"], "this is not the recorded publication"
    index = ofac_index.parse_zip(data)
    counts = meta["full_file_counts"]
    assert index.xml_sha256 == meta["xml_sha256"] and index.xml_bytes == meta["xml_bytes"]
    assert index.features == counts["address_features"]
    assert index.address_strings == counts["distinct_strings"]
    assert len(index.labels) == counts["digital_currency_labels"]
    by_label: dict[str, int] = {}
    for item in index.listings:
        by_label[item.label] = by_label.get(item.label, 0) + 1
    assert by_label == counts["features_by_label"]
    assert len({x.entry_id for x in index.listings}) == counts["entries"]
    assert len({x.entry_id for x in index.listings if x.party_type == "entity"}) == counts["entity_entries"]
    assert len({x.entry_id for x in index.listings if x.party_type == "individual"}) == counts["individual_entries"]
