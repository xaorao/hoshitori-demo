import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { loadFilm, type Build, type Film, type Reader } from "../src/data.ts";

export const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
export const PUBLIC = join(ROOT, "public");
export const SRC = join(ROOT, "src");
export const DEV = process.env["FILM_DEV"] === "1";

export const fsReader: Reader = {
  async json(path) {
    return JSON.parse(readFileSync(join(PUBLIC, path), "utf8"));
  },
  async text(path) {
    return readFileSync(join(PUBLIC, path), "utf8");
  },
  async head(path, bytes) {
    return new Uint8Array(readFileSync(join(PUBLIC, path)).subarray(0, bytes));
  },
  async exists(path) {
    return existsSync(join(PUBLIC, path)) && statSync(join(PUBLIC, path)).isFile();
  },
};

/** A build stub: the tests are about the film's content, not about the metadata `make meta` writes. */
export const BUILD: Build = { commit: "test", recordSha256: "test", renderedAt: "2000-01-01T00:00:00Z" };

export const hasReal = existsSync(join(PUBLIC, "real", "manifest.json"));
export const hasRecord = existsSync(join(PUBLIC, "record", "today.json"));

let cached: Promise<Film> | null = null;
/** The film as the renderer would load it (stand-ins allowed only when FILM_DEV=1). */
export function film(): Promise<Film> {
  cached ??= loadFilm(fsReader, BUILD, { dev: DEV });
  return cached;
}

export function sceneFiles(): string[] {
  return readdirSync(join(SRC, "scenes")).filter((f) => f.endsWith(".tsx")).sort();
}

export function read(path: string): string {
  return readFileSync(path, "utf8");
}
