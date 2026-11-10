// RFC 3161 time-stamp token verification. Second implementation of src/hoshitori/tsa.py: the same rules, checked in the
// same order, with the same closed rejection codes. No dependencies; DER and X.509 are read by ./der.mjs and ./x509.mjs.
import { createHash, createPublicKey, verify as cryptoVerify } from "node:crypto";
import { DerError, TAG, boolean, expectTag, integer, octetString, oid, parseExact, time, validateTree } from "./der.mjs";
import { OID, SIGNATURE_ALGORITHMS, algorithmIdentifier, issuedBy, parseCertificate, sha256Hex } from "./x509.mjs";

export const MAX_TOKEN_BYTES = 131072;
export const MAX_PATH_LENGTH = 6;
export const MIN_RSA_BITS = 2048;
export const ALLOWED_CURVES = new Set(["secp256r1", "secp384r1", "secp521r1"]);
export const CODES = Object.freeze([
  "SIZE", "DIGEST", "PARSE", "STATUS", "CONTENT", "VERSION", "IMPRINT", "NONCE", "EXTENSION", "TIME", "SIGNER_COUNT", "ALGORITHM",
  "ATTRIBUTES", "SIGNER_ID", "SIGNATURE", "ESS", "NO_ROOT", "PATH", "CERT",
]);

const OID_SIGNED_DATA = "1.2.840.113549.1.7.2";
const OID_TST_INFO = "1.2.840.113549.1.9.16.1.4";
const OID_CONTENT_TYPE = "1.2.840.113549.1.9.3";
const OID_MESSAGE_DIGEST = "1.2.840.113549.1.9.4";
const OID_ESS_V1 = "1.2.840.113549.1.9.16.2.12";
const OID_ESS_V2 = "1.2.840.113549.1.9.16.2.47";
const HASH_BY_OID = Object.freeze({ "2.16.840.1.101.3.4.2.1": "sha256", "2.16.840.1.101.3.4.2.2": "sha384", "2.16.840.1.101.3.4.2.3": "sha512" });
const HANDLED_CRITICAL = new Set([
  OID.BASIC_CONSTRAINTS, OID.KEY_USAGE, OID.EXT_KEY_USAGE, OID.SUBJECT_KEY_ID, OID.AUTHORITY_KEY_ID, OID.CERT_POLICIES,
  OID.SUBJECT_ALT_NAME, OID.CRL_DISTRIBUTION, OID.AUTHORITY_INFO_ACCESS,
]);

export class TokenError extends Error {
  constructor(message, code) {
    super(message);
    this.code = code;
  }
}

const fail = (message, code) => new TokenError(message, code);

export class PinnedRoot {
  constructor(sha256, operator, der = null) {
    if (!/^[0-9a-f]{64}$/.test(sha256)) throw new Error("a pin is 64 lower-case hex characters");
    if (der !== null && sha256Hex(der) !== sha256) throw new Error("the certificate does not match its pin");
    this.sha256 = sha256;
    this.operator = operator;
    this.der = der;
  }

  static fromDer(der, operator) {
    return new PinnedRoot(sha256Hex(der), operator, der);
  }
}

export class TokenPolicy {
  constructor(pinned) {
    this.pinned = pinned;
  }

  byFingerprint() {
    return new Map(this.pinned.map((p) => [p.sha256, p]));
  }
}

const hashOf = (name, data) => createHash(name).update(data).digest();

// An attribute is read lazily: its type now, its values only when a rule needs them (as the Python verifier does).
function attributeRow(attribute) {
  expectTag(attribute, TAG.SEQUENCE, "attribute");
  if (attribute.children.length !== 2) throw new DerError("bad attribute");
  const valuesElement = attribute.children[1];
  return {
    type: oid(attribute.children[0]),
    first() {
      expectTag(valuesElement, TAG.SET, "attribute values");
      if (valuesElement.children.length === 0) throw new DerError("an attribute without values");
      return valuesElement.children[0];
    },
  };
}

