"""The fields of the site's documents are pinned, so that a change of fields cannot reach a page that reads the old ones.

The static site is read by a page that is built separately and may be served beside an export written by another version. Every
document therefore carries a format number (``site_export.FORMAT``, in the index), the page refuses an index of another number with a
sentence (``web/src/load.ts``), and this test makes the number mean something: the shape of each document (its field names and the
kinds of value, recursively, with a list taken as the union of its elements' shapes) is hashed and compared with the pin below. A change
of any field fails here with the instruction to raise ``FORMAT`` in both places and to move the pin. The test also compares the two
numbers, so the exporter and the page cannot drift apart.

The shapes are read from a loopback league's documents (the fixture of ``tests/site_export_checks``) and the recorded model challenges.
A field that the fixture leaves empty or null is pinned as such, so the pin covers the fields the fixture exercises, not every field a
real league could write: a field that appears only in a case the fixture does not reach is NOT covered, and is caught only when the
page's own type for it changes (``web/src/types.ts``, checked by the type checker).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from hoshitori import call_revisions, decision_alerts, practice_view, site_export, source_value, today_items, view_models
from tests.site_export_checks import coverage_view_read, desk_runs, documents, lab_document, model_rows, practice_books, sweeps_view, views

WEB_LOAD = Path(__file__).resolve().parents[1] / "web" / "src" / "load.ts"

# The format number the pin below belongs to, and the pin: raise ``site_export.FORMAT`` and ``FORMAT`` in ``web/src/load.ts`` together when a
# document's fields change, then set both here from the failure message.
PINNED_FORMAT = 24
PIN = "d14186f5b07b2b9310ca44e662f6fce47ed3105020dfccfb82a8fb7b47bf0c95"


def shape(value: Any) -> Any:
    """The field names and kinds of value of a document, recursively; a list is the sorted union of its elements' shapes."""
    if isinstance(value, dict):
        return {k: shape(value[k]) for k in sorted(value)}
    if isinstance(value, list):
        found = {json.dumps(shape(item), sort_keys=True) for item in value}
        return [json.loads(s) for s in sorted(found)]
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "number"
    return "text"


def ryo_record() -> view_models.RyoRecord:
    """Arm R's document, from one read of each kind of outcome the record can hold (the loopback league does not read RYO)."""
    from hoshitori import arm_r, ryo_read
    from hoshitori.settlement import Settlement, State

    fell = Settlement("st", "fc", "D", "A", "1", State.SETTLED, "0", None, "100", "99", None, ())
    reads = [
        {"cycle_id": "cy", "candidate_id": f"cd{n}", "symbol": "SOL", "call": call, "reading": ryo_read.reading_of(call)}
        for n, call in enumerate(("cautious", "moon"))
    ]
    outcomes = (arm_r.outcome(reads[0], "as", {("cy", "as"): [fell]}), arm_r.outcome(reads[1], "as", {}))
    table = tuple((k, ryo_read.READING_WORDS[k], tuple(w for w, r in ryo_read.READINGS.items() if r == k)) for k in ryo_read.KINDS)
    return view_models.RyoRecord(ryo_read.TABLE_VERSION, table, arm_r.summary(outcomes), outcomes, (("cy", "2026-11-08T12:00:00.000Z"),))


