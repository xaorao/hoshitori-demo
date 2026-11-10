"""The static documents of the interface, written from the views of a ledger (``view_models``).

The site is files: ``index.json`` (what the others are, with their digests, and the state the ledger was in), ``today.json``,
``cycles.json``, ``rows.json``, ``record.json`` and one ``receipts/<forecast id>.json`` for every call, and the documents of the other pages
when they are given (among them ``model_challenges.json``, the recorded challenges of the model arm for the What if page). They are
written as canonical JSON (sorted keys, no extra whitespace, one newline) from the same views, so two exports of one ledger at one time are
the same bytes and a page, a screen-reader text and the API cannot differ. A write replaces a file whole (a temporary file in the same
directory, then a rename), so a reader never sees half a document; the files this module owns that a later export no longer produces are
removed, and nothing else in the directory is touched (the bundles of committed cycles sit under ``bundles/``).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from hoshitori import (
    call_revisions,
    challenge_view,
    claims_file,
    coverage_view,
    decision_alerts,
    desk_view,
    forgery,
    lab_view,
    model_challenge_file,
    outcome_review,
    practice_view,
    receipt_links,
    source_value,
    sweep_view,
    today_items,
    view_models,
)
from hoshitori import claim_checks as claim_checks_module
from hoshitori import drill as drill_file
from hoshitori import quality as quality_table
from hoshitori.ledger import STATE_OK

FORMAT: Final = 24  # raised for each document added or changed since 1; the page refuses an index of another number
INDEX: Final = "index.json"
TOP: Final = (
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
)
BUNDLES: Final = "bundles"
FORGED: Final = "forged"
RECEIPTS: Final = "receipts"
FORECAST_ID: Final = re.compile(r"fc[a-z2-7]{26,}")


class SiteError(ValueError):
    """Documents that cannot be written as a site."""


@dataclass(frozen=True)
class Bundle:
    """A bundle of a committed cycle as the site holds it: its folder under ``bundles/`` and every path in it.

    A page cannot list a folder, so the paths are written down."""

    cycle_id: str
    files: tuple[str, ...]
    directories: tuple[str, ...]


@dataclass(frozen=True)
class ForgedCopy:
    """The forged copy of a cycle's bundle as the site holds it, under ``forged/``: what was changed (``forgery.Summary``) and every path
    in it, and the root it is checked against (the real root with the log's key replaced by the stand-in forger's,
    ``forgery.demo_root``). It is never listed among the bundles, and a page that shows it says what it is."""

    summary: forgery.Summary
    files: tuple[str, ...]
    directories: tuple[str, ...]
    root: bytes


@dataclass(frozen=True)
class Written:
    files: int
    removed: int


@dataclass(frozen=True)
class LedgerState:
    """The state of the ledger the documents were made from (``hoshitori.ledger``: ``OK``, or a failure state such as ``INTEGRITY_FAILURE``)
    and the ledger's own reason for it; the index carries it, so every page can say it (ADR-026: visible on every surface)."""

    state: str
    detail: str


