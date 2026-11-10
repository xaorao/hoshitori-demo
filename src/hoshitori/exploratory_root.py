"""A trusted-root document for an exploratory run: the real time-stamp roots, found in what the authorities send, checked
against evidence.

This is for running the league against the real world before a release root exists. Each named authority is sent one request
for a random digest (nothing of ours leaves the machine). The root it pins is the certificate in the answer whose fingerprint
is the one the operator registry's ownership evidence names, and that evidence was collected apart from this exchange; an
authority whose answer does not contain that certificate, or that has no evidence, is refused. (A root need not be self-issued:
DigiCert's answer ends at a cross-signed root.) The root-signing key is thrown
away: the document can never be updated, and a run under it is exploratory, not a scored period.
"""

from __future__ import annotations

import base64
import hashlib
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from asn1crypto import tsp

from hoshitori import canonical, trusted_root
from hoshitori.timeutil import format_rfc3339_ms
from hoshitori.tsa import build_request
from hoshitori.tsa_gateway import status_of

Post = Callable[[str, bytes], bytes]  # (url, DER request) -> DER answer
KINDS: Final = ("ccadb_ca_owner_of_record", "operator_published_root")


class ExploratoryRootError(ValueError):
    """A root that cannot be made, with the reason."""


def _evidence(registry: Mapping[str, Any], name: str) -> dict[str, Any]:
    operators = registry.get("operators")
    entry = operators.get(name) if isinstance(operators, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("ownership_evidence"), dict):
        raise ExploratoryRootError(f"{name} has no ownership evidence in the registry")
    evidence = entry["ownership_evidence"]
    if evidence.get("kind") not in KINDS:
        raise ExploratoryRootError(f"the evidence for {name} is of a kind that is not accepted")
    return {"parent": str(entry["parent"]), "ownership_evidence": {k: evidence[k] for k in ("kind", "ref", "subject_sha256")}}


def _named_root(answer: bytes, name: str, fingerprint: str) -> bytes:
    """The certificate of the answer whose SHA-256 is ``fingerprint``."""
    try:
        status = status_of(answer)["status"].native
    except Exception as exc:
        raise ExploratoryRootError(f"{name} did not give a readable time-stamp answer: {type(exc).__name__}") from exc
    if status not in ("granted", "granted_with_mods"):
        raise ExploratoryRootError(f"{name} refused the request: {status}")
    try:
        response = tsp.TimeStampResp.load(answer)
        certificates = [bytes(c.chosen.dump()) for c in response["time_stamp_token"]["content"]["certificates"] if c.name == "certificate"]
    except Exception as exc:
        raise ExploratoryRootError(f"{name} did not give a readable time-stamp answer: {type(exc).__name__}") from exc
    for der in certificates:
        if hashlib.sha256(der).hexdigest() == fingerprint:
            return der
    raise ExploratoryRootError(f"the answer of {name} does not contain the root its ownership evidence names")


def build(
    *,
    origin: str,
    log_vkey: str,
    authorities: Sequence[tuple[str, str]],
    registry: Mapping[str, Any],
    post: Post,
    root_public_key: bytes,
    expires: datetime,
    required_independent: int = 2,
) -> dict[str, object]:
    """The document, for the named authorities ``(operator name, url)``, or an error."""
    if len(authorities) < required_independent or len({name for name, _ in authorities}) != len(authorities):
        raise ExploratoryRootError(f"{required_independent} different authorities are needed to reach C3")
    pinned: list[dict[str, str]] = []
    operators: dict[str, object] = {}
    for name, url in authorities:
        evidence = _evidence(registry, name)
        request, _ = build_request(os.urandom(32))
        fingerprint = evidence["ownership_evidence"]["subject_sha256"]
        der = _named_root(post(url, request), name, fingerprint)
        pinned.append({"sha256": fingerprint, "operator": name, "der_b64": base64.b64encode(der).decode()})
        operators[name] = evidence
    return {
        "type": trusted_root.TYPE,
        "format_version": trusted_root.FORMAT_VERSION,
        "version": 1,
        "expires": format_rfc3339_ms(expires),
        "root_keys": [base64.b64encode(root_public_key).decode()],
        "root_threshold": 1,
        "log": {"origin": origin, "eras": [{"vkey": log_vkey, "from_size": 0, "through_size": None}]},
        "time_stamp": {
            "pinned_roots": pinned,
            "operators": operators,
            "evidence_kinds_accepted": list(KINDS),
            "required_independent": required_independent,
            "window_before_commit_s": 1,
            "window_after_commit_s": 60,
            "close_after_commit_s": 90,
            "max_spread_s": 5,
            "u_constant_s": 5,
        },
        "witnesses": {"required": 0, "keys": []},
        "scored_requires_data_mode": "live",
    }


def write(document: dict[str, object]) -> bytes:
    """The canonical bytes of the document, checked by the root parser before they are returned."""
    payload = canonical.dumps(document)  # type: ignore[arg-type]
    try:
        trusted_root.parse_root(payload)
    except trusted_root.RootError as exc:
        raise ExploratoryRootError(f"the root does not parse: {exc}") from exc
    return payload
