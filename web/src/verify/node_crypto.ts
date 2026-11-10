// The part of node:crypto the offline verifier (verifier-js) uses, written in plain JavaScript so the same verifier runs in a page.
//
// It is a stand-in for the browser (and for the shim test in Node): hashes (SHA-256, SHA-384, SHA-512), Ed25519 verification,
// ECDSA verification over P-256, P-384 and P-521 with DER signatures, and RSA PKCS#1 v1.5 verification. Nothing else is offered: a
// call outside this list throws, so the verifier cannot quietly depend on something the page does not have. The signature code is
// the audited noble libraries (hashes, curves) and a short BigInt RSA verification written against RFC 8017 section 8.2.2; every
// accept and reject is compared with node:crypto in the tests, on keys and signatures made for the purpose.

import { ed25519 } from "@noble/curves/ed25519.js";
import { p256, p384, p521 } from "@noble/curves/nist.js";
import { sha256, sha384, sha512 } from "@noble/hashes/sha2.js";

type Bytes = Uint8Array;
const DIGESTS = { sha256, sha384, sha512 } as const;
type DigestName = keyof typeof DIGESTS;

function digestFor(name: string): (typeof DIGESTS)[DigestName] {
  const found = (DIGESTS as Record<string, (typeof DIGESTS)[DigestName] | undefined>)[name];
  if (found === undefined) throw new Error(`Digest method not supported: ${name}`);
  return found;
}

function bytes(data: Bytes | string): Bytes {
  return typeof data === "string" ? new TextEncoder().encode(data) : data;
}

const buffer = (b: Bytes): Buffer => Buffer.from(b.buffer, b.byteOffset, b.byteLength);

