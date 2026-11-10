// Differential-test adapter for the bundle verifier. One JSON object per line on stdin, one verdict per line on stdout:
//   {"directory": path, "root": base64 of the root payload | null, "status": "CURRENT" | "EXPIRED" | ...}
import { createInterface } from "node:readline";
import { parseRoot } from "./trusted_root.mjs";
import { verifyBundle } from "./verify.mjs";

const reader = createInterface({ input: process.stdin });
for await (const line of reader) {
  if (!line.trim()) continue;
  const q = JSON.parse(line);
  const root = q.root === null ? null : parseRoot(Buffer.from(q.root, "base64"));
  console.log(JSON.stringify(verifyBundle(q.directory, root, q.status ?? "CURRENT")));
}
