"""Offline verification of a bundle (verification spec sections 1 to 3).

The verdict has four parts that are never merged:

* **integrity**: the stored bytes are the bytes that were committed (canonical form, leaf hash, inclusion proof to a
  checkpoint signed by the log key of the trusted root, within that key's era), raw payloads match their digests;
* **timing**: which time-stamp tokens count and the level they support (``hoshitori.timing``), per record;
* **eligibility**: whether the evidence could enter a scored cycle;
* **not proven**: a standing list (verification spec section 1, item 4), which gains "that this bundle came from
  Hoshitori" when no trusted root vouches for the bundle (section 2).

A reference (``refs``) whose record is not in the bundle is listed in ``integrity.refs.unresolved``. It does not fail integrity
(what is present is intact) but it makes the bundle not scorable, with the reason ``UNRESOLVED_REFERENCE``: a forecast whose cited
records are not all in the bundle cannot be judged on what it read. A reference whose record
is present with other bytes (``refs.mismatched``) fails integrity.

The verdict is a pure function of the bundle, the trusted root and its status. It reads no clock and no network. Without
a trusted root the verdict is ``BUNDLE_ASSERTED``: the bundle is checked for internal consistency only, nothing says it
came from this log, no token is believed and the level is none. An expired root yields the same checks with state
``EXPIRED`` and is never scorable. On a runtime whose Unicode is older than 15.0 there is no verdict at all: the canonical
form cannot be checked there (``canonical.UnicodeVersionError``).

A bundle is untrusted input. Every bundle file except the raw payloads is read through ``read_bundle_file``: it must be a
regular file by its own opened descriptor and within the limit for its kind (VERIFIER_THREAT_MODEL section 4: 1 MiB for a
record; for a token what ``verify_token`` accepts; for ``publication.json``, for which no limit is stated, the same 1 MiB),
and no more than the limit and one byte of it is ever read. A FIFO or a link to a device reports size 0 and may never end.

Failures are reported as short codes in ``error`` fields (the same codes in the Node verifier); ``detail`` fields carry
free text for people and are not part of the contract between the two implementations.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Final, cast

from hoshitori import bundle as layout
from hoshitori import canonical, envelope, merkle, timing
from hoshitori.checkpoint import Checkpoint, CheckpointError, parse_text, verify_checkpoint
from hoshitori.note import NoteError, split_note
from hoshitori.timeline import TimelineError, from_ms, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339
from hoshitori.tlog_proof import MAX_PROOF_BYTES, ProofError, TlogProof, parse_proof
from hoshitori.trusted_root import ERA_ACCEPT, TimeStampRules, TrustedRoot
from hoshitori.tsa import MAX_TOKEN_BYTES, TokenError, TokenPolicy, verify_token

VERDICT_FORMAT: Final = 1
MAX_ITEMS: Final = 10_000
MAX_ITEM_BYTES: Final = 1_048_576
MAX_TOKENS: Final = 16
MAX_RAW_BYTES: Final = 64 * 1024 * 1024
MAX_PUBLICATION_BYTES: Final = 1_048_576
MAX_CONSISTENCY_BYTES: Final = 65_536
MAX_CONSISTENCY_HASHES: Final = 256
STEP_TYPE: Final = "forecast_state"
ENDING_STATES: Final = ("SETTLED", "AMBIGUOUS", "VOID", "UNSETTLEABLE")
SETTLEMENT_TYPE: Final = "settlement"
_SETTLEMENT_MEMBERS: Final = frozenset(
    {"forecast_id", "contract", "pair_id", "rule_version", "state", "outcome", "reason", "p0", "outcome_price", "touch_open_ms", "flags"}
)
_SETTLEMENT_STATES: Final = frozenset(ENDING_STATES)
_PRICE: Final = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
SETTLEMENT_FAULTS: Final = ("absent", "conflicting", "malformed", "mismatch", "early")
MINUTE_MS: Final = 60_000
_HASH: Final = re.compile(r"[0-9a-f]{64}")
EVIDENCE_TYPE: Final = "evidence"
FORECAST_TYPE: Final = "forecast"
NOT_REGULAR: Final = "not a regular file"

STATE_PINNED: Final = "PINNED"
STATE_EXPIRED: Final = "EXPIRED"
STATE_ASSERTED: Final = "BUNDLE_ASSERTED"

NOT_PROVEN: Final = [
    "that the evidence was true or complete",
    "that the forecast is good",
    "that the agent reasoned as stored",
    "who controls the signing key",
    "that the log held nothing before its first append, or that no other history exists beyond the checkpoints seen",
    "that any witness cosigned: this verifier does not check cosignatures, so no record reaches level C4",
    "that no time-stamp certificate had been revoked: revocation evidence is neither archived nor checked by this verifier version",
    "both authorities honest at once: the level assumes that the time-stamp authorities did not collude or share a time source",
    "the clock of the machine that ran this verifier",
]
NOT_PROVEN_WITHOUT_ROOT: Final = "that this bundle came from Hoshitori"


class NotRegularFile(Exception):
    """A bundle file that is a FIFO, a device, a directory or anything but a regular file; nothing of it is read."""


class FileTooLarge(Exception):
    """A bundle file larger than the limit for its kind."""


def _u16(text: str) -> bytes:
    """Sort key in UTF-16 code-unit order, as the Node verifier orders names; code-point order differs beyond U+FFFF."""
    return text.encode("utf-16-be", "surrogatepass")


def read_bundle_file(path: Path, limit: int) -> bytes:
    """The bytes of the bundle file at ``path``, which must be a regular file of at most ``limit`` bytes.

    The file is opened without blocking (a FIFO that no process writes would block an ordinary open for ever), the opened
    descriptor must be a regular file and no larger than the limit, and no more than ``limit + 1`` bytes are read, so a file
    that grows while it is read is refused too. Raises ``OSError`` when the file cannot be opened or read, ``NotRegularFile``
    and ``FileTooLarge``.
    """
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOCTTY", 0))
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise NotRegularFile(NOT_REGULAR)
        if info.st_size > limit:
            raise FileTooLarge(f"larger than {limit} bytes")
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(65_536, limit + 1 - total)):
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise FileTooLarge(f"larger than {limit} bytes")
        return b"".join(chunks)
    finally:
        os.close(descriptor)


@dataclass
class _Item:
    stem: str
    canonical_bytes: bytes | None = None
    record: dict[str, canonical.JsonValue] | None = None
    committed_at: datetime | None = None
    created_at: datetime | None = None
    window_opens_at: datetime | None = None
    proof: TlogProof | None = None
    checkpoint: Checkpoint | None = None
    note_text: bytes | None = None
    error: str | None = None
    detail: str | None = None
    canonical_ok: bool = False
    signature: str = "unauthenticated"
    inclusion: bool = False


def _ms(moment: datetime) -> str:
    return format_rfc3339_ms(moment)


def _to_the_millisecond(moment: datetime) -> datetime:
    """A time cut to the whole millisecond, as the Node verifier reads every time, so that both judge the same instant."""
    return moment.replace(microsecond=moment.microsecond // 1000 * 1000)


def _fail(item: _Item, code: str, detail: str) -> None:
    if item.error is None:
        item.error = code
        item.detail = detail


def _read_stems(directory: Path) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    items_dir = directory / layout.ITEMS
    if not items_dir.is_dir():
        return [], ["NO_ITEMS"]
    stems: list[str] = []
    for path in sorted(items_dir.iterdir(), key=lambda q: _u16(q.name)):
        if path.name.endswith(layout.CANONICAL_SUFFIX):
            stem = path.name[: -len(layout.CANONICAL_SUFFIX)]
            if layout.STEM.fullmatch(stem):
                stems.append(stem)
            else:
                errors.append("BAD_STEM")
    if len(stems) > MAX_ITEMS:
        return stems[:MAX_ITEMS], [*errors, "TOO_MANY_ITEMS"]
    if not stems:
        errors.append("NO_ITEMS")
    return stems, errors


def _load_item(directory: Path, stem: str, root: TrustedRoot | None, authenticated: bool) -> _Item:
    item = _Item(stem)
    item_path = directory / layout.ITEMS / f"{stem}{layout.CANONICAL_SUFFIX}"
    proof_path = directory / layout.ITEMS / f"{stem}{layout.PROOF_SUFFIX}"
    try:
        item.canonical_bytes = read_bundle_file(item_path, MAX_ITEM_BYTES)
    except FileTooLarge:
        _fail(item, "TOO_LARGE", "record file")
        return item
    except (OSError, NotRegularFile) as exc:
        _fail(item, "UNREADABLE", str(exc))
        return item
    try:
        item.record = canonical.parse_record(item.canonical_bytes)
        item.canonical_ok = canonical.dumps(item.record) == item.canonical_bytes
    except canonical.CanonicalError as exc:
        _fail(item, "CANONICAL", str(exc))
        return item
    if not item.canonical_ok:
        _fail(item, "CANONICAL", "not in canonical form")
    try:
        envelope.validate_committed_record(item.record)
        item.committed_at = _to_the_millisecond(parse_rfc3339(str(item.record["committed_at"])))
        item.created_at = _to_the_millisecond(parse_rfc3339(str(item.record["created_at"])))
    except (envelope.EnvelopeError, TimeError) as exc:
        _fail(item, "ENVELOPE", str(exc))
    body = item.record.get("body")
    opens = body.get("outcome_window_opens_at") if isinstance(body, dict) else None
    if opens is not None:
        try:
            item.window_opens_at = _to_the_millisecond(parse_rfc3339(opens)) if isinstance(opens, str) else None
        except TimeError:
            item.window_opens_at = None
        if item.window_opens_at is None:
            _fail(item, "WINDOW", "outcome_window_opens_at is not an RFC 3339 time")
    try:
        item.proof = parse_proof(read_bundle_file(proof_path, MAX_PROOF_BYTES))
    except FileTooLarge:
        _fail(item, "PROOF", "proof file too large")
        return item
    except (OSError, NotRegularFile):
        _fail(item, "MISSING_PROOF", str(proof_path.name))
        return item
    except ProofError as exc:
        _fail(item, "PROOF", str(exc))
        return item
    _check_checkpoint(item, root, authenticated)
    return item


def _check_checkpoint(item: _Item, root: TrustedRoot | None, authenticated: bool) -> None:
    proof = item.proof
    assert proof is not None and item.canonical_bytes is not None
    try:
        text, _ = split_note(proof.note)
        item.note_text = text
    except NoteError as exc:
        _fail(item, "CHECKPOINT", str(exc))
        return
    if authenticated and root is not None:
        verified: Checkpoint | None = None
        failure = "SIGNATURE"
        for era in root.eras:
            try:
                candidate = verify_checkpoint(proof.note, [era.key], root.origin)
            except CheckpointError:
                continue
            if root.era_check(era.key, candidate.size) != ERA_ACCEPT:
                failure = "ERA"
                continue
            verified = candidate
            break
        if verified is None:
            try:
                parse_text(text)
            except CheckpointError as exc:
                _fail(item, "CHECKPOINT", str(exc))
                return
            item.signature = "failed"
            _fail(item, failure, "no log key of the trusted root vouches for this checkpoint at this size")
            return
        item.checkpoint = verified
        item.signature = "verified"
    else:
        try:
            item.checkpoint = parse_text(text)
        except CheckpointError as exc:
            _fail(item, "CHECKPOINT", str(exc))
            return
    cp = item.checkpoint
    leaf = merkle.leaf_hash(item.canonical_bytes)
    item.inclusion = merkle.verify_inclusion(leaf, proof.index, cp.size, list(proof.path), cp.root)
    if not item.inclusion:
        _fail(item, "INCLUSION", "the inclusion proof does not reach the checkpoint root")


def _producer_bytes(record: dict[str, Any]) -> bytes:
    """A record as its producer submitted it: the stored record without the member the ledger's writer added; what a reference names."""
    return canonical.dumps({k: v for k, v in record.items() if k != "committed_at"})


