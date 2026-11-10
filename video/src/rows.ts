// Terminal rows taken from recorded logs. The text is the log's own; only its length is limited so a line fits the screen.

import { clip } from "./format.ts";
import { abbreviateCommand, commandOf, commandsOf, headline, keyPoints, lastLine, type Log, type Row } from "./logs.ts";

const LIMIT = 220;

/** The command a log records, as one typed row that ends in `→ exit N` (the exit code is the log's own). */
export function commandResultRow(log: Log, limit = LIMIT): Row {
  const cmd = abbreviateCommand(commandOf(log) ?? log.lines[0] ?? "");
  const suffix = log.exit === null ? undefined : `→ exit ${log.exit}`;
  return suffix === undefined ? { kind: "cmd", text: clip(cmd, limit) } : { kind: "cmd", text: clip(cmd, limit), suffix };
}

/** The last output line of a log, as a row. */
export function lastLineRow(log: Log, kind: Row["kind"] = "result"): Row {
  return { kind, text: clip(lastLine(log) ?? "", LIMIT) };
}

/** The skill's check commands and the one-sentence verdicts it printed, in the order they were recorded. */
export function skillRows(log: Log, keep: RegExp, commandKeep: RegExp = /./): Row[] {
  const rows: Row[] = [];
  const prompts = new Set(commandsOf(log));
  for (const line of log.lines) {
    const trimmed = line.trim();
    if (trimmed === "") continue;
    const stripped = trimmed.replace(/^[$%#>]\s+/, "");
    if (prompts.has(stripped) && stripped !== trimmed) {
      if (commandKeep.test(stripped)) rows.push({ kind: "cmd", text: clip(abbreviateCommand(stripped), LIMIT) });
    } else if (keep.test(trimmed)) rows.push({ kind: "result", text: clip(trimmed, LIMIT) });
  }
  return rows;
}

/** The address skill: each `check` command and the verdict sentence under it. */
export function addressRows(log: Log): Row[] {
  return skillRows(log, /: (?:flagged by|no flag found)/i, /\scheck\s/);
}

/** The indicator skill: the command and the envelope's own headline (its three-valued answer). */
export function indicatorRows(log: Log): Row[] {
  const rows: Row[] = [];
  const cmd = commandOf(log);
  if (cmd !== null) rows.push({ kind: "cmd", text: clip(abbreviateCommand(cmd), LIMIT) });
  rows.push({ kind: "strong", text: clip(headline(log) ?? lastLine(log) ?? "", LIMIT) });
  const point = keyPoints(log)[0];
  if (point !== undefined) rows.push({ kind: "result", text: clip(point, LIMIT) });
  return rows;
}

import { COPY } from "./copy.ts";
import type { Film } from "./data.ts";

/** Scene 10's terminal: the two published commands, then the live count line from the recording (or nothing when the run failed). */
export function resettleRows(film: Film): Row[] {
  const rows: Row[] = COPY.s10.commands.map((text): Row => ({ kind: "cmd", text }));
  if (film.resettle !== null) rows.push({ kind: "strong", text: film.resettle.line });
  return rows;
}

/** Scene 4's terminal: both verifiers' commands with their exit codes, and the diff's own verdict line. */
export function verifierRows(film: Film): Row[] {
  return [commandResultRow(film.logs.verifyPython), commandResultRow(film.logs.verifyNode), lastLineRow(film.logs.diff, "strong")];
}
