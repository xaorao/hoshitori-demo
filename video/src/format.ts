// Small pure helpers that word what the record holds. Their wording mirrors web/src/format.ts (the product's own helpers): the same
// rounding on digits (no floats), the same "4 hours", the same thousands separators. Nothing here knows about Remotion.

export const ARROW = "\u2192";

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

function decimalParts(text: string): { negative: boolean; whole: string; fraction: string } {
  const m = DECIMAL.exec(text);
  if (m === null) throw new Error(`not a decimal: ${text}`);
  return { negative: m[1] === "-", whole: m[2] ?? "0", fraction: m[3] ?? "" };
}

/** A decimal rounded half away from zero to `places` places, from its digits: "98892.5875" to 2 places is "98892.59". */
export function roundDecimal(text: string, places: number): string {
  const { negative, whole, fraction } = decimalParts(text);
  const scaled = BigInt(whole + fraction);
  let units: bigint;
  if (fraction.length <= places) {
    units = scaled * 10n ** BigInt(places - fraction.length);
  } else {
    const divisor = 10n ** BigInt(fraction.length - places);
    units = (scaled + divisor / 2n) / divisor;
  }
  const digits = units.toString().padStart(places + 1, "0");
  const head = digits.slice(0, digits.length - places);
  const tail = digits.slice(digits.length - places);
  const out = places === 0 ? head : `${head}.${tail}`;
  return negative && units !== 0n ? `-${out}` : out;
}

/** An amount of money with thousands separators and its own digits: "101398.5" as "101,398.5". */
export function groupedMoney(text: string): string {
  const { negative, whole, fraction } = decimalParts(text);
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${fraction === "" ? "" : `.${fraction}`}`;
}

/** "98892.5875" as "98,892.59". */
export function money2(text: string): string {
  return groupedMoney(roundDecimal(text, 2));
}

/** A signed two-place number with a true minus sign, the way the interval bar is labelled: "-0.1622" as "−0.16", "0.1740" as "+0.17". */
export function signed2(text: string): string {
  const rounded = roundDecimal(text, 2);
  return rounded.startsWith("-") ? `−${rounded.slice(1)}` : `+${rounded}`;
}

/** A decimal string of a record without its trailing zeros ("0.030000" is "0.03"). */
export function plainDecimal(text: string): string {
  if (!/^\d+(\.\d+)?$/.test(text)) throw new Error(`not a decimal: ${text}`);
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

/** "1 hour", "4 hours". */
export function horizon(hours: number): string {
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

/** A count with its noun: "1 call", "3 calls". */
export function count(n: number, one: string, many: string = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The kicker of a call as the receipt writes it: "BTC · 4 hours · Single model". */
export function callKicker(row: { symbol: string; hours: number; arm_name: string }): string {
  return `${row.symbol} · ${horizon(row.hours)} · ${row.arm_name}`;
}

const TIME = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})/;

function parts(iso: string): RegExpExecArray {
  const m = TIME.exec(iso);
  if (m === null) throw new Error(`not a time: ${iso}`);
  return m;
}

/** "2026-11-09T16:09:14.827Z" as "16:09:14". */
export function clock(iso: string): string {
  const m = parts(iso);
  return `${m[4]}:${m[5]}:${m[6]}`;
}

/** "2026-11-09T16:09:14.827Z" as "2026-11-09 16:09:14 UTC". */
export function stamp(iso: string): string {
  const m = parts(iso);
  return `${m[1]}-${m[2]}-${m[3]} ${m[4]}:${m[5]}:${m[6]} UTC`;
}

/** "2026-11-09T16:09:14.827Z" as "2026-11-09". */
export function dateOnly(iso: string): string {
  const m = parts(iso);
  return `${m[1]}-${m[2]}-${m[3]}`;
}

/** "0:13": a mm:ss counter from whole seconds. */
export function counter(seconds: number): string {
  const whole = Math.max(0, Math.floor(seconds));
  return `${Math.floor(whole / 60)}:${String(whole % 60).padStart(2, "0")}`;
}

/** "75189.70000000" and "74932.01000000" as "75,189.70 → 74,932.01". */
export function priceMove(from: string, to: string): string {
  return `${money2(from)} → ${money2(to)}`;
}

/** The chance of a rise in an up-or-down call as a whole number ("64% up" is 64), or null for any other contract. */
export function upPercent(chance: string | null): number | null {
  if (chance === null) return null;
  const m = /^(\d+)% up$/.exec(chance);
  return m === null ? null : Number(m[1]);
}

/** Shorten a line to `limit` characters with an ellipsis at the end. */
export function clip(text: string, limit: number): string {
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1)).trimEnd()}…`;
}

