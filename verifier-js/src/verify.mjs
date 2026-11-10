// Offline verification of a bundle. Second implementation of src/hoshitori/verify.py: the same four-part verdict, the same
// codes, the same limits. The verdict is a plain object; `detail` strings are for people and are not compared across
// implementations, except the exact ones of the refusals named in tests/test_verify_differential.py. See the Python module for the
// reasoning behind each rule.
import { createHash } from "node:crypto";
import { closeSync, constants, existsSync, fstatSync, openSync, readFileSync, readSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { UnicodeVersionError, canonicalize, dumpsRecord, parseRecord, requireRuntimeUnicode } from "./canonical.mjs";
import { validateCommittedRecord, EnvelopeError } from "./envelope.mjs";
import { MerkleTree, leafHash, verifyConsistency, verifyInclusion } from "./merkle.mjs";
import { NoteError, parseCheckpointText, splitNote, verifyCheckpoint } from "./note.mjs";
import { TimeError, formatMs, parseRfc3339 } from "./time.mjs";
import { MAX_PROOF_BYTES, ProofError, parseProof } from "./tlog_proof.mjs";
import * as timing from "./timing.mjs";
import { ERA, eraCheck } from "./trusted_root.mjs";
import { MAX_TOKEN_BYTES, TokenError, TokenPolicy, verifyToken } from "./tsa.mjs";

export const limits = { MAX_ITEMS: 10000, MAX_ITEM_BYTES: 1048576, MAX_TOKENS: 16, MAX_RAW_BYTES: 64 * 1024 * 1024, MAX_PUBLICATION_BYTES: 1048576, MAX_CONSISTENCY_BYTES: 65536, MAX_CONSISTENCY_HASHES: 256 };
export const EVIDENCE_TYPE = "evidence";
export const FORECAST_TYPE = "forecast";
const ITEMS = "items";
const TSA = "tsa";
const RAW = "raw";
const PUBLICATION = "publication.json";
const CONSISTENCY = "consistency.json";
const CONSISTENCY_FORMAT = 1;
const STEP_TYPE = "forecast_state";
const ENDING_STATES = ["SETTLED", "AMBIGUOUS", "VOID", "UNSETTLEABLE"];
const SETTLEMENT_TYPE = "settlement";
const SETTLEMENT_MEMBERS = ["forecast_id", "contract", "pair_id", "rule_version", "state", "outcome", "reason", "p0", "outcome_price", "touch_open_ms", "flags"];
const PRICE = /^(0|[1-9][0-9]*)(\.[0-9]+)?$/;
const SETTLEMENT_FAULTS = ["absent", "conflicting", "malformed", "mismatch", "early"];
const MINUTE_MS = 60000;
const HASH = /^[0-9a-f]{64}$/;
const CANONICAL_SUFFIX = ".canonical.json";
const PROOF_SUFFIX = ".tlog-proof";
const TOKEN_SUFFIX = ".tsr";
const STEM = /^[A-Za-z0-9_-]{1,64}$/;
const NOT_REGULAR = "not a regular file";
const TOO_LARGE = (limit) => `larger than ${limit} bytes`;

export const NOT_PROVEN = [
  "that the evidence was true or complete",
  "that the forecast is good",
  "that the agent reasoned as stored",
  "who controls the signing key",
  "that the log held nothing before its first append, or that no other history exists beyond the checkpoints seen",
  "that any witness cosigned: this verifier does not check cosignatures, so no record reaches level C4",
  "that no time-stamp certificate had been revoked: revocation evidence is neither archived nor checked by this verifier version",
  "both authorities honest at once: the level assumes that the time-stamp authorities did not collude or share a time source",
  "the clock of the machine that ran this verifier",
];
export const NOT_PROVEN_WITHOUT_ROOT = "that this bundle came from Hoshitori";

const bySortedName = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
const sha256Hex = (buf) => createHash("sha256").update(buf).digest("hex");

export class NotRegularFile extends Error {}
export class FileTooLarge extends Error {}

// A bundle is untrusted: a FIFO, or a link to a device such as /dev/zero, reports size 0 and may never end, and a FIFO that no process
// writes blocks an ordinary open for ever. So a bundle file is opened without blocking, its opened descriptor must be a regular file
// within `limit`, and no more than limit + 1 bytes of it are read, so that a file growing while it is read is refused too.
export function readBundleFile(path, limit) {
  const fd = openSync(path, constants.O_RDONLY | (constants.O_NONBLOCK ?? 0) | (constants.O_NOCTTY ?? 0));
  try {
    const info = fstatSync(fd);
    if (!info.isFile()) throw new NotRegularFile(NOT_REGULAR);
    if (info.size > limit) throw new FileTooLarge(TOO_LARGE(limit));
    const chunk = Buffer.alloc(Math.min(limit + 1, 65536));
    const chunks = [];
    let total = 0;
    for (;;) {
      const count = readSync(fd, chunk, 0, Math.min(chunk.length, limit + 1 - total), null);
      if (count === 0) break;
      chunks.push(Buffer.from(chunk.subarray(0, count)));
      total += count;
      if (total > limit) throw new FileTooLarge(TOO_LARGE(limit));
    }
    return Buffer.concat(chunks, total);
  } finally {
    closeSync(fd);
  }
}

function fail(item, code, detail) {
  if (item.error === null) {
    item.error = code;
    item.detail = detail;
  }
}

function listDir(path) {
  try {
    return readdirSync(path).sort(bySortedName);
  } catch {
    return null;
  }
}

function readStems(directory) {
  const itemsDir = join(directory, ITEMS);
  let isDirectory = false;
  try {
    isDirectory = statSync(itemsDir).isDirectory();
  } catch {
    isDirectory = false;
  }
  if (!isDirectory) return { stems: [], errors: ["NO_ITEMS"] };
  const errors = [];
  const stems = [];
  for (const name of listDir(itemsDir)) {
    if (name.endsWith(CANONICAL_SUFFIX)) {
      const stem = name.slice(0, -CANONICAL_SUFFIX.length);
      if (STEM.test(stem)) stems.push(stem);
      else errors.push("BAD_STEM");
    }
  }
  if (stems.length > limits.MAX_ITEMS) return { stems: stems.slice(0, limits.MAX_ITEMS), errors: [...errors, "TOO_MANY_ITEMS"] };
  if (stems.length === 0) errors.push("NO_ITEMS");
  return { stems, errors };
}

function loadItem(directory, stem, root, authenticated) {
  const item = { stem, canonicalBytes: null, record: null, committedMs: null, createdMs: null, windowMs: null, proof: null, checkpoint: null, noteText: null, error: null, detail: null, canonicalOk: false, signature: "unauthenticated", inclusion: false };
  const itemPath = join(directory, ITEMS, `${stem}${CANONICAL_SUFFIX}`);
  const proofPath = join(directory, ITEMS, `${stem}${PROOF_SUFFIX}`);
  try {
    item.canonicalBytes = readBundleFile(itemPath, limits.MAX_ITEM_BYTES);
  } catch (error) {
    if (error instanceof FileTooLarge) fail(item, "TOO_LARGE", "record file");
    else fail(item, "UNREADABLE", String(error.message));
    return item;
  }
  try {
    item.record = parseRecord(item.canonicalBytes);
    item.canonicalOk = canonicalize(item.canonicalBytes).equals(item.canonicalBytes);
  } catch (error) {
    if (error instanceof UnicodeVersionError) throw error;
    fail(item, "CANONICAL", String(error.message));
    return item;
  }
  if (!item.canonicalOk) fail(item, "CANONICAL", "not in canonical form");
  try {
    validateCommittedRecord(item.record);
    item.committedMs = parseRfc3339(String(item.record.get("committed_at")));
    item.createdMs = parseRfc3339(String(item.record.get("created_at")));
  } catch (error) {
    if (!(error instanceof EnvelopeError || error instanceof TimeError)) throw error;
    fail(item, "ENVELOPE", error.message);
  }
  const body = item.record.get("body");
  const opens = body instanceof Map ? body.get("outcome_window_opens_at") : undefined;
  if (opens !== undefined && opens !== null) {
    try {
      item.windowMs = typeof opens === "string" ? parseRfc3339(opens) : null;
    } catch (error) {
      if (!(error instanceof TimeError)) throw error;
      item.windowMs = null;
    }
    if (item.windowMs === null) fail(item, "WINDOW", "outcome_window_opens_at is not an RFC 3339 time");
  }
  let proofBytes;
  try {
    proofBytes = readBundleFile(proofPath, MAX_PROOF_BYTES);
  } catch (error) {
    if (error instanceof FileTooLarge) fail(item, "PROOF", "proof file too large");
    else fail(item, "MISSING_PROOF", `${stem}${PROOF_SUFFIX}`);
    return item;
  }
  try {
    item.proof = parseProof(proofBytes);
  } catch (error) {
    if (!(error instanceof ProofError)) throw error;
    fail(item, "PROOF", error.message);
    return item;
  }
  checkCheckpoint(item, root, authenticated);
  return item;
}

function checkCheckpoint(item, root, authenticated) {
  const proof = item.proof;
  try {
    item.noteText = splitNote(proof.note).text;
  } catch (error) {
    if (!(error instanceof NoteError)) throw error;
    fail(item, "CHECKPOINT", error.message);
    return;
  }
  if (authenticated && root !== null) {
    let verified = null;
    let failure = "SIGNATURE";
    for (const era of root.eras) {
      let candidate;
      try {
        candidate = verifyCheckpoint(proof.note, [era.key], root.origin);
      } catch (error) {
        if (error instanceof NoteError) continue;
        throw error;
      }
      if (eraCheck(root, era.key, candidate.size) !== ERA.ACCEPT) {
        failure = "ERA";
        continue;
      }
      verified = candidate;
      break;
    }
    if (verified === null) {
      try {
        parseCheckpointText(item.noteText);
      } catch (error) {
        if (!(error instanceof NoteError)) throw error;
        fail(item, "CHECKPOINT", error.message);
        return;
      }
      item.signature = "failed";
      fail(item, failure, "no log key of the trusted root vouches for this checkpoint at this size");
      return;
    }
    item.checkpoint = verified;
    item.signature = "verified";
  } else {
    try {
      item.checkpoint = parseCheckpointText(item.noteText);
    } catch (error) {
      if (!(error instanceof NoteError)) throw error;
      fail(item, "CHECKPOINT", error.message);
      return;
    }
  }
  const cp = item.checkpoint;
  const safe = (v) => (v <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(v) : -1); // trees beyond 2**53 are not supported by this verifier
  item.inclusion = verifyInclusion(leafHash(item.canonicalBytes), safe(proof.index), safe(cp.size), proof.path, cp.root);
  if (!item.inclusion) fail(item, "INCLUSION", "the inclusion proof does not reach the checkpoint root");
}

// A reference names a record as its producer submitted it: the stored record without the member the ledger's writer added.
function producerBytes(record) {
  const producer = new Map(record);
  producer.delete("committed_at");
  return dumpsRecord(producer);
}

// A reference whose record is not in the bundle is listed as unresolved; integrity is not failed by it (what is present is intact), but
// the bundle is not scorable, with the reason UNRESOLVED_REFERENCE; one whose record has other bytes fails
// integrity.
function referencesOf(items) {
  const byId = new Map();
  for (const item of items) {
    if (item.record !== null && item.canonicalBytes !== null && typeof item.record.get("id") === "string") {
      const id = item.record.get("id");
      if (!byId.has(id)) byId.set(id, new Set());
      byId.get(id).add(sha256Hex(producerBytes(item.record)));
    }
  }
  let resolved = 0;
  const unresolved = [];
  const mismatched = [];
  for (const item of items) {
    const refs = item.record !== null ? item.record.get("refs") : undefined;
    for (const ref of Array.isArray(refs) ? refs : []) {
      if (!(ref instanceof Map)) continue;
      const rid = String(ref.get("id"));
      const digest = String(ref.get("sha256"));
      const label = `${item.stem}->${rid}`;
      if (!byId.has(rid)) unresolved.push(label);
      else if (byId.get(rid).has(digest)) resolved += 1;
      else mismatched.push(label);
    }
  }
  return { resolved, unresolved: unresolved.sort(bySortedName), mismatched: mismatched.sort(bySortedName) };
}

// only a regular file of reasonable size whose bytes hash to its name is a raw payload
function intact(path, digest) {
  const info = statSync(path);
  return info.isFile() && info.size <= limits.MAX_RAW_BYTES && sha256Hex(readFileSync(path)) === digest;
}

function rawPayloads(directory, items) {
  const cited = new Set();
  for (const item of items) {
    const body = item.record !== null ? item.record.get("body") : undefined;
    const digest = body instanceof Map ? body.get("raw_sha256") : undefined;
    if (typeof digest === "string") cited.add(digest);
  }
  const verified = [];
  const missing = [];
  const mismatched = [];
  const rawDir = join(directory, RAW);
  const names = listDir(rawDir) ?? [];
  const present = new Set(names);
  for (const digest of [...cited].sort(bySortedName)) {
    if (!present.has(digest)) {
      missing.push(digest);
      continue;
    }
    if (intact(join(rawDir, digest), digest)) verified.push(digest);
    else mismatched.push(digest);
  }
  for (const name of names) {
    if (!cited.has(name) && !intact(join(rawDir, name), name)) mismatched.push(name);
  }
  return { verified, missing, mismatched: [...new Set(mismatched)].sort(bySortedName), unreferenced: names.filter((n) => !cited.has(n)).sort(bySortedName) };
}

function publication(directory) {
  let claims = 0;
  const path = join(directory, PUBLICATION);
  if (existsSync(path)) {
    try {
      const data = JSON.parse(readBundleFile(path, limits.MAX_PUBLICATION_BYTES).toString("utf8"));
      const list = data !== null && typeof data === "object" && !Array.isArray(data) ? (Object.hasOwn(data, "claims") ? data.claims : []) : null;
      claims = Array.isArray(list) ? list.length : -1;
    } catch {
      claims = -1;
    }
  }
  return { claimed: claims, authenticated_offline: 0, note: "publication claims in a bundle are not authenticated offline and never raise a level" };
}

// The parsed consistency.json: {state: "missing"} when the file is not there, {state: "failed"} when it cannot be used. Mirrors ``verify._consistency_path``.
function consistencyFile(directory) {
  let raw;
  try {
    raw = readBundleFile(join(directory, CONSISTENCY), limits.MAX_CONSISTENCY_BYTES);
  } catch (error) {
    if (!(error instanceof FileTooLarge) && !(error instanceof NotRegularFile) && error.code === "ENOENT") return { state: "missing", document: null };
    return { state: "failed", document: null };
  }
  let document;
  try {
    document = parseRecord(raw);
    if (!canonicalize(raw).equals(raw)) return { state: "failed", document: null };
  } catch (error) {
    if (error instanceof UnicodeVersionError) throw error;
    return { state: "failed", document: null };
  }
  if (!(document instanceof Map)) return { state: "failed", document: null };
  const members = ["format", "from_size", "to_size", "hashes"];
  const hashes = document.get("hashes");
  const sizes = [document.get("from_size"), document.get("to_size")];
  const keys = [...document.keys()];
  if (
    keys.length !== members.length ||
    !members.every((m) => document.has(m)) ||
    document.get("format") !== CONSISTENCY_FORMAT ||
    !Array.isArray(hashes) ||
    hashes.length > limits.MAX_CONSISTENCY_HASHES ||
    !hashes.every((h) => typeof h === "string" && HASH.test(h)) ||
    !sizes.every((s) => typeof s === "number" && Number.isInteger(s) && s >= 1)
  ) {
    return { state: "failed", document: null };
  }
  return { state: "present", document };
}

const safeSize = (v) => (v <= BigInt(Number.MAX_SAFE_INTEGER) ? Number(v) : -1);

// The items of a bundle by the checkpoint that proves them. Mirrors ``verify._groups``.
function groupsOf(directory, items) {
  const texts = new Map();
  for (const i of items) if (i.noteText !== null) texts.set(i.noteText.toString("latin1"), i.noteText);
  const whole = items.length > 0 && items.every((i) => i.noteText !== null);
  const none = (stamped, errors) => ({ stamped, later: [], stampedText: null, stampedCheckpoint: null, laterCheckpoint: null, consistency: "none", errors });
  const result = (stamped, later, text, small, large, consistency, errors) => ({ stamped, later, stampedText: text, stampedCheckpoint: small, laterCheckpoint: large, consistency, errors, ok: errors.length === 0 && (consistency === "none" || consistency === "verified") });
  if (texts.size === 1 && whole) return result(items, [], [...texts.values()][0], items[0].checkpoint, null, "none", []);
  if (items.length === 0 || texts.size === 0 || (texts.size === 1 && !whole)) return result(items, [], null, null, null, "none", items.length ? ["NO_CHECKPOINT"] : []);
  if (texts.size > 2 || !whole) return result(items, [], null, null, null, "none", ["CHECKPOINTS_DIFFER"]);
  const known = [];
  for (const [key, text] of texts) {
    const first = items.find((i) => i.noteText.toString("latin1") === key && i.checkpoint !== null);
    if (first !== undefined) known.push({ size: first.checkpoint.size, text, key, cp: first.checkpoint });
  }
  const file = consistencyFile(directory);
  let small = null;
  let large = null;
  let smallText = null;
  let smallKey = null;
  let stamped = [...items];
  let later = [];
  if (known.length === 2) {
    known.sort((a, b) => (a.size < b.size ? -1 : a.size > b.size ? 1 : Buffer.compare(a.text, b.text)));
    small = known[0].cp;
    large = known[1].cp;
    smallText = known[0].text;
    smallKey = known[0].key;
    stamped = items.filter((i) => i.noteText.toString("latin1") === smallKey);
    later = items.filter((i) => i.noteText.toString("latin1") !== smallKey);
  }
  if (file.state === "missing") return result(stamped, later, smallText, small, large, "missing", ["CHECKPOINTS_DIFFER"]);
  let verified = false;
  if (file.state === "present" && small !== null && large !== null) {
    const hashes = [...file.document.get("hashes")].map((h) => Buffer.from(h, "hex"));
    verified =
      small.size < large.size &&
      BigInt(file.document.get("from_size")) === small.size &&
      BigInt(file.document.get("to_size")) === large.size &&
      verifyConsistency(safeSize(small.size), safeSize(large.size), hashes, small.root, large.root);
  }
  if (!verified) return result(stamped, later, smallText, small, large, "failed", ["CONSISTENCY_FAILED"]);
  return result(stamped, later, smallText, small, large, "verified", []);
}

const cpSummary = (cp) => (cp === null ? null : { origin: cp.origin, size: Number(cp.size), root_sha256: cp.root.toString("hex") });

function stepTime(body, name) {
  const value = body.get(name);
  if (typeof value !== "string") return null;
  try {
    return parseRfc3339(value);
  } catch (error) {
    if (!(error instanceof TimeError)) throw error;
    return null;
  }
}

// What the records appended after the stamped checkpoint show about each forecast of the batch, held to what the tokens give.
// Mirrors ``verify._later``.
function laterPart(groups, timingVerdict, integrityOk) {
  const out = { shown: false, forecasts: {}, failures: 0 };
  if (groups.later.length === 0 || groups.consistency !== "verified" || !integrityOk) return out;
  out.shown = true;
  const good = groups.later.filter((i) => i.error === null && i.canonicalOk && i.inclusion && i.record !== null);
  const steps = new Map();
  const settles = new Map();
  for (const item of good) {
    const body = item.record.get("body");
    if (item.record.get("type") === STEP_TYPE && body instanceof Map && typeof body.get("forecast_id") === "string") {
      const id = body.get("forecast_id");
      if (!steps.has(id)) steps.set(id, []);
      steps.get(id).push(body);
    } else if (item.record.get("type") === SETTLEMENT_TYPE && body instanceof Map && typeof body.get("forecast_id") === "string") {
      const id = body.get("forecast_id");
      if (!settles.has(id)) settles.set(id, []);
      settles.get(id).push(item);
    }
  }
  for (const item of groups.stamped) {
    const forecast = item.record;
    if (forecast === null || forecast.get("type") !== FORECAST_TYPE || item.error !== null || !item.inclusion) continue;
    const forecastId = forecast.get("id");
    if (typeof forecastId !== "string") continue;
    const timed = timingVerdict.items[item.stem] ?? {};
    const attested = timed.attested_at;
    const offset = timed.eligible_after_offset_ms;
    let derived = null;
    if (timed.level === timing.LEVEL_C3 && typeof attested === "string" && typeof offset === "number" && Number.isInteger(offset)) derived = parseRfc3339(attested) + offset;
    const mine = steps.get(forecastId) ?? [];
    const committed = mine.filter((s) => s.get("state") === "COMMITTED");
    const waiting = mine.filter((s) => s.get("state") === "AWAITING_ORIGIN");
    const opened = mine.filter((s) => s.get("state") === "OPEN");
    const ended = mine.filter((s) => ENDING_STATES.includes(s.get("state")));
    const eligible = waiting.length === 1 ? stepTime(waiting[0], "eligible_after") : null;
    let awaiting = waiting.length === 0 ? "absent" : waiting.length > 1 ? "conflicting" : eligible === null ? "malformed" : "unchecked";
    if (awaiting === "unchecked" && derived !== null && eligible !== null) awaiting = eligible >= derived ? "ok" : "early";
    const originAt = opened.length === 1 ? stepTime(opened[0], "origin") : null;
    let origin = opened.length === 0 ? "absent" : opened.length > 1 ? "conflicting" : originAt === null ? "malformed" : "unchecked";
    if (origin === "unchecked" && derived !== null && originAt !== null) {
      if (originAt < (Math.floor(derived / MINUTE_MS) + 1) * MINUTE_MS) origin = "early";
      else if (eligible === null || awaiting !== "ok" || originAt !== (Math.floor(eligible / MINUTE_MS) + 1) * MINUTE_MS) origin = "wrong";
      else origin = "ok";
    }
    const level = committed.length === 1 && typeof committed[0].get("level") === "string" ? committed[0].get("level") : null;
    const ending = ended.length > 1 ? "conflicting" : ended.length === 1 ? ended[0].get("state") : null;
    out.forecasts[forecastId] = {
      committed_level: level,
      awaiting,
      eligible_after: eligible === null ? null : formatMs(eligible),
      derived_eligible_after: derived === null ? null : formatMs(derived),
      origin,
      origin_at: originAt === null ? null : formatMs(originAt),
      derived_origin: derived === null ? null : formatMs((Math.floor(derived / MINUTE_MS) + 1) * MINUTE_MS),
      ended: ending,
    };
    const [settled, faulted] = settlementOf(forecast, settles.get(forecastId) ?? [], ended, origin, originAt);
    Object.assign(out.forecasts[forecastId], settled);
    if (!["ok", "unchecked"].includes(awaiting) || ["early", "wrong", "conflicting", "malformed"].includes(origin) || faulted) out.failures += 1;
  }
  return out;
}

/** A recorded price as decimal text the settlement writes it: positive, else null. */
function priceOf(value) {
  if (typeof value !== "string" || !PRICE.test(value)) return null;
  return /[1-9]/.test(value) ? value : null;
}

/** Two positive decimal texts compared exactly: -1, 0 or 1. */
function comparePrices(a, b) {
  const [ai, af = ""] = a.split(".");
  const [bi, bf = ""] = b.split(".");
  const places = Math.max(af.length, bf.length);
  const x = BigInt(ai + af.padEnd(places, "0"));
  const y = BigInt(bi + bf.padEnd(places, "0"));
  return x < y ? -1 : x > y ? 1 : 0;
}

/** What the settlement record of one forecast shows: held to the forecast, to the step that cites it, to the directional rule and to the time the
 * call's window ends. It does not say the prices are the market's. */
/** Whether a settled barrier record agrees with itself: one of three outcomes, a reference price, no outcome price, and a touch minute that is
 * a whole minute inside the call's window exactly when a level was touched. Which level was touched is decided by the candles of the window,
 * which the bundle does not hold. */
function barrierConsistent(body, originAt, horizon) {
  const outcome = body.get("outcome");
  if (!["TARGET", "STOP", "TIMEOUT"].includes(outcome) || priceOf(body.get("p0")) === null || body.get("outcome_price") !== null) return false;
  const touch = body.get("touch_open_ms");
  if (outcome === "TIMEOUT") return touch === null;
  if (!Number.isInteger(touch) || touch % 60000 !== 0) return false;
  if (originAt === null || !Number.isInteger(horizon)) return true;
  return originAt <= touch && touch < originAt + horizon;
}

function settlementOf(forecast, records, ended, origin, originAt) {
  const shown = { settlement: "none", settlement_state: null, settlement_checked: [] };
  const cited = ended.length === 1 ? ended[0].get("settlement_id") : null;
  if (records.length === 0) {
    if (typeof cited === "string") shown.settlement = "absent";
    return [shown, shown.settlement !== "none"];
  }
  if (records.length > 1) {
    shown.settlement = "conflicting";
    return [shown, true];
  }
  const item = records[0];
  const record = item.record;
  const body = record.get("body");
  const forecastId = forecast.get("id");
  const textOrNull = (k) => body.get(k) === null || typeof body.get(k) === "string";
  if (
    !(body instanceof Map) ||
    body.size !== SETTLEMENT_MEMBERS.length ||
    !SETTLEMENT_MEMBERS.every((k) => body.has(k)) ||
    !ENDING_STATES.includes(body.get("state")) ||
    !["outcome", "reason", "p0", "outcome_price"].every(textOrNull) ||
    typeof body.get("contract") !== "string"
  ) {
    shown.settlement = "malformed";
    return [shown, true];
  }
  shown.settlement_state = body.get("state");
  const checked = ["names_forecast"];
  const refs = record.get("refs");
  let wrong = !(Array.isArray(refs) && refs.length === 1 && refs[0] instanceof Map && refs[0].get("id") === forecastId);
  if (typeof cited === "string") {
    checked.push("names_step");
    wrong = wrong || cited !== record.get("id");
  }
  const forecastBody = forecast.get("body");
  const contract = forecastBody instanceof Map ? (forecastBody.get("contract") ?? null) : null;
  const tie = body.get("state") === "VOID" && body.get("reason") === "VOID_TIE";
  if (contract === "D" && body.get("contract") === "D" && (body.get("state") === "SETTLED" || tie)) {
    checked.push("outcome_from_prices");
    const first = priceOf(body.get("p0"));
    const last = priceOf(body.get("outcome_price"));
    if (first === null || last === null) {
      wrong = true;
    } else if (body.get("state") === "SETTLED") {
      const order = comparePrices(last, first);
      wrong = wrong || order === 0 || body.get("outcome") !== (order > 0 ? "1" : "0");
    } else {
      wrong = wrong || comparePrices(last, first) !== 0;
    }
  } else if (contract === "B" && body.get("contract") === "B" && body.get("state") === "SETTLED") {
    checked.push("barrier_touch_consistent");
    wrong = wrong || !barrierConsistent(body, originAt, forecastBody instanceof Map ? (forecastBody.get("horizon_ms") ?? null) : null);
  } else if (contract !== null && body.get("contract") !== contract) {
    wrong = true;
  }
  const horizon = forecastBody instanceof Map ? (forecastBody.get("horizon_ms") ?? null) : null;
  let late = false;
  if ((body.get("state") === "SETTLED" || tie) && origin === "ok" && originAt !== null && Number.isInteger(horizon)) {
    checked.push("after_window");
    late = item.committedMs < originAt + horizon;
  }
  shown.settlement_checked = checked;
  shown.settlement = wrong ? "mismatch" : late ? "early" : "ok";
  return [shown, SETTLEMENT_FAULTS.includes(shown.settlement)];
}

function timingItem(result, rules) {
  return {
    level: result.level,
    error: result.error,
    detail: result.error === null ? null : timing.orderDetail(rules),
    independent_operators: result.independentOperators,
    chosen: [...result.chosen].sort(bySortedName),
    spread_ms: result.spreadMs,
    attested_at: result.attestedAtMs === null ? null : formatMs(result.attestedAtMs),
    eligible_after_offset_ms: result.eligibleAfterOffsetMs,
    window_declared: result.windowDeclared,
    tokens: Object.fromEntries(result.tokens.map((t) => [t.name, t.timely])),
  };
}

function tokenRows(result) {
  if (result === null) return [];
  return result.tokens.map((t) => ({
    name: t.name, valid: t.valid, reason: t.reason, gen_time: t.genTimeMs === null ? null : formatMs(t.genTimeMs), operator: t.operator, parent: t.parent,
    root_sha256: t.rootFingerprint, signer_sha256: t.signerFingerprint,
  }));
}

function timingPart(directory, items, root, authenticated, integrityOk, stampedText) {
  const out = { level: "none", reason: null, assurance_class: null, basis: null, tokens: [], items: {}, subjects: [], public_commit: publication(directory) };
  if (!authenticated || root === null) {
    out.reason = "UNAUTHENTICATED: no trusted root, so no token is believed";
    return out;
  }
  if (!integrityOk) {
    out.reason = "INTEGRITY_FAILED";
    return out;
  }
  const rules = root.timeStamp;
  // what the trusted root asked for: a loose root gives a loose pass, so the verdict says which rules it was judged by
  out.basis = {
    required_independent: rules.requiredIndependent,
    window_before_commit_s: rules.windowBeforeCommitS,
    window_after_commit_s: rules.windowAfterCommitS,
    close_after_commit_s: rules.closeAfterCommitS,
    max_spread_s: rules.maxSpreadS,
    u_constant_s: rules.uConstantS,
  };
  const digest = stampedText !== null && items.length ? createHash("sha256").update(stampedText).digest() : Buffer.alloc(0);
  const evidence = [];
  const tsaDir = join(directory, TSA);
  const names = (listDir(tsaDir) ?? []).filter((n) => n.endsWith(TOKEN_SUFFIX));
  const policy = new TokenPolicy(rules.pinned);
  names.forEach((name, index) => {
    const label = name.slice(0, -TOKEN_SUFFIX.length);
    if (index >= limits.MAX_TOKENS) {
      evidence.push({ name: label, verified: null, reason: "TOO_MANY" });
      return;
    }
    let bytes;
    try {
      bytes = readBundleFile(join(tsaDir, name), MAX_TOKEN_BYTES);
    } catch (error) {
      // SIZE is the code verifyToken gives a token over its limit
      evidence.push({ name: label, verified: null, reason: error instanceof FileTooLarge ? "SIZE" : "UNREADABLE" });
      return;
    }
    try {
      evidence.push({ name: label, verified: verifyToken(bytes, digest, policy), reason: null });
    } catch (error) {
      if (!(error instanceof TokenError)) throw error;
      evidence.push({ name: label, verified: null, reason: error.code });
    }
  });
  const withWindows = items.filter((i) => i.windowMs !== null);
  const subjects = withWindows.length ? withWindows : [...items];
  out.subjects = subjects.map((i) => i.stem);
  const levels = [];
  let first = null;
  for (const item of items) {
    const result = timing.assess(item.committedMs, item.windowMs, evidence, rules, item.createdMs);
    first = first ?? result;
    out.items[item.stem] = timingItem(result, rules);
    if (subjects.includes(item)) levels.push(result.level);
  }
  out.tokens = tokenRows(first);
  out.level = levels.length && levels.every((l) => l === timing.LEVEL_C3) ? timing.LEVEL_C3 : timing.LEVEL_C1;
  out.assurance_class = first ? first.assuranceClass : null;
  return out;
}

const isCommitting = (item) => {
  if (item.record === null || item.record.get("type") !== "cycle_state") return false;
  const body = item.record.get("body");
  return body instanceof Map && body.get("state") === "COMMITTING";
};

// Whether the bundle holds the whole batch of its cycle. The cycle's committing step is the last record of the batch
// that the time-stamps cover, and it names how many records the batch holds beside itself and their root. The records before it in the log,
// that many, are the batch: all must be in the bundle, and their digests, in log order, must make the root it names. A bundle with no
// committing step, or with more than one (a bundle shows one cycle), shows nothing. Mirrors ``verify._cycle``.
function cyclePart(items, checkpointsOk) {
  const steps = items.filter(isCommitting);
  const out = { declared: steps.length > 0, steps: steps.length, batch_size: null, members_present: 0, complete: false };
  if (steps.length !== 1 || !checkpointsOk) return out;
  const step = steps[0];
  const body = step.record.get("body");
  const size = body.get("batch_size");
  const named = body.get("batch_root");
  out.batch_size = typeof size === "number" && Number.isInteger(size) ? size : null;
  if (step.error !== null || !step.canonicalOk || !step.inclusion || step.proof === null || out.batch_size === null || out.batch_size < 1 || typeof named !== "string") return out;
  const at = Number(step.proof.index);
  const count = out.batch_size;
  const held = new Map();
  for (const item of items) {
    if (item.proof !== null && item.error === null && item.canonicalOk && item.inclusion) held.set(Number(item.proof.index), item);
  }
  const members = [];
  for (let k = Math.max(at - count, 0); k < at; k += 1) members.push(held.get(k));
  out.members_present = members.filter((m) => m !== undefined).length;
  if (out.members_present !== count) return out;
  const leaves = members.map((m) => leafHash(createHash("sha256").update(producerBytes(m.record)).digest()));
  out.complete = new MerkleTree(leaves).root().toString("hex") === named;
  return out;
}

function eligibility(items, root, state, verdict) {
  const modes = new Set();
  for (const item of items) {
    if (item.record !== null && item.record.get("type") === EVIDENCE_TYPE) {
      const body = item.record.get("body");
      const mode = body instanceof Map ? body.get("data_mode") : undefined;
      modes.add(typeof mode === "string" ? mode : "missing");
    }
  }
  const forecasts = items.filter((i) => i.record !== null && i.record.get("type") === FORECAST_TYPE && i.error === null && i.inclusion).length;
  const reasons = [];
  if (state !== "PINNED") reasons.push("ROOT_NOT_CURRENT");
  if (!verdict.integrity.ok) reasons.push("INTEGRITY");
  if (verdict.timing.level !== timing.LEVEL_C3) reasons.push("LEVEL_BELOW_C3");
  if (verdict.integrity.refs.unresolved.length > 0) reasons.push("UNRESOLVED_REFERENCE");
  if (!verdict.cycle.complete) reasons.push("COMPLETENESS_NOT_SHOWN");
  if (forecasts === 0) reasons.push("NO_FORECAST");
  if (verdict.cycle.later.failures > 0) reasons.push("ORIGIN_BEFORE_ATTESTATION");
  const required = root !== null ? root.scoredRequiresDataMode : null;
  if (modes.size !== 1 || !modes.has(required)) reasons.push("DATA_MODE");
  return { data_modes: [...modes].sort(bySortedName), forecasts, scorable: reasons.length === 0, reasons };
}

const plain = (value) => (value instanceof Map ? Object.fromEntries([...value].map(([k, v]) => [k, plain(v)])) : Array.isArray(value) ? value.map(plain) : value);

export function verifyBundle(directory, rootArgument, rootStatus = "CURRENT") {
  requireRuntimeUnicode(); // no verdict on a runtime that cannot check the canonical form
  let root = rootArgument;
  let state;
  if (root === null) state = "BUNDLE_ASSERTED";
  else if (rootStatus === "CURRENT") state = "PINNED";
  else if (rootStatus === "EXPIRED") state = "EXPIRED";
  else {
    state = "BUNDLE_ASSERTED";
    root = null;
  }
  const authenticated = root !== null;
  const { stems, errors: structural } = readStems(directory);
  const items = stems.map((stem) => loadItem(directory, stem, root, authenticated));
  const groups = groupsOf(directory, items);
  const oneCheckpoint = items.length > 0 && groups.consistency === "none" && groups.errors.length === 0;
  const refs = referencesOf(items);
  const raw = rawPayloads(directory, items);
  const errors = [...structural, ...groups.errors];
  const itemOk = items.every((i) => i.error === null && i.canonicalOk && i.inclusion);
  const consistent = itemOk && groups.ok && items.length > 0 && refs.mismatched.length === 0 && raw.mismatched.length === 0 && structural.length === 0;
  const verdict = {
    verifier: "node",
    format: 1,
    root_of_trust: { state, version: root !== null ? root.version : null, sha256: root !== null ? root.sha256 : null, status: root !== null ? rootStatus : null },
    integrity: {
      ok: consistent && authenticated,
      self_consistent: consistent,
      one_checkpoint: oneCheckpoint,
      checkpoint: groups.ok ? cpSummary(groups.stampedCheckpoint) : null,
      checkpoints: {
        stamped: groups.ok ? cpSummary(groups.stampedCheckpoint) : null,
        later: groups.ok ? cpSummary(groups.laterCheckpoint) : null,
        consistency: groups.consistency,
      },
      items: Object.fromEntries(items.map((i) => [i.stem, {
        id: i.record !== null ? plain(i.record.get("id") ?? null) : null,
        type: i.record !== null ? plain(i.record.get("type") ?? null) : null,
        committed_at: i.record !== null ? plain(i.record.get("committed_at") ?? null) : null,
        canonical: i.canonicalOk, signature: i.signature, inclusion: i.inclusion, error: i.error, detail: i.detail,
      }])),
      refs, raw,
      errors: [...new Set(errors)].sort(bySortedName),
    },
  };
  verdict.cycle = cyclePart(groups.stamped, groups.ok && items.length > 0);
  verdict.timing = timingPart(directory, groups.stamped, root, authenticated, verdict.integrity.ok, groups.ok ? groups.stampedText : null);
  verdict.timing.later_items = groups.later.map((i) => i.stem);
  verdict.cycle.later = laterPart(groups, verdict.timing, verdict.integrity.ok);
  verdict.eligibility = eligibility(items, root, state, verdict);
  verdict.not_proven = state === "BUNDLE_ASSERTED" ? [...NOT_PROVEN, NOT_PROVEN_WITHOUT_ROOT] : [...NOT_PROVEN];
  return verdict;
}
