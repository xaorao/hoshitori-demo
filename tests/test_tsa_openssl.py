"""Differential test against ``openssl ts -verify``: the product must never accept a token OpenSSL rejects.

OpenSSL is an independent implementation of RFC 3161 checking that was used to expose three defects in the first
verifiers. The product is allowed to be stricter than OpenSSL (it also requires, for example, a SHA-2 CMS digest and a
single occurrence of each signed attribute); it is not allowed to be looser. The one case where only the product accepts
is a limit of OpenSSL: its time-stamp code reads signers through the PKCS#7 interface, which cannot parse the
subject-key-identifier form of the signer identifier that RFC 3161 permits.

The test is skipped where an OpenSSL 3 binary is not found (macOS ships LibreSSL as ``/usr/bin/openssl``).
"""

from __future__ import annotations

import base64
import hashlib
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

from hoshitori import tsa
from tests import tsa_pki as P
from tests.tsa_checks import DIGEST, VECTORS, standard


def find_openssl() -> str | None:
    for candidate in (
        shutil.which("openssl"),
        "/opt/homebrew/bin/openssl",
        "/opt/homebrew/opt/openssl@3/bin/openssl",
        "/usr/local/bin/openssl",
    ):
        if candidate and Path(candidate).exists():
            out = subprocess.run([candidate, "version"], capture_output=True, text=True, check=False).stdout
            if out.startswith("OpenSSL 3"):
                return candidate
    return None


OPENSSL = find_openssl()
pytestmark = pytest.mark.skipif(OPENSSL is None, reason="OpenSSL 3 is not installed")

# Cases where the product accepts and OpenSSL cannot (a limit of OpenSSL, see the module docstring).
OPENSSL_LIMITS = {"subject-key-identifier signer id"}


def openssl_accepts(tmp: Path, token: bytes, root_der: bytes, digest: bytes, at: int, *, bare: bool = False) -> bool:
    from cryptography import x509

    (tmp / "token").write_bytes(token)
    (tmp / "root.pem").write_bytes(x509.load_der_x509_certificate(root_der).public_bytes(Encoding.PEM))
    command = [
        OPENSSL or "openssl",
        "ts",
        "-verify",
        "-in",
        str(tmp / "token"),
        "-digest",
        digest.hex(),
        "-CAfile",
        str(tmp / "root.pem"),
        "-partial_chain",
        "-attime",
        str(at),
    ]
    if bare:
        command.append("-token_in")
    return subprocess.run(command, capture_output=True, check=False).returncode == 0


