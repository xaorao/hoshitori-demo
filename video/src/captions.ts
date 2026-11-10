// The caption (English) and subtitle (Japanese) layers: types, token filling, limits and the SRT writer. The text itself is in
// captions.en.json and subtitles.ja.json (one entry per beat, with its time in and out in seconds).

import { FPS } from "./timing.ts";
import { fillTokens } from "./format.ts";

export type Beat = { beat: string; start: number; end: number; lines: string[] };
export type Layers = { main: Beat[]; cut30: Beat[]; cut10: Beat[] };
export type CompositionKey = keyof Layers;

export const LIMITS = { enLines: 2, enChars: 42, jaLines: 2, jaWidth: 22 } as const;
export const JA_PROPER_NOUNS: readonly string[] = ["RYO", "Python", "Node", "MCP", "REST", "X", "Hoshitori"];

/** Display width in full-width units: a full-width character counts 1, a half-width one 0.5. */
export function jaWidth(line: string): number {
  let w = 0;
  for (const ch of line) w += /[\u0000-ÿ｡-ﾟ]/.test(ch) ? 0.5 : 1;
  return w;
}

/** Latin letters left in a Japanese line once the permitted proper nouns are taken out. */
export function strayLatin(line: string): string[] {
  let rest = line;
  for (const noun of JA_PROPER_NOUNS) rest = rest.split(noun).join(" ");
  return rest.match(/[A-Za-z]+/g) ?? [];
}

export function resolveBeats(beats: readonly Beat[], tokens: Record<string, string>): Beat[] {
  return beats.map((b) => ({ ...b, lines: b.lines.map((l) => fillTokens(l, tokens)) }));
}

/** Problems with one language layer for one composition; an empty list means it is within every limit. */
export function checkLayer(beats: readonly Beat[], lang: "en" | "ja", tokens: Record<string, string>, length: number): string[] {
  const problems: string[] = [];
  const sorted = [...resolveBeats(beats, tokens)].sort((a, b) => a.start - b.start);
  sorted.forEach((b, i) => {
    if (b.end <= b.start) problems.push(`${b.beat}: ends before it starts`);
    if (b.end > length / FPS + 1e-9) problems.push(`${b.beat}: ends after the film`);
    const next = sorted[i + 1];
    if (next !== undefined && b.end > next.start + 1e-9) problems.push(`${b.beat}: overlaps ${next.beat}`);
    if (b.lines.length === 0) problems.push(`${b.beat}: empty`);
    if (lang === "en") {
      if (b.lines.length > LIMITS.enLines) problems.push(`${b.beat}: more than ${LIMITS.enLines} lines`);
      for (const l of b.lines) if (l.length > LIMITS.enChars) problems.push(`${b.beat}: line over ${LIMITS.enChars} characters: ${l}`);
    } else {
      if (b.lines.length > LIMITS.jaLines) problems.push(`${b.beat}: more than ${LIMITS.jaLines} lines`);
      for (const l of b.lines) {
        if (jaWidth(l) > LIMITS.jaWidth) problems.push(`${b.beat}: line wider than ${LIMITS.jaWidth}: ${l}`);
        const stray = strayLatin(l);
        if (stray.length > 0) problems.push(`${b.beat}: Latin letters ${stray.join(",")}`);
      }
    }
  });
  return problems;
}

/** Both layers must describe the same beats at the same times. */
export function checkPair(en: readonly Beat[], ja: readonly Beat[]): string[] {
  const problems: string[] = [];
  const ids = new Set([...en.map((b) => b.beat), ...ja.map((b) => b.beat)]);
  for (const id of ids) {
    const e = en.find((b) => b.beat === id);
    const j = ja.find((b) => b.beat === id);
    if (e === undefined) problems.push(`${id}: no English caption`);
    else if (j === undefined) problems.push(`${id}: no Japanese subtitle`);
    else if (e.start !== j.start || e.end !== j.end) problems.push(`${id}: English and Japanese times differ`);
  }
  return problems;
}

/** The beat showing at a time, or null. */
export function beatAt(beats: readonly Beat[], seconds: number): Beat | null {
  return beats.find((b) => seconds >= b.start - 1e-9 && seconds < b.end - 1e-9) ?? null;
}

function srtTime(seconds: number): string {
  const ms = Math.round(seconds * 1000);
  const h = Math.floor(ms / 3_600_000);
  const m = Math.floor((ms % 3_600_000) / 60_000);
  const s = Math.floor((ms % 60_000) / 1000);
  const r = ms % 1000;
  const p = (n: number, w = 2): string => String(n).padStart(w, "0");
  return `${p(h)}:${p(m)}:${p(s)},${p(r, 3)}`;
}

export function toSrt(beats: readonly Beat[]): string {
  return [...beats]
    .sort((a, b) => a.start - b.start)
    .map((b, i) => `${i + 1}\n${srtTime(b.start)} --> ${srtTime(b.end)}\n${b.lines.join("\n")}\n`)
    .join("\n");
}

export type Overrides = Record<string, { en: string[]; ja: string[] }>;

/** Beats whose text depends on what the record says: no outage to show, or no re-settle count to print. */
export function overridesFor(o: { missedCycles: number; resettleCount: boolean }, text: { noOutageEn: readonly string[]; noOutageJa: readonly string[]; noCountEn: readonly string[]; noCountJa: readonly string[] }): Overrides {
  const out: Overrides = {};
  if (o.missedCycles === 0) out["s7.missed"] = { en: [...text.noOutageEn], ja: [...text.noOutageJa] };
  if (!o.resettleCount) out["s10.check"] = { en: [...text.noCountEn], ja: [...text.noCountJa] };
  return out;
}

export function applyOverrides(beats: readonly Beat[], lang: "en" | "ja", overrides: Overrides): Beat[] {
  return beats.map((b) => {
    const o = overrides[b.beat];
    return o === undefined ? b : { ...b, lines: [...o[lang]] };
  });
}
