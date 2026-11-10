// The trusted root and its update client. Second implementation of src/hoshitori/trusted_root.py: the same document
// rules, the same codes. See that module for the reasoning behind each rule.
import { createHash, createPublicKey, verify } from "node:crypto";
import { UnicodeVersionError, canonicalize, parseRecord } from "./canonical.mjs";
import { NoteError, VerifierKey } from "./note.mjs";
import { TimeError, parseRfc3339 } from "./time.mjs";
import { PinnedRoot } from "./tsa.mjs";

export const TYPE = "hoshitori-trusted-root";
export const FORMAT_VERSION = 1;
export const MAX_CHAIN = 16;
export const MAX_DOCUMENT_BYTES = 262144;
export const MAX_SIGNATURES = 64;
export const MAX_LIST = 256;
export const CODES = Object.freeze({
  OK: "OK", EXPIRED: "EXPIRED", FORK: "FORK", MALFORMED: "MALFORMED", OLD_THRESHOLD: "OLD_THRESHOLD", NEW_THRESHOLD: "NEW_THRESHOLD",
  VERSION_STEP: "VERSION_STEP", CHAIN_BOUND: "CHAIN_BOUND", ROLLBACK: "ROLLBACK",
});
export const ERA = Object.freeze({ ACCEPT: "ACCEPT", BEFORE: "REJECT_BEFORE_ERA", BEYOND: "REJECT_BEYOND_CLOSING_SIZE", UNKNOWN: "UNKNOWN_ERA" });
const SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");

export class RootError extends Error {
  constructor(code, message) {
    super(`${code}: ${message}`);
    this.code = code;
  }
}

const fail = (message) => new RootError(CODES.MALFORMED, message);
const codePoints = (text) => [...text].length;

function object(value, members, what) {
  if (!(value instanceof Map)) throw fail(`${what} must be an object`);
  const keys = new Set(value.keys());
  const missing = [...members].filter((m) => !keys.has(m)).sort();
  const extra = [...keys].filter((k) => !members.has(k)).sort();
  if (missing.length || extra.length) throw fail(`${what}: missing ${JSON.stringify(missing)}, unexpected ${JSON.stringify(extra)}`);
  return value;
}

function integer(value, what, minimum = 0, maximum = Number.MAX_SAFE_INTEGER) {
  if (typeof value !== "number" || !Number.isInteger(value) || value < minimum || value > maximum) throw fail(`${what} must be an integer from ${minimum} to ${maximum}`);
  return value;
}

function string(value, what, limit = 512) {
  if (typeof value !== "string" || value.length === 0 || codePoints(value) > limit) throw fail(`${what} must be a non-empty string of at most ${limit} characters`);
  return value;
}

function hex(value, what, length = 64) {
  if (typeof value !== "string" || value.length !== length || !/^[0-9a-f]*$/.test(value)) throw fail(`${what} must be ${length} lower-case hex characters`);
  return value;
}

function list(value, what, minimum = 0) {
  if (!Array.isArray(value) || value.length < minimum || value.length > MAX_LIST) throw fail(`${what} must be a list of ${minimum} to ${MAX_LIST} items`);
  return value;
}

function b64(value, what, length = null) {
  if (typeof value !== "string" || !/^[A-Za-z0-9+/]*={0,2}$/.test(value) || value.length % 4 !== 0) throw fail(`${what} is not base64`);
  const raw = Buffer.from(value, "base64");
  if (raw.toString("base64") !== value || (length !== null && raw.length !== length)) throw fail(`${what} is not canonical base64${length ? ` of ${length} bytes` : ""}`);
  return raw;
}

export const keyId = (publicKey) => createHash("sha256").update(publicKey).digest("hex");

function vkey(value, what) {
  try {
    return VerifierKey.fromVkey(string(value, what));
  } catch (error) {
    if (error instanceof NoteError) throw fail(`${what}: ${error.message}`);
    throw error;
  }
}

const sameKey = (a, b) => a.name === b.name && a.publicKey.equals(b.publicKey);

function checkEras(eras) {
  const open = eras.filter((e) => e.throughSize === null);
  if (open.length !== 1 || eras[eras.length - 1].throughSize !== null) throw fail("exactly one era is open, and it is the last");
  for (let i = 0; i < eras.length; i++) for (let j = i + 1; j < eras.length; j++) if (sameKey(eras[i].key, eras[j].key)) throw fail("a log key appears in two eras");
  let previousThrough = -1;
  for (const era of eras) {
    if (era.throughSize !== null && era.throughSize < era.fromSize) throw fail("an era closes before it opens");
    if (era.fromSize <= previousThrough) throw fail("eras overlap or are out of order");
    if (era.throughSize !== null) previousThrough = era.throughSize;
  }
}