def _refs(items: list[_Item]) -> dict[str, Any]:
    by_id: dict[str, set[str]] = {}
    for item in items:
        if item.record is not None and item.canonical_bytes is not None and isinstance(item.record.get("id"), str):
            by_id.setdefault(str(item.record["id"]), set()).add(hashlib.sha256(_producer_bytes(item.record)).hexdigest())
    resolved = 0
    unresolved: list[str] = []
    mismatched: list[str] = []
    for item in items:
        references = item.record.get("refs") if item.record is not None else None
        for reference in references if isinstance(references, list) else []:
            if not isinstance(reference, dict):
                continue
            rid, digest = str(reference.get("id")), str(reference.get("sha256"))
            label = f"{item.stem}->{rid}"
            if rid not in by_id:
                unresolved.append(label)
            elif digest in by_id[rid]:
                resolved += 1
            else:
                mismatched.append(label)
    return {"resolved": resolved, "unresolved": sorted(unresolved, key=_u16), "mismatched": sorted(mismatched, key=_u16)}


def _raw(directory: Path, items: list[_Item]) -> dict[str, Any]:
    cited: dict[str, str] = {}
    for item in items:
        body = item.record.get("body") if item.record is not None else None
        digest = body.get("raw_sha256") if isinstance(body, dict) else None
        if isinstance(digest, str):
            cited[digest] = item.stem
    verified: list[str] = []
    missing: list[str] = []
    mismatched: list[str] = []
    raw_dir = directory / layout.RAW
    present = {p.name: p for p in raw_dir.iterdir()} if raw_dir.is_dir() else {}

    def intact(path: Path, digest: str) -> bool:
        # only a regular file of reasonable size whose bytes hash to its name is a raw payload
        return path.is_file() and path.stat().st_size <= MAX_RAW_BYTES and hashlib.sha256(path.read_bytes()).hexdigest() == digest

    for digest in sorted(cited, key=_u16):
        path = present.get(digest)
        if path is None:
            missing.append(digest)
        elif intact(path, digest):
            verified.append(digest)
        else:
            mismatched.append(digest)
    for name in sorted(present, key=_u16):
        if name not in cited and not intact(present[name], name):
            mismatched.append(name)
    return {
        "verified": verified,
        "missing": missing,
        "mismatched": sorted(set(mismatched), key=_u16),
        "unreferenced": sorted((name for name in present if name not in cited), key=_u16),
    }


