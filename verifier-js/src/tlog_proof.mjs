// C2SP tlog-proof v1 reader. Second implementation of src/hoshitori/tlog_proof.py; same rules, same limits.
import { b64decode, NoteError } from "./note.mjs";

export const HEADER = "c2sp.org/tlog-proof@v1";
export const MAX_PATH = 64;
export const MAX_PROOF_BYTES = 65536;
const MAX_TREE_SIZE = 2n ** 63n - 1n;

export class ProofError extends Error {}

function decimal(text, what) {
  if (!/^(0|[1-9][0-9]*)$/.test(text)) throw new ProofError(`${what} must be an ASCII decimal without leading zeros`);
  const value = BigInt(text);
  if (value > MAX_TREE_SIZE) throw new ProofError(`${what} out of range`);
  return value;
}

function hash(text) {
  let raw;
  try {
    raw = b64decode(text);
  } catch (error) {
    if (error instanceof NoteError) throw new ProofError(`bad base64: ${error.message}`);
    throw error;
  }
  if (raw.length !== 32) throw new ProofError("a path hash is exactly 32 bytes");
  return raw;
}

export function parseProof(data) {
  if (data.length > MAX_PROOF_BYTES) throw new ProofError("proof too large");
  let text;
  try {
    text = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true }).decode(data);
  } catch {
    throw new ProofError("proof is not valid UTF-8");
  }
  if (text.includes("\r")) throw new ProofError("lines end in a bare newline");
  const lines = text.split("\n");
  if (lines.length === 0 || lines[0] !== HEADER) throw new ProofError(`the first line is ${JSON.stringify(HEADER)}`);
  let position = 1;
  let extra = null;
  if (position < lines.length && lines[position].startsWith("extra ")) {
    try {
      extra = b64decode(lines[position].slice("extra ".length));
    } catch (error) {
      if (error instanceof NoteError) throw new ProofError(`extra data: ${error.message}`);
      throw error;
    }
    position += 1;
  }
  if (position >= lines.length || !lines[position].startsWith("index ")) throw new ProofError("the index line is missing");
  const index = decimal(lines[position].slice("index ".length), "index");
  position += 1;
  const path = [];
  while (position < lines.length && lines[position] !== "") {
    path.push(hash(lines[position]));
    if (path.length > MAX_PATH) throw new ProofError("inclusion path too long");
    position += 1;
  }
  if (position >= lines.length) throw new ProofError("the blank line before the checkpoint is missing");
  const note = Buffer.from(lines.slice(position + 1).join("\n"), "utf8");
  if (note.length === 0) throw new ProofError("the checkpoint is missing");
  return { index, path, note, extra };
}
