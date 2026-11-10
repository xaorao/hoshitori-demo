// The stand-in for node:crypto (src/verify/node_crypto.ts) against node:crypto itself: the same keys, messages and signatures, and the
// same verdict on every one, accepted or refused. Keys are made here for the test; none is a secret of the project.
import assert from "node:assert/strict";
import * as real from "node:crypto";
import { test } from "node:test";
import * as shim from "../src/verify/node_crypto.ts";

const message = Buffer.from("a message that is signed, with some bytes in it: \u0000ÿ and more");

function flip(data: Uint8Array, at: number): Buffer {
  const copy = Buffer.from(data);
  copy[at % copy.length] = (copy[at % copy.length] as number) ^ 0x01;
  return copy;
}

function spki(publicKey: real.KeyObject): Buffer {
  return publicKey.export({ format: "der", type: "spki" }) as Buffer;
}

function same(algorithm: string | null, data: Buffer, publicDer: Buffer, signature: Buffer, label: string): boolean {
  const want = real.verify(algorithm, data, real.createPublicKey({ key: publicDer, format: "der", type: "spki" }), signature);
  const got = shim.verify(algorithm, data, shim.createPublicKey({ key: publicDer, format: "der", type: "spki" }), signature);
  assert.equal(got, want, label);
  return want;
}

test("hashes agree with node:crypto, in pieces and whole, as Buffer and as hex", () => {
  for (const name of ["sha256", "sha384", "sha512"]) {
    for (const size of [0, 1, 55, 56, 63, 64, 65, 111, 112, 127, 128, 129, 1000]) {
      const data = real.randomBytes(size);
      assert.deepEqual(shim.createHash(name).update(data).digest(), real.createHash(name).update(data).digest(), `${name} ${size}`);
      const cut = Math.floor(size / 3);
      assert.equal(shim.createHash(name).update(data.subarray(0, cut)).update(data.subarray(cut)).digest("hex"), real.createHash(name).update(data).digest("hex"));
    }
    assert.equal(shim.createHash(name).update("text").digest("hex"), real.createHash(name).update("text").digest("hex"));
  }
  assert.throws(() => shim.createHash("md5"), /not supported/);
});

test("Ed25519: valid and damaged signatures get the verdict node:crypto gives", () => {
  for (let i = 0; i < 20; i++) {
    const { publicKey, privateKey } = real.generateKeyPairSync("ed25519");
    const der = spki(publicKey);
    const signature = real.sign(null, message, privateKey);
    assert.equal(same(null, message, der, signature, "valid"), true);
    assert.equal(same(null, flip(message, i), der, signature, "other message"), false);
    for (const at of [0, 1, 31, 32, 33, 63]) assert.equal(same(null, message, der, flip(signature, at), `signature byte ${at}`), false);
    assert.equal(same(null, message, der, signature.subarray(0, 63), "short"), false);
    assert.equal(same(null, message, der, Buffer.concat([signature, Buffer.from([0])]), "long"), false);
    const other = spki(real.generateKeyPairSync("ed25519").publicKey);
    assert.equal(same(null, message, other, signature, "other key"), false);
  }
});

test("Ed25519 takes no digest name, and a digest name needs a key that takes one", () => {
  const { publicKey, privateKey } = real.generateKeyPairSync("ed25519");
  const signature = real.sign(null, message, privateKey);
  const key = shim.createPublicKey({ key: spki(publicKey), format: "der", type: "spki" });
  assert.throws(() => shim.verify("sha256", message, key, signature));
  const rsa = shim.createPublicKey({ key: spki(real.generateKeyPairSync("rsa", { modulusLength: 2048 }).publicKey), format: "der", type: "spki" });
  assert.throws(() => shim.verify(null, message, rsa, Buffer.alloc(256)), /digest/);
});

test("ECDSA over P-256, P-384 and P-521 with every digest: valid, damaged, wrong key", () => {
  for (const [curve, size] of [["prime256v1", 32], ["secp384r1", 48], ["secp521r1", 66]] as const) {
    for (const digest of ["sha256", "sha384", "sha512"]) {
      for (let i = 0; i < 6; i++) {
        const { publicKey, privateKey } = real.generateKeyPairSync("ec", { namedCurve: curve });
        const der = spki(publicKey);
        const signature = real.sign(digest, message, privateKey);
        assert.equal(same(digest, message, der, signature, `${curve} ${digest} valid`), true);
        assert.equal(same(digest, flip(message, i), der, signature, "other message"), false);
        for (const at of [4, 8, signature.length - 1, signature.length - 5]) assert.equal(same(digest, message, der, flip(signature, at), `byte ${at}`), false);
        assert.equal(same(digest, message, der, signature.subarray(0, signature.length - 2), "truncated"), false);
        assert.equal(same(digest, message, der, Buffer.concat([signature, Buffer.from([0])]), "trailing"), false);
        const other = spki(real.generateKeyPairSync("ec", { namedCurve: curve }).publicKey);
        assert.equal(same(digest, message, other, signature, "other key"), false);
        assert.equal(size > 0, true);
      }
    }
  }
});

