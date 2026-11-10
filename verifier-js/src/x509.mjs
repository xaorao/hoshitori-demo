// X.509 certificates, read with the strict DER reader: only what the time-stamp rules need (names as DER, validity, the
// public key, the signature algorithm, and the extensions that decide whether a certificate may issue or time-stamp).
import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";
import { DerError, TAG, bitString, boolean, expectTag, integer, octetString, oid, parseExact, time } from "./der.mjs";

export const OID = Object.freeze({
  BASIC_CONSTRAINTS: "2.5.29.19", KEY_USAGE: "2.5.29.15", EXT_KEY_USAGE: "2.5.29.37", SUBJECT_KEY_ID: "2.5.29.14",
  AUTHORITY_KEY_ID: "2.5.29.35", CERT_POLICIES: "2.5.29.32", SUBJECT_ALT_NAME: "2.5.29.17", CRL_DISTRIBUTION: "2.5.29.31",
  AUTHORITY_INFO_ACCESS: "1.3.6.1.5.5.7.1.1", TIME_STAMPING: "1.3.6.1.5.5.7.3.8", RSA: "1.2.840.113549.1.1.1", EC: "1.2.840.10045.2.1",
});

export const sha256Hex = (buf) => createHash("sha256").update(buf).digest("hex");

// Parameters of the signature algorithms the policy knows: RSA PKCS#1 v1.5 carries NULL (or nothing), ECDSA carries nothing. The
// certificate library enforces this when it reads a certificate, so a certificate whose algorithm field breaks it is not usable.
const RSA_PARAMETERS_NULL = new Set(["1.2.840.113549.1.1.1", "1.2.840.113549.1.1.2", "1.2.840.113549.1.1.4", "1.2.840.113549.1.1.5", "1.2.840.113549.1.1.11", "1.2.840.113549.1.1.12", "1.2.840.113549.1.1.13", "1.2.840.113549.1.1.14"]);
const ECDSA_NO_PARAMETERS = new Set(["1.2.840.10045.4.1", "1.2.840.10045.4.3.1", "1.2.840.10045.4.3.2", "1.2.840.10045.4.3.3", "1.2.840.10045.4.3.4"]);

function checkSignatureParameters(alg, what) {
  if (RSA_PARAMETERS_NULL.has(alg.oid) && alg.parameters !== null && !(alg.parameters.tag === TAG.NULL && alg.parameters.contents.length === 0)) throw new DerError(`${what}: RSA signature parameters must be NULL`);
  if (ECDSA_NO_PARAMETERS.has(alg.oid) && alg.parameters !== null) throw new DerError(`${what}: ECDSA signature parameters must be absent`);
}

// Signature algorithm identifiers (CMS signatures and certificates), as in the Python verifier: [family, named hash or null].
export const SIGNATURE_ALGORITHMS = Object.freeze({
  "1.2.840.113549.1.1.1": ["rsa", null],
  "1.2.840.113549.1.1.11": ["rsa", "sha256"],
  "1.2.840.113549.1.1.12": ["rsa", "sha384"],
  "1.2.840.113549.1.1.13": ["rsa", "sha512"],
  "1.2.840.10045.4.3.2": ["ec", "sha256"],
  "1.2.840.10045.4.3.3": ["ec", "sha384"],
  "1.2.840.10045.4.3.4": ["ec", "sha512"],
});

const CURVES = Object.freeze({ "1.2.840.10045.3.1.7": "secp256r1", "1.3.132.0.34": "secp384r1", "1.3.132.0.35": "secp521r1" });

