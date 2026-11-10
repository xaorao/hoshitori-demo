import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig, type Plugin } from "vite";

// While the verifier's own source (verifier-js/src) is bundled, node:crypto, node:fs and node:path stand for the pure-JavaScript versions
// the page uses (src/verify). The same swap runs in Node in tools/shim_hooks.mjs, where the verifier is compared with the real thing.
const shims: Record<string, string> = {
  "node:crypto": fileURLToPath(new URL("./src/verify/node_crypto.ts", import.meta.url)),
  "node:fs": fileURLToPath(new URL("./src/verify/node_fs.ts", import.meta.url)),
  "node:path": fileURLToPath(new URL("./src/verify/node_path.ts", import.meta.url)),
};

const verifierShims = (): Plugin => ({
  name: "verifier-shims",
  enforce: "pre",
  resolveId(source, importer) {
    if (importer?.includes("/verifier-js/src/") && source in shims) return shims[source];
    return null;
  },
});

// base "./" so the built site works from any path (a file share, a bucket, a subdirectory)
export default defineConfig({
  base: "./",
  plugins: [verifierShims(), react()],
  build: { target: "es2023", sourcemap: false },
  server: { fs: { allow: [".."] } },
});
