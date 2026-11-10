"""Builders for trusted-root documents, envelopes and update chains used by the root scenarios and the cross-language corpus."""

from __future__ import annotations

import base64
import copy
from dataclasses import dataclass, field

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from hoshitori import canonical, trusted_root
from hoshitori.note import Signer
from tests import tsa_pki as P

LOG_ORIGIN = "hoshitori.example/log"
EXPIRES = "2027-10-29T00:00:00Z"
NOW = P.BASE  # a time inside the validity of every document built here


def new_signer(seed: int) -> Ed25519PrivateKey:
    return Ed25519PrivateKey.from_private_bytes(seed.to_bytes(32, "big"))


def public_bytes(key: Ed25519PrivateKey) -> bytes:
    return key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)


def log_signer(seed: int) -> Signer:
    return Signer.from_seed(LOG_ORIGIN, seed.to_bytes(32, "big"))


@dataclass
class RootSpec:
    """The members of a root document as plain data; ``payload`` canonicalises it. Tests edit the dict, then call ``payload``."""

    document: dict[str, object] = field(default_factory=dict)

    def payload(self) -> bytes:
        return canonical.dumps(copy.deepcopy(self.document))  # type: ignore[arg-type]


def make_document(
    version: int = 1,
    *,
    root_keys: list[Ed25519PrivateKey] | None = None,
    threshold: int = 1,
    expires: str = EXPIRES,
    eras: list[dict[str, object]] | None = None,
    pinned: list[tuple[P.Authority, str]] | None = None,
    witnesses: tuple[int, list[str]] = (0, []),
) -> dict[str, object]:
    keys = root_keys if root_keys is not None else [new_signer(1)]
    chain = P.Chain.build("Pinned")
    pins = pinned or [(chain.root, "operator-a")]
    operators = {
        name: {
            "parent": f"Organisation {name}",
            "ownership_evidence": {
                "kind": "ccadb_ca_owner_of_record",
                "ref": f"record for {name}",
                "subject_sha256": authority.fingerprint,
            },
        }
        for authority, name in pins
    }
    return {
        "type": trusted_root.TYPE,
        "format_version": 1,
        "version": version,
        "expires": expires,
        "root_keys": [base64.b64encode(public_bytes(k)).decode() for k in keys],
        "root_threshold": threshold,
        "log": {
            "origin": LOG_ORIGIN,
            "eras": eras or [{"vkey": log_signer(7).verifier.vkey(), "from_size": 0, "through_size": None}],
        },
        "time_stamp": {
            "pinned_roots": [{"sha256": a.fingerprint, "operator": n, "der_b64": base64.b64encode(a.der).decode()} for a, n in pins],
            "operators": operators,
            "evidence_kinds_accepted": ["ccadb_ca_owner_of_record", "operator_published_root"],
            "required_independent": 2,
            "window_before_commit_s": 1,
            "window_after_commit_s": 60,
            "close_after_commit_s": 90,
            "max_spread_s": 5,
            "u_constant_s": 5,
        },
        "witnesses": {"required": witnesses[0], "keys": witnesses[1]},
        "scored_requires_data_mode": "live",
    }


def initial(version: int = 1, **kwargs: object) -> trusted_root.TrustedRoot:
    return trusted_root.parse_root(canonical.dumps(make_document(version, **kwargs)))  # type: ignore[arg-type]


def envelope(document: dict[str, object], signers: list[Ed25519PrivateKey]) -> dict[str, object]:
    return trusted_root.sign_envelope(canonical.dumps(document), signers)  # type: ignore[arg-type]