const PRINTABLE = /^[A-Za-z0-9 '()+,\-./:=?]*$/;
const NUMERIC = /^[0-9 ]*$/;

function checkDirectoryString(value) {
  const bytes = value.contents;
  switch (value.tag) {
    case 0x13: if (!PRINTABLE.test(bytes.toString("latin1"))) throw new DerError("bad PrintableString"); break;
    case 0x12: if (!NUMERIC.test(bytes.toString("latin1"))) throw new DerError("bad NumericString"); break;
    case 0x16: if (bytes.some((b) => b > 0x7f)) throw new DerError("bad IA5String"); break;
    case 0x1a: if (bytes.some((b) => b < 0x20 || b > 0x7e)) throw new DerError("bad VisibleString"); break;
    case 0x0c:
      try {
        new TextDecoder("utf-8", { fatal: true }).decode(bytes);
      } catch {
        throw new DerError("bad UTF8String");
      }
      break;
    case 0x14: break; // TeletexString: any octets
    case 0x1e: if (bytes.length % 2 !== 0) throw new DerError("bad BMPString"); break;
    case 0x1c: if (bytes.length % 4 !== 0) throw new DerError("bad UniversalString"); break;
    default: throw new DerError("unexpected string type in a name");
  }
}

// A Name is a sequence of sets of (type, string) pairs. The Python verifier's certificate library refuses names that break
// this, so a certificate with such a name is not a candidate here either.
function validateName(element) {
  expectTag(element, TAG.SEQUENCE, "name");
  for (const rdn of element.children) {
    expectTag(rdn, TAG.SET, "relative distinguished name");
    if (rdn.children.length === 0) throw new DerError("empty relative distinguished name");
    for (const pair of rdn.children) {
      expectTag(pair, TAG.SEQUENCE, "name attribute");
      if (pair.children.length !== 2) throw new DerError("bad name attribute");
      oid(pair.children[0]);
      checkDirectoryString(pair.children[1]);
    }
  }
}

function algorithmIdentifier(element, what) {
  expectTag(element, TAG.SEQUENCE, what);
  const parts = element.children;
  if (parts.length < 1 || parts.length > 2) throw new DerError(`${what}: bad algorithm identifier`);
  return { oid: oid(parts[0]), parameters: parts[1] ?? null };
}

export { algorithmIdentifier };

function publicKeyInfo(element) {
  expectTag(element, TAG.SEQUENCE, "subjectPublicKeyInfo");
  const [algorithm, key] = element.children;
  if (element.children.length !== 2) throw new DerError("bad subjectPublicKeyInfo");
  const alg = algorithmIdentifier(algorithm, "public key algorithm");
  const bits = bitString(key);
  const info = { der: element.raw, algorithm: alg.oid, kind: "other", bits: 0, curve: null };
  if (alg.oid === OID.RSA) {
    try {
      const rsaKey = parseExact(bits.bytes);
      if (rsaKey.children.length !== 2) throw new DerError("bad RSA public key");
      const modulus = rsaKey.children[0];
      expectTag(modulus, TAG.INTEGER, "modulus");
      integer(modulus); // both integers must be minimal DER, as the certificate library requires
      integer(rsaKey.children[1]);
      const bytes = modulus.contents[0] === 0 ? modulus.contents.subarray(1) : modulus.contents;
      info.bits = bytes.length === 0 ? 0 : (bytes.length - 1) * 8 + (32 - Math.clz32(bytes[0]));
      info.kind = "rsa";
    } catch (error) {
      if (!(error instanceof DerError)) throw error; // an RSA key that cannot be read is a key of no usable kind
    }
  } else if (alg.oid === OID.EC) {
    info.kind = "ec";
    info.curve = alg.parameters && alg.parameters.tag === TAG.OID ? (CURVES[oid(alg.parameters)] ?? "other") : "other";
  }
  return info;
}

function extension(element) {
  expectTag(element, TAG.SEQUENCE, "extension");
  const parts = element.children;
  if (parts.length < 2 || parts.length > 3) throw new DerError("bad extension");
  const id = oid(parts[0]);
  const critical = parts.length === 3 ? boolean(parts[1]) : false;
  if (parts.length === 3 && !critical) throw new DerError("an explicit FALSE criticality is not DER");
  return { oid: id, critical, value: octetString(parts[parts.length - 1]) };
}

function basicConstraints(value) {
  const seq = parseExact(value);
  expectTag(seq, TAG.SEQUENCE, "basicConstraints");
  let ca = false;
  let pathLength = null;
  for (const part of seq.children) {
    if (part.tag === TAG.BOOLEAN) ca = boolean(part);
    else if (part.tag === TAG.INTEGER) pathLength = Number(integer(part));
    else throw new DerError("bad basicConstraints");
  }
  return { ca, pathLength };
}

function keyUsage(value) {
  const { bytes } = bitString(parseExact(value));
  const bit = (n) => bytes.length > (n >> 3) && (bytes[n >> 3] & (0x80 >> (n & 7))) !== 0;
  return { digitalSignature: bit(0), contentCommitment: bit(1), keyCertSign: bit(5) };
}

function extendedKeyUsage(value) {
  const seq = parseExact(value);
  expectTag(seq, TAG.SEQUENCE, "extendedKeyUsage");
  return seq.children.map(oid);
}

// Parse a certificate. Throws DerError on anything that is not a well-formed X.509 certificate.
export function parseCertificate(der) {
  const cert = parseExact(der);
  expectTag(cert, TAG.SEQUENCE, "certificate");
  if (cert.children.length !== 3) throw new DerError("a certificate has three parts");
  const [tbs, outerAlgorithm, signature] = cert.children;
  expectTag(tbs, TAG.SEQUENCE, "tbsCertificate");
  const parts = [...tbs.children];
  if (parts[0] && parts[0].tag === 0xa0) {
    const version = integer(parts[0].children[0]);
    if (version !== 0n && version !== 1n && version !== 2n) throw new DerError("unknown certificate version");
    parts.shift();
  }
  if (parts.length < 6) throw new DerError("tbsCertificate is too short");
  const serial = parts[0];
  integer(serial);
  // RFC 5280 requires a positive serial number (a later release of the certificate library may refuse the others)
  if ((serial.contents[0] & 0x80) !== 0 || (serial.contents.length === 1 && serial.contents[0] === 0)) throw new DerError("the serial number is not positive");
  checkSignatureParameters(algorithmIdentifier(parts[1], "tbs signature algorithm"), "tbs signature algorithm");
  const issuer = parts[2];
  validateName(issuer);
  const validity = parts[3];
  expectTag(validity, TAG.SEQUENCE, "validity");
  if (validity.children.length !== 2) throw new DerError("bad validity");
  const subject = parts[4];
  validateName(subject);
  const key = publicKeyInfo(parts[5]);
  const outer = algorithmIdentifier(outerAlgorithm, "signature algorithm");
  checkSignatureParameters(outer, "signature algorithm");
  const sig = bitString(signature);
  if (sig.unused !== 0) throw new DerError("the signature has unused bits");
  const result = {
    der, tbs: tbs.raw, serial: serial.contents, issuer: issuer.raw, subject: subject.raw,
    notBefore: time(validity.children[0]), notAfter: time(validity.children[1]),
    key, signatureOid: outer.oid, signature: sig.bytes, extensions: [],
    basic: null, keyUsage: null, eku: null, ekuCritical: false, subjectKeyId: null, fingerprint: sha256Hex(der),
  };
  const seen = new Set();
  for (const part of parts.slice(6)) {
    if (part.tag === 0xa3) {
      const list = part.children[0];
      expectTag(list, TAG.SEQUENCE, "extensions");
      if (part.children.length !== 1) throw new DerError("bad extensions");
      for (const item of list.children) {
        const ext = extension(item);
        if (seen.has(ext.oid)) throw new DerError("duplicate extension");
        seen.add(ext.oid);
        result.extensions.push(ext);
        if (ext.oid === OID.BASIC_CONSTRAINTS) result.basic = basicConstraints(ext.value);
        else if (ext.oid === OID.KEY_USAGE) result.keyUsage = keyUsage(ext.value);
        else if (ext.oid === OID.EXT_KEY_USAGE) {
          result.eku = extendedKeyUsage(ext.value);
          result.ekuCritical = ext.critical;
        } else if (ext.oid === OID.SUBJECT_KEY_ID) {
          // read only when a signer identifier asks for it, as the Python verifier does; a malformed value matches nothing
          try {
            result.subjectKeyId = octetString(parseExact(ext.value));
          } catch (error) {
            if (!(error instanceof DerError)) throw error;
          }
        }
      }
    }
  }
  return result;
}

// Whether `child`'s signature verifies with `issuer`'s key, under an algorithm the policy allows for certificates (PKCS#1 v1.5 or
// ECDSA with SHA-256, SHA-384 or SHA-512). Chaining by name is the caller's job.
export function issuedBy(child, issuer) {
  const entry = SIGNATURE_ALGORITHMS[child.signatureOid];
  if (!entry || entry[1] === null) return false;
  try {
    const key = createPublicKey({ key: issuer.key.der, format: "der", type: "spki" });
    return cryptoVerify(entry[1], child.tbs, key, child.signature);
  } catch {
    return false;
  }
}
