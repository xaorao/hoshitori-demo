"""Checks of the static site documents (``hoshitori.site_export``), runnable against a mutant: the documents of a real loopback league,
their bytes, their index, and what writing them does to a directory that already holds files."""

from __future__ import annotations

import dataclasses
import hashlib
import inspect
import json
import tempfile
import types
from pathlib import Path
from typing import Any

from hoshitori import (
    call_revisions,
    challenge_view,
    claims_file,
    decision_alerts,
    drill,
    forgery,
    model_challenge_file,
    quality,
    source_value,
    view_models,
)
from hoshitori import claim_checks as claim_checks_module
from tests.claims_file_checks import made
from tests.model_challenge_file_checks import RECORDED
from tests.view_models_checks import ledger


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")


def views() -> Any:
    records, protocol, now = ledger()
    return view_models.build(records, protocol, now_ms=now)


def desk_runs() -> tuple[Any, ...]:
    """One recorded run of the Desk, as the site is given it."""
    from hoshitori import desk, desk_view
    from tests.desk_checks import World

    w = World(desk)
    w.run(binding="recorded")
    return tuple(desk_view.runs(w.records))


def sweeps_view() -> Any:
    """The sweeps of a few modules, read from a sweep page."""
    from hoshitori import sweep_view
    from tests.sweep_view_checks import ENTRIES, page

    return sweep_view.read(page(ENTRIES))


def coverage_view_read() -> Any:
    """The coverage of a rubric and a contract, read from two matrices."""
    from hoshitori import coverage_view
    from tests.coverage_view_checks import contract, rubric

    return coverage_view.read(rubric(), contract())


def practice_books() -> tuple[Any, ...]:
    """The practice portfolio of a short run: one book that bought, marked and sold."""
    from hoshitori import practice_view
    from tests.practice_view_checks import one_round_trip

    return practice_view.build(one_round_trip())


def model_rows() -> tuple[Any, ...]:
    """The rows of the What if page for the three recorded model-arm challenges (``tests/recorded_model_challenges``)."""
    return tuple(model_challenge_file.row(model_challenge_file.read((RECORDED / f"{s}.json").read_bytes())) for s in ("BTC", "ETH", "SOL"))


def documents(mod: types.ModuleType, *args: Any, **kw: Any) -> Any:
    """The documents of the site as the exporter asks for them, from an intact ledger (its state is ``OK``)."""
    state = getattr(mod, "LedgerState", None)  # none in a module that cannot say what state the ledger was in: check_ledger_state says so
    return mod.documents(*args, **kw) if state is None else mod.documents(*args, ledger=state("OK", ""), **kw)


def lab_document() -> Any:
    """A Laboratory document of one frozen version, made in a throw-away book."""
    import tempfile

    from hoshitori import lab, lab_view

    with tempfile.TemporaryDirectory() as tmp:
        book = lab.Lab(Path(tmp) / "lab.jsonl")
        proposal = {
            "statement": "BTC bounces after a low close",
            "symbol": "BTC",
            "direction": "UP",
            "horizon_h": 4,
            "condition": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 100}},
            "invalidation": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}},
            "max_weight": "0.02",
            "assumptions": [{"id": "a1", "kind": "OBSERVATION", "text": "the close is low"}],
        }
        book.freeze(lab.draft_from(proposal), "alice", at_ms=1_700_000_000_000)
        return lab_view.build(book)


DETAIL = "segment segment-000000000000.jsonl: leaf hash does not match the record at offset 3256; not repaired"


