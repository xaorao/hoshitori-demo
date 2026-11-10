// Record envelope rules for a record as the ledger holds it. Second implementation of src/hoshitori/envelope.py.
import { TimeError, parseRfc3339 } from "./time.mjs";

export class EnvelopeError extends Error {}

const SPEC = "hoshitori.record/1";
const PRODUCER = new Set(["spec", "type", "type_version", "id", "source", "created_at", "body", "refs", "privacy"]);
const REQUIRED = ["spec", "type", "type_version", "id", "source", "created_at", "body"];
const TYPE = /^[a-z][a-z0-9_]{0,63}$/;
const ID = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;
const SHA256_HEX = /^[0-9a-f]{64}$/;
const PRIVACY = new Set(["public", "private_digest_only"]);

// `record` is a Map as produced by canonical.parseRecord.
export function validateCommittedRecord(record) {
  const committed = record.get("committed_at");
  if (typeof committed !== "string") throw new EnvelopeError("committed_at must be an RFC 3339 string");
  try {
    parseRfc3339(committed);
  } catch (error) {
    if (error instanceof TimeError) throw new EnvelopeError(`committed_at: ${error.message}`);
    throw error;
  }
  const unknown = [...record.keys()].filter((k) => k !== "committed_at" && !PRODUCER.has(k));
  if (unknown.length) throw new EnvelopeError(`unknown envelope members: ${unknown.sort()}`);
  const missing = REQUIRED.filter((k) => !record.has(k));
  if (missing.length) throw new EnvelopeError(`missing envelope members: ${missing}`);
  if (record.get("spec") !== SPEC) throw new EnvelopeError(`spec must be ${SPEC}`);
  const kind = record.get("type");
  if (typeof kind !== "string" || !TYPE.test(kind)) throw new EnvelopeError("type must be a lower-case identifier");
  const version = record.get("type_version");
  if (typeof version !== "number" || !Number.isInteger(version) || version < 1) throw new EnvelopeError("type_version must be a positive integer");
  const id = record.get("id");
  if (typeof id !== "string" || !ID.test(id)) throw new EnvelopeError("id must be 1 to 128 characters of letters, digits and . _ : -");
  const source = record.get("source");
  if (typeof source !== "string" || !source.startsWith("urn:") || [...source].length > 256 || !/^[\x00-\x7f]*$/.test(source)) throw new EnvelopeError("source must be an ASCII urn");
  const created = record.get("created_at");
  if (typeof created !== "string") throw new EnvelopeError("created_at must be an RFC 3339 string");
  try {
    parseRfc3339(created);
  } catch (error) {
    if (error instanceof TimeError) throw new EnvelopeError(`created_at: ${error.message}`);
    throw error;
  }
  if (!(record.get("body") instanceof Map)) throw new EnvelopeError("body must be an object");
  const refs = record.has("refs") ? record.get("refs") : [];
  if (!Array.isArray(refs)) throw new EnvelopeError("refs must be a list");
  for (const ref of refs) {
    const ok = ref instanceof Map && ref.size === 2 && ref.has("id") && ref.has("sha256") && typeof ref.get("id") === "string" && typeof ref.get("sha256") === "string" && SHA256_HEX.test(ref.get("sha256"));
    if (!ok) throw new EnvelopeError("each ref is {id, sha256 (64 lower-case hex)}");
  }
  const privacy = record.has("privacy") ? record.get("privacy") : "public";
  if (!PRIVACY.has(privacy)) throw new EnvelopeError("privacy must be 'public' or 'private_digest_only'");
}
