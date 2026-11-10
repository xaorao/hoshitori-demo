// The only door through which numbers, times, ids and program output reach the film. It reads the packed record
// (public/record/*.json), the hero receipt, the Real asset manifest and the recorded logs, and REFUSES to produce a film when a Real asset
// is missing. With `dev` set (FILM_DEV=1 / --props) it may fall back to stand-ins from public/dev/, and every frame then carries a DEV stamp.

import { ALL_NAMES, HERO, IMAGES, LOGS, MANIFEST, VIDEOS, type ImageKey, type LogKey, type VideoKey } from "./assets.ts";
import { largestDisagreement, type Disagreement } from "./format.ts";
import { parseLog, resettleCount, type Log } from "./logs.ts";
import { COUNTER_SECONDS } from "./timing.ts";
import { rankHeroCandidates, selectHero, type HeroRow } from "./select_hero.ts";

export type Reader = {
  json(path: string): Promise<unknown>;
  text(path: string): Promise<string>;
  /** The first bytes of a binary file (enough for a PNG header). */
  head(path: string, bytes: number): Promise<Uint8Array>;
  exists(path: string): Promise<boolean>;
  /** Where the dark pixels of a PNG are, in its own pixels (browser only); absent in tests. */
  ink?(path: string): Promise<Region | null>;
  /** The length of a recording in seconds (browser only); absent in tests. */
  seconds?(path: string): Promise<number>;
};

export type Build = { commit: string | null; recordSha256: string | null; renderedAt: string | null };

/** A rectangle in the picture's own pixels. */
export type Region = { x: number; y: number; w: number; h: number };

/** `ink` is where the dark pixels (the text) are: the crop frames that, so a sparse card is shown large enough to read. */
export type Asset = { path: string; w: number; h: number; standin: boolean; ink: Region; seconds: number };

export type Receipt = {
  as_of: string;
  committed_at: string;
  evidence: { source: string }[];
  invalidations: string[];
  oracles: string;
  row: {
    arm_name: string;
    chance: string;
    forecast_id: string;
    hours: number;
    symbol: string;
    result: string | null;
    settled_at: string | null;
    state: string;
    level: string;
    contract: string;
    cycle_id: string;
  };
  settlement: { outcome_price: string; reference_price: string; oracles: string; state: string };
  timeline: { at: string; state: string }[];
};

export type Today = {
  product: string;
  rows: { contract: string; symbol: string; hours: number; arm_name: string; chance: string | null; forecast_id: string }[];
  changes: { sentence: string; symbol: string; hours: number; arm_name: string; kind: string }[];
  missed_cycles: unknown[];
  mode: string;
};

export type Drill = { id: string; title: string; held: boolean };
export type CompareLine = { first: string; second: string; shared: number; lower: string | null; upper: string | null; state: string; hours: number };

export type Film = {
  dev: boolean;
  /** Names that are stand-ins. Always empty in a real render. */
  standins: string[];
  hero: Receipt;
  today: Today;
  disagreement: Disagreement | null;
  forgery: { was: string; now: string; field: string; cycle: string };
  compare: { first: string; second: string; line: CompareLine };
  drills: Drill[];
  operators: string[];
  logs: Record<LogKey, Log>;
  images: Record<ImageKey, Asset>;
  videos: Record<VideoKey, Asset>;
  meta: { commit: string; rendered: string; recordSha256: string };
  resettle: { line: string } | null;
  tokens: Record<string, string>;
};

export type Props = { dev?: boolean };

// --- the manifest ---------------------------------------------------------------------------------------------------------------------

const FILE_NAME = /([A-Za-z0-9_.-]+\.(?:png|webm|log|json|mp4|svg))$/;

/** The base names of every file the manifest lists, whatever its exact shape (an array of entries, a map by name, or nested). */
export function manifestNames(manifest: unknown): Set<string> {
  const names = new Set<string>();
  const walk = (value: unknown, key: string | null): void => {
    if (key !== null) {
      const k = FILE_NAME.exec(key);
      if (k?.[1] !== undefined) names.add(k[1]);
    }
    if (typeof value === "string") {
      const m = FILE_NAME.exec(value);
      if (m?.[1] !== undefined && !value.includes(" ")) names.add(m[1]);
    } else if (Array.isArray(value)) {
      for (const v of value) walk(v, null);
    } else if (typeof value === "object" && value !== null) {
      for (const [k, v] of Object.entries(value)) walk(v, k);
    }
  };
  walk(manifest, null);
  return names;
}