// The ContentInfo of a response or of a bare token.
function contentInfo(token) {
  const top = parseExact(token);
  validateTree(top);
  expectTag(top, TAG.SEQUENCE, "token");
  const first = top.children[0];
  if (first && first.tag === TAG.SEQUENCE) {
    // TimeStampResp: PKIStatusInfo, then the token
    if (top.children.length > 2) throw new DerError("too many parts in a response");
    const statusInfo = first.children;
    const status = integer(statusInfo[0]);
    if (status !== 0n) throw fail(`the authority answered ${status}`, "STATUS");
    if (top.children.length !== 2) throw new DerError("the response carries no token");
    return contentParts(top.children[1]);
  }
  return contentParts(top);
}

function contentParts(element) {
  expectTag(element, TAG.SEQUENCE, "ContentInfo");
  if (element.children.length !== 2) throw new DerError("bad ContentInfo");
  const wrapper = element.children[1];
  if (wrapper.tag !== 0xa0 || wrapper.children.length !== 1) throw new DerError("bad ContentInfo content");
  return { type: oid(element.children[0]), content: wrapper.children[0] };
}

function selectSigner(signerInfo, certs) {
  const sid = signerInfo.sid;
  // issuerAndSerialNumber is a SEQUENCE of exactly two parts; subjectKeyIdentifier is [0] IMPLICIT and nothing else
  if (sid.tag === TAG.SEQUENCE) {
    if (sid.children.length !== 2) throw new DerError("an issuer and serial number identifier has two parts");
  } else if (sid.tag !== 0x80) {
    throw new DerError("unknown signer identifier");
  }
  const matches = [];
  for (const cert of certs) {
    if (sid.tag === TAG.SEQUENCE) {
      const [issuer, serial] = sid.children;
      if (integerBytesEqual(cert.serial, serial) && cert.issuer.equals(issuer.raw)) matches.push(cert);
    } else if (cert.subjectKeyId !== null && cert.subjectKeyId.equals(sid.contents)) {
      matches.push(cert);
    }
  }
  if (matches.length !== 1) throw fail("the signer identifier does not select exactly one certificate in the token", "SIGNER_ID");
  return matches[0];
}

function integerBytesEqual(certSerial, element) {
  expectTag(element, TAG.INTEGER, "serial number");
  return certSerial.equals(element.contents);
}

function verifySignature(signer, signature, data, algorithm, digestName) {
  const entry = SIGNATURE_ALGORITHMS[algorithm.oid];
  if (!entry) throw fail(`signature algorithm ${algorithm.oid} is not allowed`, "ALGORITHM");
  const [family, namedHash] = entry;
  if (namedHash !== null && namedHash !== digestName) throw fail("the signature algorithm names a different hash than the digest algorithm", "ALGORITHM");
  const kind = signer.key.kind;
  if (kind !== "rsa" && kind !== "ec") throw fail("the signer key type is not allowed", "ALGORITHM");
  if (kind === "rsa" && family !== "rsa") throw fail(`signature algorithm ${algorithm.oid} is not allowed for an RSA key`, "ALGORITHM");
  if (kind === "ec" && family !== "ec") throw fail(`signature algorithm ${algorithm.oid} is not allowed for an EC key`, "ALGORITHM");
  let key;
  try {
    key = createPublicKey({ key: signer.key.der, format: "der", type: "spki" });
  } catch {
    throw fail("the signer key could not be read", "ALGORITHM");
  }
  let ok = false;
  try {
    ok = cryptoVerify(digestName, data, key, signature);
  } catch {
    ok = false;
  }
  if (!ok) throw fail("the CMS signature does not verify", "SIGNATURE");
}

function checkExtensions(cert, label) {
  for (const ext of cert.extensions) {
    if (ext.critical && !HANDLED_CRITICAL.has(ext.oid)) throw fail(`${label}: unhandled critical extension ${ext.oid}`, "CERT");
  }
}

function checkValidity(cert, genTime, label) {
  if (cert.notBefore > genTime || genTime > cert.notAfter) throw fail(`${label}: not valid at the time stamp time`, "CERT");
}

function checkCa(cert, below, label) {
  if (cert.basic === null || !cert.basic.ca) throw fail(`${label}: not a CA certificate (basic constraints)`, "CERT");
  if (cert.basic.pathLength !== null && below > cert.basic.pathLength) throw fail(`${label}: path length constraint exceeded`, "CERT");
  if (cert.keyUsage !== null && !cert.keyUsage.keyCertSign) throw fail(`${label}: key usage does not allow signing certificates`, "CERT");
}

