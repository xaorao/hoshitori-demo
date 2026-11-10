"""A small certificate authority and RFC 3161 token builder for tests.

Real authorities answer with the current time and cannot be told to misbehave, so the rules of ``hoshitori.tsa`` are
tested against tokens built here: every field a rule looks at can be set to a wrong value, and times are explicit.
Nothing in this module is used by the product.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache

from asn1crypto import cms, core, tsp
from asn1crypto import x509 as ax509
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID, ObjectIdentifier

BASE = datetime(2026, 10, 1, 12, 0, 0, tzinfo=UTC)
YEAR = timedelta(days=365)


@dataclass
class Authority:
    """A certificate and its private key."""

    cert: x509.Certificate
    key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey

    @property
    def der(self) -> bytes:
        return self.cert.public_bytes(Encoding.DER)

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.der).hexdigest()


@cache
def rsa_key(bits: int) -> rsa.RSAPrivateKey:
    """One RSA key per size for the whole test run (generation is the slow part)."""
    return rsa.generate_private_key(65537, bits)


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def make_cert(
    cn: str,
    issuer: Authority | None,
    *,
    key: ec.EllipticCurvePrivateKey | rsa.RSAPrivateKey | ed25519.Ed25519PrivateKey | None = None,
    not_before: datetime = BASE - YEAR,
    not_after: datetime = BASE + YEAR,
    ca: bool | None = False,
    path_length: int | None = None,
    key_cert_sign: bool | None = None,
    digital_signature: bool | None = None,
    eku: list[ObjectIdentifier] | None = None,
    eku_critical: bool = True,
    unknown_critical: bool = False,
    sha1_signature: bool = False,
    rsa_pss: bool = False,
    content_commitment: bool = False,
    issuer_name: str | None = None,
    signature_hash: hashes.HashAlgorithm | None = None,
    serial: int | None = None,
    raw_serial: int | None = None,
) -> Authority:
    """Issue a certificate. ``issuer=None`` makes a self-signed one. ``ca=None`` omits basic constraints."""
    key = key or new_key()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])
    signing = issuer.key if issuer else key
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(
            x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, issuer_name)]) if issuer_name else (issuer.cert.subject if issuer else name)
        )
        .public_key(key.public_key())
        .serial_number(serial or x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    )
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=path_length), critical=True)
    if key_cert_sign is not None or digital_signature is not None or content_commitment:
        builder = builder.add_extension(
            x509.KeyUsage(
                digital_signature=bool(digital_signature),
                content_commitment=content_commitment,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=bool(key_cert_sign),
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
    if eku is not None:
        builder = builder.add_extension(x509.ExtendedKeyUsage(eku), critical=eku_critical)
    if unknown_critical:
        builder = builder.add_extension(x509.UnrecognizedExtension(ObjectIdentifier("1.3.6.1.4.1.99999.1"), b"\x05\x00"), critical=True)
    algorithm = None if isinstance(signing, ed25519.Ed25519PrivateKey) else (signature_hash or hashes.SHA256())
    if rsa_pss and isinstance(signing, rsa.RSAPrivateKey):
        cert = builder.sign(signing, algorithm, rsa_padding=padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32))
    else:
        cert = builder.sign(signing, algorithm)
    if sha1_signature:
        cert = _resign_with_sha1(cert, signing)
    if raw_serial is not None:
        cert = _resign_with_serial(cert, signing, raw_serial)
    return Authority(cert, key)


def _resign_with_serial(cert: x509.Certificate, signing_key: object, serial: int) -> x509.Certificate:
    """The same certificate with a serial number the library will not issue (zero or negative), correctly signed."""
    if not isinstance(signing_key, ec.EllipticCurvePrivateKey):
        raise TypeError("only EC issuers can be re-signed here")
    parsed = ax509.Certificate.load(cert.public_bytes(Encoding.DER))
    tbs = parsed["tbs_certificate"]
    tbs["serial_number"] = serial
    signature = signing_key.sign(tbs.dump(force=True), ec.ECDSA(hashes.SHA256()))
    rebuilt = ax509.Certificate(
        {"tbs_certificate": tbs, "signature_algorithm": parsed["signature_algorithm"], "signature_value": signature}
    )
    return x509.load_der_x509_certificate(rebuilt.dump())


def _resign_with_sha1(cert: x509.Certificate, signing_key: object) -> x509.Certificate:
    """The same certificate signed with ECDSA and SHA-1 (the library will not sign with SHA-1, so the structure is rewritten)."""
    if not isinstance(signing_key, ec.EllipticCurvePrivateKey):
        raise TypeError("only EC issuers can be re-signed here")
    parsed = ax509.Certificate.load(cert.public_bytes(Encoding.DER))
    tbs = parsed["tbs_certificate"]
    tbs["signature"] = ax509.SignedDigestAlgorithm({"algorithm": "sha1_ecdsa"})
    signature = signing_key.sign(tbs.dump(force=True), ec.ECDSA(hashes.SHA1()))  # noqa: S303
    rebuilt = ax509.Certificate({"tbs_certificate": tbs, "signature_algorithm": {"algorithm": "sha1_ecdsa"}, "signature_value": signature})
    return x509.load_der_x509_certificate(rebuilt.dump())


def make_root(cn: str = "Test Root") -> Authority:
    return make_cert(cn, None, ca=True, key_cert_sign=True)


def make_intermediate(issuer: Authority, cn: str = "Test Issuing CA", **kwargs: object) -> Authority:
    options: dict[str, object] = {"ca": True, "key_cert_sign": True}
    options.update(kwargs)
    return make_cert(cn, issuer, **options)  # type: ignore[arg-type]


def make_tsa(issuer: Authority, cn: str = "Test TSA", **kwargs: object) -> Authority:
    options: dict[str, object] = {"ca": False, "digital_signature": True, "eku": [ExtendedKeyUsageOID.TIME_STAMPING]}
    options.update(kwargs)
    return make_cert(cn, issuer, **options)  # type: ignore[arg-type]


@dataclass
class Chain:
    """root -> issuing CA -> time-stamping certificate, the usual public shape."""

    root: Authority
    intermediate: Authority
    tsa: Authority

    @classmethod
    def build(cls, name: str, **tsa_options: object) -> Chain:
        root = make_root(f"{name} Root")
        intermediate = make_intermediate(root, f"{name} Issuing CA")
        return cls(root, intermediate, make_tsa(intermediate, f"{name} TSA", **tsa_options))

    def certificates(self) -> list[Authority]:
        return [self.tsa, self.intermediate, self.root]


@dataclass
class TokenOptions:
    gen_time: datetime = BASE
    policy: str = "1.2.3.4.1"
    serial: int = 1
    nonce: int | None = 12345
    imprint_algorithm: str = "sha256"
    imprint: bytes | None = None  # overrides the digest in the token
    version: str = "v1"
    accuracy_seconds: int | None = 1
    signer: Authority | None = None
    certificates: list[Authority] = field(default_factory=list)
    include_ess: str | None = "v2"  # "v2", "v1", or None
    ess_cert: Authority | None = None  # the certificate the ESS attribute names (default: the signer)
    digest_algorithm: str = "sha256"
    signature_algorithm: str = "sha256_ecdsa"
    sid: str = "issuer_and_serial"  # or "subject_key_identifier"
    tamper_message_digest: bool = False
    tamper_signature: bool = False
    status: str = "granted"
    bare: bool = False
    critical_tst_extension: bool = False
    second_signer: bool = False
    drop_signed_attrs: tuple[str, ...] = ()
    cms_version: str = "v3"
    raw_signature: bytes | None = None  # used instead of signing (for signers whose key type the builder cannot sign with)
    gen_time_without_zone: bool = False
    listed_digest_algorithms: tuple[str, ...] | None = None
    duplicate_message_digest: bool = False
    content_type_attribute: str = "tst_info"


def _resize_sequence(der: bytes, delta: int) -> bytes:
    """Change the length of the outer SEQUENCE of ``der`` by ``delta`` (the content has already been edited)."""
    if der[0] != 0x30:
        raise ValueError("not a SEQUENCE")
    first = der[1]
    if first < 0x80:
        length, header = first, 2
    else:
        count = first & 0x7F
        length, header = int.from_bytes(der[2 : 2 + count], "big"), 2 + count
    length += delta
    if length < 0x80:
        return bytes([0x30, length]) + der[header:]
    raw = length.to_bytes((length.bit_length() + 7) // 8, "big")
    return bytes([0x30, 0x80 | len(raw)]) + raw + der[header:]


def _drop_zone_designator(tst_der: bytes, gen_time: datetime) -> bytes:
    stamp = gen_time.strftime("%Y%m%d%H%M%S").encode()
    marker = b"\x18\x0f" + stamp + b"Z"
    if marker not in tst_der:
        raise ValueError("generalized time not found")
    return _resize_sequence(tst_der.replace(marker, b"\x18\x0e" + stamp), -1)


def build_token(digest: bytes, chain: Chain, options: TokenOptions | None = None) -> bytes:
    opts = options or TokenOptions()
    signer = opts.signer or chain.tsa
    certificates = opts.certificates or chain.certificates()

    info: dict[str, object] = {
        "version": opts.version,
        "policy": opts.policy,
        "message_imprint": {"hash_algorithm": {"algorithm": opts.imprint_algorithm}, "hashed_message": opts.imprint or digest},
        "serial_number": opts.serial,
        "gen_time": opts.gen_time,
    }
    if opts.accuracy_seconds is not None:
        info["accuracy"] = {"seconds": opts.accuracy_seconds}
    if opts.nonce is not None:
        info["nonce"] = opts.nonce
    if opts.critical_tst_extension:
        info["extensions"] = [{"extn_id": "1.3.6.1.4.1.99999.2", "critical": True, "extn_value": b"\x05\x00"}]
    tst = tsp.TSTInfo(info)
    tst_der = tst.dump()
    if opts.gen_time_without_zone:
        tst_der = _drop_zone_designator(tst_der, opts.gen_time)

    attributes = [
        cms.CMSAttribute({"type": "content_type", "values": [opts.content_type_attribute]}),
        cms.CMSAttribute(
            {
                "type": "message_digest",
                "values": [hashlib.new(opts.digest_algorithm, tst_der + (b"x" if opts.tamper_message_digest else b"")).digest()],
            }
        ),
    ]
    named = opts.ess_cert or signer
    named_der = named.der
    if opts.include_ess == "v2":
        attributes.append(
            cms.CMSAttribute(
                {
                    "type": "signing_certificate_v2",
                    "values": [{"certs": [{"hash_algorithm": {"algorithm": "sha256"}, "cert_hash": hashlib.sha256(named_der).digest()}]}],
                }
            )
        )
    elif opts.include_ess == "v1":
        attributes.append(
            cms.CMSAttribute({"type": "signing_certificate", "values": [{"certs": [{"cert_hash": hashlib.sha1(named_der).digest()}]}]})  # noqa: S324
        )
    attributes = [a for a in attributes if a["type"].native not in opts.drop_signed_attrs]
    if opts.duplicate_message_digest:
        attributes.append(attributes[1])
    signed_attrs = cms.CMSAttributes(attributes)

    hash_algorithm = {"sha256": hashes.SHA256(), "sha384": hashes.SHA384(), "sha512": hashes.SHA512(), "sha1": hashes.SHA1()}[  # noqa: S303
        opts.digest_algorithm
    ]
    if opts.raw_signature is not None:
        signature = opts.raw_signature
    elif isinstance(signer.key, ec.EllipticCurvePrivateKey):
        signature = signer.key.sign(signed_attrs.dump(), ec.ECDSA(hash_algorithm))
    elif isinstance(signer.key, rsa.RSAPrivateKey):
        signature = signer.key.sign(signed_attrs.dump(), padding.PKCS1v15(), hash_algorithm)
    else:
        raise TypeError("the test builder signs tokens with EC or RSA keys")
    if opts.tamper_signature:
        signature = signature[:-1] + bytes([signature[-1] ^ 1])

    signer_asn = ax509.Certificate.load(signer.der)
    if opts.sid == "issuer_and_serial":
        sid = cms.SignerIdentifier({"issuer_and_serial_number": {"issuer": signer_asn.issuer, "serial_number": signer_asn.serial_number}})
        signer_version = "v1"
    else:
        ski = x509.SubjectKeyIdentifier.from_public_key(signer.key.public_key()).digest  # type: ignore[arg-type]
        sid = cms.SignerIdentifier({"subject_key_identifier": ski})
        signer_version = "v3"
    signer_info = cms.SignerInfo(
        {
            "version": signer_version,
            "sid": sid,
            "digest_algorithm": {"algorithm": opts.digest_algorithm},
            "signed_attrs": signed_attrs,
            "signature_algorithm": {"algorithm": opts.signature_algorithm},
            "signature": signature,
        }
    )
    signer_infos = [signer_info, signer_info] if opts.second_signer else [signer_info]
    signed_data = cms.SignedData(
        {
            "version": opts.cms_version,
            "digest_algorithms": [{"algorithm": name} for name in (opts.listed_digest_algorithms or (opts.digest_algorithm,))],
            "encap_content_info": {"content_type": "tst_info", "content": core.ParsableOctetString(tst_der)},
            "certificates": [cms.CertificateChoices({"certificate": ax509.Certificate.load(c.der)}) for c in certificates],
            "signer_infos": signer_infos,
        }
    )
    content_info = cms.ContentInfo({"content_type": "signed_data", "content": signed_data})
    if opts.bare:
        return bytes(content_info.dump())
    response = tsp.TimeStampResp({"status": {"status": opts.status}, "time_stamp_token": content_info})
    return bytes(response.dump())
