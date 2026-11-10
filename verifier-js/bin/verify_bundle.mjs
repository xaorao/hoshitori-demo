#!/usr/bin/env node
// hoshitori-verify (Node): check a verification bundle offline. Same arguments, verdict and exit codes as the Python tool
// (src/hoshitori/cli.py); see that file for the meaning of each option. A runtime whose Unicode is older than 15.0 gives no verdict
// (exit status 2), as in the Python tool.
import { readFileSync } from "node:fs";
import { UnicodeVersionError } from "../src/canonical.mjs";
import { TimeError, parseRfc3339 } from "../src/time.mjs";
import { RootError, parseRoot, update } from "../src/trusted_root.mjs";
import { verifyBundle } from "../src/verify.mjs";

function usage(message) {
  console.error(`hoshitori-verify: ${message}`);
  process.exit(2);
}

const args = process.argv.slice(2);
let bundle = null;
let trustedRoot = null;
const channels = [];
let now = null;
let floor = 0;
for (let i = 0; i < args.length; i++) {
  const a = args[i];
  if (a === "--trusted-root") trustedRoot = args[++i];
  else if (a === "--channel") channels.push(args[++i]);
  else if (a === "--now") now = args[++i];
  else if (a === "--floor-version") floor = Number(args[++i]);
  else if (a.startsWith("--")) usage(`unknown option ${a}`);
  else if (bundle === null) bundle = a;
  else usage("one bundle only");
}
if (bundle === null) usage("a bundle directory is required");
if (trustedRoot === undefined || channels.includes(undefined) || now === undefined || !Number.isInteger(floor)) usage("an option is missing its value");

let root = null;
let status = "CURRENT";
let summary = null;
try {
  const nowMs = now ? parseRfc3339(now) : Date.now();
  if (trustedRoot !== null) {
    const initial = parseRoot(readFileSync(trustedRoot));
    const loaded = channels.map((path) => {
      const data = JSON.parse(readFileSync(path, "utf8"));
      if (data === null || typeof data !== "object" || Array.isArray(data) || !Object.keys(data).every((k) => /^(0|[1-9][0-9]*)$/.test(k))) throw new Error("a channel file maps versions to envelopes");
      return data;
    });
    const result = update(initial, loaded, nowMs, { floorVersion: floor });
    root = result.root;
    status = result.status;
    summary = { status: result.status, code: result.code, version: result.root.version, single_channel: result.singleChannel };
  } else if (channels.length) {
    throw new Error("--channel needs --trusted-root");
  }
} catch (error) {
  if (error instanceof TimeError || error instanceof RootError || error instanceof Error) usage(error.message);
  throw error;
}
let verdict;
try {
  verdict = JSON.parse(JSON.stringify(verifyBundle(bundle, root, status)));
} catch (error) {
  if (error instanceof UnicodeVersionError) usage(error.message); // no verdict on a runtime that cannot check the canonical form
  throw error;
}
if (summary !== null) verdict.root_update = summary;
const sortKeys = (v) => (Array.isArray(v) ? v.map(sortKeys) : v !== null && typeof v === "object" ? Object.fromEntries(Object.entries(v).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([k, x]) => [k, sortKeys(x)])) : v);
console.log(JSON.stringify(sortKeys(verdict), null, 2));
// not process.exit(): a verdict larger than a pipe holds is still being written when the process would end, and would be cut off
process.exitCode = verdict.integrity.ok ? 0 : 1;
