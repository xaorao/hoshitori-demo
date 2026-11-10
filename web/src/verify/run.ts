// Check a bundle in the page: fetch its files, put them where the verifier looks, and run the verifier of verifier-js on them with the
// pure-JavaScript crypto. Loaded only when a person asks for a check (it carries the curves and hashes), so the pages open without it.
import "./buffer_global.ts";
import { parseRoot } from "../../../verifier-js/src/trusted_root.mjs";
import { verifyBundle, type Verdict } from "../../../verifier-js/src/verify.mjs";
import { setFiles } from "./node_fs.ts";

export type { Verdict };
export type BundleFiles = { cycle_id: string; path: string; files: string[]; directories: string[] };

export class FetchError extends Error {}

async function get(url: string): Promise<Uint8Array> {
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-cache" });
  } catch {
    throw new FetchError(`${url} could not be fetched`);
  }
  if (!response.ok) throw new FetchError(`${url} answered ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

/** Every file of the bundle, a few at a time; a file that cannot be fetched is an error, never a silently smaller bundle. */
export async function fetchBundle(base: string, bundle: BundleFiles, parallel = 8): Promise<Record<string, Uint8Array>> {
  const out: Record<string, Uint8Array> = {};
  let next = 0;
  const worker = async (): Promise<void> => {
    for (;;) {
      const at = next++;
      const file = bundle.files[at];
      if (file === undefined) return;
      out[`/bundle/${file}`] = await get(`${base}/${bundle.path}/${file}`);
    }
  };
  await Promise.all(Array.from({ length: Math.min(parallel, bundle.files.length) }, worker));
  return out;
}

/** Run the verifier on files already in memory, against a trusted-root document given as bytes. */
export function checkFiles(files: Record<string, Uint8Array>, directories: string[], rootBytes: Uint8Array): Verdict {
  setFiles(files, directories.map((d) => `/bundle/${d}`));
  const root = parseRoot(Buffer.from(rootBytes)); // the verifier wants a Buffer, as it gets from a file in Node
  return verifyBundle("/bundle", root, "CURRENT");
}

export async function checkBundle(base: string, bundle: BundleFiles, rootBytes: Uint8Array): Promise<Verdict> {
  return checkFiles(await fetchBundle(base, bundle), bundle.directories, rootBytes);
}
