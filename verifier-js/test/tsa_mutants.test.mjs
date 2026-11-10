import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCases } from "./vectors.mjs";
import { mutantTree } from "./mutate.mjs";
import { run } from "./tsa.test.mjs";

const cases = loadCases("tsa_cases");

// Number of recorded cases on which the module differs from the recorded Python answers (a crash counts as a difference).
function deviations(mod) {
  let bad = 0;
  for (const c of cases) {
    let got;
    try {
      got = run(mod, c);
    } catch {
      bad++;
      continue;
    }
    const same = c.scenario === "live_tokens_sweep" ? got.ok === c.python.ok && (!got.ok || JSON.stringify(got) === JSON.stringify(c.python)) : JSON.stringify(got) === JSON.stringify(c.python);
    if (!same) bad++;
  }
  return bad;
}

// [file, old text, new text]; each target occurs exactly once in its file.
// Redundant guards without a mutant: the explicit refusal of an indefinite length (the bounds check rejects the same input) and the
// refusal of a certificate signature algorithm that names no hash (the signature could not verify without one), and the value check of
// a BOOLEAN (a changed value either reads as FALSE, which the explicit-FALSE rule refuses for criticality, or leaves a certificate whose
// signature no longer covers the bytes it carries; either way the certificate is unusable and the token ends in the same rejection).
const MUTATIONS = {
  // token content (tsa.mjs)
  "padding bits of a bit string may be set": ["src/der.mjs", "if (bytes.length > 1 && (bytes[bytes.length - 1] & ((1 << bytes[0]) - 1)) !== 0) throw new DerError(\"a bit string has padding bits that are set\");", ""],
  "a negative serial number is read": ["src/x509.mjs", "(serial.contents[0] & 0x80) !== 0 ||", ""],
  "a zero serial number is read": ["src/x509.mjs", "|| (serial.contents.length === 1 && serial.contents[0] === 0)", ""],
  "a signer identifier of another class is read": ["src/tsa.mjs", '} else if (sid.tag !== 0x80) {\n    throw new DerError("unknown signer identifier");\n  }', "}"],
  "an issuer and serial identifier may have extra parts": ["src/tsa.mjs", 'if (sid.children.length !== 2) throw new DerError("an issuer and serial number identifier has two parts");', ""],
  "the size limit is not enforced": ["src/tsa.mjs", 'if (token.length > MAX_TOKEN_BYTES) throw fail("token too large", "SIZE");', ""],
  "the digest length is not checked": ["src/tsa.mjs", 'if (digest.length !== 32) throw fail("the digest must be a SHA-256 value (32 bytes)", "DIGEST");', ""],
  "a refused response is accepted": ["src/tsa.mjs", 'if (status !== 0n) throw fail(`the authority answered ${status}`, "STATUS");', ""],
  "content that is not signed data is accepted": ["src/tsa.mjs", 'if (content.type !== OID_SIGNED_DATA) throw fail("not CMS signed data", "CONTENT");', ""],
  "content that is not TSTInfo is accepted": ["src/tsa.mjs", 'if (oid(encapsulated.children[0]) !== OID_TST_INFO) throw fail("the content is not TSTInfo", "CONTENT");', ""],
  "the TSTInfo version is not checked": ["src/tsa.mjs", 'if (tst.version !== 1n) throw fail("TSTInfo version must be 1", "VERSION");', ""],
  "the imprint algorithm is not checked": ["src/tsa.mjs", 'tst.imprintAlgorithm !== "2.16.840.1.101.3.4.2.1" || !tst.imprint.equals(digest)', "!tst.imprint.equals(digest)"],
  "the imprint is not compared": ["src/tsa.mjs", 'tst.imprintAlgorithm !== "2.16.840.1.101.3.4.2.1" || !tst.imprint.equals(digest)', 'tst.imprintAlgorithm !== "2.16.840.1.101.3.4.2.1"'],
  "the nonce is not checked": ["src/tsa.mjs", 'if (nonce !== null && tst.nonce !== nonce) throw fail("the nonce does not match the request", "NONCE");', ""],
  "a critical TSTInfo extension is tolerated": ["src/tsa.mjs", 'if (tst.extensions.some((e) => e.critical)) throw fail("TSTInfo carries a critical extension", "EXTENSION");', ""],
  "a time without a zone designator is accepted": ["src/tsa.mjs", 'if (!tst.genTimeElement.contents.toString("latin1").endsWith("Z")) throw fail("the generation time is not stated in UTC", "TIME");', ""],
  "several signers are accepted": ["src/tsa.mjs", 'if (signerSet.children.length !== 1) throw fail("exactly one signer is required", "SIGNER_COUNT");', ""],
  "a weak CMS digest is accepted": ["src/tsa.mjs", 'if (!digestName) throw fail(`CMS digest algorithm ${digestAlgorithm} is not allowed`, "ALGORITHM");', ""],
  "the digest algorithm need not be listed": ["src/tsa.mjs", 'if (!listed.includes(digestAlgorithm)) throw fail("the signer\'s digest algorithm is not listed", "ALGORITHM");', ""],
  "CMS versions are not checked": ["src/tsa.mjs", 'if (version !== 3n || signerVersion !== (sidIsIssuer ? 1n : 3n)) throw fail("inconsistent CMS versions", "VERSION");', ""],
  "duplicate attributes are tolerated": ["src/tsa.mjs", 'if (new Set(types).size !== types.length) throw fail("a signed attribute appears more than once", "ATTRIBUTES");', ""],
  "the content type attribute may be missing": ["src/tsa.mjs", 'if (!attributes.has(OID_CONTENT_TYPE)) throw fail("the signed attributes lack the content type", "ATTRIBUTES");', ""],
  "the message digest attribute may be missing": ["src/tsa.mjs", 'if (!attributes.has(OID_MESSAGE_DIGEST)) throw fail("the signed attributes lack the message digest", "ATTRIBUTES");', ""],
  "the content type attribute value is not checked": ["src/tsa.mjs", 'if (oid(attributes.get(OID_CONTENT_TYPE).first()) !== OID_TST_INFO) throw fail("the content type attribute does not name TSTInfo", "ATTRIBUTES");', ""],
  "the message digest attribute value is not compared": ["src/tsa.mjs", 'if (!octetString(attributes.get(OID_MESSAGE_DIGEST).first()).equals(hashOf(digestName, rawTst))) {\n    throw fail("the message digest attribute does not match TSTInfo", "ATTRIBUTES");\n  }', ""],
  // certificates in the token and the signer
  "an odd certificate choice is skipped": ["src/tsa.mjs", 'throw new DerError("unexpected certificate choice");', "continue;"],
  "an unreadable certificate rejects the token": ["src/tsa.mjs", "if (error instanceof DerError) continue; // a certificate that cannot be read cannot be part of a path", "throw error;"],
  "the signer need not be unique": ["src/tsa.mjs", "if (matches.length !== 1) throw", "if (matches.length < 1) throw"],
  "the signer is found by serial number alone": ["src/tsa.mjs", "if (integerBytesEqual(cert.serial, serial) && cert.issuer.equals(issuer.raw)) matches.push(cert);", "if (integerBytesEqual(cert.serial, serial)) matches.push(cert);"],
  "the signer is found by issuer alone": ["src/tsa.mjs", "if (integerBytesEqual(cert.serial, serial) && cert.issuer.equals(issuer.raw)) matches.push(cert);", "if (cert.issuer.equals(issuer.raw)) matches.push(cert);"],
  "a key identifier is not compared": ["src/tsa.mjs", "} else if (cert.subjectKeyId !== null && cert.subjectKeyId.equals(sid.contents)) {", "} else if (cert.subjectKeyId !== null) {"],
  // the signature
  "any signature algorithm is accepted": ["src/tsa.mjs", 'if (!entry) throw fail(`signature algorithm ${algorithm.oid} is not allowed`, "ALGORITHM");', "if (!entry) return;"],
  "the hash named by the signature algorithm is ignored": ["src/tsa.mjs", 'if (namedHash !== null && namedHash !== digestName) throw', "if (false) throw"],
  "an RSA key may use an EC algorithm": ["src/tsa.mjs", 'if (kind === "rsa" && family !== "rsa") throw', 'if (false) throw'],
  "an EC key may use an RSA algorithm": ["src/tsa.mjs", 'if (kind === "ec" && family !== "ec") throw', 'if (false) throw'],
  "other key types may sign": ["src/tsa.mjs", 'if (kind !== "rsa" && kind !== "ec") throw fail("the signer key type is not allowed", "ALGORITHM");', ""],
  "a signature that does not verify is accepted": ["src/tsa.mjs", 'if (!ok) throw fail("the CMS signature does not verify", "SIGNATURE");', ""],
  "the signed bytes keep their own tag": ["src/tsa.mjs", "Buffer.concat([Buffer.from([TAG.SET]), signedAttrs.raw.subarray(1)])", "signedAttrs.raw"],
  "the ESS v2 binding is not checked": ["src/tsa.mjs", 'if (name === null || !hashOf(name, signer.der).equals(certId.certHash)) {\n      throw fail("the ESS signing certificate attribute does not match the signer certificate", "ESS");\n    }', ""],
  "the ESS v1 binding is not checked": ["src/tsa.mjs", 'if (!hashOf("sha1", signer.der).equals(certId.certHash)) throw fail("the ESS signing certificate attribute does not match the signer certificate", "ESS");', ""],
  "a missing ESS attribute is tolerated": ["src/tsa.mjs", '} else {\n    throw fail("the signed attributes lack the ESS signing certificate attribute", "ESS");\n  }', "}"],
  "no pinned root is accepted as a set": ["src/tsa.mjs", 'if (pinned.size === 0) throw fail("no pinned root is configured", "NO_ROOT");', ""],
  // the certificate path
  "the path may end anywhere": ["src/tsa.mjs", "if (pinned.has(current.fingerprint)) return path;", "if (pinned.has(current.fingerprint) || current.issuer.equals(current.subject)) return path;"],
  "the path length is unbounded": ["src/tsa.mjs", "if (path.length >= MAX_PATH_LENGTH) {", "if (false) {"],
  "issuer names need not match": ["src/tsa.mjs", "if (seen.has(fingerprint) || !issuer.subject.equals(current.issuer)) continue;", "if (seen.has(fingerprint)) continue;"],
  "issuer signatures are not verified": ["src/tsa.mjs", "if (!issuedBy(current, issuer)) {", "if (false) {"],
  "issuer rules are not applied": ["src/tsa.mjs", "checkIssuer(issuer, path.length - 1, genTime, `issuer ${path.length}`, pinned.has(fingerprint));", ""],
  "only the first candidate issuer is tried": ["src/tsa.mjs", "const found = extend([...path, issuer]);\n      if (found !== null) return found;", "return extend([...path, issuer]);"],
  "anchors are judged for key strength": ["src/tsa.mjs", "if (!anchor) checkKeyStrength(issuer, label);", "checkKeyStrength(issuer, label);"],
  "issuer key strength is not judged": ["src/tsa.mjs", "if (!anchor) checkKeyStrength(issuer, label);", ""],
  "issuers may have unknown critical extensions": ["src/tsa.mjs", "function checkIssuer(issuer, below, genTime, label, anchor) {\n  checkExtensions(issuer, label);", "function checkIssuer(issuer, below, genTime, label, anchor) {"],
  "issuers need not be valid at the time stamp time": ["src/tsa.mjs", "function checkIssuer(issuer, below, genTime, label, anchor) {\n  checkExtensions(issuer, label);\n  checkValidity(issuer, genTime, label);", "function checkIssuer(issuer, below, genTime, label, anchor) {\n  checkExtensions(issuer, label);"],
  "issuers need not be CAs": ["src/tsa.mjs", "  checkCa(issuer, below, label);\n", ""],
  "a CA need not be marked as one": ["src/tsa.mjs", 'if (cert.basic === null || !cert.basic.ca) throw', "if (false) throw"],
  "path length constraints are ignored": ["src/tsa.mjs", "if (cert.basic.pathLength !== null && below > cert.basic.pathLength) throw", "if (false) throw"],
  "a CA need not be allowed to sign certificates": ["src/tsa.mjs", "if (cert.keyUsage !== null && !cert.keyUsage.keyCertSign) throw", "if (false) throw"],
  // the signer certificate
  "the signer may have unknown critical extensions": ["src/tsa.mjs", 'checkExtensions(signer, "signer");', ""],
  "the signer need not be valid at the time stamp time": ["src/tsa.mjs", 'checkValidity(signer, genTime, "signer");', ""],
  "the signer rules are skipped": ["src/tsa.mjs", "  checkSignerCertificate(signer);\n", ""],
  "the signer may be a CA": ["src/tsa.mjs", 'if (cert.basic !== null && cert.basic.ca) throw fail("the signer certificate is a CA certificate", "CERT");', ""],
  "the signer needs no extended key usage": ["src/tsa.mjs", 'if (cert.eku === null) throw fail("the signer certificate has no extended key usage", "CERT");', "if (cert.eku === null) return;"],
  "the signer's usage may be any": ["src/tsa.mjs", "if (cert.eku.length !== 1 || cert.eku[0] !== OID.TIME_STAMPING || !cert.ekuCritical) {", "if (!cert.eku.includes(OID.TIME_STAMPING)) {"],
  "the signer's usage need not be critical": ["src/tsa.mjs", "if (cert.eku.length !== 1 || cert.eku[0] !== OID.TIME_STAMPING || !cert.ekuCritical) {", "if (cert.eku.length !== 1 || cert.eku[0] !== OID.TIME_STAMPING) {"],
  "the signer's key usage is ignored": ["src/tsa.mjs", "if (cert.keyUsage !== null && !(cert.keyUsage.digitalSignature || cert.keyUsage.contentCommitment)) {", "if (false) {"],
  "the signer's key strength is not judged": ["src/tsa.mjs", 'if (!pinned.has(signer.fingerprint)) checkKeyStrength(signer, "signer");', ""],
  "short RSA keys are allowed": ["src/tsa.mjs", "if (key.bits < MIN_RSA_BITS) throw", "if (false) throw"],
  "any curve is allowed": ["src/tsa.mjs", "if (!ALLOWED_CURVES.has(key.curve)) throw", "if (false) throw"],
  // reading structures
  "validity is judged by the start only": ["src/tsa.mjs", "if (cert.notBefore > genTime || genTime > cert.notAfter) throw", "if (cert.notBefore > genTime) throw"],
  "validity is judged by the end only": ["src/tsa.mjs", "if (cert.notBefore > genTime || genTime > cert.notAfter) throw", "if (genTime > cert.notAfter) throw"],
  "the structure walk is skipped (token)": ["src/tsa.mjs", "  const top = parseExact(token);\n  validateTree(top);", "  const top = parseExact(token);"],
  "the structure walk is skipped (TSTInfo)": ["src/tsa.mjs", "  const seq = parseExact(raw);\n  validateTree(seq);", "  const seq = parseExact(raw);"],
  "padded lengths are accepted": ["src/der.mjs", 'if (length < 0x80) throw new DerError("length is not minimal");', ""],
  "leading zero lengths are accepted": ["src/der.mjs", 'if (buf[offset + 2] === 0) throw new DerError("length is not minimal");', ""],
  "multi-byte tags are accepted": ["src/der.mjs", 'if ((tag & 0x1f) === 0x1f) throw new DerError("multi-byte tags are not used here");', ""],
  "trailing data is accepted": ["src/der.mjs", 'if (end !== buf.length) throw new DerError("trailing data");', ""],
  "nesting is unbounded": ["src/der.mjs", 'if (depth > MAX_DEPTH) throw new DerError("nesting too deep");', ""],
  "names are not validated": ["src/x509.mjs", "function validateName(element) {", "function validateName(element) {\n  return;"],
  "a critical flag is not read": ["src/x509.mjs", "const critical = parts.length === 3 ? boolean(parts[1]) : false;", "const critical = false;"],
  "duplicate extensions are accepted": ["src/x509.mjs", 'if (seen.has(ext.oid)) throw new DerError("duplicate extension");', ""],
  "key usage bits are misread (certificate signing)": ["src/x509.mjs", "keyCertSign: bit(5)", "keyCertSign: bit(6)"],
  "key usage bits are misread (digital signature)": ["src/x509.mjs", "digitalSignature: bit(0)", "digitalSignature: bit(1)"],
  "key usage bits are misread (content commitment)": ["src/x509.mjs", "contentCommitment: bit(1)", "contentCommitment: bit(2)"],
  "a path length constraint is not read": ["src/x509.mjs", "else if (part.tag === TAG.INTEGER) pathLength = Number(integer(part));", "else if (part.tag === TAG.INTEGER) pathLength = null;"],
  "RSA key sizes are not computed": ["src/x509.mjs", "info.bits = bytes.length === 0 ? 0 : (bytes.length - 1) * 8 + (32 - Math.clz32(bytes[0]));", "info.bits = 4096;"],
  "curves are not read": ["src/x509.mjs", 'info.curve = alg.parameters && alg.parameters.tag === TAG.OID ? (CURVES[oid(alg.parameters)] ?? "other") : "other";', 'info.curve = "secp256r1";'],
  "certificate signatures are not verified": ["src/x509.mjs", "return cryptoVerify(entry[1], child.tbs, key, child.signature);", "return true;"],
  "certificate times are misread": ["src/der.mjs", "return epochMs(yy >= 50 ? 1900 + yy : 2000 + yy,", "return epochMs(yy >= 50 ? 2000 + yy : 1900 + yy,"],
};

test("the real modules deviate on no recorded case", async () => {
  const mod = await import("../src/tsa.mjs");
  assert.equal(deviations(mod), 0);
});

for (const [name, [file, oldText, newText]] of Object.entries(MUTATIONS)) {
  test(`mutant detected: ${name}`, async () => {
    const mod = await mutantTree(file, oldText, newText, "src/tsa.mjs");
    assert.ok(deviations(mod) > 0, "the recorded cases do not notice this change");
  });
}