function parseOperators(value) {
  if (!(value instanceof Map) || value.size === 0 || value.size > MAX_LIST) throw fail("operators must be a non-empty object");
  const operators = new Map();
  for (const [name, entry] of value) {
    const item = object(entry, new Set(["parent", "ownership_evidence"]), `operator ${name}`);
    const evidence = item.get("ownership_evidence");
    let parsed = null;
    if (evidence !== null) {
      const proof = object(evidence, new Set(["kind", "ref", "subject_sha256"]), `operator ${name} evidence`);
      parsed = { kind: string(proof.get("kind"), "evidence kind", 128), ref: string(proof.get("ref"), "evidence ref", 1024), subjectSha256: hex(proof.get("subject_sha256"), "evidence subject_sha256") };
    }
    operators.set(name, { name, parent: string(item.get("parent"), `operator ${name} parent`, 256), evidence: parsed });
  }
  return operators;
}

export function parseRoot(payload) {
  if (payload.length > MAX_DOCUMENT_BYTES) throw fail("document too large");
  let document;
  try {
    document = parseRecord(payload);
    if (!canonicalize(payload).equals(payload)) throw fail("document is not in canonical form");
  } catch (error) {
    if (error instanceof RootError || error instanceof UnicodeVersionError) throw error; // an old runtime is not a malformed root
    throw fail(`document: ${error.message}`);
  }
  const top = object(document, new Set(["type", "format_version", "version", "expires", "root_keys", "root_threshold", "log", "time_stamp", "witnesses", "scored_requires_data_mode"]), "document");
  if (top.get("type") !== TYPE) throw fail("not a trusted-root document");
  if (top.get("format_version") !== FORMAT_VERSION || typeof top.get("format_version") !== "number") throw fail(`format_version must be ${FORMAT_VERSION}`);
  const version = integer(top.get("version"), "version", 1);
  let expires;
  try {
    expires = parseRfc3339(string(top.get("expires"), "expires"));
  } catch (error) {
    if (error instanceof TimeError) throw fail(`expires: ${error.message}`);
    throw error;
  }
  const rootKeys = new Map();
  for (const item of list(top.get("root_keys"), "root_keys", 1)) {
    const publicKey = b64(item, "root key", 32);
    const identifier = keyId(publicKey);
    if (rootKeys.has(identifier)) throw fail("a root key is listed twice");
    rootKeys.set(identifier, publicKey);
  }
  const threshold = integer(top.get("root_threshold"), "root_threshold", 1, rootKeys.size);

  const log = object(top.get("log"), new Set(["origin", "eras"]), "log");
  const origin = string(log.get("origin"), "log origin", 256);
  const eras = [];
  list(log.get("eras"), "log eras", 1).forEach((entry, index) => {
    const era = object(entry, new Set(["vkey", "from_size", "through_size"]), `era ${index}`);
    const key = vkey(era.get("vkey"), `era ${index} vkey`);
    if (key.name !== origin) throw fail(`era ${index}: the key name must be the log origin`);
    const through = era.get("through_size");
    eras.push({ key, fromSize: integer(era.get("from_size"), `era ${index} from_size`), throughSize: through === null ? null : integer(through, `era ${index} through_size`) });
  });
  checkEras(eras);

  const stamp = object(top.get("time_stamp"), new Set(["pinned_roots", "operators", "evidence_kinds_accepted", "required_independent", "window_before_commit_s", "window_after_commit_s", "close_after_commit_s", "max_spread_s", "u_constant_s"]), "time_stamp");
  const operators = parseOperators(stamp.get("operators"));
  const pinned = [];
  list(stamp.get("pinned_roots"), "pinned_roots", 1).forEach((entry, index) => {
    const pin = object(entry, new Set(["sha256", "operator", "der_b64"]), `pinned root ${index}`);
    const name = string(pin.get("operator"), `pinned root ${index} operator`);
    if (!operators.has(name)) throw fail(`pinned root ${index}: operator ${JSON.stringify(name)} is not in the registry`);
    const der = pin.get("der_b64") === null ? null : b64(pin.get("der_b64"), `pinned root ${index} der_b64`);
    try {
      pinned.push(new PinnedRoot(hex(pin.get("sha256"), `pinned root ${index} sha256`), name, der));
    } catch (error) {
      if (error instanceof RootError) throw error;
      throw fail(`pinned root ${index}: ${error.message}`);
    }
  });
  if (new Set(pinned.map((p) => p.sha256)).size !== pinned.length) throw fail("a time-stamp root is pinned twice");
  const kinds = new Set(list(stamp.get("evidence_kinds_accepted"), "evidence_kinds_accepted").map((k) => string(k, "evidence kind", 128)));
  const windowAfter = integer(stamp.get("window_after_commit_s"), "window_after_commit_s", 0, 3600);
  const rules = {
    pinned, operators, evidenceKindsAccepted: kinds,
    requiredIndependent: integer(stamp.get("required_independent"), "required_independent", 2, 16),
    windowBeforeCommitS: integer(stamp.get("window_before_commit_s"), "window_before_commit_s", 0, 3600),
    windowAfterCommitS: windowAfter,
    closeAfterCommitS: integer(stamp.get("close_after_commit_s"), "close_after_commit_s", windowAfter, 7200),
    maxSpreadS: integer(stamp.get("max_spread_s"), "max_spread_s", 0, 3600),
    uConstantS: integer(stamp.get("u_constant_s"), "u_constant_s", 1, 3600),
  };
  const witnesses = object(top.get("witnesses"), new Set(["required", "keys"]), "witnesses");
  const witnessKeys = list(witnesses.get("keys"), "witness keys").map((k) => vkey(k, "witness key"));
  const required = integer(witnesses.get("required"), "witnesses required", 0, witnessKeys.length);
  const mode = string(top.get("scored_requires_data_mode"), "scored_requires_data_mode", 32);
  return {
    version, expires, rootKeys, rootThreshold: threshold, origin, eras, timeStamp: rules, witnessKeys, witnessesRequired: required, scoredRequiresDataMode: mode, payload,
    sha256: createHash("sha256").update(payload).digest("hex"),
  };
}

