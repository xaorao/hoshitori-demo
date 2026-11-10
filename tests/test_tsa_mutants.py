"""Each rule of the token verifier is protected by a scenario that fails when the rule is broken.

Redundant guards that have no mutant (stated, not hidden):
* the explicit digest-length check in ``verify_token`` (the imprint comparison would also fail for a short digest);
* the ``TokenError`` re-raise ahead of the catch-all (a pass-through);
* the final ``else`` of ``_check_key_strength`` (a public key that is neither RSA nor EC: the certificate signed by such a key
  already fails the signature-algorithm policy, and a certificate it issued cannot be verified, before the strength check is reached);
* the signer-is-pinned condition before the signer's key and signature-hash checks (a pinned signer is exempt by design,
  and the scenario that pins the signer would fail without the exemption only for the SHA-224 case, which is not built).
"""

from __future__ import annotations

import pytest

from hoshitori import tsa
from tests.mutation import mutant
from tests.tsa_checks import SCENARIOS, failures

MUTATIONS = {
    # token content
    "the imprint is not compared": ('or imprint["hashed_message"].native != digest', "or False"),
    "the imprint algorithm is not checked": ('if imprint["hash_algorithm"]["algorithm"].native != "sha256" or', "if"),
    "the nonce is not checked": ('if nonce is not None and tst["nonce"].native != nonce:', "if False:"),
    "a missing nonce is tolerated when one was requested": (
        'if nonce is not None and tst["nonce"].native != nonce:',
        'if nonce is not None and tst["nonce"].native not in (nonce, None):',
    ),
    "the authority status is not checked": ('if status != "granted":', "if False:"),
    "a critical TSTInfo extension is tolerated": ('if extension["critical"].native:', "if False:"),
    "a time without a zone designator is accepted": ('if not tst["gen_time"].contents.endswith(b"Z"):', "if False:"),
    "several signers are accepted": ('if len(signed["signer_infos"]) != 1:', 'if len(signed["signer_infos"]) < 1:'),
    "a SHA-1 CMS digest is accepted": (
        'if digest_name not in HASHES:\n        raise TokenError(f"CMS digest',
        'if False:\n        raise TokenError(f"CMS digest',
    ),
    "the digest algorithm need not be listed": (
        'if digest_name not in {a["algorithm"].native for a in signed["digest_algorithms"]}:',
        "if False:",
    ),
    "CMS versions are not checked": ('if signed["version"].native != "v3" or', "if False and"),
    "a duplicate signed attribute is tolerated": ("if len(listed) != len(set(listed)):", "if False:"),
    "the content type attribute is missing without notice": ('if "content_type" not in names:', "if False:"),
    "the message digest attribute may be missing": ('if "message_digest" not in names:', "if False:"),
    "the content type attribute value is not checked": (
        'if next(a["values"][0].native for a in signer_info["signed_attrs"] if a["type"].native == "content_type") != "tst_info":',
        "if False:",
    ),
    "the message digest attribute is not compared": ("if message_digest != hashlib.new(digest_name, raw_tst).digest():", "if False:"),
    # signature and signer binding
    "the signature is not verified (EC)": ("public.verify(signature, data, ec.ECDSA(HASHES[digest_name]()))", "pass"),
    "the signature is not verified (RSA)": ("public.verify(signature, data, padding.PKCS1v15(), HASHES[digest_name]())", "pass"),
    "an RSA key may use any algorithm identifier": ('            if family != "rsa":', "            if False:"),
    "an EC key may use any algorithm identifier": ('            if family != "ec":', "            if False:"),
    "any signature algorithm identifier is accepted": (
        '    if entry is None:\n        raise TokenError(f"signature algorithm {identifier} is not allowed", "ALGORITHM")',
        '    if entry is None:\n        entry = ("rsa", None)',
    ),
    "the signature algorithm may name another hash": ("if named_hash is not None and named_hash != digest_name:", "if False:"),
    "ESS binding is not checked (v2)": (
        'if algorithm not in HASHES or hashlib.new(algorithm, _der(signer)).digest() != first["cert_hash"].native:',
        "if False:",
    ),
    "ESS binding is not checked (v1)": ('if hashlib.sha1(_der(signer)).digest() != first["cert_hash"].native:', "if False:"),
    "a missing ESS attribute is tolerated": (
        '    else:\n        raise TokenError("the signed attributes lack the ESS signing certificate attribute", "ESS")',
        "    else:\n        pass",
    ),
    "the signer need not be unique": ("if len(matches) != 1:", "if len(matches) < 1:"),
    "the signer is chosen by serial number alone": (
        'cert.serial_number == sid.chosen["serial_number"].native and cert.issuer.public_bytes() == sid.chosen["issuer"].dump()',
        'cert.serial_number == sid.chosen["serial_number"].native',
    ),
    # certificate path
    "no pinned root is accepted as an empty set": ("if not pinned:\n        raise TokenError", "if False:\n        raise TokenError"),
    "the path need not end at a pinned certificate": (
        "if _fingerprint(current) in pinned:\n            return path",
        "if _fingerprint(current) in pinned or current.issuer == current.subject:\n            return path",
    ),
    "issuer signatures are not verified": ("current.verify_directly_issued_by(issuer)", "pass"),
    "issuers need not be CAs": ("    _check_ca(issuer, below, label)\n", "    pass\n"),
    "the CA flag is not required": (
        'if basic is None or not basic.ca:\n        raise TokenError(f"{label}: not a CA',
        'if False:\n        raise TokenError(f"{label}: not a CA',
    ),
    "path length is ignored": ("if basic.path_length is not None and below > basic.path_length:", "if False:"),
    "key usage of an issuer is ignored": ("    if not usage.key_cert_sign:", "    if False:"),
    "unknown critical extensions are tolerated": ("if ext.critical and ext.oid not in HANDLED_CRITICAL:", "if False:"),
    "certificate validity is not checked": ("if cert.not_valid_before_utc > gen_time or gen_time > cert.not_valid_after_utc:", "if False:"),
    "validity is checked only for expiry": (
        "if cert.not_valid_before_utc > gen_time or gen_time > cert.not_valid_after_utc:",
        "if gen_time > cert.not_valid_after_utc:",
    ),
    "an issuer's key strength is not checked": (
        "    if not anchor:\n        _check_key_strength(issuer, label)",
        "    if not anchor:\n        pass",
    ),
    "a certificate's signature algorithm is not checked": (
        '            _check_cert_signature_algorithm(current, f"certificate {len(path) - 1}")',
        "            pass",
    ),
    "any elliptic curve is allowed": ("if public.curve.name not in ALLOWED_CURVES:", "if False:"),
    "short RSA keys are allowed": ("if public.key_size < MIN_RSA_BITS:", "if False:"),
    "the path length is unbounded": ("if len(path) >= MAX_PATH_LENGTH:", "if False:"),
    "only the first candidate issuer is tried": (
        "            found = extend([*path, issuer])\n            if found is not None:\n                return found\n",
        "            return extend([*path, issuer])\n",
    ),
    "anchors are also judged for strength": (
        "    if not anchor:\n        _check_key_strength(issuer, label)",
        "    if True:\n        _check_key_strength(issuer, label)",
    ),
    # the signer certificate
    "the signer may be a CA": (
        'if basic is not None and basic.ca:\n        raise TokenError("the signer certificate is a CA',
        'if False:\n        raise TokenError("the signer certificate is a CA',
    ),
    "the signer needs no extended key usage": (
        '    except x509.ExtensionNotFound as exc:\n        raise TokenError("the signer certificate has no extended key usage", "CERT") from exc',
        "    except x509.ExtensionNotFound:\n        return",
    ),
    "the signer's usage may be any": (
        "if list(eku.value) != [ExtendedKeyUsageOID.TIME_STAMPING] or not eku.critical:",
        "if ExtendedKeyUsageOID.TIME_STAMPING not in list(eku.value):",
    ),
    "the signer's usage need not be critical": (
        "if list(eku.value) != [ExtendedKeyUsageOID.TIME_STAMPING] or not eku.critical:",
        "if list(eku.value) != [ExtendedKeyUsageOID.TIME_STAMPING]:",
    ),
    "the signer's key usage is ignored": ("if not (usage.digital_signature or usage.content_commitment):", "if False:"),
    "the signer's own critical extensions are tolerated": ('_check_extensions(signer, "signer")', "pass"),
    "the signer's validity is not checked": ('_check_validity(signer, gen_time, "signer")', "pass"),
    "the signer's key strength is not checked": ('        _check_key_strength(signer, "signer")', "        pass"),
    "the signer certificate checks are skipped": ("    _check_signer_certificate(signer)\n    if _fingerprint", "    if _fingerprint"),
    # certificates carried in the token
    "duplicate certificates are ambiguous": (
        "certs.setdefault(_fingerprint(cert), cert)",
        "certs[_fingerprint(cert) + str(len(certs))] = cert",
    ),
    "a damaged certificate rejects the token": (
        "        except Exception:  # noqa: S112  (the library raises several unrelated types for unreadable certificates)\n            continue\n        certs.setdefault",
        '        except Exception as exc:  # noqa: BLE001\n            raise TokenError("a certificate in the token cannot be parsed", "PARSE") from exc\n        certs.setdefault',
    ),
    "a certificate with a serial number that is not positive is used": (
        "                if cert.serial_number <= 0:",
        "                if False:",
    ),
    "a certificate is read from the parser's re-encoding": ("            raw = encoding\n", "            raw = choice.chosen.dump()\n"),
    "a signer identifier with a third part is read": (
        '    if sid.name == "issuer_and_serial_number" and len(_elements(sid.chosen.contents)) != 2:',
        "    if False:",
    ),
    # structure of the token
    "indefinite lengths are accepted": (
        '        if first == 0x80:\n            raise TokenError(prefix + "indefinite length", "PARSE")',
        '        if False:\n            raise TokenError(prefix + "indefinite length", "PARSE")',
    ),
    "padded lengths are accepted": (
        '            if length < 0x80:\n                raise TokenError(prefix + "length is not minimal", "PARSE")',
        '            if False:\n                raise TokenError(prefix + "length is not minimal", "PARSE")',
    ),
    "lengths with a leading zero are accepted": ("or data[offset + 2] == 0:", ":"),
    "multi-byte tags are accepted": ("        if tag & 0x1F == 0x1F:", "        if False:"),
    "nesting is unbounded": ("    if depth > MAX_DER_DEPTH:", "    if False:"),
    "several elements are accepted as one token": ("    if single and count_elements != 1:", "    if False:"),
    "the token structure is not walked": (
        "    _check_der(token, single=True)\n    content = _content_info(token)",
        "    content = _content_info(token)",
    ),
    "the TSTInfo structure is not walked": ("    _check_der(raw_tst, single=True)\n", ""),
    "other content types are accepted": ('    if content["content_type"].native != "signed_data":', "    if False:"),
    "other encapsulated types are accepted": ('    if encapsulated["content_type"].native != "tst_info":', "    if False:"),
    "the TSTInfo version is ignored": ('    if tst["version"].native != "v1":', "    if False:"),
    "any key type may sign": (
        '        else:\n            raise TokenError("the signer key type is not allowed", "ALGORITHM")',
        "        else:\n            pass",
    ),
    "an unreadable signer key is called a signature failure": (
        '        raise TokenError("the signer key could not be read", "ALGORITHM") from exc',
        '        raise TokenError("the signer key could not be read", "SIGNATURE") from exc',
    ),
    # pins
    "a pin may be any string": (
        'if len(self.sha256) != 64 or self.sha256 != self.sha256.lower() or any(c not in "0123456789abcdef" for c in self.sha256):',
        "if False:",
    ),
    "a certificate need not match its pin": (
        "if self.der is not None and hashlib.sha256(self.der).hexdigest() != self.sha256:",
        "if False:",
    ),
    # size and parsing
    "the size limit is not enforced": ("if len(token) > MAX_TOKEN_BYTES:", "if False:"),
}


QUICK = [
    name for name in SCENARIOS if name != "live_tokens_sweep"
]  # the bit-flip sweep is the slow part; the table below is meant to need only these


def run(old: str, new: str) -> list[str]:
    return failures(mutant("hoshitori.tsa", old, new), only=QUICK)


def test_real_module_passes() -> None:
    assert failures(tsa) == []


@pytest.mark.parametrize("name", MUTATIONS)
def test_each_broken_rule_is_detected(name: str) -> None:
    old, new = MUTATIONS[name]
    assert run(old, new), f"mutant not detected: {name}"