def check_ledger_state(mod: types.ModuleType) -> list[str]:
    """The index says what state the ledger was in when the site was exported, and a ledger that is not OK has no record in the site (ADR-026:
    INTEGRITY_FAILURE is visible on every surface; LEDGER_SPEC section 8: NOT RECONSTRUCTIBLE for affected records, never a pass)."""
    c = Case(mod)
    state = getattr(mod, "LedgerState", None)
    if state is None:
        c.bad.append("there is no LedgerState: the documents cannot say what state the ledger was in")
        return c.bad
    c.eq("a ledger's state is a frozen value", state.__dataclass_params__.frozen, True)
    c.eq("its fields", [f.name for f in dataclasses.fields(state)], ["state", "detail"])
    given = inspect.signature(mod.documents).parameters["ledger"]
    c.eq(
        "every caller says it: the ledger's state is asked for by name",
        (given.kind, given.default),
        (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.empty),
    )
    v = views()
    c.eq(
        "the index of an intact ledger's site says so",
        json.loads(mod.documents(v, ledger=state("OK", ""))["index.json"])["ledger"],
        {"state": "OK", "detail": ""},
    )
    _, protocol, now = ledger()
    empty = view_models.build([], protocol, now_ms=now)
    failed = mod.documents(empty, (), b"{}\n", ledger=state("INTEGRITY_FAILURE", DETAIL))
    index = json.loads(failed["index.json"])
    c.eq("and of one in INTEGRITY_FAILURE, with the ledger's reason", index["ledger"], {"state": "INTEGRITY_FAILURE", "detail": DETAIL})
    c.eq(
        "with no record of it",
        (index["counts"], sorted(p for p in failed if "/" in p)),
        ({"cycles": 0, "rows": 0, "receipts": 0, "bundles": 0}, []),
    )
    c.eq(
        "an empty list of the model arm's challenges holds no record, so it may be published",
        "model_challenges.json" in mod.documents(empty, model_challenges=(), ledger=state("FENCED", "f")),
        True,
    )
    for label, call, says in (
        ("its records", lambda: mod.documents(v, ledger=state("INTEGRITY_FAILURE", DETAIL)), "INTEGRITY_FAILURE"),
        ("a bundle", lambda: mod.documents(empty, [mod.Bundle("cyaaaa", (), ())], ledger=state("FENCED", "fenced")), "FENCED"),
        (
            "a challenge of its model arm",
            lambda: mod.documents(empty, model_challenges=model_rows()[:1], ledger=state("FENCED", "f")),
            "FENCED",
        ),
    ):
        try:
            call()
        except mod.SiteError as exc:
            want = f"the ledger is in {says}: its records are NOT RECONSTRUCTIBLE, so the site holds none of them"
            c.eq(f"{label} of a ledger that is not OK are refused", str(exc), want)
        else:
            c.bad.append(f"{label} of a ledger in {says} were published")
    return c.bad


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq(
        "names",
        (mod.FORMAT, mod.INDEX, mod.TOP, mod.RECEIPTS, mod.BUNDLES),
        (
            24,
            "index.json",
            (
                "today.json",
                "items.json",
                "cycles.json",
                "rows.json",
                "record.json",
                "bundles.json",
                "trusted_root.json",
                "quality.json",
                "sweeps.json",
                "coverage.json",
                "drill.json",
                "claims.json",
                "challenges.json",
                "boundaries.json",
                "claim_checks.json",
                "desk.json",
                "practice.json",
                "alerts.json",
                "sources.json",
                "model_challenges.json",
                "signals.json",
                "reviews.json",
                "forgery.json",
                "forged_root.json",
                "lab.json",
                "ryo.json",
                "comparisons.json",
            ),
            "receipts",
            "bundles",
        ),
    )
    c.eq("the forged copy's folder", mod.FORGED, "forged")
    c.eq("a forged copy is a frozen value", mod.ForgedCopy.__dataclass_params__.frozen, True)
    c.eq(
        "a forecast id",
        [bool(mod.FORECAST_ID.fullmatch(x)) for x in ("fc4opxtnhk3fprz3b4wufs2aclcx", "fc../x", "ft4opxtnhk3fprz3b4wufs2aclcx", "")],
        [True, False, False, False],
    )
    c.eq("written is frozen", mod.Written.__dataclass_params__.frozen, True)
    return c.bad


