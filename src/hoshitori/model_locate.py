"""Find a committed call of the single-model arm in the ledger, with its cycle's frozen snapshot and what one source put into it: what the
scripts that ask the model again about a recorded call share (``scripts/challenge_live.py``, ``scripts/model_source_value.py``)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hoshitori import arm_l1, deployment, forecast, lineage, model_challenge, model_settings, snapshot
from hoshitori.snapshot import Candidate

HOUR_MS: Final = 3_600_000
ATTEMPTS: Final = 2  # a stage is asked once and repaired at most once (ModelClient.generate; a call record's attempt is 1 or 2)


class Refused(Exception):
    """A file or a setting the script cannot use; the message says which."""


@dataclass(frozen=True)
class Located:
    frozen: snapshot.Snapshot
    store: lineage.EvidenceStore
    candidate: Candidate
    removed: tuple[str, ...]
    forecast_id: str
    original: str
    agent_version: str  # of the committed forecast


def locate(records: list[dict[str, Any]], cycle: str, symbol: str, hours: int, source: str) -> Located:
    """The committed cycle's snapshot, the candidate, what the source has in it, and the forecast of the single judgment to challenge."""
    shot = next((r for r in records if r.get("type") == "snapshot" and r["body"]["cycle_id"] == cycle), None)
    manifest = next((r for r in records if r.get("type") == "manifest" and r["body"]["cycle_id"] == cycle), None)
    if shot is None or manifest is None:
        raise Refused("the ledger holds no snapshot and manifest of that cycle")
    evidence = [r for r in records if r.get("type") == "evidence"]
    frozen = snapshot.rebuild(shot, {r["id"]: r for r in evidence})
    store = lineage.rebuild(
        [{k: v for k, v in r.items() if k != "committed_at"} for r in evidence]
    )  # the ledger adds the time it was written
    entry = next((e for e in manifest["body"]["entries"] if e["inclusion"] == "INCLUDED" and e["symbol"] == symbol), None)
    if entry is None:
        raise Refused(f"{symbol} is not a candidate of that cycle")
    slot = next(s for s in frozen.slots if s.candidate_id == entry["candidate_id"])
    removed = tuple(item.evidence_id for item, _ in frozen.items if item.source == source and item.evidence_id in slot.item_ids)
    if not removed:
        raise Refused(f"the candidate holds nothing from {source}")
    fid = forecast.forecast_id(cycle, arm_l1.ARM, slot.asset_id, "D", hours * HOUR_MS)
    committed = next((r for r in records if r.get("type") == forecast.TYPE and r["id"] == fid), None)
    if committed is None:
        raise Refused("the model arm made no forecast in that slot, so there is no committed action to challenge")
    p = Decimal(committed["body"]["p"])
    original = "UP" if p > model_challenge.HALF else "DOWN" if p < model_challenge.HALF else "FLAT"
    candidate = Candidate(slot.candidate_id, slot.asset_id)
    return Located(frozen, store, candidate, removed, fid, original, committed["body"]["agent_version_hash"])


def the_arm(settings: deployment.Settings) -> arm_l1.Config:
    if settings.models is None:
        raise Refused("the settings name no model")
    l1 = model_settings.load(settings.models).l1
    if l1 is None:
        raise Refused("the model settings have no L1 arm")
    return l1