def built_cases() -> list[tuple[str, P.Chain, bytes, bool]]:
    base = standard("A")
    root = base.root
    cases: list[tuple[str, P.Chain, bytes, bool]] = []

    def add(label: str, chain: P.Chain, certificates: list[P.Authority] | None = None, **options: object) -> None:
        opts = replace(P.TokenOptions(nonce=None), **options)  # type: ignore[arg-type]
        if certificates is not None:
            opts = replace(opts, certificates=certificates)
        cases.append((label, chain, P.build_token(DIGEST, chain, opts), opts.bare))

    add("honest", base)
    add("bare token", base, bare=True)
    add("ESS v1", base, include_ess="v1")
    add("subject-key-identifier signer id", base, sid="subject_key_identifier")
    add("no ESS attribute", base, include_ess=None)
    add("ESS names another certificate", base, ess_cert=base.intermediate)
    add("signature changed", base, tamper_signature=True)
    add("message digest changed", base, tamper_message_digest=True)
    add("imprint of another digest", base, imprint=hashlib.sha256(b"other").digest())
    add("SHA-1 CMS digest", base, digest_algorithm="sha1", signature_algorithm="sha1_ecdsa")
    add("two signers", base, second_signer=True)
    add("time without zone", base, gen_time_without_zone=True)
    add("message digest attribute twice", base, duplicate_message_digest=True)
    add("content type attribute names another type", base, content_type_attribute="data")
    leaf = P.make_cert("TLS leaf", base.intermediate, ca=False, digital_signature=True, eku=[ExtendedKeyUsageOID.SERVER_AUTH])
    under_leaf = P.make_tsa(leaf, "TSA under a TLS leaf")
    add("TSA under a TLS leaf", P.Chain(root, leaf, under_leaf), [under_leaf, leaf, base.intermediate, root])
    for label, options in (
        ("issuer without certificate-signing usage", {"key_cert_sign": False, "digital_signature": True}),
        ("issuer without basic constraints", {"ca": None, "key_cert_sign": None}),
        ("issuer with an unknown critical extension", {"unknown_critical": True}),
        ("issuer expired", {"not_after": P.BASE - timedelta(days=1)}),
    ):
        issuer = P.make_intermediate(root, "Issuer", **options)
        add(label, P.Chain(root, issuer, P.make_tsa(issuer)))
    first = P.make_intermediate(root, "CA a", path_length=0)
    second = P.make_intermediate(first, "CA b")
    below = P.make_tsa(second, "TSA below b")
    add("path length exceeded", P.Chain(root, second, below), [below, second, first, root])
    for label, options in (
        ("signer without extended key usage", {"eku": None}),
        ("signer extended key usage not critical", {"eku_critical": False}),
        ("signer with extra usages", {"eku": [ExtendedKeyUsageOID.TIME_STAMPING, ExtendedKeyUsageOID.SERVER_AUTH]}),
        ("signer is a CA", {"ca": True, "key_cert_sign": True, "digital_signature": True}),
        ("signer key usage forbids signatures", {"digital_signature": False, "key_cert_sign": False}),
        ("signer with an unknown critical extension", {"unknown_critical": True}),
        ("signer expired", {"not_after": P.BASE - timedelta(days=1)}),
        ("signer signed with SHA-224", {"signature_hash": hashes.SHA224()}),
    ):
        signer = P.make_tsa(base.intermediate, "Signer", **options)
        add(label, P.Chain(root, base.intermediate, signer), [signer, base.intermediate, root])
    return cases


CASES = built_cases() if OPENSSL else []


@pytest.mark.parametrize("label,chain,token,bare", CASES, ids=[c[0] for c in CASES])
def test_product_is_never_looser_than_openssl(tmp_path: Path, label: str, chain: P.Chain, token: bytes, bare: bool) -> None:
    policy = tsa.TokenPolicy((tsa.PinnedRoot.from_der(chain.root.der, "x"),))
    try:
        tsa.verify_token(token, DIGEST, policy)
    except tsa.TokenError:
        return  # rejecting is always safe
    theirs = openssl_accepts(tmp_path, token, chain.root.der, DIGEST, int(P.BASE.timestamp()), bare=bare)
    if label in OPENSSL_LIMITS:
        assert not theirs, f"{label} is listed as an OpenSSL limit but OpenSSL accepts it now: remove it from the list"
    else:
        assert theirs, f"the product accepted a token that OpenSSL rejects: {label}"


def test_the_honest_cases_are_accepted_by_both() -> None:
    accepted = {label for label, chain, token, _ in CASES if _product_accepts(chain, token)}
    assert {"honest", "bare token", "ESS v1", "subject-key-identifier signer id"} <= accepted


def _product_accepts(chain: P.Chain, token: bytes) -> bool:
    try:
        tsa.verify_token(token, DIGEST, tsa.TokenPolicy((tsa.PinnedRoot.from_der(chain.root.der, "x"),)))
    except tsa.TokenError:
        return False
    return True


def test_live_tokens_the_product_accepts_are_accepted_by_openssl(tmp_path: Path) -> None:
    entries = json.loads((VECTORS / "tsa_live.json").read_text())["entries"]
    checked = 0
    for entry in entries:
        token = base64.b64decode(entry["token_base64"])
        digest = bytes.fromhex(entry["digest_sha256_hex"])
        root_der = base64.b64decode(entry["pinned_root_der_base64"])
        try:
            result = tsa.verify_token(
                token, digest, tsa.TokenPolicy((tsa.PinnedRoot.from_der(root_der, entry["authority"]),)), nonce=entry["nonce"]
            )
        except tsa.TokenError:
            continue
        assert openssl_accepts(tmp_path, token, root_der, digest, int(result.gen_time.timestamp())), entry["authority"]
        checked += 1
    assert checked >= 7
