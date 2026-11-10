// Differential-test adapter for the trusted-root parser and update client. One JSON object per line in, one per line out.
//   {"op": "parse", "payload": base64}
//   {"op": "update", "initial": base64, "channels": [{version: envelope}], "now_ms": number, "max_chain": number | null, "floor": number}
import { createInterface } from "node:readline";
import { RootError, parseRoot, update } from "./trusted_root.mjs";

function facts(root) {
  const t = root.timeStamp;
  return {
    version: root.version, expires_ms: root.expires, threshold: root.rootThreshold, keys: [...root.rootKeys.keys()].sort(), origin: root.origin,
    eras: root.eras.map((e) => [e.key.name, e.key.keyId.toString("hex"), e.fromSize, e.throughSize]),
    pinned: t.pinned.map((p) => [p.sha256, p.operator, p.der !== null]),
    operators: Object.fromEntries([...t.operators].sort(([a], [b]) => (a < b ? -1 : 1)).map(([n, o]) => [n, [o.parent, o.evidence === null ? null : [o.evidence.kind, o.evidence.ref, o.evidence.subjectSha256]]])),
    kinds: [...t.evidenceKindsAccepted].sort(),
    rules: [t.requiredIndependent, t.windowBeforeCommitS, t.windowAfterCommitS, t.closeAfterCommitS, t.maxSpreadS, t.uConstantS],
    witnesses: [root.witnessesRequired, root.witnessKeys.map((k) => k.keyId.toString("hex"))],
    mode: root.scoredRequiresDataMode, sha256: root.sha256,
  };
}

const reader = createInterface({ input: process.stdin });
for await (const line of reader) {
  if (!line.trim()) continue;
  const q = JSON.parse(line);
  try {
    if (q.op === "parse") {
      console.log(JSON.stringify({ ok: true, facts: facts(parseRoot(Buffer.from(q.payload, "base64"))) }));
    } else {
      const options = { floorVersion: q.floor };
      if (q.max_chain !== null) options.maxChain = q.max_chain;
      const r = update(parseRoot(Buffer.from(q.initial, "base64")), q.channels, q.now_ms, options);
      console.log(JSON.stringify({ status: r.status, code: r.code, version: r.root.version, sha256: r.root.sha256, single: r.singleChannel }));
    }
  } catch (error) {
    console.log(JSON.stringify({ ok: false, code: error instanceof RootError ? error.code : "INTERNAL", message: String(error.message).slice(0, 200) }));
  }
}
