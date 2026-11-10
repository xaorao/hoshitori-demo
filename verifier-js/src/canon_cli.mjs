// Differential-test adapter: reads one JSON object per line ({"id", "hex"}) on stdin, writes the verdict per line.
import { createInterface } from "node:readline";
import { CanonicalError, canonicalize } from "./canonical.mjs";

const reader = createInterface({ input: process.stdin });
for await (const line of reader) {
  if (!line.trim()) continue;
  const item = JSON.parse(line);
  try {
    const out = canonicalize(Buffer.from(item.hex, "hex")).toString("hex");
    console.log(JSON.stringify({ id: item.id, ok: true, hex: out }));
  } catch (error) {
    const internal = !(error instanceof CanonicalError);
    console.log(JSON.stringify({ id: item.id, ok: false, error: (internal ? "INTERNAL " : "") + error.message }));
  }
}