def pinned_shapes() -> dict[str, Any]:
    """The shapes of the documents of a loopback league. What a run generates (forecast ids, so the names of receipts and the keys of the
    index's digest table) is not a field: every receipt is read, their shapes are one union, and the digest table is its file names with
    the receipts as one."""
    v = views()
    docs = documents(
        site_export,
        v,
        model_challenges=model_rows(),
        desk_runs=desk_runs(),
        practice=[
            dataclasses.replace(practice_books()[0], steps=(practice_view.Step("2026-11-09T12:00:00.000Z", "HALVED", "0.05"),)),
            *practice_books()[1:],
        ],
        sweeps=sweeps_view(),
        coverage=coverage_view_read(),
        lab=lab_document(),
        items=today_items.items([], desk_runs()),
        sources=source_value.SourceValue(
            "0.05",
            "10.0",
            10,
            (
                source_value.SourceLine(
                    "L0", "Rules", "binance-spot", 1, 12, "0.1600", "0.0100", "0.3100", "Not distinguishable yet", None, 3, 0, 0
                ),
                source_value.SourceLine("L1", "Single model", "ryo", 4, 0, None, None, None, "Too few to show", None, 0, 1, 2),
            ),
        ),
        revisions=[
            call_revisions.Revision(
                v.rows[0].forecast_id,
                v.rows[1].forecast_id,
                "L1",
                "BTC",
                4,
                "0.42",
                "0.55",
                "2026-11-09T12:00:00.000Z",
                "2026-11-09T13:00:00.000Z",
                call_revisions.Reading("100", "-0.03"),
                call_revisions.Reading(None, None),
                True,
            )
        ],
        alerts=[
            decision_alerts.Alert("INVALIDATED", "2026-11-09T12:00:00.000Z", "t", "fc", "BTC", "L1", None),
            decision_alerts.Alert("RISK_STEP", "2026-11-09T12:00:00.000Z", "t", None, None, "L1", "L1:BASE"),
        ],
    )
    out: dict[str, Any] = {}
    for path in sorted(docs):
        if path.startswith("receipts/"):
            continue
        doc = json.loads(docs[path])
        if path == "index.json":
            names = sorted({"receipts/<forecast>.json" if k.startswith("receipts/") else k for k in doc["files"]})
            doc = {**doc, "files": names}
        out[path] = shape(doc)
    out["ryo.json"] = shape(json.loads(site_export.canonical_bytes(ryo_record())))
    receipts = [json.loads(docs[p]) for p in sorted(docs) if p.startswith("receipts/")]
    assert receipts, "the fixture has no receipt"
    out["receipts/<forecast>.json"] = shape(receipts)
    return out


def digest(shapes: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(shapes, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def test_shape_reads_names_and_kinds_not_values() -> None:
    assert shape({"b": [1, 2], "a": None}) == {"a": "null", "b": ["int"]}
    assert shape({"a": [{"x": 1}, {"x": 2}, {"x": "t", "y": None}]}) == {"a": [{"x": "int"}, {"x": "text", "y": "null"}]}
    assert shape([]) == []
    assert shape([{"a": 1}, {"a": None}]) == [{"a": "int"}, {"a": "null"}]  # the union: both kinds a field takes are in the pin
    assert shape({"a": True, "b": 1.5, "c": "s"}) == {"a": "bool", "b": "number", "c": "text"}
    # a value is not part of the shape; a field is
    assert digest({"d": shape({"a": 1})}) == digest({"d": shape({"a": 2})})
    assert digest({"d": shape({"a": 1})}) != digest({"d": shape({"a": 1, "b": 1})})
    assert digest({"d": shape({"a": 1})}) != digest({"d": shape({"a": "1"})})


def test_the_page_reads_the_format_the_exporter_writes() -> None:
    match = re.search(r"^export const FORMAT = (\d+);$", WEB_LOAD.read_text(encoding="utf-8"), re.M)
    assert match is not None, "web/src/load.ts no longer states `export const FORMAT = N;`"
    assert int(match.group(1)) == site_export.FORMAT, "the page and the exporter read and write different formats"


def test_the_fields_of_the_documents_are_those_the_format_names() -> None:
    shapes = pinned_shapes()
    got = digest(shapes)
    assert site_export.FORMAT == PINNED_FORMAT and got == PIN, (
        "a document's fields changed (or the format number did) without the pin: raise site_export.FORMAT and `FORMAT` in web/src/load.ts "
        f"together, then set PINNED_FORMAT = {site_export.FORMAT} and PIN = {got!r} in this file. Documents covered: {sorted(shapes)}"
    )
