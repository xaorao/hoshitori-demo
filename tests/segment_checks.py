"""Checks of the segment module, runnable against the real module or a mutant of it."""

from __future__ import annotations

import hashlib
import types


def failures(seg: types.ModuleType) -> list[str]:
    bad: list[str] = []
    P = seg.Problem

    def record(n: int) -> bytes:
        return seg.canonical.dumps({"id": f"r{n}", "n": n, "s": "x" * (n % 5)}) if hasattr(seg, "canonical") else b""

    recs = [record(i) for i in range(6)]
    lines = [seg.encode_record_line(i, r) for i, r in enumerate(recs)]
    data = b"".join(lines)

    scan = seg.scan_segment(data, 0, None)
    if scan.problem is not P.NONE or [r.seq for r in scan.records] != list(range(6)) or scan.valid_end != len(data):
        bad.append("clean segment not accepted")
    if [r.record for r in scan.records] != recs or any(r.leaf != seg.leaf_hash(r.record) for r in scan.records):
        bad.append("records or leaf hashes wrong")
    if scan.records and (scan.records[2].offset != len(lines[0]) + len(lines[1]) or scan.records[2].length != len(lines[2])):
        bad.append("offsets wrong")

    def expect(label: str, blob: bytes, problem, valid_records: int, first_seq: int = 0, previous=None) -> None:  # type: ignore[no-untyped-def]
        got = seg.scan_segment(blob, first_seq, previous)
        if got.problem is not problem or len(got.records) != valid_records:
            bad.append(f"{label}: problem {got.problem.name} with {len(got.records)} records")

    expect("torn tail", data[:-5], P.TORN_TAIL, 5)
    expect("torn tail of one byte", data + b"{", P.TORN_TAIL, 6)
    flipped = bytearray(data)
    flipped[len(lines[0]) + 20] ^= 1
    expect("flip in a middle line", bytes(flipped), P.DAMAGE, 1)
    last_flip = bytearray(data)
    last_flip[len(data) - 10] ^= 1
    expect("flip in the last line", bytes(last_flip), P.LAST_LINE_INVALID, 5)
    expect("gap in sequence", lines[0] + lines[2], P.LAST_LINE_INVALID, 1)
    expect("duplicate line", lines[0] + lines[0], P.LAST_LINE_INVALID, 1)
    expect("wrong first sequence", data, P.DAMAGE, 0, first_seq=3)
    wrong_leaf = lines[1].replace(seg.leaf_hash(recs[1]).hex().encode(), b"0" * 64)
    expect("wrong leaf hash", lines[0] + wrong_leaf + lines[2], P.DAMAGE, 1)
    spaced = lines[1].replace(b'{"leaf"', b'{ "leaf"')
    expect("non-canonical wrapper", lines[0] + spaced + lines[2], P.DAMAGE, 1)
    expect("empty line", lines[0] + b"\n" + lines[1], P.DAMAGE, 1)
    expect("extra wrapper member", lines[0] + lines[1].replace(b'{"leaf"', b'{"extra":1,"leaf"') + lines[2], P.DAMAGE, 1)

    # a record text that is valid JSON but not canonical must not be accepted inside a wrapper
    noncanon = b'{"b":1,"a":2}'
    wrapper = seg.canonical.dumps({"leaf": seg.leaf_hash(noncanon).hex(), "rec": noncanon.decode(), "seq": 0}) + b"\n"
    expect("non-canonical record text", wrapper, P.LAST_LINE_INVALID, 0)
    try:
        seg.encode_record_line(0, noncanon)
        bad.append("encode accepted non-canonical text")
    except seg.SegmentError:
        pass

    # chaining
    first_bytes = data
    previous = ("segment-000000000000.jsonl", hashlib.sha256(first_bytes).digest(), 6)
    manifest = seg.encode_manifest_line(*previous)
    more = [seg.encode_record_line(6 + i, record(6 + i)) for i in range(3)]
    second = manifest + b"".join(more)
    got = seg.scan_segment(second, 6, previous)
    if got.problem is not P.NONE or [r.seq for r in got.records] != [6, 7, 8] or got.manifest is None:
        bad.append("continuation segment not accepted")
    expect("missing manifest", b"".join(more), P.DAMAGE, 0, first_seq=6, previous=previous)
    bad_hash = ("segment-000000000000.jsonl", hashlib.sha256(first_bytes + b"x").digest(), 6)
    expect("manifest with another hash", second, P.DAMAGE, 0, first_seq=6, previous=bad_hash)
    bad_count = ("segment-000000000000.jsonl", previous[1], 5)
    expect("manifest with another count", second, P.DAMAGE, 0, first_seq=6, previous=bad_count)
    expect("manifest in the first segment", manifest + b"".join(more), P.DAMAGE, 0, first_seq=6)
    expect("manifest only", manifest, P.NONE, 0, first_seq=6, previous=previous)
    expect("empty continuation", b"", P.DAMAGE, 0, first_seq=6, previous=previous)
    expect("second manifest line", manifest + manifest + b"".join(more), P.DAMAGE, 0, first_seq=6, previous=previous)

    for name in ("segment-1.jsonl", "segment-000000000001.json", "x/segment-000000000001.jsonl", "segment-00000000000a.jsonl"):
        try:
            seg.segment_first_seq(name)
            bad.append(f"file name accepted: {name}")
        except seg.SegmentError:
            pass
    if seg.segment_name(12) != "segment-000000000012.jsonl" or seg.segment_first_seq("segment-000000000012.jsonl") != 12:
        bad.append("file name round trip")
    return bad