export function eraCheck(root, key, size) {
  const big = BigInt(size);
  for (const era of root.eras) {
    if (sameKey(era.key, key)) {
      if (big < BigInt(era.fromSize)) return ERA.BEFORE;
      if (era.throughSize !== null && big > BigInt(era.throughSize)) return ERA.BEYOND;
      return ERA.ACCEPT;
    }
  }
  return ERA.UNKNOWN;
}

function envelope(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value) || Object.keys(value).sort().join() !== "payload_b64,signatures") throw fail("an envelope has a payload and signatures");
  const payload = b64(value.payload_b64, "payload");
  const raw = value.signatures;
  if (!Array.isArray(raw) || raw.length > MAX_SIGNATURES) throw fail("signatures must be a short list");
  const signatures = raw.map((item) => {
    if (item === null || typeof item !== "object" || Array.isArray(item) || Object.keys(item).sort().join() !== "keyid,sig_b64") throw fail("a signature has a key id and bytes");
    return [hex(item.keyid, "key id"), b64(item.sig_b64, "signature", 64)];
  });
  return { payload, signatures };
}

export function countValid(root, signatures, payload) {
  const counted = new Set();
  for (const [identifier, signature] of signatures) {
    const publicKey = root.rootKeys.get(identifier);
    if (publicKey === undefined) continue;
    const key = createPublicKey({ key: Buffer.concat([SPKI_PREFIX, publicKey]), format: "der", type: "spki" });
    if (verify(null, payload, key, signature)) counted.add(identifier);
  }
  return counted.size;
}

// Follow the signed chain of roots from the one shipped with the verifier. `channels` is a list of objects mapping a version
// (as text) to an envelope; `nowMs` is the fixed start time in milliseconds.
export function update(initial, channels, nowMs, { maxChain = MAX_CHAIN, floorVersion = 0 } = {}) {
  let current = initial;
  let steps = 0;
  let single = false;
  const result = (status, code) => ({ status, code, root: current, singleChannel: single, usable: status === "CURRENT" });
  for (;;) {
    const wanted = current.version + 1;
    const candidates = channels.filter((c) => Object.hasOwn(c, String(wanted))).map((c) => c[String(wanted)]);
    if (candidates.length === 0) break;
    if (candidates.length < 2) {
      single = true; // a step needs two independent channels that agree (contract 8): one is not followed, and is shown
      break;
    }
    if (steps >= maxChain) return result("REJECTED", CODES.CHAIN_BOUND);
    const marks = new Set(candidates.map((c) => (c !== null && typeof c === "object" && !Array.isArray(c) ? JSON.stringify(c.payload_b64 ?? null) : "?")));
    if (marks.size > 1) return result("FORK", CODES.FORK);
    let parsed;
    try {
      parsed = candidates.map(envelope);
    } catch (error) {
      if (error instanceof RootError) return result("REJECTED", CODES.MALFORMED);
      throw error;
    }
    const payload = parsed[0].payload;
    const signatures = parsed.flatMap((p) => p.signatures);
    let proposed;
    try {
      proposed = parseRoot(payload);
    } catch (error) {
      if (error instanceof RootError) return result("REJECTED", CODES.MALFORMED);
      throw error;
    }
    if (countValid(current, signatures, payload) < current.rootThreshold) return result("REJECTED", CODES.OLD_THRESHOLD);
    if (countValid(proposed, signatures, payload) < proposed.rootThreshold) return result("REJECTED", CODES.NEW_THRESHOLD);
    if (proposed.version !== wanted) return result("REJECTED", CODES.VERSION_STEP);
    current = proposed;
    steps += 1;
  }
  if (current.version < floorVersion) return result("REJECTED", CODES.ROLLBACK);
  if (current.expires <= nowMs) return result("EXPIRED", CODES.EXPIRED);
  return result("CURRENT", CODES.OK);
}
