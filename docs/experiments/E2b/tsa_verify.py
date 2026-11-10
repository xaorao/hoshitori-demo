"""Experiment E2b, verifier B: an RFC 3161 time-stamp token verifier written with asn1crypto and cryptography only
(no OpenSSL), so that its verdicts can be compared with `openssl ts -verify`.

verify_token(blob, digest, nonce, pinned_roots) returns {"ok": bool, "checks": {...}, "error"?: str}.
Checks follow RFC 3161 section 2.4.2 and RFC 5816, plus the profile in contract section 8:
  status granted; content type TSTInfo; version 1; message imprint equals the digest; nonce equals the request nonce;
  signed attributes carry content-type and message-digest and the digest matches the TSTInfo bytes; the CMS signature
  verifies with the signer certificate; the signing-certificate attribute (ESS, RFC 2634 / 5035) matches that
  certificate; the signer certificate's EKU is exactly id-kp-timeStamping and critical; every certificate of the path
  was valid at genTime; every signature on the path verifies up to a root pinned by SHA-256 fingerprint of its DER.
No revocation check is made here (see the experiment report). Any parse failure is a rejection, never an exception.
"""
from __future__ import annotations

import hashlib
from datetime import timezone

from asn1crypto import cms, tsp
from cryptography import x509
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
from cryptography.hazmat.primitives.serialization import Encoding
from cryptography.x509.oid import ExtendedKeyUsageOID