def _reject_constant(name: str) -> None:
    raise ValueError(f"{name} is not valid JSON")


def _publication(directory: Path) -> dict[str, Any]:
    claims = 0
    path = directory / layout.PUBLICATION
    if path.exists():
        try:
            text = read_bundle_file(path, MAX_PUBLICATION_BYTES).decode("utf-8")
            data = json.loads(text, parse_constant=_reject_constant)
            claims = len(data.get("claims", [])) if isinstance(data, dict) and isinstance(data.get("claims", []), list) else -1
        except Exception:
            claims = -1
    return {
        "claimed": claims, "authenticated_offline": 0,
        "note": "publication claims in a bundle are not authenticated offline and never raise a level",
    }  # fmt: skip


@dataclass
class _Groups:
    """The items of a bundle by the checkpoint that proves them: the stamped one, which the tokens cover, and the later one, which the log
    signed after it, when the bundle holds a consistency proof between the two."""

    stamped: list[_Item]
    later: list[_Item]
    stamped_text: bytes | None
    stamped_checkpoint: Checkpoint | None
    later_checkpoint: Checkpoint | None
    consistency: str  # "none", "verified", "failed" or "missing"
    errors: list[str]

    @property
    def ok(self) -> bool:
        """Whether the checkpoints of the bundle are one, or two that are shown to belong to one log."""
        return not self.errors and self.consistency in ("none", "verified")