function checkSignerCertificate(cert) {
  if (cert.basic !== null && cert.basic.ca) throw fail("the signer certificate is a CA certificate", "CERT");
  if (cert.eku === null) throw fail("the signer certificate has no extended key usage", "CERT");
  if (cert.eku.length !== 1 || cert.eku[0] !== OID.TIME_STAMPING || !cert.ekuCritical) {
    throw fail("the signer certificate's extended key usage must be exactly critical time stamping", "CERT");
  }
  if (cert.keyUsage !== null && !(cert.keyUsage.digitalSignature || cert.keyUsage.contentCommitment)) {
    throw fail("the signer certificate's key usage does not allow signatures", "CERT");
  }
}

function checkKeyStrength(cert, label) {
  const key = cert.key;
  if (key.kind === "rsa") {
    if (key.bits < MIN_RSA_BITS) throw fail(`${label}: RSA key shorter than ${MIN_RSA_BITS} bits`, "CERT");
  } else if (key.kind === "ec") {
    if (!ALLOWED_CURVES.has(key.curve)) throw fail(`${label}: elliptic curve ${key.curve} is not allowed`, "CERT");
  } else {
    throw fail(`${label}: public key type is not allowed`, "CERT");
  }
}

function checkIssuer(issuer, below, genTime, label, anchor) {
  checkExtensions(issuer, label);
  checkValidity(issuer, genTime, label);
  checkCa(issuer, below, label);
  if (!anchor) checkKeyStrength(issuer, label);
}

function buildPath(signer, pool, pinned, genTime) {
  const candidates = new Map();
  for (const cert of pool) if (!candidates.has(cert.fingerprint)) candidates.set(cert.fingerprint, cert);
  for (const root of pinned.values()) {
    if (root.der === null) continue;
    const cert = parseCertificate(root.der);
    if (!candidates.has(cert.fingerprint)) candidates.set(cert.fingerprint, cert);
  }
  const failures = [];
  const extend = (path) => {
    const current = path[path.length - 1];
    if (pinned.has(current.fingerprint)) return path;
    if (path.length >= MAX_PATH_LENGTH) {
      failures.push("the certificate path is too long");
      return null;
    }
    const seen = new Set(path.map((c) => c.fingerprint));
    for (const [fingerprint, issuer] of candidates) {
      if (seen.has(fingerprint) || !issuer.subject.equals(current.issuer)) continue;
      if (!issuedBy(current, issuer)) {
        failures.push("a certificate signature in the path does not verify");
        continue;
      }
      try {
        checkIssuer(issuer, path.length - 1, genTime, `issuer ${path.length}`, pinned.has(fingerprint));
      } catch (error) {
        if (!(error instanceof TokenError)) throw error;
        failures.push(error.message);
        continue;
      }
      const found = extend([...path, issuer]);
      if (found !== null) return found;
    }
    return null;
  };
  const path = extend([signer]);
  if (path === null) {
    const detail = failures.length ? ` (${failures[failures.length - 1]})` : "";
    throw fail(`no valid certificate path to a pinned root${detail}`, "PATH");
  }
  return { path, root: pinned.get(path[path.length - 1].fingerprint) };
}

function checkEss(attributes, signer) {
  if (attributes.has(OID_ESS_V2)) {
    const certId = essCertId(attributes.get(OID_ESS_V2).first());
    const name = certId.hashAlgorithm === null ? "sha256" : (HASH_BY_OID[certId.hashAlgorithm] ?? null);
    if (name === null || !hashOf(name, signer.der).equals(certId.certHash)) {
      throw fail("the ESS signing certificate attribute does not match the signer certificate", "ESS");
    }
  } else if (attributes.has(OID_ESS_V1)) {
    const certId = essCertId(attributes.get(OID_ESS_V1).first());
    if (!hashOf("sha1", signer.der).equals(certId.certHash)) throw fail("the ESS signing certificate attribute does not match the signer certificate", "ESS");
  } else {
    throw fail("the signed attributes lack the ESS signing certificate attribute", "ESS");
  }
}