def check_bytes(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    data = mod.canonical_bytes({"b": [1, {"y": "é", "x": None}], "a": "z"})
    c.eq("sorted keys, no spaces, UTF-8, one newline", data, '{"a":"z","b":[1,{"x":null,"y":"é"}]}\n'.encode())
    v = views()
    c.eq("a view is its own JSON", json.loads(mod.canonical_bytes(v.today)), view_models.to_json(v.today))
    return c.bad


def check_documents(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    v = views()
    docs = documents(mod, v)
    receipts = sorted(p for p in docs if p.startswith("receipts/"))
    c.eq(
        "the files",
        sorted(p for p in docs if "/" not in p),
        ["bundles.json", "comparisons.json", "cycles.json", "index.json", "record.json", "reviews.json", "rows.json", "today.json"],
    )
    c.eq("a receipt for every call, named by its forecast", receipts, sorted(f"receipts/{r.row.forecast_id}.json" for r in v.receipts))
    c.eq("the index is last", list(docs)[-1], "index.json")
    c.eq("no read of RYO, no document of it", "ryo.json" in docs, False)
    from tests.test_site_document_shapes import ryo_record

    with_ryo = documents(mod, dataclasses.replace(v, ryo=ryo_record()))
    c.eq("RYO's record is its own document", json.loads(with_ryo["ryo.json"]), json.loads(mod.canonical_bytes(ryo_record())))
    c.eq("and the index names it", "ryo.json" in json.loads(with_ryo["index.json"])["files"], True)
    index = json.loads(docs["index.json"])
    c.eq("the index says what it is", (index["format"], index["generated_at"], index["mode"]), (24, v.today.generated_at, v.today.mode))
    c.eq(
        "and how many there are", index["counts"], {"cycles": len(v.cycles), "rows": len(v.rows), "receipts": len(v.receipts), "bundles": 0}
    )
    listed = [mod.Bundle("cyaaaa", ("items/a", "manifest.json"), ("items",)), mod.Bundle("cybbbb", (), ())]
    with_bundles = documents(mod, v, listed, b'{"a": 1}\n')
    c.eq(
        "the bundles are listed with their folders and every path in them",
        json.loads(with_bundles["bundles.json"]),
        [
            {"cycle_id": "cyaaaa", "path": "bundles/cyaaaa", "files": ["items/a", "manifest.json"], "directories": ["items"]},
            {"cycle_id": "cybbbb", "path": "bundles/cybbbb", "files": [], "directories": []},
        ],
    )
    c.eq("and counted", json.loads(with_bundles["index.json"])["counts"]["bundles"], 2)
    c.eq("the trusted root is published as it is, byte for byte", with_bundles["trusted_root.json"], b'{"a": 1}\n')
    c.eq(
        "and its digest is in the index",
        json.loads(with_bundles["index.json"])["files"]["trusted_root.json"],
        hashlib.sha256(b'{"a": 1}\n').hexdigest(),
    )
    c.eq("with no root given there is none", "trusted_root.json" in docs, False)
    c.eq("a bundle is a frozen value", mod.Bundle.__dataclass_params__.frozen, True)
    summary = forgery.Summary("cyaaaa", forgery.Edit("fc1", "p", "0.4", "0.990000"), "k+a1b2c3", 7, "a" * 64, "b" * 64)
    forged = mod.ForgedCopy(summary, ("items/a", "manifest.json"), ("items",), b'{"demo": 1}\n')
    with_forgery = documents(mod, v, listed, b'{"a": 1}\n', forged=forged)
    c.eq(
        "a forged copy is described, with what was changed and every path in it",
        json.loads(with_forgery["forgery.json"]),
        {
            "format": 1,
            "cycle_id": "cyaaaa",
            "edit": {"record": "fc1", "field": "p", "was": "0.4", "now": "0.990000"},
            "standin_vkey": "k+a1b2c3",
            "size": 7,
            "honest_root": "a" * 64,
            "forged_root": "b" * 64,
            "path": "forged/cyaaaa",
            "files": ["items/a", "manifest.json"],
            "directories": ["items"],
        },
    )
    c.eq(
        "and its digest is in the index",
        json.loads(with_forgery["index.json"])["files"]["forgery.json"],
        hashlib.sha256(with_forgery["forgery.json"]).hexdigest(),
    )
    c.eq("the stand-in root is published as it is, byte for byte", with_forgery["forged_root.json"], b'{"demo": 1}\n')
    c.eq(
        "and its digest is in the index",
        json.loads(with_forgery["index.json"])["files"]["forged_root.json"],
        hashlib.sha256(b'{"demo": 1}\n').hexdigest(),
    )
    c.eq(
        "without a forged copy there is neither document",
        ("forgery.json" in with_bundles, "forged_root.json" in with_bundles),
        (False, False),
    )
    try:
        documents(mod, v, listed, b'{"a": 1}\n', forged=mod.ForgedCopy(dataclasses.replace(summary, cycle_id="cycccc"), (), (), b"{}"))
        c.bad.append("a forged copy of a cycle the site holds no bundle of was accepted")
    except mod.SiteError as exc:
        c.eq("a forged copy shows only beside its own bundle", str(exc), "a forged copy is shown only beside the bundle it was made from")
    table = quality.read_status(
        "| Unit | Status | Evidence |\n|---|---|---|\n| A part | TESTED | its checks | a stranger has not read it |\n"
    )
    shown = documents(mod, v, status=table)
    c.eq(
        "the status table is published as rows",
        json.loads(shown["quality.json"]),
        [{"unit": "A part", "status": "TESTED", "word": "TESTED", "evidence": "its checks", "limit": "a stranger has not read it"}],
    )
    c.eq(
        "and its digest is in the index",
        json.loads(shown["index.json"])["files"]["quality.json"],
        hashlib.sha256(shown["quality.json"]).hexdigest(),
    )
    c.eq("with no table there is no such document", "quality.json" in docs, False)
    held = drill.read_drills(
        json.dumps(
            [
                {
                    "id": "d",
                    "title": "t",
                    "injected": "i",
                    "expected": "e",
                    "observed": {"cycle": "Committed", "level": "C3", "slots": [["Open", 1]], "reasons": [], "forecasts": 1},
                    "expected_observed": {"cycle": "Committed", "level": "C3", "slots": [["Open", 1]], "reasons": [], "forecasts": 1},
                    "held": True,
                }
            ]
        ).encode()
    )
    withdrill = documents(mod, v, drills=held)
    c.eq("drills are published as they were read", json.loads(withdrill["drill.json"])[0]["observed"]["slots"], [["Open", 1]])
    c.eq("and are in the index", "drill.json" in json.loads(withdrill["index.json"])["files"], True)
    c.eq("with no drills there is no such document", "drill.json" in docs, False)
    heard = claims_file.read_claims(json.dumps([made(claims_file)]).encode())
    withclaims = documents(mod, v, claims=heard)
    c.eq("claims are published as they were read", json.loads(withclaims["claims.json"])[0]["id"], "golden")
    c.eq("and are in the index", "claims.json" in json.loads(withclaims["index.json"])["files"], True)
    c.eq("with no claims there is no such document", "claims.json" in docs, False)
    row = challenge_view.Row("cy1", "L0", "BTC", 1, "binance-spot", 6, "EVIDENCE_REQUIRED", "words", "70% up", "No call")
    withchallenges = documents(mod, v, challenges=(row,))
    c.eq("challenges are published as they were made", json.loads(withchallenges["challenges.json"])[0]["source"], "binance-spot")
    c.eq("and are in the index", "challenges.json" in json.loads(withchallenges["index.json"])["files"], True)
    c.eq("with none given there is no such document", "challenges.json" in docs, False)
    boundary = challenge_view.Boundary(
        "cy1", "L0", "BTC", 1, "+0.10", "flat", "50% up, flat", (challenge_view.Move("UP", "rising", "+0.25", "+0.15", "70% up, long"),)
    )
    withboundaries = documents(mod, v, boundaries=(boundary,))
    c.eq("boundaries are published as they were made", json.loads(withboundaries["boundaries.json"])[0]["moves"][0]["says"], "70% up, long")
    c.eq("and are in the index", "boundaries.json" in json.loads(withboundaries["index.json"])["files"], True)
    c.eq("with none given there is no such document", "boundaries.json" in docs, False)
    one = claim_checks_module.Check("c", "a", "BTC fell 4%", "SUPPORTED", "r", "ev1", "cy1", "2026-10-30T12:00:00.000Z")
    withcheck = documents(mod, v, claim_checks=(one,))
    c.eq("claim checks are published as they were made", json.loads(withcheck["claim_checks.json"])[0]["status"], "SUPPORTED")
    c.eq("and are in the index", "claim_checks.json" in json.loads(withcheck["index.json"])["files"], True)
    c.eq("with none given there is no such document", "claim_checks.json" in docs, False)
    desk = desk_runs()
    withdesk = documents(mod, v, desk_runs=desk)
    c.eq(
        "the Desk's runs are published as they were made, in the order given",
        json.loads(withdesk["desk.json"]),
        [view_models.to_json(r) for r in desk],
    )
    c.eq(
        "the first of them is the recorded run",
        (json.loads(withdesk["desk.json"])[0]["run_id"], json.loads(withdesk["desk.json"])[0]["label"]),
        ("run-1", "recorded"),
    )
    c.eq("and they are in the index", "desk.json" in json.loads(withdesk["index.json"])["files"], True)
    c.eq("with none given there is no such document", "desk.json" in docs, False)
    c.eq("none at all is a document of no runs", json.loads(documents(mod, v, desk_runs=())["desk.json"]), [])
    c.eq("no items given: no items document", "items.json" in documents(mod, v), False)
    c.eq("none at all is a document of no items", json.loads(documents(mod, v, items=())["items.json"]), [])
    c.eq(
        "no alerts or sources given: neither document",
        ("alerts.json" in documents(mod, v), "sources.json" in documents(mod, v)),
        (False, False),
    )
    alert = decision_alerts.Alert("SETTLED", "2026-11-09T12:00:00.000Z", "BTC rose", "fc-a", "BTC", "L0", None)
    sources = source_value.SourceValue(
        "0.05",
        "10.0",
        10,
        (source_value.SourceLine("L0", "Rules", "binance-spot", 1, 0, None, None, None, "Too few to show", None, 3, 0, 0),),
    )
    both = documents(mod, v, alerts=[alert], sources=sources)
    c.eq("given alerts are written as they are", both["alerts.json"], mod.canonical_bytes([alert]))
    c.eq("given sources are written as they are", both["sources.json"], mod.canonical_bytes(sources))
    first = v.rows[0].forecast_id
    blank = call_revisions.Reading(None, None)
    rev = call_revisions.Revision(first, "fc-later", "L1", "BTC", 4, "0.4", "0.6", "a", "b", blank, blank, False)
    linked = documents(mod, v, revisions=[rev])
    c.eq(
        "a reversal is linked on the receipt of its call",
        json.loads(linked[f"receipts/{first}.json"])["links"]["revisions"],
        [view_models.to_json(rev)],
    )
    c.eq("and none is linked without them", json.loads(documents(mod, v)[f"receipts/{first}.json"])["links"]["revisions"], [])
    c.eq(
        "each with the digest of its bytes",
        [json.loads(both["index.json"])["files"][name] for name in ("alerts.json", "sources.json")],
        [hashlib.sha256(both[name]).hexdigest() for name in ("alerts.json", "sources.json")],
    )
    swept = sweeps_view()
    withsweeps = documents(mod, v, sweeps=swept)
    c.eq("the sweeps are published as they were read", json.loads(withsweeps["sweeps.json"]), view_models.to_json(swept))
    c.eq("the summary says how many modules", json.loads(withsweeps["sweeps.json"])["summary"]["modules"], 3)
    c.eq("and they are in the index", "sweeps.json" in json.loads(withsweeps["index.json"])["files"], True)
    c.eq("with none given there is no such document", "sweeps.json" in docs, False)
    covered = coverage_view_read()
    withcoverage = documents(mod, v, coverage=covered)
    c.eq("the coverage is published as it was read", json.loads(withcoverage["coverage.json"]), view_models.to_json(covered))
    c.eq("the contract's sections are counted", json.loads(withcoverage["coverage.json"])["contract_total"], 6)
    c.eq("and it is in the index", "coverage.json" in json.loads(withcoverage["index.json"])["files"], True)
    c.eq("with none given there is no such document", "coverage.json" in docs, False)
    books = practice_books()
    withpractice = documents(mod, v, practice=books)
    c.eq(
        "the practice books are published as they were read",
        json.loads(withpractice["practice.json"]),
        [view_models.to_json(b) for b in books],
    )
    c.eq("the first holds the round trip", json.loads(withpractice["practice.json"])[0]["trades"][0]["net"], "1398.5")
    c.eq("and they are in the index", "practice.json" in json.loads(withpractice["index.json"])["files"], True)
    c.eq("with none given there is no such document", "practice.json" in docs, False)
    c.eq("none at all is a document of no books", json.loads(documents(mod, v, practice=())["practice.json"]), [])
    rows = model_rows()
    withmodel = documents(mod, v, model_challenges=rows)
    c.eq(
        "the model arm's challenges are published as they were read, in the order given",
        json.loads(withmodel["model_challenges.json"]),
        [view_models.to_json(r) for r in rows],
    )
    c.eq(
        "with their counts and their numbers as text",
        [
            (r["symbol"], r["outcome"], r["treated"]["same_changed"], r["failed"], r["attempted"], r["shift"])
            for r in json.loads(withmodel["model_challenges.json"])
        ],
        [
            ("BTC", "EFFECT_UNCERTAIN", 3, 0, 60, "+10"),
            ("ETH", "NOT_EVALUATED(unstable_baseline)", 4, 0, 60, "0"),
            ("SOL", "EFFECT_UNCERTAIN", 3, 0, 60, "-5"),
        ],
    )
    c.eq(
        "and are in the index",
        json.loads(withmodel["index.json"])["files"]["model_challenges.json"],
        hashlib.sha256(withmodel["model_challenges.json"]).hexdigest(),
    )
    c.eq("with none given there is no such document", "model_challenges.json" in docs, False)
    c.eq("nor are there signals unless they are given", "signals.json" in docs, False)
    shown = documents(mod, v, signals={"format": 1, "tokens": []})
    c.eq("given signals are written as they are and listed in the index", json.loads(shown["signals.json"]), {"format": 1, "tokens": []})
    c.eq(
        "with the digest of their bytes",
        json.loads(shown["index.json"])["files"]["signals.json"],
        hashlib.sha256(shown["signals.json"]).hexdigest(),
    )
    c.eq("nor is there a Laboratory unless one is given", "lab.json" in docs, False)
    laboratory = lab_document()
    seen = documents(mod, v, lab=laboratory)
    c.eq("a given Laboratory is written as it is", seen["lab.json"], mod.canonical_bytes(laboratory))
    c.eq("with the digest of its bytes", json.loads(seen["index.json"])["files"]["lab.json"], hashlib.sha256(seen["lab.json"]).hexdigest())
    c.eq("and it is the one document of its shape", sorted(json.loads(seen["lab.json"])), ["format", "versions"])
    c.eq("none of them is a document of no rows", json.loads(documents(mod, v, model_challenges=())["model_challenges.json"]), [])
    c.eq("an empty table is a document of no rows", json.loads(documents(mod, v, status=())["quality.json"]), [])
    c.eq("none listed is an empty list", json.loads(docs["bundles.json"]), [])
    c.eq(
        "and the digest of every other file, not of itself",
        index["files"],
        {p: hashlib.sha256(d).hexdigest() for p, d in sorted(docs.items()) if p != "index.json"},
    )
    c.eq("the same ledger at the same time is the same bytes", documents(mod, views()), docs)
    c.eq("a document is its view", json.loads(docs["rows.json"]), view_models.to_json(v.rows))
    first = v.receipts[0]
    shown = json.loads(docs[f"receipts/{first.row.forecast_id}.json"])
    c.eq("a receipt is its view", {k: v for k, v in shown.items() if k != "links"}, view_models.to_json(first))
    from hoshitori import receipt_links

    c.eq(
        "and the links of its call, from the rows the site holds",
        shown["links"],
        view_models.to_json(receipt_links.links(first.row, v.rows)),
    )
    peers = [r for r in v.rows if r.cycle_id == first.row.cycle_id and r.symbol == first.row.symbol and r.hours == first.row.hours]
    peers = [r for r in peers if r.contract == first.row.contract and r.forecast_id != first.row.forecast_id]
    c.eq(
        "which name every other agent on the same call",
        sorted(o["forecast_id"] for o in shown["links"]["others"]),
        sorted(r.forecast_id for r in peers),
    )
    bad = dataclasses.replace(v, receipts=(dataclasses.replace(first, row=dataclasses.replace(first.row, forecast_id="../escape")),))
    try:
        documents(mod, bad)
    except mod.SiteError as exc:
        c.eq(
            "a receipt whose id is not a forecast id is refused",
            str(exc),
            "'../escape' is not the id of a forecast and cannot be a file name",
        )
    else:
        c.bad.append("a path-like id was accepted as a file name")
    return c.bad


def check_write(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    docs = documents(mod, views())
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "deeper" / "site"  # its parents do not exist yet
        done = mod.write(out, docs)
        c.eq("a first write", (done.files, done.removed), (len(docs), 0))
        c.eq("every file is its bytes", {p: (out / p).read_bytes() == d for p, d in docs.items()}, dict.fromkeys(docs, True))
        c.eq("no temporary file is left", sorted(p.name for p in out.rglob("*.tmp")), [])
        (out / "bundles").mkdir()
        (out / "bundles" / "keep.txt").write_text("mine")
        (out / "notes.md").write_text("mine too")
        (out / "receipts" / "readme.txt").write_text("not a document of ours")
        gone = next(p for p in docs if p.startswith("receipts/"))
        (out / "receipts" / "fcstale000000000000000000000.json").write_text("{}")
        again = mod.write(out, {p: d for p, d in docs.items() if p != gone})
        c.eq("a second write, one receipt fewer, one stale one", (again.files, again.removed), (len(docs) - 1, 2))
        c.eq(
            "the stale receipts are gone",
            [(out / "receipts" / "fcstale000000000000000000000.json").exists(), (out / gone).exists()],
            [False, False],
        )
        c.eq(
            "what is not ours stays",
            [(out / "bundles" / "keep.txt").read_text(), (out / "notes.md").read_text(), (out / "receipts" / "readme.txt").read_text()],
            ["mine", "mine too", "not a document of ours"],
        )
        mod.write(out, docs)
        c.eq("and a third write brings it back", (out / gone).read_bytes(), docs[gone])
        (out / "today.json").write_bytes(b"torn")
        mod.write(out, docs)
        c.eq("a changed file is replaced whole", (out / "today.json").read_bytes(), docs["today.json"])
        only = {"today.json": docs["today.json"], "index.json": docs["index.json"]}
        cleared = mod.write(out, only)
        c.eq("a write of fewer documents removes the others it owns", (cleared.files, cleared.removed), (2, len(docs) - 2))
        c.eq(
            "only ours were removed", sorted(p.name for p in out.iterdir()), ["bundles", "index.json", "notes.md", "receipts", "today.json"]
        )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_bytes, check_documents, check_write, check_ledger_state):
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
