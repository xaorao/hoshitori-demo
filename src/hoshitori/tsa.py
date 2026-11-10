"""RFC 3161 time-stamp requests and token verification.

``verify_token`` is the check behind commitment level C3: it decides whether a token proves that an independent
authority saw a digest at ``gen_time``. It follows RFC 3161 section 2.4.2 and RFC 5816 and adds the rules found by
attacking the first verifiers:

* **signer binding**: the signer identifier of the signature must select the certificate whose key verifies it; a
  certificate that is merely present in the token never counts;
* **certificate path**: every issuer is a CA (basic constraints, key usage, path length), every certificate is valid
  at ``gen_time`` (not "today": a token must stay verifiable after its signer expires, which is why the chain and
  revocation evidence are archived with it), signatures use SHA-2, and an unknown *critical* extension rejects the
  token (openssl does the same);
* **signer certificate**: extended key usage is exactly time stamping and critical (RFC 3161 section 2.3);
* **trust**: the path must end at a root pinned by the SHA-256 of its DER. The pinned set comes from the trusted root
  and never from the token.

Revocation is not checked here; the bundle archives revocation evidence and the verifier reports its absence.
Any malformed input is a ``TokenError`` with a reason, never another exception.
"""

from __future__ import annotations

import hashlib
import secrets
import warnings
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from asn1crypto import cms, core, parser, tsp
from cryptography import x509
from cryptography.exceptions import InvalidSignature, UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.utils import CryptographyDeprecationWarning
from cryptography.x509.oid import ExtendedKeyUsageOID, ExtensionOID

MAX_TOKEN_BYTES: Final = 131_072
MAX_PATH_LENGTH: Final = 6
MIN_RSA_BITS: Final = 2048
ALLOWED_CURVES: Final = frozenset({"secp256r1", "secp384r1", "secp521r1"})
OID_ESS_SIGNING_CERT_V1: Final = "1.2.840.113549.1.9.16.2.12"
OID_ESS_SIGNING_CERT_V2: Final = "1.2.840.113549.1.9.16.2.47"
HASHES: Final[dict[str, Callable[[], hashes.HashAlgorithm]]] = {
    "sha256": hashes.SHA256,
    "sha384": hashes.SHA384,
    "sha512": hashes.SHA512,
}
# Signature algorithm identifiers accepted for the CMS signature: (key family, hash named by the identifier or None when the
# digest algorithm of the signer names it). Everything else is rejected, whatever the ASN.1 library would make of it.
SIGNATURE_ALGORITHMS: Final = {
    "1.2.840.113549.1.1.1": ("rsa", None),  # rsaEncryption
    "1.2.840.113549.1.1.11": ("rsa", "sha256"),
    "1.2.840.113549.1.1.12": ("rsa", "sha384"),
    "1.2.840.113549.1.1.13": ("rsa", "sha512"),
    "1.2.840.10045.4.3.2": ("ec", "sha256"),
    "1.2.840.10045.4.3.3": ("ec", "sha384"),
    "1.2.840.10045.4.3.4": ("ec", "sha512"),
}
# Signature algorithms accepted on certificates: PKCS#1 v1.5 and ECDSA with SHA-256, SHA-384 or SHA-512 (no PSS, no SHA-1).
CERTIFICATE_SIGNATURE_OIDS: Final = frozenset(oid for oid, (_, named) in SIGNATURE_ALGORITHMS.items() if named is not None)
# Critical extensions this verifier understands and applies; any other critical extension rejects the certificate.
HANDLED_CRITICAL: Final = frozenset(
    {
        ExtensionOID.BASIC_CONSTRAINTS,
        ExtensionOID.KEY_USAGE,
        ExtensionOID.EXTENDED_KEY_USAGE,
        ExtensionOID.SUBJECT_KEY_IDENTIFIER,
        ExtensionOID.AUTHORITY_KEY_IDENTIFIER,
        ExtensionOID.CERTIFICATE_POLICIES,
        ExtensionOID.SUBJECT_ALTERNATIVE_NAME,
        ExtensionOID.CRL_DISTRIBUTION_POINTS,
        ExtensionOID.AUTHORITY_INFORMATION_ACCESS,
    }
)