export class Hash {
  readonly #h: ReturnType<(typeof DIGESTS)[DigestName]["create"]>;
  constructor(name: string) {
    this.#h = digestFor(name).create();
  }
  update(data: Bytes | string): this {
    this.#h.update(bytes(data));
    return this;
  }
  digest(): Buffer;
  digest(encoding: "hex"): string;
  digest(encoding?: "hex"): Buffer | string {
    const out = buffer(this.#h.digest());
    return encoding === "hex" ? out.toString("hex") : out;
  }
}

export function createHash(name: string): Hash {
  return new Hash(name);
}

// --- DER, only what a SubjectPublicKeyInfo needs ---------------------------------------------------------------------------------------
type Tlv = { tag: number; value: Bytes; end: number };

function tlv(data: Bytes, at: number): Tlv {
  if (at + 2 > data.length) throw new Error("DER: truncated");
  const tag = data[at] as number;
  let length = data[at + 1] as number;
  let start = at + 2;
  if (length & 0x80) {
    const n = length & 0x7f;
    if (n === 0 || n > 4 || start + n > data.length) throw new Error("DER: bad length");
    length = 0;
    for (let i = 0; i < n; i++) length = length * 256 + (data[start + i] as number);
    start += n;
  }
  if (start + length > data.length) throw new Error("DER: truncated");
  return { tag, value: data.subarray(start, start + length), end: start + length };
}

function children(value: Bytes): Tlv[] {
  const out: Tlv[] = [];
  for (let at = 0; at < value.length; ) {
    const item = tlv(value, at);
    out.push(item);
    at = item.end;
  }
  return out;
}

function oid(value: Bytes): string {
  const parts: number[] = [Math.floor((value[0] as number) / 40), (value[0] as number) % 40];
  let n = 0;
  for (const b of value.subarray(1)) {
    n = n * 128 + (b & 0x7f);
    if (!(b & 0x80)) {
      parts.push(n);
      n = 0;
    }
  }
  return parts.join(".");
}

function integer(value: Bytes): bigint {
  let n = 0n;
  for (const b of value) n = (n << 8n) | BigInt(b);
  return n;
}

const OID_ED25519 = "1.3.101.112";
const OID_RSA = "1.2.840.113549.1.1.1";
const OID_EC = "1.2.840.10045.2.1";
const CURVES: Record<string, typeof p256 | typeof p384 | typeof p521> = {
  "1.2.840.10045.3.1.7": p256,
  "1.3.132.0.34": p384,
  "1.3.132.0.35": p521,
};

export type PublicKey =
  | { kind: "ed25519"; key: Bytes }
  | { kind: "rsa"; n: bigint; e: bigint; size: number }
  | { kind: "ec"; curve: typeof p256 | typeof p384 | typeof p521; point: Bytes };

export class KeyObject {
  readonly type = "public";
  readonly key: PublicKey;
  constructor(key: PublicKey) {
    this.key = key;
  }
}

/** A SubjectPublicKeyInfo in DER; anything else is refused, as node:crypto refuses it. */
export function createPublicKey(options: { key: Bytes; format: "der"; type: "spki" }): KeyObject {
  if (options.format !== "der" || options.type !== "spki") throw new Error("only DER SubjectPublicKeyInfo keys are supported");
  const der = options.key;
  const outer = tlv(der, 0);
  if (outer.tag !== 0x30 || outer.end !== der.length) throw new Error("DER: not a SubjectPublicKeyInfo");
  const [algorithm, bits] = children(outer.value);
  if (algorithm === undefined || bits === undefined || algorithm.tag !== 0x30 || bits.tag !== 0x03 || bits.value[0] !== 0) {
    throw new Error("DER: not a SubjectPublicKeyInfo");
  }
  const [id, parameters] = children(algorithm.value);
  if (id === undefined || id.tag !== 0x06) throw new Error("DER: no algorithm");
  const algorithmId = oid(id.value);
  const keyBytes = bits.value.subarray(1);
  if (algorithmId === OID_ED25519) {
    if (keyBytes.length !== 32 || parameters !== undefined) throw new Error("bad Ed25519 key");
    return new KeyObject({ kind: "ed25519", key: keyBytes });
  }
  if (algorithmId === OID_RSA) {
    const sequence = tlv(keyBytes, 0);
    if (sequence.tag !== 0x30 || sequence.end !== keyBytes.length) throw new Error("bad RSA key");
    const [modulus, exponent] = children(sequence.value);
    if (modulus === undefined || exponent === undefined || modulus.tag !== 0x02 || exponent.tag !== 0x02) throw new Error("bad RSA key");
    const n = integer(modulus.value);
    const e = integer(exponent.value);
    if (n < 2n || e < 3n || e % 2n === 0n || e >= n) throw new Error("bad RSA key");
    return new KeyObject({ kind: "rsa", n, e, size: Math.ceil(n.toString(16).length / 2) });
  }
  if (algorithmId === OID_EC) {
    if (parameters === undefined || parameters.tag !== 0x06) throw new Error("bad EC key");
    const curve = CURVES[oid(parameters.value)];
    if (curve === undefined) throw new Error("unsupported curve");
    if (keyBytes[0] !== 0x04) throw new Error("only uncompressed points are supported");
    curve.Point.fromBytes(keyBytes).assertValidity();
    return new KeyObject({ kind: "ec", curve, point: keyBytes });
  }
  throw new Error(`unsupported public key algorithm ${algorithmId}`);
}

// --- RSA PKCS#1 v1.5 (RFC 8017 section 8.2.2) ----------------------------------------------------------------------------------------
const DIGEST_INFO: Record<string, Bytes> = {
  sha256: Uint8Array.from([0x30, 0x31, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x01, 0x05, 0x00, 0x04, 0x20]),
  sha384: Uint8Array.from([0x30, 0x41, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x02, 0x05, 0x00, 0x04, 0x30]),
  sha512: Uint8Array.from([0x30, 0x51, 0x30, 0x0d, 0x06, 0x09, 0x60, 0x86, 0x48, 0x01, 0x65, 0x03, 0x04, 0x02, 0x03, 0x05, 0x00, 0x04, 0x40]),
};

function modPow(base: bigint, exponent: bigint, modulus: bigint): bigint {
  let result = 1n;
  let b = base % modulus;
  let e = exponent;
  while (e > 0n) {
    if (e & 1n) result = (result * b) % modulus;
    b = (b * b) % modulus;
    e >>= 1n;
  }
  return result;
}

function rsaVerify(key: Extract<PublicKey, { kind: "rsa" }>, digestName: string, data: Bytes, signature: Bytes): boolean {
  const prefix = DIGEST_INFO[digestName];
  if (prefix === undefined) throw new Error(`Digest method not supported: ${digestName}`);
  if (signature.length !== key.size) return false;
  const s = integer(signature);
  if (s >= key.n) return false;
  const m = modPow(s, key.e, key.n);
  const hex = m.toString(16).padStart(key.size * 2, "0");
  const em = Uint8Array.from(hex.match(/../g)?.map((h) => parseInt(h, 16)) ?? []);
  const hash = digestFor(digestName)(data);
  const tail = new Uint8Array(prefix.length + hash.length);
  tail.set(prefix, 0);
  tail.set(hash, prefix.length);
  const padding = key.size - tail.length - 3;
  if (padding < 8) return false;
  const expected = new Uint8Array(key.size);
  expected[1] = 0x01;
  expected.fill(0xff, 2, 2 + padding);
  expected.set(tail, 3 + padding);
  let different = 0;
  for (let i = 0; i < key.size; i++) different |= (em[i] as number) ^ (expected[i] as number);
  return different === 0;
}

/** node:crypto's verify for the key kinds above: Ed25519 takes no digest name, RSA and EC take one; a bad signature is false. */
export function verify(algorithm: string | null | undefined, data: Bytes | string, key: KeyObject, signature: Bytes): boolean {
  const message = bytes(data);
  const k = key.key;
  try {
    if (k.kind === "ed25519") {
      if (algorithm !== null && algorithm !== undefined) throw new Error("Ed25519 takes no digest algorithm");
      return ed25519.verify(signature, message, k.key, { zip215: false });
    }
    if (algorithm === null || algorithm === undefined) throw new Error("a digest algorithm is required");
    if (k.kind === "rsa") return rsaVerify(k, algorithm, message, signature);
    const digest = digestFor(algorithm)(message);
    return k.curve.verify(signature, digest, k.point, { prehash: false, lowS: false, format: "der" });
  } catch (error) {
    if (error instanceof Error && /not supported|takes no digest|digest algorithm is required/.test(error.message)) throw error;
    return false;
  }
}
