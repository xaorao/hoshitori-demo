// Terminal logs recorded by record_terminals.sh (`script -q`). The film types text taken from these files and never retypes it by hand.
// The parser is deliberately tolerant about the recorder's framing (ANSI colour, carriage returns, `script` banners) and strict about content.

export type Log = {
  /** Clean lines, in order, without banners, blank edges or the exit marker. */
  lines: string[];
  /** The exit code the recorder captured, or null when the log carries none. */
  exit: number | null;
};

// eslint-disable-next-line no-control-regex
const ANSI = /\u001b\[[0-9;?]*[ -/]*[@-~]|\u001b\][^\u0007]*\u0007|\u001b[()][A-Za-z0-9]/g;
const BANNER = /^(Script (started|done)|\^D|\[?(Process|Command) (completed|exited))/i;
const EXIT_LINE = /^(?:\[?\s*)?(?:exit(?:\s*(?:code|status))?|\+\+exit|rc)\s*[:=]?\s*(-?\d+)\s*\]?\s*$/i;
const EXIT_TRAIL = /^(?:Script done.*|.*\bexit(?:\s*code)?[:= ]+(-?\d+)\b.*)$/i;

/** Strip control framing from a recorded terminal. */
export function cleanText(raw: string): string {
  let text = raw.replace(ANSI, "");
  // Backspace editing from the line discipline: remove the character before it.
  while (/[^\u0008]\u0008/.test(text)) text = text.replace(/[^\u0008]\u0008/g, "");
  return text.replace(/\u0008/g, "").replace(/\r\n/g, "\n").replace(/\r/g, "\n");
}

export function parseLog(raw: string): Log {
  const out: string[] = [];
  let exit: number | null = null;
  for (const original of cleanText(raw).split("\n")) {
    const line = original.replace(/\s+$/, "");
    const trimmed = line.trim();
    if (BANNER.test(trimmed)) continue;
    const e = EXIT_LINE.exec(trimmed);
    if (e !== null) {
      exit = Number(e[1]);
      continue;
    }
    out.push(line);
  }
  while (out.length > 0 && out[0]?.trim() === "") out.shift();
  while (out.length > 0 && out[out.length - 1]?.trim() === "") out.pop();
  if (exit === null) {
    const last = [...out].reverse().find((l) => l.trim() !== "");
    const t = last === undefined ? null : EXIT_TRAIL.exec(last.trim());
    if (t?.[1] !== undefined && /^exit/i.test(last?.trim() ?? "")) exit = Number(t[1]);
  }
  return { lines: out, exit };
}

const PROMPT = /^(?:[$%#>]|\(.*\)\s*[$%#>]|\S+@\S+[^$%#>]*[$%#>])\s+(.*)$/;

/** The command the recorder typed: the first prompt line, or null when the log shows none. */
export function commandOf(log: Log): string | null {
  for (const l of log.lines) {
    const m = PROMPT.exec(l);
    if (m?.[1] !== undefined && m[1].trim() !== "") return m[1].trim();
  }
  return null;
}

/** All prompt lines, without their prompt. */
export function commandsOf(log: Log): string[] {
  const out: string[] = [];
  for (const l of log.lines) {
    const m = PROMPT.exec(l);
    if (m?.[1] !== undefined && m[1].trim() !== "") out.push(m[1].trim());
  }
  return out;
}

/** Lines that are output, not prompt lines. */
export function outputOf(log: Log): string[] {
  return log.lines.filter((l) => !PROMPT.test(l) && l.trim() !== "");
}

/** The last non-empty line of output. */
export function lastLine(log: Log): string | null {
  const out = outputOf(log);
  return out.length === 0 ? null : (out[out.length - 1] ?? null);
}

/** The first complete JSON object document in a log (a line that is just "{" starts it), or null. */
export function firstJson(log: Log): Record<string, unknown> | null {
  const start = log.lines.findIndex((l) => l.trim() === "{");
  if (start < 0) return null;
  let depth = 0;
  let inString = false;
  let escaped = false;
  let text = "";
  for (const line of log.lines.slice(start)) {
    text += `${line}\n`;
    for (const ch of line) {
      if (inString) {
        if (escaped) escaped = false;
        else if (ch === "\\") escaped = true;
        else if (ch === '"') inString = false;
      } else if (ch === '"') inString = true;
      else if (ch === "{") depth += 1;
      else if (ch === "}") depth -= 1;
    }
    if (depth === 0) break;
  }
  try {
    const value: unknown = JSON.parse(text);
    return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : null;
  } catch {
    return null;
  }
}

/** The top-level keys of the skill's `--json` envelope, in the order the skill wrote them. */
export function envelopeKeys(log: Log): string[] {
  const doc = firstJson(log);
  return doc === null ? [] : Object.keys(doc);
}

/** The envelope's own one-line headline (`summary.headline`), or null. */
export function headline(log: Log): string | null {
  const doc = firstJson(log);
  const summary = doc?.["summary"];
  if (typeof summary === "object" && summary !== null) {
    const h = (summary as Record<string, unknown>)["headline"];
    return typeof h === "string" ? h : null;
  }
  return null;
}

/** The envelope's own key points (`summary.key_points`), in order. */
export function keyPoints(log: Log): string[] {
  const summary = firstJson(log)?.["summary"];
  if (typeof summary !== "object" || summary === null) return [];
  const points = (summary as Record<string, unknown>)["key_points"];
  return Array.isArray(points) ? points.filter((p): p is string => typeof p === "string") : [];
}

const CYCLE_ID = /(?:^|\/)(cy[a-z0-9]{20,})$/;
const LONG_HEX = /^0x[0-9a-fA-F]{20,}$/;

/**
 * A recorded command as the film shows it: the program name, the cycle as <cycle>, a long address as the skill's own short form
 * (0x0000…dEaD), other paths and values as an ellipsis, and the `--now` stamp dropped. Every kept token is the log's own.
 */
export function abbreviateCommand(command: string): string {
  const tokens = command.trim().split(/\s+/);
  const out: string[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const t = tokens[i] ?? "";
    if (t === "--now") break;
    if (i === 0 && /^(python3?|node)$/.test(t)) continue;
    if (t === "-m" && /^(python3?)$/.test(tokens[i - 1] ?? "")) continue;
    const prev = tokens[i - 1] ?? "";
    const cycle = CYCLE_ID.exec(t);
    if (/^'?\{/.test(t)) out.push("'{\u2026}'");
    else if (cycle !== null) out.push("<cycle>");
    else if (LONG_HEX.test(t)) out.push(`${t.slice(0, 6)}\u2026${t.slice(-4)}`);
    else if (prev === "--address" && t.length > 12) out.push(`${t.slice(0, 6)}\u2026${t.slice(-4)}`);
    else if (prev.startsWith("--") && /\.json$|\//.test(t) && !t.startsWith("{")) out.push("\u2026");
    else if (!t.startsWith("-") && !t.startsWith("{") && t.includes("/")) out.push(t.slice(t.lastIndexOf("/") + 1));
    else out.push(t);
  }
  return out.join(" ");
}

/** The re-settle result: the log's own last line when the run succeeded and states a count, else null. */
export function resettleCount(log: Log): { line: string; matched: string } | null {
  if (log.exit === null || log.exit !== 0) return null;
  const line = lastLine(log);
  if (line === null) return null;
  const m = /(\d[\d,]*)\s+match\b/i.exec(line) ?? /(\d[\d,]*)\s+of\s+\d[\d,]*/.exec(line);
  if (m === null) return null;
  return { line: line.trim(), matched: m[1] ?? "" };
}

export type Row = { text: string; kind: "cmd" | "out" | "result" | "strong"; suffix?: string };

/** The faster typing speeds a terminal may fall back to (multiples of 40 characters a second) when a scene gives it too little time. */
export const SPEEDUPS = [1, 1.25, 1.5, 2, 2.5] as const;

/**
 * Plan how a terminal plays inside a time budget at `cps` characters a second, keeping `hold` frames at the end so the last line is
 * readable. Preference: everything typed at the stated speed; else commands typed and output printed line by line (a terminal prints
 * output, it does not type it); else commands typed faster, up to 2.5 times. Pure, so a test can bound every terminal.
 */
export function planTerminal(
  rows: readonly Row[],
  seconds: number,
  cps: number,
  fps: number,
  gap: number,
  lead: number,
  hold = 0,
): { typed: boolean[]; speed: number; starts: number[]; durations: number[]; total: number } {
  const budget = Math.floor(seconds * fps) - hold;
  const build = (typedOf: (r: Row) => boolean, speed: number): { typed: boolean[]; speed: number; starts: number[]; durations: number[]; total: number } => {
    const typed = rows.map(typedOf);
    const starts: number[] = [];
    const durations: number[] = [];
    let cursor = lead;
    rows.forEach((r, i) => {
      starts.push(cursor);
      const d = typed[i] ? Math.ceil((r.text.length / (cps * speed)) * fps) : 0;
      durations.push(d);
      cursor += d + gap;
    });
    return { typed, speed, starts, durations, total: cursor };
  };
  const every = build(() => true, 1);
  if (every.total <= budget) return every;
  const last = SPEEDUPS[SPEEDUPS.length - 1] ?? 1;
  let plan = build((r) => r.kind === "cmd", 1);
  for (const speed of SPEEDUPS) {
    plan = build((r) => r.kind === "cmd", speed);
    if (plan.total <= budget || speed === last) break;
  }
  return plan;
}