test("an ECDSA signature with a high s is accepted, as node:crypto accepts it", () => {
  const { publicKey, privateKey } = real.generateKeyPairSync("ec", { namedCurve: "prime256v1" });
  const der = spki(publicKey);
  const order = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551n;
  let found = false;
  for (let i = 0; i < 40 && !found; i++) {
    const signature = real.sign("sha256", Buffer.concat([message, Buffer.from([i])]), privateKey);
    // r and s are two INTEGERs in a SEQUENCE; replace s by order - s
    const r = real.sign("sha256", Buffer.concat([message, Buffer.from([i])]), { key: privateKey, dsaEncoding: "ieee-p1363" });
    const s = BigInt("0x" + r.subarray(32).toString("hex"));
    const high = (order - s).toString(16).padStart(64, "0");
    const flipped = Buffer.concat([r.subarray(0, 32), Buffer.from(high, "hex")]);
    const data = Buffer.concat([message, Buffer.from([i])]);
    const okReal = real.verify("sha256", data, { key: privateKey, dsaEncoding: "ieee-p1363" }, flipped);
    assert.equal(typeof okReal, "boolean");
    // the DER form of the flipped pair
    const der2 = (x: Buffer): Buffer => {
      let v = x;
      while (v.length > 1 && v[0] === 0) v = v.subarray(1);
      return Buffer.concat([Buffer.from([0x02, v.length + ((v[0] as number) & 0x80 ? 1 : 0)]), (v[0] as number) & 0x80 ? Buffer.from([0]) : Buffer.alloc(0), v]);
    };
    const body = Buffer.concat([der2(flipped.subarray(0, 32)), der2(flipped.subarray(32))]);
    const flippedDer = Buffer.concat([Buffer.from([0x30, body.length]), body]);
    assert.equal(same("sha256", data, der, flippedDer, "high s"), true);
    assert.equal(same("sha256", data, der, signature, "low s"), true);
    found = true;
  }
  assert.equal(found, true);
});

test("RSA PKCS#1 v1.5 with every digest: valid, damaged, wrong key, and sizes", () => {
  for (const bits of [1024, 2048, 3072, 4096]) {
    const { publicKey, privateKey } = real.generateKeyPairSync("rsa", { modulusLength: bits });
    const der = spki(publicKey);
    for (const digest of ["sha256", "sha384", "sha512"]) {
      const signature = real.sign(digest, message, privateKey);
      assert.equal(same(digest, message, der, signature, `${bits} ${digest} valid`), true);
      assert.equal(same(digest, flip(message, 3), der, signature, "other message"), false);
      for (const at of [0, 1, 5, signature.length - 1]) assert.equal(same(digest, message, der, flip(signature, at), `byte ${at}`), false);
      assert.equal(same(digest, message, der, signature.subarray(1), "short"), false);
      assert.equal(same(digest, message, der, Buffer.concat([Buffer.from([0]), signature]), "long"), false);
      const other = spki(real.generateKeyPairSync("rsa", { modulusLength: bits }).publicKey);
      assert.equal(same(digest, message, other, signature, "other key"), false);
    }
    const signature = real.sign("sha256", message, privateKey);
    assert.equal(same("sha384", message, der, signature, "signed with one digest, checked with another"), false);
  }
});

test("an RSA signature as large as the modulus, or larger, is refused", () => {
  const { publicKey } = real.generateKeyPairSync("rsa", { modulusLength: 2048 });
  const der = spki(publicKey);
  assert.equal(same("sha256", message, der, Buffer.alloc(256, 0xff), "all ones"), false);
  assert.equal(same("sha256", message, der, Buffer.alloc(256, 0), "all zeros"), false);
});

test("keys that are not a SubjectPublicKeyInfo of a kind the verifier uses are refused", () => {
  const good = spki(real.generateKeyPairSync("ed25519").publicKey);
  for (const bad of [Buffer.alloc(0), Buffer.from([0x30, 0x00]), good.subarray(0, good.length - 1), flip(good, 5), Buffer.from("not der")]) {
    const realThrows = (() => { try { real.createPublicKey({ key: bad, format: "der", type: "spki" }); return false; } catch { return true; } })();
    const shimThrows = (() => { try { shim.createPublicKey({ key: bad, format: "der", type: "spki" }); return false; } catch { return true; } })();
    assert.equal(shimThrows, realThrows, `a bad key: ${bad.toString("hex").slice(0, 24)}`);
  }
  // the one place the stand-in is stricter: node:crypto (OpenSSL) accepts bytes after the end of a key, the stand-in refuses them
  const trailing = Buffer.concat([good, Buffer.from([0])]);
  assert.doesNotThrow(() => real.createPublicKey({ key: trailing, format: "der", type: "spki" }));
  assert.throws(() => shim.createPublicKey({ key: trailing, format: "der", type: "spki" }), /not a SubjectPublicKeyInfo/);
  const x25519 = spki(real.generateKeyPairSync("x25519").publicKey);
  assert.throws(() => shim.createPublicKey({ key: x25519, format: "der", type: "spki" }), /unsupported/);
  assert.throws(() => shim.createPublicKey({ key: good, format: "pem" as "der", type: "spki" }), /only DER/);
});