// SigningCertificate(V2) ::= SEQUENCE { certs SEQUENCE OF ESSCertID(v2), policies OPTIONAL }; only the first certificate id is used.
function essCertId(valueElement) {
  expectTag(valueElement, TAG.SEQUENCE, "signing certificate");
  const certs = valueElement.children[0];
  expectTag(certs, TAG.SEQUENCE, "signing certificate ids");
  const id = certs.children[0];
  expectTag(id, TAG.SEQUENCE, "signing certificate id");
  const parts = id.children;
  if (parts[0].tag === TAG.SEQUENCE) {
    return { hashAlgorithm: algorithmIdentifier(parts[0], "ESS hash algorithm").oid, certHash: octetString(parts[1]) };
  }
  return { hashAlgorithm: null, certHash: octetString(parts[0]) };
}

function tstInfo(raw) {
  const seq = parseExact(raw);
  validateTree(seq);
  expectTag(seq, TAG.SEQUENCE, "TSTInfo");
  const parts = seq.children;
  let at = 0;
  const info = { version: integer(parts[at++]), policy: oid(parts[at++]) };
  const imprint = parts[at++];
  expectTag(imprint, TAG.SEQUENCE, "message imprint");
  info.imprintAlgorithm = algorithmIdentifier(imprint.children[0], "imprint algorithm").oid;
  info.imprint = octetString(imprint.children[1]);
  info.serial = integer(parts[at++]);
  info.genTimeElement = expectTag(parts[at++], TAG.GENERALIZED_TIME, "genTime");
  info.nonce = null;
  info.extensions = [];
  for (; at < parts.length; at++) {
    const part = parts[at];
    if (part.tag === TAG.SEQUENCE) continue; // accuracy
    if (part.tag === TAG.BOOLEAN) continue; // ordering
    if (part.tag === TAG.INTEGER) info.nonce = integer(part);
    else if (part.tag === 0xa0) continue; // tsa
    else if (part.tag === 0xa1) {
      for (const ext of part.children) {
        const items = ext.children;
        const critical = items.length === 3 ? boolean(items[1]) : false;
        info.extensions.push({ critical });
      }
    } else throw new DerError("unexpected element in TSTInfo");
  }
  return info;
}

// Verify a response or bare token for a SHA-256 digest. `nonce` is a BigInt or null. Throws TokenError.
export function verifyToken(token, digest, policy, nonce = null) {
  if (token.length > MAX_TOKEN_BYTES) throw fail("token too large", "SIZE");
  if (digest.length !== 32) throw fail("the digest must be a SHA-256 value (32 bytes)", "DIGEST");
  try {
    return verifyParsed(token, digest, policy, nonce);
  } catch (error) {
    if (error instanceof TokenError) throw error;
    throw fail(`unparsable token: ${error.message}`.slice(0, 160), "PARSE");
  }
}

