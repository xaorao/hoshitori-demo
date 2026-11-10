// Loading the exported documents. Each document is checked against the digest the index holds for it before it is used, so a
// half-copied or edited file is a visible failure and never a quiet wrong number.

import { when } from "./format.ts";
import type { ModelChallenge, SiteIndex } from "./types.ts";

export class LoadError extends Error {
  readonly kind: "missing" | "changed" | "unreadable";
  readonly path: string;
  constructor(
    kind: "missing" | "changed" | "unreadable",
    path: string,
    message: string,
  ) {
    super(message);
    this.kind = kind;
    this.path = path;
  }
}

export async function sha256Hex(
  bytes: ArrayBuffer | Uint8Array<ArrayBuffer>,
): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)]
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** The parsed document if its bytes are the ones the index names; otherwise a LoadError that says which. */
export async function checked<T>(
  index: SiteIndex,
  path: string,
  bytes: Uint8Array<ArrayBuffer>,
): Promise<T> {
  const want = index.files[path];
  if (want === undefined)
    throw new LoadError(
      "missing",
      path,
      `${path} is not in the index of this site`,
    );
  const got = await sha256Hex(bytes);
  if (got !== want)
    throw new LoadError(
      "changed",
      path,
      `${path} is not the file the index describes`,
    );
  try {
    return JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    ) as T;
  } catch {
    throw new LoadError("unreadable", path, `${path} is not readable JSON`);
  }
}

async function fetchBytes(
  url: string,
  path: string,
): Promise<Uint8Array<ArrayBuffer>> {
  let response: Response;
  try {
    response = await fetch(url, { cache: "no-cache" });
  } catch {
    throw new LoadError("missing", path, `${path} could not be fetched`);
  }
  if (!response.ok)
    throw new LoadError("missing", path, `${path} answered ${response.status}`);
  return new Uint8Array(await response.arrayBuffer());
}

/** A document's bytes, if they are the ones the index names (the trusted root is read as bytes, exactly as published). */
export async function checkedBytes(
  index: SiteIndex,
  path: string,
  bytes: Uint8Array<ArrayBuffer>,
): Promise<Uint8Array<ArrayBuffer>> {
  const want = index.files[path];
  if (want === undefined)
    throw new LoadError(
      "missing",
      path,
      `${path} is not in the index of this site`,
    );
  if ((await sha256Hex(bytes)) !== want)
    throw new LoadError(
      "changed",
      path,
      `${path} is not the file the index describes`,
    );
  return bytes;
}

export async function loadBytes(
  base: string,
  index: SiteIndex,
  path: string,
): Promise<Uint8Array<ArrayBuffer>> {
  return checkedBytes(index, path, await fetchBytes(`${base}/${path}`, path));
}

/** The format of the documents this page reads. `hoshitori-league site` writes the same number as `FORMAT` in `site_export.py`, and it is raised
 * whenever a document's fields change (a test in the repository pins their shapes and compares the two numbers), so a page never shows
 * an export written for other fields as if it were this one's. */
export const FORMAT = 24;

/** The sentence for an index of another format: what it states, what this page reads, and what to do. */
export function formatWords(stated: unknown): string {
  const said =
    typeof stated === "number" && Number.isInteger(stated)
      ? `exported in format ${stated}`
      : stated === undefined || stated === null
        ? "states no format"
        : `states the format ${typeof stated === "string" ? JSON.stringify(stated) : String(stated)}`;
  return `This site was ${said}, and this page reads format ${FORMAT}. Export it again with the same version of hoshitori-league as this page.`;
}

export async function loadIndex(base: string): Promise<SiteIndex> {
  const bytes = await fetchBytes(`${base}/index.json`, "index.json");
  let parsed: SiteIndex;
  try {
    parsed = JSON.parse(
      new TextDecoder("utf-8", { fatal: true }).decode(bytes),
    ) as SiteIndex;
  } catch {
    throw new LoadError(
      "unreadable",
      "index.json",
      "index.json is not a site index this page understands",
    );
  }
  if (
    typeof parsed !== "object" ||
    parsed === null ||
    parsed.format !== FORMAT
  ) {
    throw new LoadError(
      "unreadable",
      "index.json",
      formatWords(
        typeof parsed === "object" && parsed !== null
          ? parsed.format
          : undefined,
      ),
    );
  }
  try {
    const index = parsed;
    // every page says when its documents were made, outside the page itself: a time it cannot read would take the whole site down
    when(index.generated_at);
    if (Number.isNaN(Date.parse(index.generated_at))) throw new Error("time");
    // and, the same way, the state of the ledger they were exported from, when the index states it
    const ledger: unknown = index.ledger;
    if (ledger !== undefined) {
      const l = ledger as { state?: unknown; detail?: unknown } | null;
      if (
        typeof l !== "object" ||
        l === null ||
        typeof l.state !== "string" ||
        l.state === "" ||
        typeof l.detail !== "string"
      )
        throw new Error("ledger");
    }
    return index;
  } catch {
    throw new LoadError(
      "unreadable",
      "index.json",
      "index.json is not a site index this page understands",
    );
  }
}

export async function loadDocument<T>(
  base: string,
  index: SiteIndex,
  path: string,
): Promise<T> {
  return checked<T>(index, path, await fetchBytes(`${base}/${path}`, path));
}

export const MODEL_CHALLENGES = "model_challenges.json";

/** The recorded challenges of the model agent, if the site was exported with them; none when its index does not list them. The index is
 * read again here, so the document is checked against the digest its own index holds. */
export async function loadModelChallenges(
  base: string,
): Promise<ModelChallenge[] | null> {
  const index = await loadIndex(base);
  if (index.files[MODEL_CHALLENGES] === undefined) return null;
  return loadDocument<ModelChallenge[]>(base, index, MODEL_CHALLENGES);
}