def _consistency_path(directory: Path) -> tuple[str, dict[str, Any] | None]:
    """The parsed ``consistency.json``: ``("missing", None)`` when the file is not there, ``("failed", None)`` when it cannot be used."""
    path = directory / layout.CONSISTENCY
    try:
        raw = read_bundle_file(path, MAX_CONSISTENCY_BYTES)
    except FileNotFoundError:
        return "missing", None
    except (OSError, NotRegularFile, FileTooLarge):
        return "failed", None
    try:
        document = canonical.parse_record(raw)
        if canonical.dumps(document) != raw:
            return "failed", None
    except canonical.CanonicalError:
        return "failed", None
    members = {"format", "from_size", "to_size", "hashes"}
    hashes = document.get("hashes")
    sizes = (document.get("from_size"), document.get("to_size"))
    if (
        set(document) != members
        or document.get("format") != layout.CONSISTENCY_FORMAT
        or isinstance(document.get("format"), bool)
        or not isinstance(hashes, list)
        or len(hashes) > MAX_CONSISTENCY_HASHES
        or not all(isinstance(h, str) and _HASH.fullmatch(h) is not None for h in hashes)
        or not all(isinstance(s, int) and not isinstance(s, bool) and s >= 1 for s in sizes)
    ):
        return "failed", None
    return "present", cast(dict[str, Any], document)


def _groups(directory: Path, items: list[_Item]) -> _Groups:
    texts = {i.note_text for i in items if i.note_text is not None}
    whole = bool(items) and all(i.note_text is not None for i in items)
    if len(texts) == 1 and whole:
        only = next(iter(texts))
        return _Groups(items, [], only, items[0].checkpoint, None, "none", [])
    if not items or len(texts) == 0 or (len(texts) == 1 and not whole):
        return _Groups(items, [], None, None, None, "none", ["NO_CHECKPOINT"] if items else [])
    if len(texts) > 2 or not whole:
        return _Groups(items, [], None, None, None, "none", ["CHECKPOINTS_DIFFER"])
    # two checkpoints: the bundle must prove that the larger extends the smaller
    by_text: dict[bytes, Checkpoint | None] = {}
    for text in texts:
        by_text[text] = next((i.checkpoint for i in items if i.note_text == text and i.checkpoint is not None), None)
    known = [(cp.size, text, cp) for text, cp in by_text.items() if cp is not None]
    state, document = _consistency_path(directory)
    small: Checkpoint | None = None
    large: Checkpoint | None = None
    small_text: bytes | None = None
    stamped, later = list(items), []
    if len(known) == 2:
        known.sort(key=lambda entry: (entry[0], entry[1]))
        small_text, small, large = known[0][1], known[0][2], known[1][2]
        stamped = [i for i in items if i.note_text == small_text]
        later = [i for i in items if i.note_text != small_text]
    if state == "missing":
        return _Groups(stamped, later, small_text, small, large, "missing", ["CHECKPOINTS_DIFFER"])
    verified = False
    if state == "present" and document is not None and small is not None and large is not None:
        path = [bytes.fromhex(h) for h in document["hashes"]]
        verified = (
            small.size < large.size
            and document["from_size"] == small.size
            and document["to_size"] == large.size
            and merkle.verify_consistency(small.size, large.size, path, small.root, large.root)
        )
    if not verified:
        return _Groups(stamped, later, small_text, small, large, "failed", ["CONSISTENCY_FAILED"])
    return _Groups(stamped, later, small_text, small, large, "verified", [])