function verifyParsed(token, digest, policy, nonce) {
  const content = contentInfo(token);
  if (content.type !== OID_SIGNED_DATA) throw fail("not CMS signed data", "CONTENT");
  const signedData = content.content;
  expectTag(signedData, TAG.SEQUENCE, "SignedData");
  const sd = [...signedData.children];
  const version = integer(sd[0]);
  const digestAlgorithms = sd[1];
  expectTag(digestAlgorithms, TAG.SET, "digest algorithms");
  const encapsulated = sd[2];
  expectTag(encapsulated, TAG.SEQUENCE, "encapsulated content");
  if (oid(encapsulated.children[0]) !== OID_TST_INFO) throw fail("the content is not TSTInfo", "CONTENT");
  const wrapper = encapsulated.children[1];
  if (!wrapper || wrapper.tag !== 0xa0) throw new DerError("no encapsulated content");
  const rawTst = octetString(wrapper.children[0]);
  const tst = tstInfo(rawTst);
  if (tst.version !== 1n) throw fail("TSTInfo version must be 1", "VERSION");
  if (tst.imprintAlgorithm !== "2.16.840.1.101.3.4.2.1" || !tst.imprint.equals(digest)) {
    throw fail("the message imprint is not the SHA-256 of the committed digest", "IMPRINT");
  }
  if (nonce !== null && tst.nonce !== nonce) throw fail("the nonce does not match the request", "NONCE");
  if (tst.extensions.some((e) => e.critical)) throw fail("TSTInfo carries a critical extension", "EXTENSION");
  if (!tst.genTimeElement.contents.toString("latin1").endsWith("Z")) throw fail("the generation time is not stated in UTC", "TIME");
  const genTime = time(tst.genTimeElement);

  let rest = sd.slice(3);
  let certElements = [];
  if (rest[0] && rest[0].tag === 0xa0) {
    certElements = rest[0].children;
    rest = rest.slice(1);
  }
  if (rest[0] && rest[0].tag === 0xa1) rest = rest.slice(1);
  const signerSet = rest[0];
  expectTag(signerSet, TAG.SET, "signer infos");
  if (signerSet.children.length !== 1) throw fail("exactly one signer is required", "SIGNER_COUNT");
  const info = signerSet.children[0];
  expectTag(info, TAG.SEQUENCE, "signer info");
  const si = info.children;
  const signerVersion = integer(si[0]);
  const sid = si[1];
  const digestAlgorithm = algorithmIdentifier(si[2], "digest algorithm").oid;
  const digestName = HASH_BY_OID[digestAlgorithm];
  if (!digestName) throw fail(`CMS digest algorithm ${digestAlgorithm} is not allowed`, "ALGORITHM");
  const listed = digestAlgorithms.children.map((a) => algorithmIdentifier(a, "listed digest algorithm").oid);
  if (!listed.includes(digestAlgorithm)) throw fail("the signer's digest algorithm is not listed", "ALGORITHM");
  const sidIsIssuer = sid.tag === TAG.SEQUENCE;
  if (version !== 3n || signerVersion !== (sidIsIssuer ? 1n : 3n)) throw fail("inconsistent CMS versions", "VERSION");
  const signedAttrs = si[3];
  if (!signedAttrs || signedAttrs.tag !== 0xa0) throw new DerError("signed attributes are required");
  const rows = signedAttrs.children.map(attributeRow);
  const types = rows.map((r) => r.type);
  if (new Set(types).size !== types.length) throw fail("a signed attribute appears more than once", "ATTRIBUTES");
  const attributes = new Map(rows.map((r) => [r.type, r]));
  if (!attributes.has(OID_CONTENT_TYPE)) throw fail("the signed attributes lack the content type", "ATTRIBUTES");
  if (!attributes.has(OID_MESSAGE_DIGEST)) throw fail("the signed attributes lack the message digest", "ATTRIBUTES");
  if (oid(attributes.get(OID_CONTENT_TYPE).first()) !== OID_TST_INFO) throw fail("the content type attribute does not name TSTInfo", "ATTRIBUTES");
  if (!octetString(attributes.get(OID_MESSAGE_DIGEST).first()).equals(hashOf(digestName, rawTst))) {
    throw fail("the message digest attribute does not match TSTInfo", "ATTRIBUTES");
  }

  const certs = [];
  const seenCerts = new Set();
  for (const choice of certElements) {
    if (choice.tag !== TAG.SEQUENCE) {
      if (choice.tag >= 0xa0 && choice.tag <= 0xa3) continue; // attribute certificates and other formats are not X.509 certificates
      throw new DerError("unexpected certificate choice");
    }
    let cert;
    try {
      cert = parseCertificate(choice.raw);
    } catch (error) {
      if (error instanceof DerError) continue; // a certificate that cannot be read cannot be part of a path
      throw error;
    }
    if (!seenCerts.has(cert.fingerprint)) {
      seenCerts.add(cert.fingerprint);
      certs.push(cert);
    }
  }
  const signer = selectSigner({ sid }, certs);
  const signatureAlgorithm = algorithmIdentifier(si[4], "signature algorithm");
  const signature = octetString(si[5]);
  const signedBytes = Buffer.concat([Buffer.from([TAG.SET]), signedAttrs.raw.subarray(1)]);
  verifySignature(signer, signature, signedBytes, signatureAlgorithm, digestName);
  checkEss(attributes, signer);

  const pinned = policy.byFingerprint();
  if (pinned.size === 0) throw fail("no pinned root is configured", "NO_ROOT");
  const { path, root } = buildPath(signer, certs, pinned, genTime);
  checkExtensions(signer, "signer");
  checkValidity(signer, genTime, "signer");
  checkSignerCertificate(signer);
  if (!pinned.has(signer.fingerprint)) checkKeyStrength(signer, "signer");
  return {
    genTimeMs: genTime,
    policyOid: tst.policy,
    serial: tst.serial.toString(),
    rootFingerprint: root.sha256,
    signerFingerprint: signer.fingerprint,
    operator: root.operator,
    pathFingerprints: path.map((c) => c.fingerprint),
  };
}