CODES: Final = (
    "SIZE",
    "DIGEST",
    "PARSE",
    "STATUS",
    "CONTENT",
    "VERSION",
    "IMPRINT",
    "NONCE",
    "EXTENSION",
    "TIME",
    "SIGNER_COUNT",
    "ALGORITHM",
    "ATTRIBUTES",
    "SIGNER_ID",
    "SIGNATURE",
    "ESS",
    "NO_ROOT",
    "PATH",
    "CERT",
)


class TokenError(ValueError):
    """The token does not prove what a time-stamp token must prove.

    ``code`` is one of ``CODES`` and names the family of rule that failed; the message says which one. Both verifiers
    check in the same order and report the same code.
    """

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class PinnedRoot:
    """A trusted certificate, pinned by the SHA-256 of its DER, with the operator it belongs to.

    ``der`` is optional: with only the fingerprint, a token must carry the pinned certificate itself; with the
    certificate, a token may leave it out. A given certificate must hash to the pin.
    """

    sha256: str
    operator: str
    der: bytes | None = None

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or self.sha256 != self.sha256.lower() or any(c not in "0123456789abcdef" for c in self.sha256):
            raise ValueError("a pin is 64 lower-case hex characters")
        if self.der is not None and hashlib.sha256(self.der).hexdigest() != self.sha256:
            raise ValueError("the certificate does not match its pin")

    @classmethod
    def from_der(cls, der: bytes, operator: str) -> PinnedRoot:
        return cls(hashlib.sha256(der).hexdigest(), operator, der)

    @property
    def fingerprint(self) -> str:
        return self.sha256


@dataclass(frozen=True)
class TokenPolicy:
    pinned: tuple[PinnedRoot, ...]

    def by_fingerprint(self) -> dict[str, PinnedRoot]:
        return {p.fingerprint: p for p in self.pinned}


@dataclass(frozen=True)
class VerifiedToken:
    gen_time: datetime
    policy_oid: str
    serial: int
    nonce: int | None
    accuracy: dict[str, int | None] | None
    root_fingerprint: str
    signer_fingerprint: str
    operator: str
    path_fingerprints: tuple[str, ...]


def build_request(digest: bytes, nonce: int | None = None) -> tuple[bytes, int]:
    """A TimeStampReq for a SHA-256 digest asking for the certificates; returns ``(DER, nonce)``."""
    if len(digest) != 32:
        raise TokenError("the digest must be a SHA-256 value (32 bytes)", "DIGEST")
    nonce = secrets.randbits(63) if nonce is None else nonce
    request = tsp.TimeStampReq(
        {
            "version": "v1",
            "message_imprint": {"hash_algorithm": {"algorithm": "sha256"}, "hashed_message": digest},
            "nonce": nonce,
            "cert_req": True,
        }
    )
    return request.dump(), nonce


MAX_DER_DEPTH: Final = 32


def _check_der(data: bytes, depth: int = 0, *, single: bool = False) -> None:
    """Walk the structure of ``data`` as DER: definite and shortest lengths, one-byte tags, nothing left over at any level.

    With ``single`` the data must be exactly one element. The ASN.1 library accepts BER forms (indefinite or padded
    lengths). A signature covers exact bytes, so a token that is not DER is refused rather than interpreted. Primitive
    contents are opaque here.
    """
    prefix = "unparsable token: "
    offset = 0
    count_elements = 0
    while offset < len(data):
        if depth > MAX_DER_DEPTH:
            raise TokenError(prefix + "nesting too deep", "PARSE")
        if offset + 2 > len(data):
            raise TokenError(prefix + "truncated element", "PARSE")
        tag, first = data[offset], data[offset + 1]
        if tag & 0x1F == 0x1F:
            raise TokenError(prefix + "multi-byte tags are not used", "PARSE")
        header = 2
        length = first
        if first == 0x80:
            raise TokenError(prefix + "indefinite length", "PARSE")
        if first > 0x80:
            count = first & 0x7F
            if count > 4 or offset + 2 + count > len(data) or data[offset + 2] == 0:
                raise TokenError(prefix + "bad length encoding", "PARSE")
            length = int.from_bytes(data[offset + 2 : offset + 2 + count], "big")
            if length < 0x80:
                raise TokenError(prefix + "length is not minimal", "PARSE")
            header = 2 + count
        end = offset + header + length
        if end > len(data):
            raise TokenError(prefix + "element runs past the end", "PARSE")
        if tag & 0x20:
            _check_der(data[offset + header : end], depth + 1)
        offset = end
        count_elements += 1
    if single and count_elements != 1:
        raise TokenError(prefix + "expected exactly one element", "PARSE")