function findString(value: unknown, keys: string[]): string | null {
  if (typeof value !== "object" || value === null) return null;
  for (const k of keys) {
    const v = (value as Record<string, unknown>)[k];
    if (typeof v === "string" && v !== "") return v;
  }
  for (const v of Object.values(value)) {
    if (typeof v === "object" && v !== null) {
      const hit = findString(v, keys);
      if (hit !== null) return hit;
    }
  }
  return null;
}

export function manifestCommit(manifest: unknown): string | null {
  return findString(manifest, ["commit", "git_commit", "sha", "head"]);
}

/** Read width and height from a PNG header. */
export function pngSize(bytes: Uint8Array): { w: number; h: number } {
  const sig = [0x89, 0x50, 0x4e, 0x47];
  if (bytes.length < 24 || sig.some((b, i) => bytes[i] !== b)) throw new Error("not a PNG");
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  return { w: view.getUint32(16), h: view.getUint32(20) };
}

// --- loading --------------------------------------------------------------------------------------------------------------------------

export async function loadFilm(read: Reader, build: Build, props: Props = {}): Promise<Film> {
  const dev = props.dev === true;
  const problems: string[] = [];

  let manifest: unknown = null;
  if (await read.exists(`real/${MANIFEST}`)) manifest = await read.json(`real/${MANIFEST}`);
  else if (!dev) throw new Error(`public/real/${MANIFEST} is missing: run \`make capture\` and \`make terminals\` before rendering (or set FILM_DEV=1 for stand-ins)`);
  const listed = manifest === null ? new Set<string>() : manifestNames(manifest);

  const standins: string[] = [];
  const locate = async (name: string): Promise<{ path: string; standin: boolean }> => {
    if (listed.has(name) && (await read.exists(`real/${name}`))) return { path: `real/${name}`, standin: false };
    if (!dev) {
      problems.push(listed.has(name) ? `${name} is in the manifest but not on disk` : `${name} is not in the manifest`);
      return { path: `real/${name}`, standin: false };
    }
    if (await read.exists(`dev/${name}`)) {
      standins.push(name);
      return { path: `dev/${name}`, standin: true };
    }
    problems.push(`${name} has neither a real asset nor a dev stand-in`);
    return { path: `real/${name}`, standin: false };
  };

  const images = {} as Record<ImageKey, Asset>;
  for (const [key, name] of Object.entries(IMAGES) as [ImageKey, string][]) {
    const where = await locate(name);
    let size = { w: 1, h: 1 };
    if (problems.length === 0) size = pngSize(await read.head(where.path, 32));
    const ink = problems.length === 0 && read.ink !== undefined ? await read.ink(where.path) : null;
    images[key] = { ...where, ...size, ink: ink ?? { x: 0, y: 0, w: size.w, h: size.h }, seconds: 0 };
  }
  const videos = {} as Record<VideoKey, Asset>;
  for (const [key, name] of Object.entries(VIDEOS) as [VideoKey, string][]) {
    const where = await locate(name);
    const seconds = problems.length === 0 && read.seconds !== undefined ? await read.seconds(where.path) : 0;
    videos[key] = { ...where, w: 0, h: 0, ink: { x: 0, y: 0, w: 0, h: 0 }, seconds };
  }
  const logs = {} as Record<LogKey, Log>;
  for (const [key, name] of Object.entries(LOGS) as [LogKey, string][]) {
    const where = await locate(name);
    if (problems.length > 0) {
      logs[key] = { lines: [], exit: null };
      continue;
    }
    const log = parseLog(await read.text(where.path));
    const exitPath = where.path.replace(/\.log$/, ".exit");
    if (await read.exists(exitPath)) {
      const code = Number((await read.text(exitPath)).trim());
      if (Number.isInteger(code)) log.exit = code;
    }
    logs[key] = log;
  }
  if (problems.length > 0) {
    throw new Error(`the film refuses to render, ${problems.length} Real asset problem(s):\n  ${problems.join("\n  ")}`);
  }

  // Provenance: a real render must carry a commit, a record id and a render time.
  const commit = manifestCommit(manifest) ?? build.commit;
  const recordSha256 = findString(manifest, ["record_id", "record_sha256"]) ?? build.recordSha256;
  const rendered = build.renderedAt ?? findString(manifest, ["generated_at"]);
  if (!dev && (commit === null || recordSha256 === null || rendered === null)) {
    throw new Error("build metadata is incomplete: run `make meta` (commit, record sha256, render time) before rendering");
  }
  if (!dev) {
    for (const [key, log] of Object.entries(logs) as [LogKey, Log][]) {
      if (log.exit === null) throw new Error(`${LOGS[key]} carries no exit code; record_terminals.sh must write one`);
      if (log.lines.length === 0) throw new Error(`${LOGS[key]} is empty`);
    }
  }

  // The record.
  const rows = (await read.json("record/rows.json")) as (HeroRow & { symbol: string })[];
  const today = (await read.json("record/today.json")) as Today;
  const heroId = await chooseHero(read, rows, dev);
  const hero = (await read.json(`record/receipts/${heroId}.json`)) as Receipt;
  const forgeryDoc = (await read.json("record/forgery.json")) as { cycle_id: string; edit: { field: string; was: string; now: string } };
  const compareDoc = (await read.json("record/comparisons.json")) as { lines: CompareLine[] };
  const recordDoc = (await read.json("record/record.json")) as { arm: string; arm_name: string }[];
  const drillDoc = (await read.json("record/drill.json")) as { id: string; title: string; held: boolean }[];
  const root = (await read.json("record/trusted_root.json")) as { time_stamp: { operators: Record<string, { parent: string }> } };

  const first = compareDoc.lines[0];
  if (first === undefined) throw new Error("comparisons.json has no line");
  const armName = (arm: string): string => recordDoc.find((a) => a.arm === arm)?.arm_name ?? arm;
  const operators = Object.values(root.time_stamp.operators).map((o) => o.parent.split(" ")[0] ?? o.parent);

  const heldAll = drillDoc.every((d) => d.held);
  if (!heldAll) throw new Error("a failure drill did not hold; the film's scene 7 would misstate the record");

  const film: Film = {
    dev,
    standins,
    hero,
    today,
    disagreement: largestDisagreement(today.rows),
    forgery: { was: forgeryDoc.edit.was, now: forgeryDoc.edit.now, field: forgeryDoc.edit.field, cycle: forgeryDoc.cycle_id },
    compare: { first: armName(first.first), second: armName(first.second), line: first },
    drills: drillDoc.map((d) => ({ id: d.id, title: d.title, held: d.held })),
    operators,
    logs,
    images,
    videos,
    meta: {
      commit: commit ?? "dev",
      rendered: rendered ?? "dev",
      recordSha256: recordSha256 ?? "dev",
    },
    resettle: null,
    tokens: {},
  };
  const count = resettleCount(logs.resettle);
  film.resettle = count === null ? null : { line: count.line };
  film.tokens = { drills: String(film.drills.length), secs: String(COUNTER_SECONDS) };
  return film;
}

async function chooseHero(read: Reader, rows: readonly HeroRow[], dev: boolean): Promise<string> {
  if (await read.exists(`real/${HERO}`)) {
    const doc = await read.json(`real/${HERO}`);
    const id = findString(doc, ["forecast_id", "receipt", "id", "hero"]);
    if (id === null) throw new Error(`${HERO} names no receipt`);
    return id.replace(/^receipts\//, "").replace(/\.json$/, "");
  }
  if (!dev) throw new Error(`public/real/${HERO} is missing: capture.py writes it`);
  const usable = async (id: string): Promise<boolean> => {
    if (!(await read.exists(`record/receipts/${id}.json`))) return false;
    const r = (await read.json(`record/receipts/${id}.json`)) as Receipt;
    return Array.isArray(r.invalidations) && r.invalidations.length > 0 && r.settlement?.state === "SETTLED";
  };
  return selectHero(rows, usable);
}

export { rankHeroCandidates, ALL_NAMES };
