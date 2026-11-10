"""Scenarios for RFC 3161 token verification (contract section 8).

Each scenario takes the module under test and returns a list of problems, so the same scenarios run against the real
module and against deliberately broken copies (``test_tsa_mutants``). Tokens come from ``tsa_pki``, which can set every
field a rule looks at; the live responses of nine authorities are in ``vectors/tsa_live.json``.
"""

from __future__ import annotations

import base64
import hashlib
import json
import types
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from functools import cache
from pathlib import Path

from asn1crypto import tsp
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID

from tests import der_tools as D
from tests import tsa_pki as P

VECTORS = Path(__file__).parent / "vectors"
DIGEST = hashlib.sha256(b"the committed checkpoint").digest()
NONCE = 777


# The rule each rejection must come from (a rejection for an incidental reason would hide an untested rule).
WHY = {
    "digest of another checkpoint": "message imprint",
    "digest of the wrong length": "32 bytes",
    "imprint of another digest inside the token": "message imprint",
    "SHA-1 imprint algorithm": "message imprint",
    "nonce differs from the request": "nonce",
    "nonce requested but the token has none": "nonce",
    "authority refused": "answered rejection",
    "critical extension in TSTInfo": "critical extension",
    "two signers": "exactly one signer",
    "generation time without a zone designator": "UTC",
    "message digest attribute twice": "more than once",
    "content type attribute names another type": "content type attribute",
    "message digest attribute does not match TSTInfo": "message digest attribute",
    "signature bytes changed": "signature does not verify",
    "SHA-1 CMS digest": "not allowed",
    "signature algorithm names another hash": "different hash",
    "RSA signer with a SHA-1 digest and the plain RSA identifier": "not allowed",
    "RSA signer with an identifier for an unlisted algorithm": "not allowed",
    "EC signer with an RSA algorithm identifier": "not allowed for an EC key",
    "RSA signer with an ECDSA algorithm identifier": "not allowed for an RSA key",
    "digest algorithm not listed": "not listed",
    "ESS v1 names another certificate": "ESS",
    "no ESS signing-certificate attribute": "ESS",
    "ESS names another certificate": "ESS",
    "no content-type attribute": "content type",
    "no message-digest attribute": "lack the message digest",
    "signer certificate not in the token": "signer identifier",
    "two certificates match the signer identifier": "signer identifier",
    "the signer certificate is damaged": "signer identifier",
    "empty input": "unparsable",
    "garbage": "unparsable",
    "truncated": "unparsable",
    "trailing data": "unparsable",
    "oversized": "too large",
    "no pinned root configured": "no pinned root",
    "CMS version 4": "CMS versions",
    "TSA issued by a non-CA leaf": "not a CA certificate",
    "issuer without certificate-signing key usage": "key usage",
    "issuer without basic constraints": "not a CA certificate",
    "issuer with an unknown critical extension": "critical extension",
    "issuer expired at the time stamp time": "not valid at the time stamp time",
    "issuer not yet valid": "not valid at the time stamp time",
    "issuer signed with SHA-1": "signature algorithm",
    "issuer signed with SHA-224": "signature algorithm",
    "issuer signed with RSA-PSS": "signature algorithm",
    "issuer with a 1024-bit RSA key": "RSA key shorter",
    "signer with a 1024-bit RSA key": "RSA key shorter",
    "issuer on an unlisted curve": "not allowed",
    "issuer with an Ed25519 key": "signature algorithm",
    "path length constraint exceeded": "path length",
    "path too long": "too long",
    "signer without extended key usage": "no extended key usage",
    "signer's extended key usage not critical": "critical time stamping",
    "signer with extra extended key usages": "critical time stamping",
    "signer with only another usage": "critical time stamping",
    "signer is a CA": "is a CA certificate",
    "signer key usage forbids signatures": "key usage",
    "signer with an unknown critical extension": "critical extension",
    "signer expired at the time stamp time": "not valid at the time stamp time",
    "signer not yet valid": "not valid at the time stamp time",
    "signer certificate signed with SHA-224": "signature algorithm",
    "pinned root expired at the time stamp time": "not valid at the time stamp time",
    "root with the pinned root's name but another key": "does not verify",
    "root pinned by fingerprint, omitted from the token": "no valid certificate path",
    "content type is not signed data": "signed data",
    "encapsulated content is not TSTInfo": "not TSTInfo",
    "TSTInfo version 2": "version",
    "a damaged accuracy field": "unparsable",
    "a length written with a padded long form": "not minimal",
    "a length written with a leading zero": "bad length",
    "an indefinite length": "indefinite",
    "a multi-byte tag": "multi-byte",
    "an extra element after TSTInfo": "expected exactly one",
    "structure nested deeper than allowed": "too deep",
    "the signer certificate lists an extension twice": "signer identifier",
    "the signer's RSA exponent is not minimal DER": "could not be read",
    "the signer has an Ed25519 key": "key type",
    "an issuer's name does not chain to its issuer": "no valid certificate path",
}