def verify_bundle(directory: Path, root: TrustedRoot | None, root_status: str = "CURRENT") -> dict[str, Any]:
    """Verify the bundle at ``directory`` against ``root`` (the trusted root, as updated to ``root_status``).

    Raises ``canonical.UnicodeVersionError``, before anything is read, on a runtime whose Unicode is older than 15.0.
    """
    canonical.require_runtime_unicode()
    if root is None:
        state = STATE_ASSERTED
    elif root_status == "CURRENT":
        state = STATE_PINNED
    elif root_status == "EXPIRED":
        state = STATE_EXPIRED
    else:
        state = STATE_ASSERTED
        root = None
    authenticated = root is not None
    stems, structural = _read_stems(directory)
    items = [_load_item(directory, stem, root, authenticated) for stem in stems]
    groups = _groups(directory, items)
    one_checkpoint = bool(items) and groups.consistency == "none" and not groups.errors
    refs = _refs(items)
    raw = _raw(directory, items)
    errors = [*structural, *groups.errors]
    item_ok = all(i.error is None and i.canonical_ok and i.inclusion for i in items)
    consistent = item_ok and groups.ok and bool(items) and not refs["mismatched"] and not raw["mismatched"] and not structural
    verdict: dict[str, Any] = {
        "verifier": "python",
        "format": VERDICT_FORMAT,
        "root_of_trust": {
            "state": state,
            "version": root.version if root is not None else None,
            "sha256": root.sha256 if root is not None else None,
            "status": root_status if root is not None else None,
        },
        "integrity": {
            "ok": consistent and authenticated,
            "self_consistent": consistent,
            "one_checkpoint": one_checkpoint,
            "checkpoint": _cp_summary(groups.stamped_checkpoint) if groups.ok else None,
            "checkpoints": {
                "stamped": _cp_summary(groups.stamped_checkpoint) if groups.ok else None,
                "later": _cp_summary(groups.later_checkpoint) if groups.ok else None,
                "consistency": groups.consistency,
            },
            "items": {
                i.stem: {
                    "id": i.record.get("id") if i.record else None,
                    "type": i.record.get("type") if i.record else None,
                    "committed_at": i.record.get("committed_at") if i.record else None,
                    "canonical": i.canonical_ok,
                    "signature": i.signature,
                    "inclusion": i.inclusion,
                    "error": i.error,
                    "detail": i.detail,
                }
                for i in items
            },
            "refs": refs,
            "raw": raw,
            "errors": sorted(set(errors)),
        },
    }
    verdict["cycle"] = _cycle(groups.stamped, groups.ok and bool(items))
    verdict["timing"] = _timing(
        directory, groups.stamped, root, authenticated, bool(verdict["integrity"]["ok"]), groups.stamped_text if groups.ok else None
    )
    verdict["timing"]["later_items"] = [i.stem for i in groups.later]
    verdict["cycle"]["later"] = _later(groups, verdict["timing"], bool(verdict["integrity"]["ok"]))
    verdict["eligibility"] = _eligibility(items, root, state, verdict)
    verdict["not_proven"] = [*NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT] if state == STATE_ASSERTED else list(NOT_PROVEN)
    return verdict


def _cycle(items: list[_Item], checkpoints_ok: bool) -> dict[str, Any]:
    """Whether the bundle holds the whole batch of its cycle.

    The cycle's committing step is the last record of the batch that the time-stamps cover, and it names how many records the batch holds
    beside itself and their root. The records before it in the log, that many, are the batch: all must be in the bundle, and their digests,
    in log order, must make the root it names. A record left out, added or changed makes a different root; a step that names a size or a
    root the records do not give leaves the bundle not complete. A bundle with no committing step, or with more than one (a bundle shows one
    cycle), shows nothing."""
    steps = [i for i in items if _is_committing(i)]
    out: dict[str, Any] = {"declared": bool(steps), "steps": len(steps), "batch_size": None, "members_present": 0, "complete": False}
    if len(steps) != 1 or not checkpoints_ok:
        return out
    step = steps[0]
    body = cast(dict[str, Any], step.record["body"]) if step.record is not None else {}
    size, named = body.get("batch_size"), body.get("batch_root")
    out["batch_size"] = size if isinstance(size, int) and not isinstance(size, bool) else None
    if (
        step.error is not None
        or not step.canonical_ok
        or not step.inclusion
        or step.proof is None
        or out["batch_size"] is None
        or out["batch_size"] < 1
        or not isinstance(named, str)
    ):
        return out
    at, count = step.proof.index, out["batch_size"]
    held = {i.proof.index: i for i in items if i.proof is not None and i.error is None and i.canonical_ok and i.inclusion}
    members = [held.get(k) for k in range(max(at - count, 0), at)]
    out["members_present"] = sum(1 for m in members if m is not None)
    if out["members_present"] != count:
        return out
    leaves = [merkle.leaf_hash(hashlib.sha256(_producer_bytes(cast(_Item, m).record or {})).digest()) for m in members]
    out["complete"] = merkle.MerkleTree(leaves).root().hex() == named
    return out


def _step_time(body: dict[str, Any], name: str) -> int | None:
    value = body.get(name)
    if not isinstance(value, str):
        return None
    try:
        return to_ms(_to_the_millisecond(parse_rfc3339(value)))
    except (TimeError, TimelineError):
        return None