def canonical_bytes(value: object) -> bytes:
    """A document as bytes: sorted keys, no extra whitespace, UTF-8, one newline."""
    return (json.dumps(view_models.to_json(value), sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n").encode()


def documents(
    views: view_models.Views,
    bundles: Sequence[Bundle] = (),
    root: bytes | None = None,
    status: Sequence[quality_table.Row] | None = None,
    drills: Sequence[drill_file.Drill] | None = None,
    claims: Sequence[claims_file.Claim] | None = None,
    challenges: Sequence[challenge_view.Row] | None = None,
    boundaries: Sequence[challenge_view.Boundary] | None = None,
    claim_checks: Sequence[claim_checks_module.Check] | None = None,
    model_challenges: Sequence[model_challenge_file.Row] | None = None,
    forged: ForgedCopy | None = None,
    desk_runs: Sequence[desk_view.DeskRun] | None = None,
    practice: Sequence[practice_view.PracticeBook] | None = None,
    sweeps: sweep_view.Sweeps | None = None,
    coverage: coverage_view.Coverage | None = None,
    signals: Mapping[str, object] | None = None,
    *,
    lab: lab_view.Document | None = None,
    items: Sequence[today_items.TodayItem] | None = None,
    alerts: Sequence[decision_alerts.Alert] | None = None,
    sources: source_value.SourceValue | None = None,
    revisions: Sequence[call_revisions.Revision] | None = None,
    ledger: LedgerState,
) -> dict[str, bytes]:
    """Every document of the site, by relative path, the index last.

    ``bundles`` are the bundles that sit under ``bundles/``; ``root`` is the trusted-root document the league runs under, as it is;
    ``status`` are the rows of the status table, for the Evidence of quality page; ``drills`` are the recorded failure drills;
    ``claims`` are claims that were read, recorded; ``challenges`` are the rules arm's answers to taking each source out of a cycle;
    ``boundaries`` are how far each of its calls was from changing; ``claim_checks`` are the checks run on the recorded claims;
    ``model_challenges`` are the recorded challenges of the model arm, each held to the ledger (``model_challenge_file.for_site``);
    ``desk_runs`` are the Desk's runs (``desk_view``), shown as they were recorded and labelled so;
    ``practice`` are the practice books (``practice_view``), a simulated portfolio shown as such;
    ``sweeps`` are the mutation sweeps of the modules (``sweep_view``), for the Evidence of quality page;
    ``coverage`` is how much of the rubric and the contract has a check behind it (``coverage_view``), for the same page;
    ``lab`` is the Hypothesis Laboratory's document (``lab_view``): its versions in words, with previews on stored candles and stored runs;
    ``items`` are the developments of Today (``today_items``): what happened, whether it was checked and where the check stands;
    ``revisions`` are the agents' reversals of open calls (``call_revisions``), linked on both receipts of each;
    ``sources`` is whether each source made the rules arm's settled calls better (``source_value``), exploratory;
    ``alerts`` are the decision alerts (``decision_alerts``): calls proved wrong by their own terms, changed premises, rungs, settlements;
    ``forged`` is the forged copy of one of the bundles, made to show the verifier refuse it (``hoshitori.forgery``);
    ``ledger`` is the state the ledger was in. The records of a ledger that is not ``OK`` are not reconstructible, so its site holds none
    of them, nor a challenge of one of them (``LEDGER_SPEC`` section 8: the product shows NOT RECONSTRUCTIBLE for affected records, never a
    pass)."""
    if ledger.state != STATE_OK and (views.cycles or bundles or model_challenges):
        raise SiteError(f"the ledger is in {ledger.state}: its records are NOT RECONSTRUCTIBLE, so the site holds none of them")
    out: dict[str, bytes] = {
        "today.json": canonical_bytes(views.today),
        "cycles.json": canonical_bytes(views.cycles),
        "rows.json": canonical_bytes(views.rows),
        "record.json": canonical_bytes(views.record),
        "bundles.json": canonical_bytes(
            [
                {"cycle_id": b.cycle_id, "path": f"{BUNDLES}/{b.cycle_id}", "files": list(b.files), "directories": list(b.directories)}
                for b in bundles
            ]
        ),
    }
    out["reviews.json"] = canonical_bytes(outcome_review.build(views))
    if views.ryo is not None:
        out["ryo.json"] = canonical_bytes(views.ryo)
    if views.comparisons is not None:
        out["comparisons.json"] = canonical_bytes(views.comparisons)
    if root is not None:
        out["trusted_root.json"] = root
    if status is not None:
        out["quality.json"] = canonical_bytes(list(status))
    if drills is not None:
        out["drill.json"] = canonical_bytes(list(drills))
    if claims is not None:
        out["claims.json"] = canonical_bytes(list(claims))
    if challenges is not None:
        out["challenges.json"] = canonical_bytes(list(challenges))
    if boundaries is not None:
        out["boundaries.json"] = canonical_bytes(list(boundaries))
    if claim_checks is not None:
        out["claim_checks.json"] = canonical_bytes(list(claim_checks))
    if desk_runs is not None:
        out["desk.json"] = canonical_bytes(list(desk_runs))
    if practice is not None:
        out["practice.json"] = canonical_bytes(list(practice))
    if sweeps is not None:
        out["sweeps.json"] = canonical_bytes(sweeps)
    if coverage is not None:
        out["coverage.json"] = canonical_bytes(coverage)
    if signals is not None:
        out["signals.json"] = canonical_bytes(signals)
    if lab is not None:
        out["lab.json"] = canonical_bytes(lab)
    if items is not None:
        out["items.json"] = canonical_bytes(list(items))
    if alerts is not None:
        out["alerts.json"] = canonical_bytes(list(alerts))
    if sources is not None:
        out["sources.json"] = canonical_bytes(sources)
    if model_challenges is not None:
        out["model_challenges.json"] = canonical_bytes(list(model_challenges))
    if forged is not None:
        if forged.summary.cycle_id not in {b.cycle_id for b in bundles}:
            raise SiteError("a forged copy is shown only beside the bundle it was made from")
        edit = forged.summary.edit
        out["forgery.json"] = canonical_bytes(
            {
                "format": forgery.FORMAT,
                "cycle_id": forged.summary.cycle_id,
                "edit": {"record": edit.record_id, "field": edit.field, "was": edit.was, "now": edit.now},
                "standin_vkey": forged.summary.standin_vkey,
                "size": forged.summary.size,
                "honest_root": forged.summary.honest_root,
                "forged_root": forged.summary.forged_root,
                "path": f"{FORGED}/{forged.summary.cycle_id}",
                "files": list(forged.files),
                "directories": list(forged.directories),
            }
        )
        out["forged_root.json"] = forged.root
    for receipt in views.receipts:
        ident = receipt.row.forecast_id
        if FORECAST_ID.fullmatch(ident) is None:
            raise SiteError(f"{ident!r} is not the id of a forecast and cannot be a file name")
        linked = receipt_links.links(receipt.row, views.rows, practice or (), desk_runs or (), revisions or ())
        out[f"{RECEIPTS}/{ident}.json"] = canonical_bytes({**view_models.to_json(receipt), "links": view_models.to_json(linked)})
    index = {
        "format": FORMAT,
        "generated_at": views.today.generated_at,
        "mode": views.today.mode,
        "ledger": {"state": ledger.state, "detail": ledger.detail},
        "counts": {"cycles": len(views.cycles), "rows": len(views.rows), "receipts": len(views.receipts), "bundles": len(bundles)},
        "files": {path: hashlib.sha256(data).hexdigest() for path, data in out.items()},
    }
    out[INDEX] = canonical_bytes(index)
    return out


def _owned(root: Path) -> set[str]:
    found = {name for name in (*TOP, INDEX) if (root / name).is_file()}
    receipts = root / RECEIPTS
    if receipts.is_dir():
        found |= {f"{RECEIPTS}/{p.name}" for p in receipts.iterdir() if p.is_file() and p.suffix == ".json"}
    return found


def write(out: Path, docs: Mapping[str, bytes]) -> Written:
    """Write the documents under ``out``; a file this module wrote before and this export does not produce is removed."""
    out.mkdir(parents=True, exist_ok=True)
    (out / RECEIPTS).mkdir(exist_ok=True)
    stale = _owned(out) - set(docs)
    for path, data in docs.items():
        target = out / path
        temporary = target.with_name(target.name + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, target)
    for path in stale:
        (out / path).unlink()
    return Written(len(docs), len(stale))