# The family of rule each rejection belongs to (the verifiers report the same code).
CODE_OF = {
    "digest of another checkpoint": "IMPRINT",
    "digest of the wrong length": "DIGEST",
    "imprint of another digest inside the token": "IMPRINT",
    "SHA-1 imprint algorithm": "IMPRINT",
    "nonce differs from the request": "NONCE",
    "nonce requested but the token has none": "NONCE",
    "authority refused": "STATUS",
    "critical extension in TSTInfo": "EXTENSION",
    "two signers": "SIGNER_COUNT",
    "CMS version 4": "VERSION",
    "generation time without a zone designator": "TIME",
    "message digest attribute twice": "ATTRIBUTES",
    "content type attribute names another type": "ATTRIBUTES",
    "message digest attribute does not match TSTInfo": "ATTRIBUTES",
    "signature bytes changed": "SIGNATURE",
    "SHA-1 CMS digest": "ALGORITHM",
    "EC signer with an RSA algorithm identifier": "ALGORITHM",
    "RSA signer with an ECDSA algorithm identifier": "ALGORITHM",
    "RSA signer with a SHA-1 digest and the plain RSA identifier": "ALGORITHM",
    "RSA signer with an identifier for an unlisted algorithm": "ALGORITHM",
    "signature algorithm names another hash": "ALGORITHM",
    "digest algorithm not listed": "ALGORITHM",
    "ESS v1 names another certificate": "ESS",
    "no ESS signing-certificate attribute": "ESS",
    "ESS names another certificate": "ESS",
    "no content-type attribute": "ATTRIBUTES",
    "no message-digest attribute": "ATTRIBUTES",
    "signer certificate not in the token": "SIGNER_ID",
    "two certificates match the signer identifier": "SIGNER_ID",
    "the signer certificate is damaged": "SIGNER_ID",
    "empty input": "PARSE",
    "garbage": "PARSE",
    "truncated": "PARSE",
    "trailing data": "PARSE",
    "oversized": "SIZE",
    "no pinned root configured": "NO_ROOT",
    "TSA issued by a non-CA leaf": "PATH",
    "issuer without certificate-signing key usage": "PATH",
    "issuer without basic constraints": "PATH",
    "issuer with an unknown critical extension": "PATH",
    "issuer expired at the time stamp time": "PATH",
    "issuer not yet valid": "PATH",
    "issuer signed with SHA-1": "PATH",
    "issuer signed with SHA-224": "PATH",
    "issuer with a 1024-bit RSA key": "PATH",
    "signer with a 1024-bit RSA key": "CERT",
    "issuer on an unlisted curve": "PATH",
    "issuer with an Ed25519 key": "PATH",
    "path length constraint exceeded": "PATH",
    "path too long": "PATH",
    "signer without extended key usage": "CERT",
    "signer's extended key usage not critical": "CERT",
    "signer with extra extended key usages": "CERT",
    "signer with only another usage": "CERT",
    "signer is a CA": "CERT",
    "signer key usage forbids signatures": "CERT",
    "signer with an unknown critical extension": "CERT",
    "signer expired at the time stamp time": "CERT",
    "signer not yet valid": "CERT",
    "signer certificate signed with SHA-224": "PATH",
    "issuer signed with RSA-PSS": "PATH",
    "pinned root expired at the time stamp time": "PATH",
    "root pinned by fingerprint, omitted from the token": "PATH",
    "root not pinned": "PATH",
    "only an unrelated issuer pinned": "PATH",
    "root with the pinned root's name but another key": "PATH",
    "self-signed signer that is not pinned": "PATH",
    "content type is not signed data": "CONTENT",
    "encapsulated content is not TSTInfo": "CONTENT",
    "TSTInfo version 2": "VERSION",
    "a damaged accuracy field": "PARSE",
    "a length written with a padded long form": "PARSE",
    "a length written with a leading zero": "PARSE",
    "an indefinite length": "PARSE",
    "a multi-byte tag": "PARSE",
    "an extra element after TSTInfo": "PARSE",
    "structure nested deeper than allowed": "PARSE",
    "the signer certificate lists an extension twice": "SIGNER_ID",
    "the signer's RSA exponent is not minimal DER": "ALGORITHM",
    "the signer has an Ed25519 key": "ALGORITHM",
    "an issuer's name does not chain to its issuer": "PATH",
}


@cache
def standard(name: str = "A") -> P.Chain:
    return P.Chain.build(name)


def policy(mod: types.ModuleType, *pins: tuple[P.Authority, str]) -> object:
    return mod.TokenPolicy(tuple(mod.PinnedRoot.from_der(a.der, op) for a, op in pins))


def accepted(
    bad: list[str], mod: types.ModuleType, label: str, token: bytes, pol: object, *, digest: bytes = DIGEST, nonce: int | None = None
):  # type: ignore[no-untyped-def]
    try:
        return mod.verify_token(token, digest, pol, nonce=nonce)
    except mod.TokenError as exc:
        bad.append(f"{label}: rejected ({exc})")
    except Exception as exc:
        bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
    return None


def rejected(
    bad: list[str], mod: types.ModuleType, label: str, token: bytes, pol: object, *, digest: bytes = DIGEST, nonce: int | None = None
) -> None:
    try:
        mod.verify_token(token, digest, pol, nonce=nonce)
    except mod.TokenError as exc:
        expected = WHY.get(label)
        if expected is not None and expected not in str(exc):
            bad.append(f"{label}: rejected for another reason ({exc})")
        if label in CODE_OF and exc.code != CODE_OF[label]:
            bad.append(f"{label}: code {exc.code}, expected {CODE_OF[label]}")
        if exc.code not in mod.CODES:
            bad.append(f"{label}: code {exc.code} is not in the closed list")
        return
    except Exception as exc:
        bad.append(f"{label}: raised {type(exc).__name__} instead of TokenError: {exc}")
        return
    bad.append(f"{label}: accepted")


def token(chain: P.Chain, **options: object) -> bytes:
    return P.build_token(DIGEST, chain, replace(P.TokenOptions(nonce=NONCE), **options))  # type: ignore[arg-type]


def custom_chain(root: P.Authority, issuers: list[P.Authority], tsa: P.Authority) -> P.Chain:
    return P.Chain(root, issuers[0] if issuers else root, tsa)


