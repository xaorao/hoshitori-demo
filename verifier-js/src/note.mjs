// Signed notes (C2SP signed-note v1.1.0) and checkpoints (C2SP tlog-checkpoint v1.1.0), Ed25519 only, verification
// side. Second implementation; the first is src/hoshitori/note.py and checkpoint.py. Rules are identical:
// unknown keys are ignored, a known key whose signature fails rejects the note, at least one trusted signature is
// required, base64 must be canonical, duplicate (name, key id) is rejected, checkpoints have exactly three lines.
import { createHash, createPublicKey, verify } from "node:crypto";
import { HASH_LENGTH } from "./merkle.mjs";

export const MAX_SIGNATURES = 32;
export const MAX_TREE_SIZE = 2n ** 63n - 1n;
const EM_DASH = String.fromCodePoint(0x2014);
const SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

export class NoteError extends Error {}

const strictUtf8 = (bytes) => {
  try {
    return new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new NoteError("not valid UTF-8");
  }
};

export function b64decode(text) {
  if (!/^[A-Za-z0-9+/]*={0,2}$/.test(text) || text.length % 4 !== 0) throw new NoteError("invalid base64");
  const data = Buffer.from(text, "base64");
  if (data.toString("base64") !== text) throw new NoteError("non-canonical base64");
  return data;
}

export function checkKeyName(name) {
  if (name.length === 0 || name.includes("+") || /[\p{White_Space}\x00-\x1f]/u.test(name)) {
    throw new NoteError("bad key name");
  }
  return name;
}

export const keyId = (name, publicKey) =>
  createHash("sha256").update(Buffer.from(name, "utf8")).update(Buffer.from([0x0a, 0x01])).update(publicKey).digest().subarray(0, 4);

export class VerifierKey {
  constructor(name, publicKey) {
    this.name = checkKeyName(name);
    if (!Buffer.isBuffer(publicKey) || publicKey.length !== 32) throw new NoteError("an Ed25519 public key is 32 bytes");
    this.publicKey = publicKey;
    this.keyId = keyId(name, publicKey);
  }

  static fromVkey(text) {
    const first = text.indexOf("+");
    const second = first < 0 ? -1 : text.indexOf("+", first + 1);
    if (first < 0 || second < 0) throw new NoteError("a verifier key has three '+'-separated parts");
    const material = b64decode(text.slice(second + 1));
    if (material.length !== 33 || material[0] !== 0x01) throw new NoteError("only Ed25519 verifier keys are supported");
    const key = new VerifierKey(text.slice(0, first), material.subarray(1));
    if (text.slice(first + 1, second) !== key.keyId.toString("hex")) throw new NoteError("key id does not match the key");
    return key;
  }
}

export function splitNote(bytes) {
  const decoded = strictUtf8(bytes);
  if (/[\u0000-\u0009\u000b-\u001f]/.test(decoded)) throw new NoteError("control character in note");
  const cut = decoded.lastIndexOf("\n\n");
  if (cut < 0 || !decoded.endsWith("\n")) throw new NoteError("malformed note");
  const text = decoded.slice(0, cut + 1);
  if (text.replace(/\n/g, "").length === 0) throw new NoteError("empty note text");
  const tail = decoded.slice(cut + 2);
  const seen = new Set();
  const signatures = [];
  for (const line of tail.split("\n").slice(0, -1)) {
    if (!line.startsWith(EM_DASH + " ")) throw new NoteError("a signature line starts with an em dash and a space");
    const body = line.slice(2);
    const space = body.lastIndexOf(" ");
    if (space < 0) throw new NoteError("a signature line has a key name and a signature");
    const name = checkKeyName(body.slice(0, space));
    const blob = b64decode(body.slice(space + 1));
    if (blob.length < 5) throw new NoteError("signature too short");
    const id = blob.subarray(0, 4);
    const ident = `${name}\u0000${id.toString("hex")}`;
    if (seen.has(ident)) throw new NoteError("duplicate signature for one key name and key id");
    seen.add(ident);
    signatures.push({ name, id, signature: blob.subarray(4) });
  }
  if (signatures.length === 0) throw new NoteError("no signature lines");
  if (signatures.length > MAX_SIGNATURES) throw new NoteError("too many signatures");
  return { text: Buffer.from(text, "utf8"), signatures };
}

export function verifyNote(bytes, keys) {
  const { text, signatures } = splitNote(bytes);
  const trusted = new Map(keys.map((k) => [`${k.name}\u0000${k.keyId.toString("hex")}`, k]));
  const verified = [];
  for (const item of signatures) {
    const key = trusted.get(`${item.name}\u0000${item.id.toString("hex")}`);
    if (!key) continue;
    const publicKey = createPublicKey({ key: Buffer.concat([SPKI_PREFIX, key.publicKey]), format: "der", type: "spki" });
    if (!verify(null, text, publicKey, item.signature)) throw new NoteError(`signature of ${item.name} does not verify`);
    verified.push(item.name);
  }
  if (verified.length === 0) throw new NoteError("no signature from a trusted key");
  return { text, verified };
}

export function parseCheckpointText(text) {
  const decoded = strictUtf8(text);
  if (!decoded.endsWith("\n")) throw new NoteError("checkpoint text must end in a newline");
  const lines = decoded.slice(0, -1).split("\n");
  if (lines.length !== 3 || !lines.every((l) => l.length > 0)) throw new NoteError("a checkpoint is exactly three non-empty lines");
  const [origin, sizeText, rootText] = lines;
  if (/[\p{White_Space}\x00-\x1f]/u.test(origin)) throw new NoteError("bad origin");
  if (!/^(0|[1-9][0-9]*)$/.test(sizeText)) throw new NoteError("the tree size is an ASCII decimal without leading zeros");
  const size = BigInt(sizeText);
  if (size > MAX_TREE_SIZE) throw new NoteError("tree size out of range");
  const root = b64decode(rootText);
  if (root.length !== HASH_LENGTH) throw new NoteError("the root hash must be exactly 32 bytes");
  return { origin, size, root };
}

export function verifyCheckpoint(bytes, keys, origin) {
  const { text } = verifyNote(bytes, keys);
  const checkpoint = parseCheckpointText(text);
  if (checkpoint.origin !== origin) throw new NoteError("checkpoint is for another log");
  return checkpoint;
}
