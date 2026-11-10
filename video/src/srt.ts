// Writes main.en.srt, main.ja.srt (and the cuts') from the caption JSON. Run with: node src/srt.ts <out dir>
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { toSrt, type Layers } from "./captions.ts";

const here = dirname(fileURLToPath(import.meta.url));
const out = process.argv[2] ?? "out";
mkdirSync(out, { recursive: true });
const en = JSON.parse(readFileSync(join(here, "captions.en.json"), "utf8")) as Layers;
const ja = JSON.parse(readFileSync(join(here, "subtitles.ja.json"), "utf8")) as Layers;
const tokens = process.env["FILM_TOKENS"] ? (JSON.parse(process.env["FILM_TOKENS"]) as Record<string, string>) : null;
for (const key of ["main", "cut30", "cut10"] as const) {
  for (const [lang, layers] of [["en", en], ["ja", ja]] as const) {
    const beats = layers[key].map((b) => ({ ...b, lines: b.lines.map((l) => (tokens === null ? l : l.replace(/\{(\w+)\}/g, (_m, k: string) => tokens[k] ?? `{${k}}`))) }));
    writeFileSync(join(out, `${key}.${lang}.srt`), toSrt(beats), "utf8");
  }
}
console.log(`wrote SRT files to ${out}`);