# ---------------------------------------------------------------------------------------------------------------------
def accepts_valid_forms(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    chain = standard("A")
    pol = policy(mod, (chain.root, "operator-a"))
    result = accepted(bad, mod, "plain response", token(chain), pol, nonce=NONCE)
    if result is not None:
        if result.gen_time != P.BASE:
            bad.append(f"gen_time {result.gen_time}")
        if result.operator != "operator-a" or result.root_fingerprint != chain.root.fingerprint:
            bad.append("operator or root fingerprint wrong")
        if result.signer_fingerprint != chain.tsa.fingerprint:
            bad.append("signer fingerprint wrong")
        if result.accuracy != {"seconds": 1, "millis": None, "micros": None} or result.policy_oid != "1.2.3.4.1" or result.serial != 1:
            bad.append(f"accuracy/policy/serial wrong: {result}")
        if result.path_fingerprints != tuple(c.fingerprint for c in chain.certificates()):
            bad.append("path fingerprints wrong")
        if result.nonce != NONCE:
            bad.append("nonce not reported")
    accepted(bad, mod, "no nonce supplied by the caller", token(chain), pol)
    accepted(bad, mod, "token without a nonce, caller supplies none", token(chain, nonce=None), pol)
    accepted(bad, mod, "bare token", token(chain, bare=True), pol, nonce=NONCE)
    accepted(bad, mod, "subject-key-identifier signer id", token(chain, sid="subject_key_identifier"), pol, nonce=NONCE)
    accepted(bad, mod, "ESS v1 signing certificate", token(chain, include_ess="v1"), pol, nonce=NONCE)
    accepted(bad, mod, "no accuracy field", token(chain, accuracy_seconds=None), pol, nonce=NONCE)
    accepted(bad, mod, "sha384 CMS digest", token(chain, digest_algorithm="sha384", signature_algorithm="sha384_ecdsa"), pol, nonce=NONCE)
    accepted(
        bad, mod, "duplicate certificate in the token", token(chain, certificates=[*chain.certificates(), chain.tsa]), pol, nonce=NONCE
    )

    # RSA signers, with the plain and the combined algorithm identifiers
    rsa_chain = P.Chain.build("R", key=P.rsa_key(2048))
    rsa_pol = policy(mod, (rsa_chain.root, "operator-r"))
    accepted(
        bad, mod, "RSA signer, rsaEncryption identifier", token(rsa_chain, signature_algorithm="rsassa_pkcs1v15"), rsa_pol, nonce=NONCE
    )
    accepted(bad, mod, "RSA signer, sha256WithRSA identifier", token(rsa_chain, signature_algorithm="sha256_rsa"), rsa_pol, nonce=NONCE)

    # another certificate that merely shares the signer's serial number does not make the signer ambiguous
    other_issuer = standard("B").intermediate
    same_serial = P.make_cert("Unrelated", other_issuer, serial=chain.tsa.cert.serial_number, ca=False, digital_signature=True)
    accepted(
        bad,
        mod,
        "decoy with the signer's serial under another issuer",
        token(chain, certificates=[*chain.certificates(), same_serial]),
        pol,
        nonce=NONCE,
    )

    # a pin by fingerprint alone needs the certificate in the token
    by_fingerprint = mod.TokenPolicy((mod.PinnedRoot(chain.root.fingerprint, "operator-a"),))
    accepted(bad, mod, "root pinned by fingerprint, carried in the token", token(chain), by_fingerprint, nonce=NONCE)

    # a certificate in the token that cannot be read is ignored; it cannot be part of a path
    damaged_root = chain.root.der.replace(b"A Root", b"\xff Root")  # the name stops being valid UTF-8; the structure is intact
    accepted(bad, mod, "a damaged certificate next to sound ones", token(chain).replace(chain.root.der, damaged_root), pol, nonce=NONCE)

    # a copy of the root with a damaged extension (same key, so its children still verify against it) does not hide the real one
    damaged_extension = bytearray(chain.root.der)
    at = bytes(damaged_extension).index(b"\x55\x1d\x0e\x04\x16\x04") + 5  # the inner OCTET STRING tag of the subject key identifier value
    damaged_extension[at] ^= 0x01
    with_copy = token(chain).replace(chain.root.der, bytes(damaged_extension))
    accepted(bad, mod, "a root copy with a damaged extension next to the pinned root", with_copy, pol, nonce=NONCE)

    # pinning the issuing CA or the signer itself is a partial chain and still works
    accepted(bad, mod, "issuing CA pinned", token(chain), policy(mod, (chain.intermediate, "operator-a")), nonce=NONCE)
    accepted(bad, mod, "signer pinned", token(chain), policy(mod, (chain.tsa, "operator-a")), nonce=NONCE)
    # a token that omits the root still verifies when the root is pinned
    accepted(bad, mod, "root omitted from the token", token(chain, certificates=[chain.tsa, chain.intermediate]), pol, nonce=NONCE)

    # a longer path with a path-length limit that is exactly met
    root = P.make_root("Deep Root")
    one = P.make_intermediate(root, "Deep CA 1", path_length=1)
    two = P.make_intermediate(one, "Deep CA 2", path_length=0)
    deep = P.Chain(root, two, P.make_tsa(two, "Deep TSA"))
    accepted(bad, mod, "path length met", token(deep, certificates=[deep.tsa, two, one, root]), policy(mod, (root, "deep")), nonce=NONCE)

    # cross-signed roots share a subject; the right one is found even when a decoy comes first
    new_root = P.make_cert("Shared Name", None, ca=True, key_cert_sign=True)
    cross = P.make_cert("Shared Name", new_root, ca=True, key_cert_sign=True, key=new_root.key)  # same subject, signed by another key
    decoy = P.make_cert("Shared Name", None, ca=True, key_cert_sign=True)
    dead_end = P.make_cert(
        "Shared Name", standard("B").root, key=new_root.key, ca=True, key_cert_sign=True
    )  # same key, issuer not in the token
    inter = P.make_intermediate(new_root, "Cross CA")
    cross_chain = P.Chain(new_root, inter, P.make_tsa(inter, "Cross TSA"))
    accepted(
        bad,
        mod,
        "decoy with the same subject listed first",
        token(cross_chain, certificates=[cross_chain.tsa, decoy, cross, inter, new_root]),
        policy(mod, (new_root, "cross")),
        nonce=NONCE,
    )
    accepted(
        bad,
        mod,
        "a candidate issuer that leads nowhere is skipped",
        token(cross_chain, certificates=[cross_chain.tsa, dead_end, inter]),
        policy(mod, (new_root, "cross")),
        nonce=NONCE,
    )

    # weak or odd parameters on the trust anchor itself do not matter, only on what the anchor vouches for
    sha1_root = P.make_cert("Old Root", None, ca=True, key_cert_sign=True, sha1_signature=True)
    sha1_inter = P.make_intermediate(sha1_root, "Old Root CA")
    old = P.Chain(sha1_root, sha1_inter, P.make_tsa(sha1_inter, "Old Root TSA"))
    accepted(bad, mod, "anchor signed with SHA-1", token(old), policy(mod, (sha1_root, "old")), nonce=NONCE)
    short_root = P.make_cert("Short-key Root", None, key=P.rsa_key(1024), ca=True, key_cert_sign=True)
    short_inter = P.make_intermediate(short_root, "Short-key Root CA")
    short = P.Chain(short_root, short_inter, P.make_tsa(short_inter, "Short-key Root TSA"))
    accepted(bad, mod, "anchor with a 1024-bit key", token(short), policy(mod, (short_root, "short")), nonce=NONCE)
    return bad


def rejects_token_content(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    chain = standard("A")
    pol = policy(mod, (chain.root, "operator-a"))
    good = token(chain)
    rejected(bad, mod, "digest of another checkpoint", good, pol, digest=hashlib.sha256(b"another").digest(), nonce=NONCE)
    rejected(bad, mod, "digest of the wrong length", good, pol, digest=DIGEST[:31], nonce=NONCE)
    rejected(bad, mod, "imprint of another digest inside the token", token(chain, imprint=hashlib.sha256(b"x").digest()), pol, nonce=NONCE)
    rejected(bad, mod, "SHA-1 imprint algorithm", token(chain, imprint_algorithm="sha1"), pol, nonce=NONCE)
    rejected(bad, mod, "nonce differs from the request", good, pol, nonce=NONCE + 1)
    rejected(bad, mod, "nonce requested but the token has none", token(chain, nonce=None), pol, nonce=NONCE)
    rejected(bad, mod, "authority refused", token(chain, status="rejection"), pol, nonce=NONCE)
    rejected(bad, mod, "critical extension in TSTInfo", token(chain, critical_tst_extension=True), pol, nonce=NONCE)
    rejected(bad, mod, "two signers", token(chain, second_signer=True), pol, nonce=NONCE)
    rejected(bad, mod, "CMS version 4", token(chain, cms_version="v4"), pol, nonce=NONCE)
    rejected(bad, mod, "generation time without a zone designator", token(chain, gen_time_without_zone=True), pol, nonce=NONCE)
    rejected(bad, mod, "message digest attribute twice", token(chain, duplicate_message_digest=True), pol, nonce=NONCE)
    rejected(bad, mod, "content type attribute names another type", token(chain, content_type_attribute="data"), pol, nonce=NONCE)
    rejected(bad, mod, "message digest attribute does not match TSTInfo", token(chain, tamper_message_digest=True), pol, nonce=NONCE)
    rejected(bad, mod, "signature bytes changed", token(chain, tamper_signature=True), pol, nonce=NONCE)
    rejected(bad, mod, "SHA-1 CMS digest", token(chain, digest_algorithm="sha1", signature_algorithm="sha1_ecdsa"), pol, nonce=NONCE)
    rejected(bad, mod, "EC signer with an RSA algorithm identifier", token(chain, signature_algorithm="rsassa_pkcs1v15"), pol, nonce=NONCE)
    rsa_chain = P.Chain.build("R", key=P.rsa_key(2048))
    rejected(
        bad,
        mod,
        "RSA signer with an ECDSA algorithm identifier",
        token(rsa_chain, signature_algorithm="sha256_ecdsa"),
        policy(mod, (rsa_chain.root, "r")),
        nonce=NONCE,
    )
    rejected(
        bad,
        mod,
        "RSA signer with an identifier for an unlisted algorithm",
        token(rsa_chain, signature_algorithm="md5_rsa"),
        policy(mod, (rsa_chain.root, "r")),
        nonce=NONCE,
    )
    rsa_sha1 = replace(P.TokenOptions(nonce=NONCE), digest_algorithm="sha1", signature_algorithm="rsassa_pkcs1v15")
    rejected(
        bad,
        mod,
        "RSA signer with a SHA-1 digest and the plain RSA identifier",
        P.build_token(DIGEST, rsa_chain, rsa_sha1),
        policy(mod, (rsa_chain.root, "r")),
        nonce=NONCE,
    )
    rejected(bad, mod, "signature algorithm names another hash", token(chain, signature_algorithm="sha384_ecdsa"), pol, nonce=NONCE)
    rejected(bad, mod, "digest algorithm not listed", token(chain, listed_digest_algorithms=("sha512",)), pol, nonce=NONCE)
    rejected(bad, mod, "ESS v1 names another certificate", token(chain, include_ess="v1", ess_cert=chain.intermediate), pol, nonce=NONCE)
    rejected(bad, mod, "no ESS signing-certificate attribute", token(chain, include_ess=None), pol, nonce=NONCE)
    rejected(bad, mod, "ESS names another certificate", token(chain, ess_cert=chain.intermediate), pol, nonce=NONCE)
    rejected(bad, mod, "no content-type attribute", token(chain, drop_signed_attrs=("content_type",)), pol, nonce=NONCE)
    rejected(bad, mod, "no message-digest attribute", token(chain, drop_signed_attrs=("message_digest",)), pol, nonce=NONCE)
    rejected(bad, mod, "signer certificate not in the token", token(chain, certificates=[chain.intermediate, chain.root]), pol, nonce=NONCE)

    # two different certificates answer to the signer identifier (the injection that made one token count twice)
    twin = P.make_cert(
        chain.tsa.cert.subject.rfc4514_string().replace("CN=", ""),
        chain.intermediate,
        serial=chain.tsa.cert.serial_number,
        ca=False,
        digital_signature=True,
        eku=[ExtendedKeyUsageOID.TIME_STAMPING],
    )
    damaged_signer = chain.tsa.der.replace(b"A TSA", b"\xff TSA")
    rejected(bad, mod, "the signer certificate is damaged", good.replace(chain.tsa.der, damaged_signer), pol, nonce=NONCE)
    rejected(
        bad, mod, "two certificates match the signer identifier", token(chain, certificates=[*chain.certificates(), twin]), pol, nonce=NONCE
    )

    rejected(bad, mod, "empty input", b"", pol, nonce=NONCE)
    rejected(bad, mod, "garbage", b"\x00\x01\x02not a token", pol, nonce=NONCE)
    rejected(bad, mod, "truncated", good[: len(good) // 2], pol, nonce=NONCE)
    rejected(bad, mod, "trailing data", good + b"\x00", pol, nonce=NONCE)
    rejected(bad, mod, "oversized", b"\x30\x84\x00\x30\x00\x00" + b"\x00" * (mod.MAX_TOKEN_BYTES + 1), pol, nonce=NONCE)
    rejected(bad, mod, "no pinned root configured", good, mod.TokenPolicy(()), nonce=NONCE)
    return bad


def rejects_certificate_path(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    base = standard("A")
    root = base.root

    def check(
        label: str, chain: P.Chain, certificates: list[P.Authority] | None = None, *, pins: P.Authority | None = None, **options: object
    ) -> None:
        opts = dict(options)
        if certificates is not None:
            opts["certificates"] = certificates
        rejected(bad, mod, label, token(chain, **opts), policy(mod, (pins or chain.root, "x")), nonce=NONCE)

    # an ordinary TLS leaf used as an issuer
    leaf = P.make_cert("TLS leaf", base.intermediate, ca=False, digital_signature=True, eku=[ExtendedKeyUsageOID.SERVER_AUTH])
    forged = P.make_tsa(leaf, "TSA under a TLS leaf")
    check("TSA issued by a non-CA leaf", P.Chain(root, leaf, forged), [forged, leaf, base.intermediate, root])

    def under(label: str, **issuer_options: object) -> None:
        inter = P.make_intermediate(root, "Test CA", **issuer_options)
        chain = P.Chain(root, inter, P.make_tsa(inter, "Test TSA"))
        check(label, chain)

    under("issuer without certificate-signing key usage", key_cert_sign=False, digital_signature=True)
    under("issuer without basic constraints", ca=None, key_cert_sign=None)
    under("issuer with an unknown critical extension", unknown_critical=True)
    under("issuer expired at the time stamp time", not_after=P.BASE - timedelta(days=1))
    under("issuer not yet valid", not_before=P.BASE + timedelta(days=1))
    under("issuer signed with SHA-1", sha1_signature=True)
    under("issuer signed with SHA-224", signature_hash=hashes.SHA224())
    rsa_root = P.make_cert("RSA root", None, key=P.rsa_key(2048), ca=True, key_cert_sign=True)
    pss_ca = P.make_cert("PSS CA", rsa_root, ca=True, key_cert_sign=True, rsa_pss=True)
    check("issuer signed with RSA-PSS", P.Chain(rsa_root, pss_ca, P.make_tsa(pss_ca, "PSS TSA")))
    weak = P.rsa_key(1024)
    inter_weak = P.make_cert("Weak CA", root, key=weak, ca=True, key_cert_sign=True)
    check("issuer with a 1024-bit RSA key", P.Chain(root, inter_weak, P.make_tsa(inter_weak, "Weak TSA")))

    weak_inter = P.make_intermediate(root, "Test CA for a weak signer")
    weak_tsa = P.make_tsa(weak_inter, "Weak-key TSA", key=P.rsa_key(1024))
    check("signer with a 1024-bit RSA key", P.Chain(root, weak_inter, weak_tsa), signature_algorithm="rsassa_pkcs1v15")
    odd_curve = P.make_cert("Odd curve CA", root, key=ec.generate_private_key(ec.SECP224R1()), ca=True, key_cert_sign=True)
    check("issuer on an unlisted curve", P.Chain(root, odd_curve, P.make_tsa(odd_curve, "Odd curve TSA")))
    edwards = P.make_cert("Ed25519 CA", root, key=ed25519.Ed25519PrivateKey.generate(), ca=True, key_cert_sign=True)
    check("issuer with an Ed25519 key", P.Chain(root, edwards, P.make_tsa(edwards, "Ed25519 TSA")))

    # path length limit exceeded: root -> a (pathlen 0) -> b -> signer
    a = P.make_intermediate(root, "CA a", path_length=0)
    b = P.make_intermediate(a, "CA b")
    t = P.make_tsa(b, "TSA below b")
    check("path length constraint exceeded", P.Chain(root, b, t), [t, b, a, root])

    # a path longer than the verifier allows
    parent, issuers = root, []
    for i in range(mod.MAX_PATH_LENGTH + 1):
        parent = P.make_intermediate(parent, f"Chain CA {i}")
        issuers.append(parent)
    long_tsa = P.make_tsa(parent, "TSA at the end of a long chain")
    check("path too long", P.Chain(root, parent, long_tsa), [long_tsa, *reversed(issuers), root])

    def signer(label: str, **tsa_options: object) -> None:
        chain = P.Chain(root, base.intermediate, P.make_tsa(base.intermediate, "Odd TSA", **tsa_options))
        check(label, chain, [chain.tsa, base.intermediate, root])

    signer("signer without extended key usage", eku=None)
    signer("signer's extended key usage not critical", eku_critical=False)
    signer("signer with extra extended key usages", eku=[ExtendedKeyUsageOID.TIME_STAMPING, ExtendedKeyUsageOID.SERVER_AUTH])
    signer("signer with only another usage", eku=[ExtendedKeyUsageOID.SERVER_AUTH])
    signer("signer is a CA", ca=True, key_cert_sign=True, digital_signature=True)
    signer("signer key usage forbids signatures", digital_signature=False, key_cert_sign=False)
    signer("signer with an unknown critical extension", unknown_critical=True)
    signer("signer expired at the time stamp time", not_after=P.BASE - timedelta(days=1))
    signer("signer not yet valid", not_before=P.BASE + timedelta(days=1))
    signer("signer certificate signed with SHA-224", signature_hash=hashes.SHA224())

    # a pinned root that is itself expired at the time stamp time
    old_root = P.make_cert("Expired Root", None, ca=True, key_cert_sign=True, not_after=P.BASE - timedelta(days=1))
    old_inter = P.make_intermediate(old_root, "Expired Root CA")
    old = P.Chain(old_root, old_inter, P.make_tsa(old_inter, "Expired Root TSA"))
    check("pinned root expired at the time stamp time", old)

    # a pin by fingerprint alone, with the root left out of the token, cannot complete a path
    fingerprint_only = mod.TokenPolicy((mod.PinnedRoot(base.root.fingerprint, "a"),))
    rejected(
        bad,
        mod,
        "root pinned by fingerprint, omitted from the token",
        token(base, certificates=[base.tsa, base.intermediate]),
        fingerprint_only,
        nonce=NONCE,
    )

    # the chain leads to a root that is not pinned
    other = standard("B")
    rejected(bad, mod, "root not pinned", token(base), policy(mod, (other.root, "b")), nonce=NONCE)
    rejected(bad, mod, "only an unrelated issuer pinned", token(base), policy(mod, (other.intermediate, "b")), nonce=NONCE)

    # an attacker's root with the pinned root's name does not borrow its trust
    lookalike = P.make_cert(root.cert.subject.rfc4514_string().replace("CN=", ""), None, ca=True, key_cert_sign=True)
    inter = P.make_intermediate(lookalike, "Lookalike CA")
    lie = P.Chain(lookalike, inter, P.make_tsa(inter, "Lookalike TSA"))
    check("root with the pinned root's name but another key", lie, [lie.tsa, inter, lookalike], pins=root)

    # the token's own claim that its signer is trusted counts for nothing without a pin: a self-signed signer
    selfsigned = P.make_cert("Self-signed TSA", None, ca=False, digital_signature=True, eku=[ExtendedKeyUsageOID.TIME_STAMPING])
    chain_self = P.Chain(selfsigned, selfsigned, selfsigned)
    rejected(
        bad,
        mod,
        "self-signed signer that is not pinned",
        token(chain_self, certificates=[selfsigned]),
        policy(mod, (root, "a")),
        nonce=NONCE,
    )
    return bad


def patched(blob: bytes, old: bytes, new: bytes, *, after: bytes = b"") -> bytes:
    """``blob`` with the first ``old`` at or after the first ``after`` replaced by the same-length ``new``."""
    assert len(old) == len(new)
    start = blob.index(after) if after else 0
    at = blob.index(old, start)
    return blob[:at] + new + blob[at + len(old) :]


def rejects_malformed_structure(mod: types.ModuleType) -> list[str]:
    """Tokens that are not DER, or that name another content type or version, or whose certificates cannot be used."""
    bad: list[str] = []
    chain = standard("A")
    pol = policy(mod, (chain.root, "operator-a"))
    good = token(chain)

    signed_data_oid = bytes.fromhex("2a864886f70d010702")
    tst_oid = bytes.fromhex("2a864886f70d0109100104")
    rejected(bad, mod, "content type is not signed data", patched(good, signed_data_oid, signed_data_oid[:-1] + b"\x01"), pol, nonce=NONCE)
    rejected(bad, mod, "encapsulated content is not TSTInfo", patched(good, tst_oid, tst_oid[:-1] + b"\x03"), pol, nonce=NONCE)
    rejected(bad, mod, "TSTInfo version 2", patched(good, b"\x02\x01\x01\x06", b"\x02\x01\x02\x06", after=tst_oid), pol, nonce=NONCE)
    rejected(bad, mod, "a damaged accuracy field", patched(good, b"\x30\x03\x02\x01\x01", b"\x30\x03\x1f\x01\x01"), pol, nonce=NONCE)

    def reshaped_from(blob: bytes, change) -> bytes:  # type: ignore[no-untyped-def]
        tree = D.parse(blob)
        change(tree)
        return tree.encode()

    def reshaped(change) -> bytes:  # type: ignore[no-untyped-def]
        return reshaped_from(good, change)

    # tree: response -> [status, content info -> [oid, [0] -> [signed data -> [version, digest algorithms, encapsulated, [0] certificates, signer infos]]]]
    def signed_data(tree):  # type: ignore[no-untyped-def]
        return tree.children[1].children[1].children[0]

    def style(name):  # type: ignore[no-untyped-def]
        return lambda tree: setattr(signed_data(tree).children[1], "style", name)

    accepted(bad, mod, "the same token re-encoded unchanged", reshaped(lambda tree: None), pol, nonce=NONCE)
    rejected(bad, mod, "a length written with a padded long form", reshaped(style("padded")), pol, nonce=NONCE)
    # (the certificate set is long, so a leading zero is not caught merely by the length being short)
    rejected(
        bad,
        mod,
        "a length written with a leading zero",
        reshaped(lambda tree: setattr(signed_data(tree).children[3], "style", "leading_zero")),
        pol,
        nonce=NONCE,
    )
    rejected(
        bad,
        mod,
        "an indefinite length",
        reshaped(lambda tree: setattr(signed_data(tree).children[3], "style", "indefinite")),
        pol,
        nonce=NONCE,
    )

    def append_to_tstinfo(tree) -> None:  # type: ignore[no-untyped-def]
        content = signed_data(tree).children[2].children[1].children[0]  # the OCTET STRING that holds TSTInfo
        content.value += b"\x05\x00"

    rejected(bad, mod, "an extra element after TSTInfo", reshaped(append_to_tstinfo), pol, nonce=NONCE)
    rejected(
        bad,
        mod,
        "a multi-byte tag",
        reshaped(lambda tree: setattr(signed_data(tree).children[1], "raw_tag", b"\x3f\x01")),
        pol,
        nonce=NONCE,
    )

    def add_deep_unsigned_attributes(tree) -> None:  # type: ignore[no-untyped-def]
        info = signed_data(tree).children[4].children[0]
        deep = D.Node(0x30, children=[])
        for _ in range(40):
            deep = D.Node(0x30, children=[deep])
        info.children.append(D.Node(0xA1, children=[deep]))

    rejected(bad, mod, "structure nested deeper than allowed", reshaped(add_deep_unsigned_attributes), pol, nonce=NONCE)

    def add_shallow_unsigned_attributes(tree) -> None:  # type: ignore[no-untyped-def]
        info = signed_data(tree).children[4].children[0]
        nest = D.Node(0x30, children=[])
        for _ in range(4):
            nest = D.Node(0x30, children=[nest])
        info.children.append(D.Node(0xA1, children=[nest]))

    accepted(bad, mod, "unsigned attributes that nest within the limit", reshaped(add_shallow_unsigned_attributes), pol, nonce=NONCE)

    # certificates that cannot be used
    tsa_der = chain.tsa.der
    duplicate_extension = patched(
        tsa_der, b"\x06\x03\x55\x1d\x13", b"\x06\x03\x55\x1d\x0e"
    )  # basic constraints relabelled as a second key identifier
    rejected(bad, mod, "the signer certificate lists an extension twice", good.replace(tsa_der, duplicate_extension), pol, nonce=NONCE)

    # tokens found by the random-damage differential: a certificate flag changed after signing that a re-encoding parser would repair
    for case in json.loads((VECTORS / "tsa_noncanonical_boolean.json").read_text())["cases"]:
        pins = tuple(mod.PinnedRoot.from_der(base64.b64decode(p["der"]), p["operator"]) for p in case["pins"])
        rejected(
            bad,
            mod,
            f"fuzz-found: {case['label']}",
            base64.b64decode(case["token"]),
            mod.TokenPolicy(pins),
            digest=bytes.fromhex(case["digest"]),
            nonce=int(case["nonce"]),
        )

    # RFC 5280 serial numbers are positive; a certificate library may refuse the others in a later release, so neither verifier uses one
    for serial in (0, -5):
        odd = standard("Z")
        odd = P.Chain(odd.root, odd.intermediate, P.make_tsa(odd.intermediate, "Z TSA", raw_serial=serial))
        rejected(
            bad, mod, f"the signer certificate has serial number {serial}", token(odd), policy(mod, (odd.root, "operator-z")), nonce=NONCE
        )

    # a BOOLEAN written with a value other than 0x00 or 0xFF is not DER; some parsers re-encode it as 0xFF, which would let a
    # certificate pass whose signature covers the repaired bytes and not the bytes the token carries
    def non_canonical_boolean(der: bytes) -> bytes:
        at = der.rindex(b"\x01\x01\xff")
        return der[:at] + b"\x01\x01\x01" + der[at + 3 :]

    rejected(
        bad,
        mod,
        "the signer certificate carries a non-canonical BOOLEAN",
        good.replace(tsa_der, non_canonical_boolean(tsa_der)),
        pol,
        nonce=NONCE,
    )
    accepted(
        bad,
        mod,
        "another certificate carries a non-canonical BOOLEAN",
        good.replace(chain.root.der, non_canonical_boolean(chain.root.der)),
        pol,
        nonce=NONCE,
    )

    # the signer identifier alternatives: issuerAndSerialNumber is a SEQUENCE of two, subjectKeyIdentifier is [0] and nothing else
    ski_chain = standard("S")
    ski_pol = policy(mod, (ski_chain.root, "operator-s"))
    ski_token = token(ski_chain, sid="subject_key_identifier")
    accepted(bad, mod, "the signer identified by key identifier", ski_token, ski_pol, nonce=NONCE)

    def signer_identifier(tree):  # type: ignore[no-untyped-def]
        return signed_data(tree).children[4].children[0].children[1]

    for tag in (0x81, 0xC0, 0x04):
        rejected(
            bad,
            mod,
            f"a key identifier under tag {tag:#04x}",
            reshaped_from(ski_token, lambda tree, tag=tag: setattr(signer_identifier(tree), "tag", tag)),
            ski_pol,
            nonce=NONCE,
        )
    rejected(
        bad,
        mod,
        "an issuer and serial identifier with a third element",
        reshaped(lambda tree: signer_identifier(tree).children.append(D.Node(0x05, value=b""))),
        pol,
        nonce=NONCE,
    )

    rsa_chain = P.Chain.build("R", key=P.rsa_key(2048))
    rsa_pol = policy(mod, (rsa_chain.root, "r"))
    rsa_token = P.build_token(DIGEST, rsa_chain, replace(P.TokenOptions(nonce=NONCE), signature_algorithm="rsassa_pkcs1v15"))
    padded_exponent = patched(rsa_chain.tsa.der, b"\x02\x03\x01\x00\x01", b"\x02\x03\x00\x00\x01")  # 65537 written with a leading zero
    rejected(
        bad,
        mod,
        "the signer's RSA exponent is not minimal DER",
        rsa_token.replace(rsa_chain.tsa.der, padded_exponent),
        rsa_pol,
        nonce=NONCE,
    )

    edwards = P.make_tsa(chain.intermediate, "Ed25519 TSA", key=ed25519.Ed25519PrivateKey.generate())
    ed_chain = P.Chain(chain.root, chain.intermediate, edwards)
    rejected(
        bad,
        mod,
        "the signer has an Ed25519 key",
        token(ed_chain, raw_signature=b"\x01" * 64, certificates=ed_chain.certificates()),
        pol,
        nonce=NONCE,
    )

    commitment_only = P.make_tsa(chain.intermediate, "Commitment TSA", digital_signature=False, content_commitment=True)
    accepted(
        bad,
        mod,
        "a signer whose key usage is content commitment only",
        token(P.Chain(chain.root, chain.intermediate, commitment_only)),
        pol,
        nonce=NONCE,
    )

    renamed = P.make_cert("Renamed CA", chain.root, ca=True, key_cert_sign=True, issuer_name="Not The Root")
    unchained = P.Chain(chain.root, renamed, P.make_tsa(renamed, "Unchained TSA"))
    rejected(bad, mod, "an issuer's name does not chain to its issuer", token(unchained), pol, nonce=NONCE)
    return bad


def live_tokens(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    entries = json.loads((VECTORS / "tsa_live.json").read_text())["entries"]
    # Apple's responder signs with a SHA-1 CMS digest, which the policy rejects (CF-04); every other authority verifies.
    expected_reject = {"apple"}
    seen_operators: set[str] = set()
    for entry in entries:
        name = entry["authority"]
        blob = base64.b64decode(entry["token_base64"])
        digest = bytes.fromhex(entry["digest_sha256_hex"])
        root = mod.PinnedRoot.from_der(base64.b64decode(entry["pinned_root_der_base64"]), name)
        pol = mod.TokenPolicy((root,))
        if name in expected_reject:
            rejected(bad, mod, f"{name} (SHA-1 signer)", blob, pol, digest=digest, nonce=entry["nonce"])
            continue
        result = accepted(bad, mod, name, blob, pol, digest=digest, nonce=entry["nonce"])
        if result is None:
            continue
        seen_operators.add(result.operator)
        if result.root_fingerprint != entry["pinned_root_sha256"]:
            bad.append(f"{name}: root fingerprint differs from the pin")
        rejected(bad, mod, f"{name} with another digest", blob, pol, digest=hashlib.sha256(b"other").digest(), nonce=entry["nonce"])
        rejected(bad, mod, f"{name} with another nonce", blob, pol, digest=digest, nonce=entry["nonce"] + 1)
        other = next(e for e in entries if e["pinned_root_sha256"] != entry["pinned_root_sha256"] and e["authority"] != "apple")
        wrong = mod.TokenPolicy((mod.PinnedRoot.from_der(base64.b64decode(other["pinned_root_der_base64"]), "wrong"),))
        rejected(bad, mod, f"{name} against another authority's root", blob, wrong, digest=digest, nonce=entry["nonce"])
        # the signature itself: any change to its bytes is a rejection
        signature = tsp.TimeStampResp.load(blob)["time_stamp_token"]["content"]["signer_infos"][0]["signature"].native
        at = blob.find(signature)
        if at < 0:
            bad.append(f"{name}: signature bytes not found in the token")
        else:
            for offset in (0, len(signature) // 2, len(signature) - 1):
                forged = bytearray(blob)
                forged[at + offset] ^= 0x01
                rejected(bad, mod, f"{name} with a changed signature byte", bytes(forged), pol, digest=digest, nonce=entry["nonce"])
    if len(seen_operators) < 7:
        bad.append(f"only {len(seen_operators)} live authorities verified")
    return bad


def live_tokens_sweep(mod: types.ModuleType) -> list[str]:
    """Flip single bits across every live token: nothing may be accepted with a different statement, and nothing may crash."""
    bad: list[str] = []
    for entry in json.loads((VECTORS / "tsa_live.json").read_text())["entries"]:
        name = entry["authority"]
        if name == "apple":
            continue
        blob = base64.b64decode(entry["token_base64"])
        digest = bytes.fromhex(entry["digest_sha256_hex"])
        pol = mod.TokenPolicy((mod.PinnedRoot.from_der(base64.b64decode(entry["pinned_root_der_base64"]), name),))
        original = mod.verify_token(blob, digest, pol, nonce=entry["nonce"])
        step = max(1, len(blob) // 160)
        for position in range(0, len(blob), step):
            mutated = bytearray(blob)
            mutated[position] ^= 0x01
            try:
                changed = mod.verify_token(bytes(mutated), digest, pol, nonce=entry["nonce"])
            except mod.TokenError:
                continue
            except Exception as exc:
                bad.append(f"{name}: byte {position} raised {type(exc).__name__}")
                continue
            if (changed.gen_time, changed.root_fingerprint, changed.signer_fingerprint, changed.serial) != (
                original.gen_time,
                original.root_fingerprint,
                original.signer_fingerprint,
                original.serial,
            ):
                bad.append(f"{name}: a change at byte {position} produced a different accepted statement")
    return bad


def validates_pins(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    chain = standard("A")
    good = chain.root.fingerprint
    for label, args in (
        ("upper-case hex", (good.upper(), "x")),
        ("short pin", (good[:62], "x")),
        ("non-hex pin", ("z" * 64, "x")),
        ("a certificate that does not match the pin", (hashlib.sha256(b"other").hexdigest(), "x", chain.root.der)),
    ):
        try:
            mod.PinnedRoot(*args)
            bad.append(f"{label}: accepted")
        except ValueError:
            pass
    pin = mod.PinnedRoot.from_der(chain.root.der, "x")
    if pin.fingerprint != good or pin.der != chain.root.der:
        bad.append("from_der gave the wrong pin")
    return bad


def builds_requests(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    request, nonce = mod.build_request(DIGEST)
    parsed = tsp.TimeStampReq.load(request, strict=True)
    if (
        parsed["message_imprint"]["hash_algorithm"]["algorithm"].native != "sha256"
        or parsed["message_imprint"]["hashed_message"].native != DIGEST
    ):
        bad.append("request imprint is wrong")
    if parsed["nonce"].native != nonce or not 0 < nonce < 2**63:
        bad.append("request nonce is wrong")
    if not parsed["cert_req"].native:
        bad.append("request does not ask for the certificates")
    if mod.build_request(DIGEST)[1] == nonce and mod.build_request(DIGEST)[1] == nonce:
        bad.append("nonces repeat")
    fixed, given = mod.build_request(DIGEST, 42)
    if given != 42 or tsp.TimeStampReq.load(fixed)["nonce"].native != 42:
        bad.append("a given nonce is not used")
    try:
        mod.build_request(DIGEST[:20])
        bad.append("a 20-byte digest was accepted")
    except mod.TokenError:
        pass
    return bad


SCENARIOS: dict[str, Callable[[types.ModuleType], list[str]]] = {
    "accepts_valid_forms": accepts_valid_forms,
    "rejects_token_content": rejects_token_content,
    "rejects_certificate_path": rejects_certificate_path,
    "rejects_malformed_structure": rejects_malformed_structure,
    "live_tokens": live_tokens,
    "live_tokens_sweep": live_tokens_sweep,
    "validates_pins": validates_pins,
    "builds_requests": builds_requests,
}


def failures(mod: types.ModuleType, only: list[str] | None = None) -> list[str]:
    bad: list[str] = []
    for name, scenario in SCENARIOS.items():
        if only is not None and name not in only:
            continue
        try:
            bad += [f"{name}: {message}" for message in scenario(mod)]
        except Exception as exc:
            bad.append(f"{name}: raised {type(exc).__name__}: {exc}")
    return bad
