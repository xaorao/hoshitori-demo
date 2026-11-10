"""The trusted root: what a verifier believes, supplied from outside every bundle, and how it is updated.

A bundle can be replaced as a whole together with the records it vouches for, so a verifier takes its trust anchors
from a document that ships with the verifier release and is updated only by a signed chain (ADR-025, ADR-028,
verification spec section 2). The document names the log origin and the key of each era of the log, the pinned
time-stamp roots with the operator registry, the timing rules of the commitment levels and the witness set.

The update rules follow The Update Framework's root rotation (specification section 5.3), tightened by the cases the
source study reproduced against the first client:

* a version is accepted only with valid signatures from a threshold of the previous version's root keys and a threshold
  of its own, each *key* counted once (a key id is the hash of the key, so one key cannot appear under two ids);
* thresholds are at least 1; an unknown ``format_version`` or an unknown member is a rejection, never ignored;
* a missing or mistyped member is a rejection with a code, never an exception;
* the version goes up by exactly one; the chain length is bounded; only the final root's expiry is checked, against a
  time fixed at the start of the update;
* several channels are consulted and two different documents for one version are a FORK;
* a verifier may refuse a final root below the highest version it has seen (rollback protection).

Both verifiers (Python and Node) implement the same client and are checked against each other on a generated corpus.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hoshitori import canonical
from hoshitori.note import NoteError, VerifierKey
from hoshitori.timeutil import TimeError, ensure_utc, parse_rfc3339
from hoshitori.tsa import PinnedRoot

TYPE: Final = "hoshitori-trusted-root"
FORMAT_VERSION: Final = 1
MAX_CHAIN: Final = 16
MAX_DOCUMENT_BYTES: Final = 262_144
MAX_SIGNATURES: Final = 64
MAX_LIST: Final = 256
HEX = frozenset("0123456789abcdef")

OK: Final = "OK"
EXPIRED: Final = "EXPIRED"
FORK: Final = "FORK"
MALFORMED: Final = "MALFORMED"
OLD_THRESHOLD: Final = "OLD_THRESHOLD"
NEW_THRESHOLD: Final = "NEW_THRESHOLD"
VERSION_STEP: Final = "VERSION_STEP"
CHAIN_BOUND: Final = "CHAIN_BOUND"
ROLLBACK: Final = "ROLLBACK"

ERA_ACCEPT: Final = "ACCEPT"
ERA_BEFORE: Final = "REJECT_BEFORE_ERA"
ERA_BEYOND: Final = "REJECT_BEYOND_CLOSING_SIZE"
ERA_UNKNOWN: Final = "UNKNOWN_ERA"


class RootError(ValueError):
    """A trusted-root document or envelope is not acceptable; ``code`` names why."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class LogEra:
    """One signing key of the log and the tree sizes it may sign (a retired key may not sign past its closing size)."""

    key: VerifierKey
    from_size: int
    through_size: int | None


@dataclass(frozen=True)
class OwnershipEvidence:
    kind: str
    ref: str
    subject_sha256: str


@dataclass(frozen=True)
class Operator:
    name: str
    parent: str
    evidence: OwnershipEvidence | None


@dataclass(frozen=True)
class TimeStampRules:
    pinned: tuple[PinnedRoot, ...]
    operators: Mapping[str, Operator]
    evidence_kinds_accepted: frozenset[str]
    required_independent: int
    window_before_commit_s: int
    window_after_commit_s: int
    close_after_commit_s: int
    max_spread_s: int
    u_constant_s: int


@dataclass(frozen=True)
class TrustedRoot:
    version: int
    expires: datetime
    root_keys: Mapping[str, bytes]  # key id (hex SHA-256 of the key) -> raw Ed25519 public key
    root_threshold: int
    origin: str
    eras: tuple[LogEra, ...]
    time_stamp: TimeStampRules
    witness_keys: tuple[VerifierKey, ...]
    witnesses_required: int
    scored_requires_data_mode: str
    payload: bytes

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    def era_check(self, key: VerifierKey, size: int) -> str:
        """Whether ``key`` may sign a checkpoint of ``size`` entries."""
        for era in self.eras:
            if era.key == key:
                if size < era.from_size:
                    return ERA_BEFORE
                if era.through_size is not None and size > era.through_size:
                    return ERA_BEYOND
                return ERA_ACCEPT
        return ERA_UNKNOWN

    def pinned_by_fingerprint(self) -> dict[str, PinnedRoot]:
        return {pin.sha256: pin for pin in self.time_stamp.pinned}


