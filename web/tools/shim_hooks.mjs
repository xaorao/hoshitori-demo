// A module-resolution hook for the shim test: while the verifier's own source (verifier-js/src) is loaded, node:crypto, node:fs and node:path
// resolve to the stand-ins the page uses, so the same verifier code runs on them in Node and can be compared with the real thing.
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

const root = new URL("../src/verify/", import.meta.url);
const SHIMS = {
  "node:crypto": new URL("node_crypto.ts", root).href,
  "node:fs": new URL("node_fs.ts", root).href,
  "node:path": new URL("node_path.ts", root).href,
};
const verifierSource = pathToFileURL(resolve(new URL("../../verifier-js/src/", import.meta.url).pathname)).href;

export async function resolveHook(specifier, context, next) {
  if (specifier in SHIMS && context.parentURL?.startsWith(verifierSource)) return { url: SHIMS[specifier], shortCircuit: true };
  return next(specifier, context);
}
export { resolveHook as resolve };