def _der(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(Encoding.DER)


def _fingerprint(cert: x509.Certificate) -> str:
    return hashlib.sha256(_der(cert)).hexdigest()


def _verify_signature(
    cert: x509.Certificate, signature: bytes, data: bytes, algorithm: cms.SignedDigestAlgorithm, digest_name: str
) -> None:
    identifier = algorithm["algorithm"].dotted
    entry = SIGNATURE_ALGORITHMS.get(identifier)
    if entry is None:
        raise TokenError(f"signature algorithm {identifier} is not allowed", "ALGORITHM")
    family, named_hash = entry
    if named_hash is not None and named_hash != digest_name:
        raise TokenError("the signature algorithm names a different hash than the digest algorithm", "ALGORITHM")
    try:
        public = cert.public_key()
    except (ValueError, UnsupportedAlgorithm) as exc:
        raise TokenError("the signer key could not be read", "ALGORITHM") from exc
    try:
        if isinstance(public, rsa.RSAPublicKey):
            if family != "rsa":
                raise TokenError(f"signature algorithm {identifier} is not allowed for an RSA key", "ALGORITHM")
            public.verify(signature, data, padding.PKCS1v15(), HASHES[digest_name]())
        elif isinstance(public, ec.EllipticCurvePublicKey):
            if family != "ec":
                raise TokenError(f"signature algorithm {identifier} is not allowed for an EC key", "ALGORITHM")
            public.verify(signature, data, ec.ECDSA(HASHES[digest_name]()))
        else:
            raise TokenError("the signer key type is not allowed", "ALGORITHM")
    except TokenError:
        raise
    except InvalidSignature as exc:
        raise TokenError("the CMS signature does not verify", "SIGNATURE") from exc
    except (KeyError, ValueError, TypeError) as exc:
        raise TokenError(f"unusable signature parameters: {exc}", "SIGNATURE") from exc


def _check_extensions(cert: x509.Certificate, label: str) -> None:
    for ext in cert.extensions:
        if ext.critical and ext.oid not in HANDLED_CRITICAL:
            raise TokenError(f"{label}: unhandled critical extension {ext.oid.dotted_string}", "CERT")


def _basic_constraints(cert: x509.Certificate) -> x509.BasicConstraints | None:
    try:
        return cert.extensions.get_extension_for_class(x509.BasicConstraints).value
    except x509.ExtensionNotFound:
        return None


def _check_ca(cert: x509.Certificate, below: int, label: str) -> None:
    """``cert`` must be allowed to issue certificates, with ``below`` CA certificates between it and the signer."""
    basic = _basic_constraints(cert)
    if basic is None or not basic.ca:
        raise TokenError(f"{label}: not a CA certificate (basic constraints)", "CERT")
    if basic.path_length is not None and below > basic.path_length:
        raise TokenError(f"{label}: path length constraint exceeded", "CERT")
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return
    if not usage.key_cert_sign:
        raise TokenError(f"{label}: key usage does not allow signing certificates", "CERT")


def _check_signer_certificate(cert: x509.Certificate) -> None:
    basic = _basic_constraints(cert)
    if basic is not None and basic.ca:
        raise TokenError("the signer certificate is a CA certificate", "CERT")
    try:
        eku = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
    except x509.ExtensionNotFound as exc:
        raise TokenError("the signer certificate has no extended key usage", "CERT") from exc
    if list(eku.value) != [ExtendedKeyUsageOID.TIME_STAMPING] or not eku.critical:
        raise TokenError("the signer certificate's extended key usage must be exactly critical time stamping", "CERT")
    try:
        usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    except x509.ExtensionNotFound:
        return
    if not (usage.digital_signature or usage.content_commitment):
        raise TokenError("the signer certificate's key usage does not allow signatures", "CERT")


def _check_key_strength(cert: x509.Certificate, label: str) -> None:
    public = cert.public_key()
    if isinstance(public, rsa.RSAPublicKey):
        if public.key_size < MIN_RSA_BITS:
            raise TokenError(f"{label}: RSA key shorter than {MIN_RSA_BITS} bits", "CERT")
    elif isinstance(public, ec.EllipticCurvePublicKey):
        if public.curve.name not in ALLOWED_CURVES:
            raise TokenError(f"{label}: elliptic curve {public.curve.name} is not allowed", "CERT")
    else:
        raise TokenError(f"{label}: public key type is not allowed", "CERT")


def _check_cert_signature_algorithm(cert: x509.Certificate, label: str) -> None:
    if cert.signature_algorithm_oid.dotted_string not in CERTIFICATE_SIGNATURE_OIDS:
        raise TokenError(f"{label}: weak or unknown certificate signature algorithm", "CERT")


def _elements(contents: bytes) -> list[bytes]:
    """The elements, each exactly as encoded, that make up the contents of a constructed value."""
    elements = []
    at = 0
    while at < len(contents):
        _class, _method, _tag, header, body, trailer = parser.parse(contents[at:])
        end = at + len(header) + len(body) + len(trailer)
        elements.append(contents[at:end])
        at = end
    return elements


def _load_certificates(signed: cms.SignedData) -> list[x509.Certificate]:
    """The certificates carried in the token that can be read, without exact duplicates.

    Certificates in a token are hints for building a path; only the pinned roots are trusted. One that cannot be read is
    left out (it cannot be part of a path), so damage to an unused certificate does not reject an otherwise sound token.
    A certificate is used exactly as the token carries it: the parser can re-encode a certificate it has read (a BOOLEAN of
    any non-zero value becomes 0xFF), and a certificate whose signature verifies only after such a repair is not the
    certificate that was signed.
    """
    certs: dict[str, x509.Certificate] = {}
    choices = signed["certificates"]
    if not choices:
        return []
    for choice, encoding in zip(choices, _elements(choices.contents), strict=True):
        if choice.name != "certificate":
            continue
        try:
            raw = encoding
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", CryptographyDeprecationWarning)  # non-positive serial numbers; refused below
                cert = x509.load_der_x509_certificate(raw)
                _ = (cert.subject, cert.issuer, cert.extensions)  # parsed lazily; one that is unreadable is unusable
                if cert.serial_number <= 0:  # RFC 5280 requires a positive serial number
                    continue
        except Exception:  # noqa: S112  (the library raises several unrelated types for unreadable certificates)
            continue
        certs.setdefault(_fingerprint(cert), cert)
    return list(certs.values())


def _select_signer(signer_info: cms.SignerInfo, certs: list[x509.Certificate]) -> x509.Certificate:
    sid = signer_info["sid"]
    if sid.name == "issuer_and_serial_number" and len(_elements(sid.chosen.contents)) != 2:
        raise TokenError("unparsable token: an issuer and serial number identifier has two parts", "PARSE")
    matches: list[x509.Certificate] = []
    for cert in certs:
        if sid.name == "issuer_and_serial_number":
            if cert.serial_number == sid.chosen["serial_number"].native and cert.issuer.public_bytes() == sid.chosen["issuer"].dump():
                matches.append(cert)
        else:
            try:
                digest = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
            except x509.ExtensionNotFound:
                continue
            if digest == sid.chosen.native:
                matches.append(cert)
    if len(matches) != 1:
        raise TokenError("the signer identifier does not select exactly one certificate in the token", "SIGNER_ID")
    return matches[0]


def _check_validity(cert: x509.Certificate, gen_time: datetime, label: str) -> None:
    if cert.not_valid_before_utc > gen_time or gen_time > cert.not_valid_after_utc:
        raise TokenError(f"{label}: not valid at the time stamp time", "CERT")


def _check_issuer(issuer: x509.Certificate, below: int, gen_time: datetime, label: str, *, anchor: bool) -> None:
    """Rules for a certificate that issues another. A pinned anchor is trusted by its fingerprint, so how it was signed
    and how long its key is are not judged; its role and validity at the time stamp time still are."""
    _check_extensions(issuer, label)
    _check_validity(issuer, gen_time, label)
    _check_ca(issuer, below, label)
    if not anchor:
        _check_key_strength(issuer, label)


def _build_path(
    signer: x509.Certificate, pool: list[x509.Certificate], pinned: dict[str, PinnedRoot], gen_time: datetime
) -> tuple[list[x509.Certificate], PinnedRoot]:
    """Find a certificate path from the signer to a pinned certificate in which every issuer is a valid CA.

    Every candidate issuer with the right subject is tried (cross-signed roots share a subject), and an issuer that breaks
    a rule is skipped, so a decoy in the token can at worst make verification fail, never make it succeed.
    """
    pinned_certs = [x509.load_der_x509_certificate(root.der) for root in pinned.values() if root.der is not None]
    candidates = {_fingerprint(c): c for c in [*pool, *pinned_certs]}
    failures: list[str] = []

    def extend(path: list[x509.Certificate]) -> list[x509.Certificate] | None:
        current = path[-1]
        if _fingerprint(current) in pinned:
            return path
        if len(path) >= MAX_PATH_LENGTH:
            failures.append("the certificate path is too long")
            return None
        try:
            _check_cert_signature_algorithm(current, f"certificate {len(path) - 1}")
        except TokenError as exc:
            failures.append(str(exc))
            return None
        seen = {_fingerprint(c) for c in path}
        for fp, issuer in candidates.items():
            if fp in seen or issuer.subject != current.issuer:
                continue
            try:
                current.verify_directly_issued_by(issuer)
            except (ValueError, TypeError, InvalidSignature, UnsupportedAlgorithm):
                failures.append("a certificate signature in the path does not verify")
                continue
            try:
                _check_issuer(issuer, len(path) - 1, gen_time, f"issuer {len(path)}", anchor=fp in pinned)
            except TokenError as exc:
                failures.append(str(exc))
                continue
            found = extend([*path, issuer])
            if found is not None:
                return found
        return None

    path = extend([signer])
    if path is None:
        detail = f" ({failures[-1]})" if failures else ""
        raise TokenError(f"no valid certificate path to a pinned root{detail}", "PATH")
    return path, pinned[_fingerprint(path[-1])]


def verify_token(token: bytes, digest: bytes, policy: TokenPolicy, *, nonce: int | None = None) -> VerifiedToken:
    """Verify a time-stamp response or bare token for a SHA-256 ``digest``. Raises ``TokenError``."""
    if len(token) > MAX_TOKEN_BYTES:
        raise TokenError("token too large", "SIZE")
    if len(digest) != 32:
        raise TokenError("the digest must be a SHA-256 value (32 bytes)", "DIGEST")
    try:
        return _verify(token, digest, policy, nonce)
    except TokenError:
        raise
    except Exception as exc:  # a parser bug or hostile structure is a rejection, never a crash
        raise TokenError(f"unparsable token: {type(exc).__name__}: {str(exc)[:100]}", "PARSE") from exc


def _content_info(token: bytes) -> cms.ContentInfo:
    try:
        response = tsp.TimeStampResp.load(token, strict=True)
        status = response["status"]["status"].native
        if status != "granted":
            raise TokenError(f"the authority answered {status}", "STATUS")
        return response["time_stamp_token"]
    except TokenError:
        raise
    except ValueError:
        return cms.ContentInfo.load(token, strict=True)  # a bare token (the bundle may store either form)


def _verify(token: bytes, digest: bytes, policy: TokenPolicy, nonce: int | None) -> VerifiedToken:
    _check_der(token, single=True)
    content = _content_info(token)
    if content["content_type"].native != "signed_data":
        raise TokenError("not CMS signed data", "CONTENT")
    signed = content["content"]
    encapsulated = signed["encap_content_info"]
    if encapsulated["content_type"].native != "tst_info":
        raise TokenError("the content is not TSTInfo", "CONTENT")
    raw_tst = encapsulated["content"].contents
    _check_der(raw_tst, single=True)
    tst = tsp.TSTInfo.load(raw_tst)
    if tst["version"].native != "v1":
        raise TokenError("TSTInfo version must be 1", "VERSION")
    imprint = tst["message_imprint"]
    if imprint["hash_algorithm"]["algorithm"].native != "sha256" or imprint["hashed_message"].native != digest:
        raise TokenError("the message imprint is not the SHA-256 of the committed digest", "IMPRINT")
    if nonce is not None and tst["nonce"].native != nonce:
        raise TokenError("the nonce does not match the request", "NONCE")
    if tst["extensions"].native:
        for extension in tst["extensions"]:
            if extension["critical"].native:
                raise TokenError("TSTInfo carries a critical extension", "EXTENSION")
    if not tst["gen_time"].contents.endswith(b"Z"):  # asn1crypto reads a zone-less time as naive, which astimezone() takes as local
        raise TokenError("the generation time is not stated in UTC", "TIME")
    gen_time = tst["gen_time"].native.astimezone(UTC)

    if len(signed["signer_infos"]) != 1:
        raise TokenError("exactly one signer is required", "SIGNER_COUNT")
    signer_info = signed["signer_infos"][0]
    digest_name = signer_info["digest_algorithm"]["algorithm"].native
    if digest_name not in HASHES:
        raise TokenError(f"CMS digest algorithm {digest_name} is not allowed", "ALGORITHM")
    if digest_name not in {a["algorithm"].native for a in signed["digest_algorithms"]}:
        raise TokenError("the signer's digest algorithm is not listed", "ALGORITHM")
    sid_is_issuer = signer_info["sid"].name == "issuer_and_serial_number"
    if signed["version"].native != "v3" or signer_info["version"].native != ("v1" if sid_is_issuer else "v3"):
        raise TokenError("inconsistent CMS versions", "VERSION")
    listed = [a["type"].dotted for a in signer_info["signed_attrs"]]
    if len(listed) != len(set(listed)):
        raise TokenError("a signed attribute appears more than once", "ATTRIBUTES")
    attributes = {a["type"].dotted: a for a in signer_info["signed_attrs"]}
    names = {a["type"].native for a in signer_info["signed_attrs"]}
    if "content_type" not in names:
        raise TokenError("the signed attributes lack the content type", "ATTRIBUTES")
    if "message_digest" not in names:
        raise TokenError("the signed attributes lack the message digest", "ATTRIBUTES")
    if next(a["values"][0].native for a in signer_info["signed_attrs"] if a["type"].native == "content_type") != "tst_info":
        raise TokenError("the content type attribute does not name TSTInfo", "ATTRIBUTES")
    message_digest = next(a["values"][0].native for a in signer_info["signed_attrs"] if a["type"].native == "message_digest")
    if message_digest != hashlib.new(digest_name, raw_tst).digest():
        raise TokenError("the message digest attribute does not match TSTInfo", "ATTRIBUTES")

    certs = _load_certificates(signed)
    signer = _select_signer(signer_info, certs)
    _verify_signature(
        signer,
        signer_info["signature"].native,
        signer_info["signed_attrs"].untag().dump(),
        signer_info["signature_algorithm"],
        digest_name,
    )
    _check_ess(attributes, signer)

    pinned = policy.by_fingerprint()
    if not pinned:
        raise TokenError("no pinned root is configured", "NO_ROOT")
    path, root = _build_path(signer, certs, pinned, gen_time)
    _check_extensions(signer, "signer")
    _check_validity(signer, gen_time, "signer")
    _check_signer_certificate(signer)
    if _fingerprint(signer) not in pinned:
        _check_key_strength(signer, "signer")
    accuracy = _accuracy(tst)
    return VerifiedToken(
        gen_time=gen_time,
        policy_oid=tst["policy"].dotted,
        serial=int(tst["serial_number"].native),
        nonce=tst["nonce"].native,
        accuracy=accuracy,
        root_fingerprint=root.fingerprint,
        signer_fingerprint=_fingerprint(signer),
        operator=root.operator,
        path_fingerprints=tuple(_fingerprint(c) for c in path),
    )


def _check_ess(attributes: dict[str, cms.CMSAttribute], signer: x509.Certificate) -> None:
    if OID_ESS_SIGNING_CERT_V2 in attributes:
        first = attributes[OID_ESS_SIGNING_CERT_V2]["values"][0]["certs"][0]
        algorithm = first["hash_algorithm"]["algorithm"].native
        if algorithm not in HASHES or hashlib.new(algorithm, _der(signer)).digest() != first["cert_hash"].native:
            raise TokenError("the ESS signing certificate attribute does not match the signer certificate", "ESS")
    elif OID_ESS_SIGNING_CERT_V1 in attributes:
        first = attributes[OID_ESS_SIGNING_CERT_V1]["values"][0]["certs"][0]
        if hashlib.sha1(_der(signer)).digest() != first["cert_hash"].native:  # noqa: S324  (binding only; the signature is SHA-2)
            raise TokenError("the ESS signing certificate attribute does not match the signer certificate", "ESS")
    else:
        raise TokenError("the signed attributes lack the ESS signing certificate attribute", "ESS")


def _accuracy(tst: tsp.TSTInfo) -> dict[str, int | None] | None:
    accuracy = tst["accuracy"]
    if isinstance(accuracy, core.Void) or accuracy.native is None:
        return None
    native = accuracy.native
    return {"seconds": native.get("seconds"), "millis": native.get("millis"), "micros": native.get("micros")}