# ---------------------------------------------------------------------------------------------------------------------
# parsing


def _fail(message: str) -> RootError:
    return RootError(MALFORMED, message)


def _object(value: object, members: set[str], what: str) -> dict[str, canonical.JsonValue]:
    if not isinstance(value, dict):
        raise _fail(f"{what} must be an object")
    keys = set(value)
    if keys != members:
        missing, extra = sorted(members - keys), sorted(keys - members)
        raise _fail(f"{what}: missing {missing}, unexpected {extra}")
    return value


def _integer(value: object, what: str, *, minimum: int = 0, maximum: int = 2**53 - 1) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise _fail(f"{what} must be an integer from {minimum} to {maximum}")
    return value


def _string(value: object, what: str, *, limit: int = 512) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        raise _fail(f"{what} must be a non-empty string of at most {limit} characters")
    return value


def _hex(value: object, what: str, length: int = 64) -> str:
    if not isinstance(value, str) or len(value) != length or not set(value) <= HEX:
        raise _fail(f"{what} must be {length} lower-case hex characters")
    return value


def _list(value: object, what: str, *, minimum: int = 0) -> list[canonical.JsonValue]:
    if not isinstance(value, list) or not minimum <= len(value) <= MAX_LIST:
        raise _fail(f"{what} must be a list of {minimum} to {MAX_LIST} items")
    return value