def _later(groups: _Groups, timing_verdict: dict[str, Any], integrity_ok: bool) -> dict[str, Any]:
    """What the records appended after the stamped checkpoint show about each forecast of the batch, held to what the tokens give.

    The earliest instant a call may become eligible is the attested time of the forecast's own timing result plus the uncertainty and the
    spread; the earliest origin is the first minute close strictly after it. A later step is read, not believed: an eligible time before
    the first, an origin before the second or not the minute close after the step's own eligible time, a step that is missing or that
    appears twice, all fail the forecast. Nothing here says whether a settlement is right."""
    out: dict[str, Any] = {"shown": False, "forecasts": {}, "failures": 0}
    if not groups.later or groups.consistency != "verified" or not integrity_ok:
        return out
    out["shown"] = True
    good = [i for i in groups.later if i.error is None and i.canonical_ok and i.inclusion and i.record is not None]
    steps: dict[str, list[dict[str, Any]]] = {}
    settles: dict[str, list[_Item]] = {}
    for item in good:
        record = cast(dict[str, Any], item.record)
        step_body = record.get("body")
        if record.get("type") == STEP_TYPE and isinstance(step_body, dict) and isinstance(step_body.get("forecast_id"), str):
            steps.setdefault(step_body["forecast_id"], []).append(step_body)
        elif record.get("type") == SETTLEMENT_TYPE and isinstance(step_body, dict) and isinstance(step_body.get("forecast_id"), str):
            settles.setdefault(step_body["forecast_id"], []).append(item)
    for item in groups.stamped:
        forecast = item.record
        if forecast is None or forecast.get("type") != FORECAST_TYPE or item.error is not None or not item.inclusion:
            continue
        forecast_id = forecast.get("id")
        if not isinstance(forecast_id, str):
            continue
        timed = timing_verdict["items"].get(item.stem, {})
        attested, offset = timed.get("attested_at"), timed.get("eligible_after_offset_ms")
        derived: int | None = None
        if timed.get("level") == timing.LEVEL_C3 and isinstance(attested, str) and isinstance(offset, int):
            derived = to_ms(parse_rfc3339(attested)) + offset
        mine = steps.get(forecast_id, [])
        committed = [s for s in mine if s.get("state") == "COMMITTED"]
        waiting = [s for s in mine if s.get("state") == "AWAITING_ORIGIN"]
        opened = [s for s in mine if s.get("state") == "OPEN"]
        ended = [s for s in mine if s.get("state") in ENDING_STATES]
        eligible = _step_time(waiting[0], "eligible_after") if len(waiting) == 1 else None
        awaiting = "absent" if not waiting else "conflicting" if len(waiting) > 1 else "malformed" if eligible is None else "unchecked"
        if awaiting == "unchecked" and derived is not None and eligible is not None:
            awaiting = "ok" if eligible >= derived else "early"
        origin_at = _step_time(opened[0], "origin") if len(opened) == 1 else None
        origin = "absent" if not opened else "conflicting" if len(opened) > 1 else "malformed" if origin_at is None else "unchecked"
        if origin == "unchecked" and derived is not None and origin_at is not None:
            if origin_at < (derived // MINUTE_MS + 1) * MINUTE_MS:
                origin = "early"
            elif eligible is None or awaiting != "ok" or origin_at != (eligible // MINUTE_MS + 1) * MINUTE_MS:
                origin = "wrong"
            else:
                origin = "ok"
        level = committed[0].get("level") if len(committed) == 1 and isinstance(committed[0].get("level"), str) else None
        ending = "conflicting" if len(ended) > 1 else cast(str, ended[0]["state"]) if ended else None
        out["forecasts"][forecast_id] = {
            "committed_level": level,
            "awaiting": awaiting,
            "eligible_after": None if eligible is None else _ms(from_ms(eligible)),
            "derived_eligible_after": None if derived is None else _ms(from_ms(derived)),
            "origin": origin,
            "origin_at": None if origin_at is None else _ms(from_ms(origin_at)),
            "derived_origin": None if derived is None else _ms(from_ms((derived // MINUTE_MS + 1) * MINUTE_MS)),
            "ended": ending,
        }
        settled, faulted = _settlement(forecast, settles.get(forecast_id, []), ended, origin, origin_at)
        out["forecasts"][forecast_id].update(settled)
        if awaiting not in ("ok", "unchecked") or origin in ("early", "wrong", "conflicting", "malformed") or faulted:
            out["failures"] += 1
    return out


def _price(value: object) -> Decimal | None:
    """A recorded price: positive decimal text as the settlement writes it, else none."""
    if not isinstance(value, str) or _PRICE.fullmatch(value) is None:
        return None
    number = Decimal(value)
    return number if number > 0 else None


def _barrier_consistent(body: dict[str, Any], origin_at: int | None, horizon: object) -> bool:
    """Whether a settled barrier record agrees with itself: one of the three outcomes, a reference price, no outcome price, and a touch
    minute that is a whole minute inside the call's window exactly when a level was touched. Which level was touched is decided by the
    candles of the window; the bundle does not hold them, so that part is for a reviewer who has the oracle's captures."""
    if body["outcome"] not in ("TARGET", "STOP", "TIMEOUT") or _price(body["p0"]) is None or body["outcome_price"] is not None:
        return False
    touch = body["touch_open_ms"]
    if body["outcome"] == "TIMEOUT":
        return touch is None
    if not isinstance(touch, int) or isinstance(touch, bool) or touch % 60_000 != 0:
        return False
    if origin_at is None or not isinstance(horizon, int) or isinstance(horizon, bool):
        return True  # the window is not known, so only the shape of the minute can be held
    return origin_at <= touch < origin_at + horizon


def _settlement(
    forecast: dict[str, Any], records: list[_Item], ended: list[dict[str, Any]], origin: str, origin_at: int | None
) -> tuple[dict[str, Any], bool]:
    """What the settlement record of one forecast shows, held to the forecast, to the step that cites it, to the directional rule and to the
    time the call's window ends. It does not say the prices are the market's: that is what the oracle's captures are for. That its reference
    to the forecast holds the forecast's digest is the integrity check of the references, which covers the later records too."""
    shown: dict[str, Any] = {"settlement": "none", "settlement_state": None, "settlement_checked": []}
    cited = ended[0].get("settlement_id") if len(ended) == 1 else None
    if not records:
        if isinstance(cited, str):
            shown["settlement"] = "absent"
        return shown, shown["settlement"] != "none"
    if len(records) > 1:
        shown["settlement"] = "conflicting"
        return shown, True
    item = records[0]
    record = cast(dict[str, Any], item.record)
    body = record.get("body")
    forecast_id = forecast.get("id")
    if (
        not isinstance(body, dict)
        or set(body) != _SETTLEMENT_MEMBERS
        or body["state"] not in _SETTLEMENT_STATES
        or not all(body[k] is None or isinstance(body[k], str) for k in ("outcome", "reason", "p0", "outcome_price"))
        or not isinstance(body["contract"], str)
    ):
        shown["settlement"] = "malformed"
        return shown, True
    shown["settlement_state"] = body["state"]
    checked = ["names_forecast"]
    refs = record.get("refs")
    named = isinstance(refs, list) and len(refs) == 1 and isinstance(refs[0], dict) and refs[0].get("id") == forecast_id
    wrong = not named
    if isinstance(cited, str):
        checked.append("names_step")
        wrong = wrong or cited != record.get("id")
    contract = forecast.get("body", {}).get("contract") if isinstance(forecast.get("body"), dict) else None
    tie = body["state"] == "VOID" and body["reason"] == "VOID_TIE"
    if contract == "D" and body["contract"] == "D" and (body["state"] == "SETTLED" or tie):
        checked.append("outcome_from_prices")
        first, last = _price(body["p0"]), _price(body["outcome_price"])
        if first is None or last is None:
            wrong = True
        elif body["state"] == "SETTLED":
            wrong = wrong or last == first or body["outcome"] != ("1" if last > first else "0")
        else:
            wrong = wrong or last != first
    elif contract == "B" and body["contract"] == "B" and body["state"] == "SETTLED":
        checked.append("barrier_touch_consistent")
        wrong = wrong or not _barrier_consistent(body, origin_at, forecast.get("body", {}).get("horizon_ms"))
    elif contract is not None and body["contract"] != contract:
        wrong = True
    horizon = forecast.get("body", {}).get("horizon_ms") if isinstance(forecast.get("body"), dict) else None
    late = False
    if (
        (body["state"] == "SETTLED" or tie)
        and origin == "ok"
        and origin_at is not None
        and isinstance(horizon, int)
        and not isinstance(horizon, bool)
    ):
        checked.append("after_window")
        assert item.committed_at is not None
        late = to_ms(item.committed_at) < origin_at + horizon
    shown["settlement_checked"] = checked
    shown["settlement"] = "mismatch" if wrong else "early" if late else "ok"
    return shown, shown["settlement"] in SETTLEMENT_FAULTS


def _is_committing(item: _Item) -> bool:
    body = item.record.get("body") if item.record is not None else None
    return (
        item.record is not None
        and item.record.get("type") == "cycle_state"
        and isinstance(body, dict)
        and body.get("state") == "COMMITTING"
    )


def _cp_summary(cp: Checkpoint | None) -> dict[str, Any] | None:
    if cp is None:
        return None
    return {"origin": cp.origin, "size": cp.size, "root_sha256": cp.root.hex()}


def _timing(
    directory: Path, items: list[_Item], root: TrustedRoot | None, authenticated: bool, integrity_ok: bool, stamped_text: bytes | None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "level": "none",
        "reason": None,
        "assurance_class": None,
        "basis": None,
        "tokens": [],
        "items": {},
        "subjects": [],
        "public_commit": _publication(directory),
    }
    if not authenticated or root is None:
        out["reason"] = "UNAUTHENTICATED: no trusted root, so no token is believed"
        return out
    if not integrity_ok:
        out["reason"] = "INTEGRITY_FAILED"
        return out
    rules = root.time_stamp
    out["basis"] = {
        "required_independent": rules.required_independent,
        "window_before_commit_s": rules.window_before_commit_s,
        "window_after_commit_s": rules.window_after_commit_s,
        "close_after_commit_s": rules.close_after_commit_s,
        "max_spread_s": rules.max_spread_s,
        "u_constant_s": rules.u_constant_s,
    }  # what the trusted root asked for: a loose root gives a loose pass, so the verdict says which rules it was judged by
    digest = hashlib.sha256(stamped_text).digest() if stamped_text is not None and items else b""
    evidence: list[timing.TokenEvidence] = []
    tsa_dir = directory / layout.TSA
    names = sorted((p.name for p in tsa_dir.iterdir() if p.name.endswith(layout.TOKEN_SUFFIX)), key=_u16) if tsa_dir.is_dir() else []
    policy = TokenPolicy(rules.pinned)
    for index, name in enumerate(names):
        label = name[: -len(layout.TOKEN_SUFFIX)]
        if index >= MAX_TOKENS:
            evidence.append(timing.TokenEvidence(label, None, "TOO_MANY"))
            continue
        try:
            blob = read_bundle_file(tsa_dir / name, MAX_TOKEN_BYTES)
        except FileTooLarge:
            evidence.append(timing.TokenEvidence(label, None, "SIZE"))  # the code verify_token gives a token over its limit
            continue
        except (OSError, NotRegularFile):
            evidence.append(timing.TokenEvidence(label, None, "UNREADABLE"))
            continue
        try:
            evidence.append(timing.TokenEvidence(label, verify_token(blob, digest, policy)))
        except TokenError as exc:
            evidence.append(timing.TokenEvidence(label, None, exc.code))
    subjects = [i for i in items if i.window_opens_at is not None] or list(items)
    out["subjects"] = [i.stem for i in subjects]
    levels: list[str] = []
    first: timing.TimingResult | None = None
    for item in items:
        assert item.committed_at is not None
        result = timing.assess(item.committed_at, item.window_opens_at, evidence, rules, created_at=item.created_at)
        first = first or result
        out["items"][item.stem] = _timing_item(result, rules)
        if item in subjects:
            levels.append(result.level)
    out["tokens"] = _token_rows(first)
    out["level"] = timing.LEVEL_C3 if levels and all(level == timing.LEVEL_C3 for level in levels) else timing.LEVEL_C1
    out["assurance_class"] = first.assurance_class if first else None
    return out


def _timing_item(result: timing.TimingResult, rules: TimeStampRules) -> dict[str, Any]:
    return {
        "level": result.level,
        "error": result.error,
        "detail": None if result.error is None else timing.order_detail(rules),
        "independent_operators": result.independent_operators,
        "chosen": sorted(result.chosen),
        "spread_ms": None if result.spread_s is None else round(result.spread_s * 1000),
        "attested_at": None if result.attested_at is None else _ms(result.attested_at),
        "eligible_after_offset_ms": None if result.eligible_after_offset_s is None else round(result.eligible_after_offset_s * 1000),
        "window_declared": result.window_declared,
        "tokens": {t.name: t.timely for t in result.tokens},
    }


def _token_rows(result: timing.TimingResult | None) -> list[dict[str, Any]]:
    if result is None:
        return []
    return [
        {
            "name": t.name,
            "valid": t.valid,
            "reason": t.reason,
            "gen_time": None if t.gen_time is None else _ms(t.gen_time),
            "operator": t.operator,
            "parent": t.parent,
            "root_sha256": t.root_fingerprint,
            "signer_sha256": t.signer_fingerprint,
        }
        for t in result.tokens
    ]


def _eligibility(items: list[_Item], root: TrustedRoot | None, state: str, verdict: dict[str, Any]) -> dict[str, Any]:
    modes: set[str] = set()
    for item in items:
        if item.record is not None and item.record.get("type") == EVIDENCE_TYPE:
            body = item.record.get("body")
            mode = body.get("data_mode") if isinstance(body, dict) else None
            modes.add(mode if isinstance(mode, str) else "missing")
    forecasts = sum(1 for i in items if i.record is not None and i.record.get("type") == FORECAST_TYPE and i.error is None and i.inclusion)
    reasons: list[str] = []
    if state != STATE_PINNED:
        reasons.append("ROOT_NOT_CURRENT")
    if not verdict["integrity"]["ok"]:
        reasons.append("INTEGRITY")
    if verdict["timing"]["level"] != timing.LEVEL_C3:
        reasons.append("LEVEL_BELOW_C3")
    if verdict["integrity"]["refs"]["unresolved"]:
        reasons.append("UNRESOLVED_REFERENCE")
    if not verdict["cycle"]["complete"]:
        reasons.append("COMPLETENESS_NOT_SHOWN")
    if forecasts == 0:
        reasons.append("NO_FORECAST")
    if verdict["cycle"]["later"]["failures"]:
        reasons.append("ORIGIN_BEFORE_ATTESTATION")
    required = root.scored_requires_data_mode if root is not None else None
    if not modes or modes != {required}:
        reasons.append("DATA_MODE")
    return {"data_modes": sorted(modes), "forecasts": forecasts, "scorable": not reasons, "reasons": reasons}