/** A middle ellipsis for ids: keeps both ends. */
export function middle(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const keep = limit - 1;
  const head = Math.ceil(keep / 2);
  return `${text.slice(0, head)}…${text.slice(text.length - (keep - head))}`;
}

export type Pair = { arm_name: string; p: number; forecast_id: string };
export type Disagreement = { symbol: string; hours: number; low: Pair; high: Pair; gap: number };

/** The product's own floor for "the agents disagree": one at most 49, the other at least 51, and this far apart. */
export const DISAGREEMENT_GAP = 6;

/** The pair of agents furthest apart on one call among a cycle's rows (web/src/format.ts `largestDisagreement`, same rule). */
export function largestDisagreement(
  rows: readonly { contract: string; symbol: string; hours: number; arm_name: string; chance: string | null; forecast_id: string }[],
): Disagreement | null {
  let best: Disagreement | null = null;
  const groups = new Map<string, Pair[]>();
  for (const r of rows) {
    if (r.contract !== "D") continue;
    const p = upPercent(r.chance);
    if (p === null) continue;
    const key = `${r.symbol}\u0000${r.hours}`;
    const list = groups.get(key) ?? [];
    list.push({ arm_name: r.arm_name, p, forecast_id: r.forecast_id });
    groups.set(key, list);
  }
  const keys = [...groups.keys()].sort((a, b) => {
    const [sa = "", ha = ""] = a.split("\u0000");
    const [sb = "", hb = ""] = b.split("\u0000");
    return sa.localeCompare(sb) || Number(ha) - Number(hb);
  });
  for (const key of keys) {
    const list = groups.get(key) ?? [];
    if (list.length === 0) continue;
    const low = list.reduce((a, b) => (b.p < a.p ? b : a));
    const high = list.reduce((a, b) => (b.p > a.p ? b : a));
    const gap = high.p - low.p;
    if (low.p > 49 || high.p < 51 || gap < DISAGREEMENT_GAP) continue;
    if (best === null || gap > best.gap) {
      const [symbol, hours] = key.split("\u0000");
      best = { symbol: symbol ?? "", hours: Number(hours), low, high, gap };
    }
  }
  return best;
}

/** "Rules 50% up · Single model 54% up · LINK, 4 hours": the two sides ordered as the product orders them (the lower chance first). */
export function disagreementLine(d: Disagreement): string {
  return `${d.low.arm_name} ${d.low.p}% up · ${d.high.arm_name} ${d.high.p}% up · ${d.symbol}, ${horizon(d.hours)}`;
}

/** Replace {name} tokens in a caption by values the data supplies; an unknown token is an error, never a blank. */
export function fillTokens(text: string, values: Record<string, string>): string {
  return text.replace(/\{([a-z0-9_]+)\}/gi, (_all, key: string) => {
    const v = values[key];
    if (v === undefined) throw new Error(`caption token without a value: {${key}}`);
    return v;
  });
}

export const DOT = "\u00b7";

/** Join parts with the middle dot the product uses between facts: "BTC · 4 hours". */
export function dots(...parts: (string | number)[]): string {
  return parts.join(` ${DOT} `);
}