def _b64(value: object, what: str, length: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise _fail(f"{what} must be base64 text")
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise _fail(f"{what} is not base64") from exc
    if base64.b64encode(raw).decode() != value or (length is not None and len(raw) != length):
        raise _fail(f"{what} is not canonical base64" + (f" of {length} bytes" if length else ""))
    return raw


def key_id(public_key: bytes) -> str:
    return hashlib.sha256(public_key).hexdigest()


def _vkey(value: object, what: str) -> VerifierKey:
    try:
        return VerifierKey.from_vkey(_string(value, what))
    except NoteError as exc:
        raise _fail(f"{what}: {exc}") from exc


def parse_root(payload: bytes) -> TrustedRoot:
    """Parse a root document. The bytes must already be in canonical form so that every implementation reads the same text."""
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise _fail("document too large")
    try:
        document = canonical.parse_record(payload)
        if canonical.dumps(document) != payload:
            raise _fail("document is not in canonical form")
    except canonical.CanonicalError as exc:
        raise _fail(f"document: {exc}") from exc
    top = _object(
        document,
        {
            "type",
            "format_version",
            "version",
            "expires",
            "root_keys",
            "root_threshold",
            "log",
            "time_stamp",
            "witnesses",
            "scored_requires_data_mode",
        },
        "document",
    )
    if top["type"] != TYPE:
        raise _fail("not a trusted-root document")
    if top["format_version"] != FORMAT_VERSION or type(top["format_version"]) is not int:
        raise _fail(f"format_version must be {FORMAT_VERSION}")
    version = _integer(top["version"], "version", minimum=1)
    try:
        expires = parse_rfc3339(_string(top["expires"], "expires"))
    except TimeError as exc:
        raise _fail(f"expires: {exc}") from exc

    keys: dict[str, bytes] = {}
    for item in _list(top["root_keys"], "root_keys", minimum=1):
        public = _b64(item, "root key", 32)
        identifier = key_id(public)
        if identifier in keys:
            raise _fail("a root key is listed twice")
        keys[identifier] = public
    threshold = _integer(top["root_threshold"], "root_threshold", minimum=1, maximum=len(keys))

    log = _object(top["log"], {"origin", "eras"}, "log")
    origin = _string(log["origin"], "log origin", limit=256)
    eras: list[LogEra] = []
    for index, entry in enumerate(_list(log["eras"], "log eras", minimum=1)):
        era = _object(entry, {"vkey", "from_size", "through_size"}, f"era {index}")
        key = _vkey(era["vkey"], f"era {index} vkey")
        if key.name != origin:
            raise _fail(f"era {index}: the key name must be the log origin")
        through = era["through_size"]
        eras.append(
            LogEra(
                key,
                _integer(era["from_size"], f"era {index} from_size"),
                None if through is None else _integer(through, f"era {index} through_size"),
            )
        )
    _check_eras(eras)

    stamp = _object(
        top["time_stamp"],
        {
            "pinned_roots",
            "operators",
            "evidence_kinds_accepted",
            "required_independent",
            "window_before_commit_s",
            "window_after_commit_s",
            "close_after_commit_s",
            "max_spread_s",
            "u_constant_s",
        },
        "time_stamp",
    )
    operators = _parse_operators(stamp["operators"])
    pinned: list[PinnedRoot] = []
    for index, entry in enumerate(_list(stamp["pinned_roots"], "pinned_roots", minimum=1)):
        pin = _object(entry, {"sha256", "operator", "der_b64"}, f"pinned root {index}")
        name = _string(pin["operator"], f"pinned root {index} operator")
        if name not in operators:
            raise _fail(f"pinned root {index}: operator {name!r} is not in the registry")
        der = None if pin["der_b64"] is None else _b64(pin["der_b64"], f"pinned root {index} der_b64")
        try:
            pinned.append(PinnedRoot(_hex(pin["sha256"], f"pinned root {index} sha256"), name, der))
        except ValueError as exc:
            raise _fail(f"pinned root {index}: {exc}") from exc
    if len({pin.sha256 for pin in pinned}) != len(pinned):
        raise _fail("a time-stamp root is pinned twice")
    kinds = frozenset(
        _string(kind, "evidence kind", limit=128) for kind in _list(stamp["evidence_kinds_accepted"], "evidence_kinds_accepted")
    )
    window_after = _integer(stamp["window_after_commit_s"], "window_after_commit_s", maximum=3600)
    rules = TimeStampRules(
        pinned=tuple(pinned),
        operators=operators,
        evidence_kinds_accepted=kinds,
        # C3 is two tokens from distinct operators (contract section 8), so a root cannot ask for fewer
        required_independent=_integer(stamp["required_independent"], "required_independent", minimum=2, maximum=16),
        window_before_commit_s=_integer(stamp["window_before_commit_s"], "window_before_commit_s", maximum=3600),
        window_after_commit_s=window_after,
        close_after_commit_s=_integer(stamp["close_after_commit_s"], "close_after_commit_s", minimum=window_after, maximum=7200),
        max_spread_s=_integer(stamp["max_spread_s"], "max_spread_s", maximum=3600),
        u_constant_s=_integer(stamp["u_constant_s"], "u_constant_s", minimum=1, maximum=3600),
    )

    witnesses = _object(top["witnesses"], {"required", "keys"}, "witnesses")
    witness_keys = tuple(_vkey(item, "witness key") for item in _list(witnesses["keys"], "witness keys"))
    required = _integer(witnesses["required"], "witnesses required", maximum=len(witness_keys))
    mode = _string(top["scored_requires_data_mode"], "scored_requires_data_mode", limit=32)
    return TrustedRoot(version, expires, keys, threshold, origin, tuple(eras), rules, witness_keys, required, mode, payload)


def _check_eras(eras: list[LogEra]) -> None:
    open_eras = [era for era in eras if era.through_size is None]
    if len(open_eras) != 1 or eras[-1].through_size is not None:
        raise _fail("exactly one era is open, and it is the last")
    if len({era.key for era in eras}) != len(eras):
        raise _fail("a log key appears in two eras")
    previous_through = -1
    for era in eras:
        if era.through_size is not None and era.through_size < era.from_size:
            raise _fail("an era closes before it opens")
        if era.from_size <= previous_through:
            raise _fail("eras overlap or are out of order")
        if era.through_size is not None:
            previous_through = era.through_size


def _parse_operators(value: object) -> dict[str, Operator]:
    if not isinstance(value, dict) or not value or len(value) > MAX_LIST:
        raise _fail("operators must be a non-empty object")
    operators: dict[str, Operator] = {}
    for name, entry in value.items():
        item = _object(entry, {"parent", "ownership_evidence"}, f"operator {name}")
        evidence = item["ownership_evidence"]
        parsed: OwnershipEvidence | None = None
        if evidence is not None:
            proof = _object(evidence, {"kind", "ref", "subject_sha256"}, f"operator {name} evidence")
            parsed = OwnershipEvidence(
                _string(proof["kind"], "evidence kind", limit=128),
                _string(proof["ref"], "evidence ref", limit=1024),
                _hex(proof["subject_sha256"], "evidence subject_sha256"),
            )
        operators[name] = Operator(name, _string(item["parent"], f"operator {name} parent", limit=256), parsed)
    return operators


# ---------------------------------------------------------------------------------------------------------------------
# envelopes and the update client


def sign_envelope(payload: bytes, signers: Sequence[Ed25519PrivateKey]) -> dict[str, object]:
    """An envelope for a root payload: the payload and one signature per key over the payload bytes."""
    signatures = []
    for signer in signers:
        public = signer.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        signatures.append({"keyid": key_id(public), "sig_b64": base64.b64encode(signer.sign(payload)).decode()})
    return {"payload_b64": base64.b64encode(payload).decode(), "signatures": signatures}


def _envelope(value: object) -> tuple[bytes, list[tuple[str, bytes]]]:
    if not isinstance(value, Mapping) or set(value) != {"payload_b64", "signatures"}:
        raise _fail("an envelope has a payload and signatures")
    payload = _b64(value["payload_b64"], "payload")
    raw = value["signatures"]
    if not isinstance(raw, list) or len(raw) > MAX_SIGNATURES:
        raise _fail("signatures must be a short list")
    signatures: list[tuple[str, bytes]] = []
    for item in raw:
        if not isinstance(item, Mapping) or set(item) != {"keyid", "sig_b64"}:
            raise _fail("a signature has a key id and bytes")
        signatures.append((_hex(item["keyid"], "key id"), _b64(item["sig_b64"], "signature", 64)))
    return payload, signatures


def count_valid(root: TrustedRoot, signatures: list[tuple[str, bytes]], payload: bytes) -> int:
    """Distinct root keys of ``root`` whose signature over ``payload`` verifies. A key counts once however often it signs (a set)."""
    counted: set[str] = set()
    for identifier, signature in signatures:
        public = root.root_keys.get(identifier)
        if public is None:
            continue
        try:
            Ed25519PublicKey.from_public_bytes(public).verify(signature, payload)
        except InvalidSignature:
            continue
        counted.add(identifier)
    return len(counted)


@dataclass(frozen=True)
class UpdateResult:
    status: str  # CURRENT, EXPIRED, FORK or REJECTED
    code: str
    root: TrustedRoot  # the last root whose chain verified; usable only when ``status`` is CURRENT
    single_channel: bool  # the newest accepted step was served by one of several channels

    @property
    def usable(self) -> bool:
        return self.status == "CURRENT"


def update(
    initial: TrustedRoot,
    channels: Sequence[Mapping[int, object]],
    now: datetime,
    *,
    max_chain: int = MAX_CHAIN,
    floor_version: int = 0,
) -> UpdateResult:
    """Follow the signed chain of roots from the one shipped with the verifier.

    ``channels`` are the independent places roots were fetched from, each a map from version to envelope. ``now`` is the
    fixed start time of the update, with a zone; the final root's expiry is judged against it and nothing else.
    """
    started = ensure_utc(now)
    current = initial
    steps = 0
    single = False

    def result(status: str, code: str) -> UpdateResult:
        return UpdateResult(status, code, current, single)

    while True:
        wanted = current.version + 1
        candidates = [channel[wanted] for channel in channels if wanted in channel]
        if not candidates:
            break
        if len(candidates) < 2:  # a step needs two independent channels that agree (contract 8): one is not followed, and is shown
            single = True
            break
        if steps >= max_chain:
            return result("REJECTED", CHAIN_BOUND)
        if len({repr(candidate.get("payload_b64")) if isinstance(candidate, Mapping) else "?" for candidate in candidates}) > 1:
            return result("FORK", FORK)  # two different documents (or a document and garbage) for one version
        try:
            parsed = [_envelope(candidate) for candidate in candidates]
        except RootError:
            return result("REJECTED", MALFORMED)
        payload = parsed[0][0]
        signatures = [signature for _, group in parsed for signature in group]  # signatures over one payload add up across channels
        try:
            proposed = parse_root(payload)
        except RootError:
            return result("REJECTED", MALFORMED)
        if count_valid(current, signatures, payload) < current.root_threshold:
            return result("REJECTED", OLD_THRESHOLD)
        if count_valid(proposed, signatures, payload) < proposed.root_threshold:
            return result("REJECTED", NEW_THRESHOLD)
        if proposed.version != wanted:
            return result("REJECTED", VERSION_STEP)
        current = proposed
        steps += 1
    if current.version < floor_version:
        return result("REJECTED", ROLLBACK)
    if current.expires <= started:
        return result("EXPIRED", EXPIRED)
    return result("CURRENT", OK)