HASHES = {"sha1": hashes.SHA1, "sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}
OID_ESS_V1 = "1.2.840.113549.1.9.16.2.12"
OID_ESS_V2 = "1.2.840.113549.1.9.16.2.47"


def _der(c: x509.Certificate) -> bytes:
    return c.public_bytes(Encoding.DER)


ALLOWED_SIG = {"rsassa_pkcs1v15", "rsassa_pss", "sha256_rsa", "sha384_rsa", "sha512_rsa", "sha256_ecdsa", "sha384_ecdsa", "sha512_ecdsa"}


def _verify_sig(cert: x509.Certificate, sig: bytes, data: bytes, sig_alg, digest_name: str) -> bool:
    pub = cert.public_key()
    if sig_alg["algorithm"].native not in ALLOWED_SIG or digest_name not in ("sha256", "sha384", "sha512"):
        return False  # an unrecognised or weak algorithm identifier is a rejection, not a default
    try:
        if isinstance(pub, rsa.RSAPublicKey):
            if sig_alg["algorithm"].native == "rsassa_pss":
                p = sig_alg["parameters"]
                pub.verify(sig, data, padding.PSS(mgf=padding.MGF1(HASHES[p["mask_gen_algorithm"]["parameters"]["algorithm"].native]()), salt_length=p["salt_length"].native), HASHES[p["hash_algorithm"]["algorithm"].native]())
            else:
                pub.verify(sig, data, padding.PKCS1v15(), HASHES[digest_name]())
            return True
        if isinstance(pub, ec.EllipticCurvePublicKey):
            pub.verify(sig, data, ec.ECDSA(HASHES[digest_name]()))
            return True
    except (InvalidSignature, KeyError, ValueError):
        return False
    return False


def verify_token(blob: bytes, digest: bytes, nonce: int | None, pinned_roots: set[str], hash_name: str = "sha256") -> dict:
    checks: dict[str, object] = {}
    try:
        if len(blob) > 1_000_000:
            return {"ok": False, "checks": checks, "error": "token too large"}
        try:
            resp = tsp.TimeStampResp.load(blob, strict=True)
            status = resp["status"]["status"].native
            checks["status"] = status
            if status != "granted":
                return {"ok": False, "checks": checks, "error": "status " + str(status)}
            token = resp["time_stamp_token"]
        except ValueError as e:
            if "trailing" in str(e).lower() or "extra" in str(e).lower():
                return {"ok": False, "checks": checks, "error": "trailing data"}
            token = cms.ContentInfo.load(blob, strict=True)
            checks["status"] = "bare token"
        except Exception:
            token = cms.ContentInfo.load(blob, strict=True)
            checks["status"] = "bare token"
        if token["content_type"].native != "signed_data":
            return {"ok": False, "checks": checks, "error": "not signed data"}
        sd = token["content"]
        eci = sd["encap_content_info"]
        if eci["content_type"].native != "tst_info":
            return {"ok": False, "checks": checks, "error": "content is not TSTInfo"}
        raw_tst = eci["content"].contents
        tst = tsp.TSTInfo.load(raw_tst)
        checks["version"] = tst["version"].native
        mi = tst["message_imprint"]
        checks["imprint_alg"] = mi["hash_algorithm"]["algorithm"].native
        checks["imprint_matches"] = mi["hashed_message"].native == digest and checks["imprint_alg"] == hash_name
        checks["nonce_matches"] = nonce is None or tst["nonce"].native == nonce
        gen = tst["gen_time"].native
        checks["gen_time"] = gen.astimezone(timezone.utc).isoformat()
        checks["accuracy"] = tst["accuracy"].native if tst["accuracy"].native is not None else None
        checks["policy"] = tst["policy"].dotted
        checks["ordering"] = tst["ordering"].native
        if len(sd["signer_infos"]) != 1:
            return {"ok": False, "checks": checks, "error": "exactly one signer is required"}
        si = sd["signer_infos"][0]
        digest_name = si["digest_algorithm"]["algorithm"].native
        checks["cms_digest_alg"] = digest_name
        checks["cms_versions_consistent"] = sd["version"].native == "v3" and si["version"].native == ("v1" if si["sid"].name == "issuer_and_serial_number" else "v3")
        checks["digest_algorithms_listed"] = digest_name in {a["algorithm"].native for a in sd["digest_algorithms"]}
        attrs = {a["type"].dotted: a for a in si["signed_attrs"]}
        names = {a["type"].native for a in si["signed_attrs"]}
        checks["signed_attrs_content_type"] = "content_type" in names
        md = None
        for a in si["signed_attrs"]:
            if a["type"].native == "message_digest":
                md = a["values"][0].native
        checks["message_digest_matches"] = md == hashlib.new(digest_name, raw_tst).digest()
        certs = [x509.load_der_x509_certificate(c.chosen.dump()) for c in sd["certificates"] if c.name == "certificate"]
        checks["certificates_in_token"] = len(certs)
        sid = si["sid"]
        signer = None
        for c in certs:
            if sid.name == "issuer_and_serial_number":
                if c.serial_number == sid.chosen["serial_number"].native and c.issuer.public_bytes() == sid.chosen["issuer"].dump():
                    signer = c
            else:
                try:
                    if c.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest == sid.chosen.native:
                        signer = c
                except x509.ExtensionNotFound:
                    pass
        if signer is None:
            return {"ok": False, "checks": checks, "error": "signer certificate not in token"}
        checks["cms_signature_valid"] = _verify_sig(signer, si["signature"].native, si["signed_attrs"].untag().dump(), si["signature_algorithm"], digest_name)
        ess_ok = False
        if OID_ESS_V2 in attrs:
            first = attrs[OID_ESS_V2]["values"][0]["certs"][0]
            ess_ok = hashlib.new(first["hash_algorithm"]["algorithm"].native, _der(signer)).digest() == first["cert_hash"].native
        elif OID_ESS_V1 in attrs:
            first = attrs[OID_ESS_V1]["values"][0]["certs"][0]
            ess_ok = hashlib.sha1(_der(signer)).digest() == first["cert_hash"].native
        checks["ess_signing_certificate_matches"] = ess_ok
        try:
            eku = signer.extensions.get_extension_for_class(x509.ExtendedKeyUsage)
            checks["eku_only_timestamping"] = list(eku.value) == [ExtendedKeyUsageOID.TIME_STAMPING]
            checks["eku_critical"] = eku.critical
        except x509.ExtensionNotFound:
            checks["eku_only_timestamping"] = checks["eku_critical"] = False
        pool = {c.subject.public_bytes(): c for c in certs}
        path, cur, reached = [signer], signer, False
        for _ in range(6):
            if hashlib.sha256(_der(cur)).hexdigest() in pinned_roots:
                reached = True
                break
            if cur.issuer == cur.subject:
                break  # self-issued and not pinned: the path ends without a trusted root
            issuer = pool.get(cur.issuer.public_bytes())
            if issuer is None:
                break
            try:
                cur.verify_directly_issued_by(issuer)
            except Exception:
                return {"ok": False, "checks": checks, "error": "certificate signature does not verify"}
            path.append(issuer)
            cur = issuer
        checks["signer_fp"] = hashlib.sha256(_der(signer)).hexdigest()
        checks["root_fp"] = hashlib.sha256(_der(cur)).hexdigest() if reached else None
        checks["path_reaches_pinned_root"] = reached
        checks["path_length"] = len(path)
        checks["path_valid_at_gentime"] = all(c.not_valid_before_utc <= gen <= c.not_valid_after_utc for c in path)
        keys = ["imprint_matches", "nonce_matches", "message_digest_matches", "signed_attrs_content_type", "cms_signature_valid", "ess_signing_certificate_matches",
                "eku_only_timestamping", "eku_critical", "path_reaches_pinned_root", "path_valid_at_gentime", "cms_versions_consistent", "digest_algorithms_listed"]
        ok = all(bool(checks[k]) for k in keys) and checks["version"] == "v1"
        return {"ok": ok, "checks": checks}
    except Exception as e:
        return {"ok": False, "checks": checks, "error": "parse/verify exception: " + type(e).__name__ + ": " + str(e)[:100]}
