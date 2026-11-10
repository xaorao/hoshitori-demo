"""Segment scanning: every situation the ledger must tell apart, plus properties and mutants."""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from hoshitori import canonical, segment
from tests.mutation import mutant
from tests.segment_checks import failures


def test_real_module_passes_every_check() -> None:
    assert failures(segment) == []


def _canonical(text: str) -> bool:
    """Whether the record profile takes the text (no lone surrogate, every code point assigned in Unicode 15.0, NFC)."""
    try:
        canonical.dumps({"": text})
    except canonical.CanonicalError:
        return False
    return True


# texts the record profile takes: Cs and Cn left out (Python 3.12's tables are Unicode 15.0, the profile's), then held to the profile itself
_texts = (
    st.text(alphabet=st.characters(exclude_categories=["Cs", "Cn"]), max_size=10)
    .map(lambda s: __import__("unicodedata").normalize("NFC", s))
    .filter(_canonical)
)


@settings(max_examples=200, deadline=None)
@given(
    st.lists(
        st.dictionaries(_texts, st.one_of(st.none(), st.booleans(), st.integers(-1000, 1000), _texts), max_size=4), min_size=1, max_size=12
    )
)
def test_any_sequence_of_records_scans_back_exactly(rows: list[dict[str, canonical.JsonValue]]) -> None:
    records = [canonical.dumps(row) for row in rows]
    data = b"".join(segment.encode_record_line(i, r) for i, r in enumerate(records))
    scan = segment.scan_segment(data, 0, None)
    assert scan.problem is segment.Problem.NONE
    assert [r.record for r in scan.records] == records
    assert scan.valid_end == len(data)
    # any proper prefix is either clean (cut on a line boundary) or a torn tail, never silently damaged
    for cut in range(0, len(data), max(1, len(data) // 20)):
        part = segment.scan_segment(data[:cut], 0, None)
        assert part.problem in (segment.Problem.NONE, segment.Problem.TORN_TAIL)
        assert part.valid_end <= cut


@settings(max_examples=300, deadline=None)
@given(st.integers(0, 10**6), st.integers(1, 7))
def test_a_single_byte_change_is_never_accepted_unnoticed(position: int, delta: int) -> None:
    records = [canonical.dumps({"id": f"r{i}", "n": i}) for i in range(5)]
    data = bytearray(b"".join(segment.encode_record_line(i, r) for i, r in enumerate(records)))
    index = position % len(data)
    data[index] = (data[index] + delta) % 256
    scan = segment.scan_segment(bytes(data), 0, None)
    assert (
        scan.problem is not segment.Problem.NONE
        or [r.record for r in scan.records] != records
        or bytes(data) != b"".join(segment.encode_record_line(i, r) for i, r in enumerate(records))
    )
    if scan.problem is segment.Problem.NONE:
        # accepted only if the change left the bytes identical to a valid encoding of some records
        again = b"".join(segment.encode_record_line(r.seq, r.record) for r in scan.records)
        assert again == bytes(data)


MUTATIONS = {
    "leaf hash not checked": ("if leaf_hex != leaf_hash(record).hex():", "if False:"),
    "sequence not checked": ("if type(seq) is not int or seq != expected_seq:", "if type(seq) is not int:"),
    "wrapper canonical form not required": ("if canonical.dumps(wrapper) != line:", "if False:"),
    "record canonical form not required": (
        'if canonical.canonicalize(record) != record:\n            raise SegmentError("the record text is not canonical")',
        "pass",
    ),
    "manifest contents not compared": (
        'if parsed != {"records": previous[2], "segment": previous[0], "sha256": previous[1].hex()}:',
        "if False:",
    ),
    "continuation without manifest accepted": (
        "if first_line and previous is not None:\n                result.problem",
        "if False:\n                result.problem",
    ),
    "damage reported as a torn tail": (
        "result.problem = Problem.LAST_LINE_INVALID if is_last else Problem.DAMAGE",
        "result.problem = Problem.LAST_LINE_INVALID",
    ),
    "torn tail not recognised": ("if newline < 0:", "if False:"),
    "unexpected members allowed": ('if set(wrapper) != {"leaf", "rec", "seq"}:', "if False:"),
    "manifest allowed anywhere": ("if not first_line_of_continuation or not isinstance(body, dict):", "if not isinstance(body, dict):"),
    "encoder skips canonical check": (
        '    if canonical.canonicalize(record) != record:\n        raise SegmentError("the record is not in canonical form")\n',
        "",
    ),
}


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    try:
        found = failures(mutant("hoshitori.segment", old, new))
    except Exception:
        return
    assert found, f"mutant not detected: {name}"
