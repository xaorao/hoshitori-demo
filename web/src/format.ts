// Small, pure helpers for wording what the documents hold. No page logic lives here; every function is tested.

import type {
  Boundary,
  ChallengeRow,
  Coverage,
  CoverageCount,
  PairCounts,
  SweepSummary,
} from "./types.ts";

/** "2026-10-30T22:40:24.966Z" as "30 Oct 2026, 22:40 UTC". */
export function when(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(iso);
  if (m === null) throw new Error(`not a time: ${iso}`);
  const months = [
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
  ];
  const month = months[Number(m[2]) - 1];
  if (month === undefined) throw new Error(`not a month: ${iso}`);
  return `${Number(m[3])} ${month} ${m[1]}, ${m[4]}:${m[5]} UTC`;
}

/** "30 Oct, 16:07 UTC": a time within the next day or so, without its year. */
export function whenShort(iso: string): string {
  return when(iso).replace(/ \d{4},/, ",");
}

/** The cycle the Calls page opens on: the last one that was committed, or the last of all when none was (a cycle still being collected has no calls). */
export function defaultCycle(
  cycles: { cycle_id: string; state: string }[],
): string | null {
  const committed = cycles.filter((c) => c.state === "COMMITTED");
  const pick =
    committed.length > 0
      ? committed[committed.length - 1]
      : cycles[cycles.length - 1];
  return pick === undefined ? null : pick.cycle_id;
}

/** What the Calls page says of a cycle that has no calls, by the state of the cycle (none when the site holds no such cycle). */
export function emptyCycleWords(state: string | undefined): string {
  if (state === undefined) return "No cycle of that name is in this site.";
  if (state === "ABANDONED")
    return "This cycle was abandoned before it made any call.";
  if (state === "COMMITTED") return "Nothing was scheduled in this cycle.";
  return "This cycle is being collected; its calls appear when it commits.";
}

/** What Today says while a newer cycle than the one it shows is being collected (no calls yet); none when there is no such cycle. */
export function inProgressLine(planned: string | null): string | null {
  return planned === null
    ? null
    : `The cycle planned for ${whenShort(planned)} is being collected; its calls will replace these.`;
}

/** An RFC 3339 UTC time plus whole hours, in the same form (seconds and fractions dropped to the millisecond). */
export function plusHours(iso: string, hours: number): string {
  const ms = Date.parse(iso);
  if (Number.isNaN(ms)) throw new Error(`not a time: ${iso}`);
  return new Date(ms + hours * 3_600_000).toISOString();
}

/** When the open calls of each horizon settle, from the start time they share: [[hours, count, time or null]]. */
export function settling(
  rows: { hours: number; state: string; origin: string | null }[],
): [number, number, string | null][] {
  const hours = [
    ...new Set(rows.filter((r) => r.state === "Open").map((r) => r.hours)),
  ].sort((a, b) => a - b);
  return hours.map((h) => {
    const open = rows.filter((r) => r.state === "Open" && r.hours === h);
    const origins = open
      .map((r) => r.origin)
      .filter((o): o is string => o !== null)
      .sort();
    const first = origins[0];
    return [h, open.length, first === undefined ? null : plusHours(first, h)];
  });
}

/** The calls still open, by horizon, in one sentence: "Next to settle: 14 calls by 30 Oct, 16:07 UTC (1 hour), ...". */
export function nextLine(
  items: [number, number, string | null][],
): string | null {
  if (items.length === 0) return null;
  const parts = items.map(([hours, n, at]) =>
    at === null
      ? `${count(n, "call")} after they start (${horizon(hours)})`
      : `${count(n, "call")} by ${whenShort(at)} (${horizon(hours)})`,
  );
  return `Next to settle: ${parts.join("; ")}.`;
}

/** "1 hour", "4 hours". */
export function horizon(hours: number): string {
  return hours === 1 ? "1 hour" : `${hours} hours`;
}

/** A count with its noun: "1 call", "3 calls". */
export function count(
  n: number,
  one: string,
  many: string = `${one}s`,
): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** What a model arm's run cost: its requests and tokens, and the time it was allowed (a limit, not the time it took). */
export function costWords(
  calls: number,
  tokens: number,
  cutoffS: number,
): string {
  return `${count(calls, "request")}, ${tokens} tokens; it was allowed up to ${cutoffS} seconds`;
}

/** The sentence at the top of Today, from the counts of the latest cycle's rows by state. With ``apart``, the calls that did not finish are
 * left out, because the warning below the line says them (one count, said once); null when nothing else is left to say. */
export function summary(
  byState: [string, number][],
  apart: true,
): string | null;
export function summary(byState: [string, number][]): string;
export function summary(
  byState: [string, number][],
  apart = false,
): string | null {
  const n = (state: string): number =>
    byState.find(([s]) => s === state)?.[1] ?? 0;
  const parts: string[] = [];
  if (n("Open") > 0)
    parts.push(`${count(n("Open"), "call")} waiting to settle`);
  if (n("Settled") > 0) parts.push(`${n("Settled")} settled`);
  if (n("Abstained") > 0) parts.push(`${n("Abstained")} made no call`);
  const broken = n("Failed") + n("Too late");
  if (broken > 0 && !apart) parts.push(`${broken} did not finish`);
  if (n("Void") + n("Not settled") > 0)
    parts.push(`${n("Void") + n("Not settled")} could not be settled`);
  if (n("Deciding") > 0) parts.push(`${n("Deciding")} still deciding`);
  if (parts.length === 0)
    return apart && broken > 0 ? null : "Nothing was scheduled in this cycle.";
  const first = parts[0] ?? "";
  return `${first.charAt(0).toUpperCase()}${first.slice(1)}${parts
    .slice(1)
    .map((p) => `, ${p}`)
    .join("")}.`;
}

/** What every percentage in a call means: a chance the agent gives, never a price move. */
export const CHANCE_NOTE =
  "Each percentage is the chance the agent gives, not a price move.";

/** What a barrier call's three words mean, once. */
export const BARRIER_NOTE =
  "A barrier call says which comes first within the horizon: the price rises by the target, falls by the stop, or neither.";

/** "0.2481" for the record table; a score that is not there is a dash, never zero. */
export function score(value: string | null): string {
  return value === null ? "–" : value;
}

/** A percentage that is not there is a dash, never zero. */
export function share(value: string | null): string {
  return value === null ? "–" : value;
}

/** What settled since a moment, as one sentence from the counts by result; nothing to say when there is no earlier moment. */
export function changed(
  since: string | null,
  byResult: [string, number][],
): string | null {
  if (since === null) return null;
  const n = (result: string): number =>
    byResult.find(([r]) => r === result)?.[1] ?? 0;
  const total = byResult.reduce((sum, [, k]) => sum + k, 0);
  const head = `Since the last cycle (${whenShort(since)})`;
  if (total === 0) return `${head}: nothing has settled.`;
  const parts: string[] = [];
  if (n("Rose") > 0) parts.push(`${n("Rose")} rose`);
  if (n("Fell") > 0) parts.push(`${n("Fell")} fell`);
  if (n("Target first") > 0)
    parts.push(`${n("Target first")} reached the target`);
  if (n("Stop first") > 0) parts.push(`${n("Stop first")} reached the stop`);
  if (n("Neither") > 0) parts.push(`${n("Neither")} reached neither`);
  return `${head}: ${count(total, "call")} settled. ${parts.join(", ")}.`;
}

/** The fewest settled calls a score is read on (contract section 10: a prior, fixed by the protocol). */
export const LEAST_SETTLED = 20;

/** The calls a score counts (contract sections 8 and 17: a forecast counts towards a headline only if it reached C3 by its origin and still
 * holds C3 or more), in the words the first layer gives that level (hoshitori.labels: C3 is "Independently time-stamped before the outcome
 * window"). */
const COUNTED = "independently time-stamped";

/** What an agent's score says so far, in words; the score is compared with the one for always saying 50%, never with other agents. It is the
 * score of the settled calls that count toward it, and says so. */
export function skill(
  armName: string,
  meanBrier: string | null,
  n: number,
): string {
  if (meanBrier === null || n === 0)
    return `${armName}: no settled up-or-down calls ${COUNTED} yet.`;
  if (n < LEAST_SETTLED)
    return `${armName}: ${count(n, "settled call")} ${COUNTED}, too few for a score to mean anything.`;
  return `${armName}: ${meanBrier} over ${count(n, "settled call")} ${COUNTED} (always saying 50% scores 0.25).`;
}

/** The line beside the score (contract section 8): the result on every settled call, whatever its level, and the share of them that was not
 * independently time-stamped; nothing when no call has settled. */
export function allSettled(
  meanBrier: string | null,
  n: number,
  missed: string | null,
): string | null {
  if (meanBrier === null || n === 0) return null;
  const result =
    n < LEAST_SETTLED
      ? `${n}, too few for a score to mean anything`
      : `${meanBrier} over ${n}`;
  return `All settled calls: ${result}; ${share(missed)} were not ${COUNTED}.`;
}

// --- what a bundle check says, in words -------------------------------------------------------------------------------------------------
/** The first-layer wording of a commitment level (the same five phrases as hoshitori.labels; a test keeps them equal). */
export const TRUST_PHRASES: Record<string, string> = {
  C1: "Recorded by us only",
  C2: "Publication reported, not independently time-verified",
  C3: "Independently time-stamped",
  C4: "Independently time-stamped and publicly logged",
};
export const NO_FORECAST = "No forecast recorded";

/** The parts of a verifier verdict that the page words (the verifier's own JSON has more). */
export type VerdictView = {
  root_of_trust: {
    state: string;
    version: number | null;
    sha256: string | null;
  };
  integrity: { ok: boolean; items: Record<string, unknown>; errors: string[] };
  timing: {
    level: string;
    tokens: { valid: boolean }[];
    basis: TimingBasis | null;
  };
  cycle: {
    declared: boolean;
    steps: number;
    batch_size: number | null;
    members_present: number;
    complete: boolean;
    /** What the records appended after the stamped checkpoint show; absent in the verdict of a verifier that does not read them. */
    later?: LaterPart;
  };
  eligibility: { scorable: boolean; reasons: string[] };
  not_proven: string[];
};

/** The later part of a verdict (``cycle.later``): whether it is shown, how many calls fail it, and each call's eligible time and start. */
export type LaterPart = {
  shown: boolean;
  failures: number;
  forecasts: Record<
    string,
    { awaiting: string; origin: string; settlement?: string }
  >;
};

export type Finding = { ok: boolean; text: string };

/** What the trusted root asked of the time-stamps, as the verdict states it (both verifiers print it). */
export type TimingBasis = {
  required_independent: number;
  window_before_commit_s: number;
  window_after_commit_s: number;
  close_after_commit_s: number;
  max_spread_s: number;
  u_constant_s: number;
};

/** The rules a pass was judged by, in a sentence: a root with loose rules gives a loose pass, and the page says which rules it was. None
 * when no root was used (the verdict then says no time-stamp is believed). */
export function basisWords(basis: TimingBasis | null): string | null {
  if (basis === null) return null;
  const stamps = count(basis.required_independent, "independent time-stamp");
  return `Judged by the root's rules: ${stamps}, each from ${basis.window_before_commit_s} s before to ${basis.window_after_commit_s} s after the commit, no more than ${basis.max_spread_s} s apart, with ${basis.u_constant_s} s of clock uncertainty allowed.`;
}

/** Why a bundle cannot count toward a score, in words. The eight codes are the verifiers' (hoshitori.verify, verifier-js); one that is not
 * among them is an error, never printed as its code. */
const NOT_SCORABLE: Record<string, string> = {
  ROOT_NOT_CURRENT: "the trusted root is not a current one",
  INTEGRITY: "a record failed its checks",
  LEVEL_BELOW_C3: "the time-stamps do not reach independent time-stamping",
  UNRESOLVED_REFERENCE: "a record that a call cites is not in the bundle",
  COMPLETENESS_NOT_SHOWN:
    "the bundle does not show that it holds the whole batch its cycle committed",
  NO_FORECAST: "the bundle holds no call to score",
  ORIGIN_BEFORE_ATTESTATION:
    "a call's later steps do not show it becoming eligible and starting after its time-stamps",
  DATA_MODE: "the evidence is not all of the one kind of data a score needs",
};
export function notScorable(reason: string): string {
  const words = NOT_SCORABLE[reason];
  if (words === undefined)
    throw new Error(`not a reason a bundle cannot be scored: ${reason}`);
  return words;
}

/** Whether the bundle holds the whole batch of its cycle, in a sentence (the verifiers' ``cycle`` part): the committing step names how many
 * records the batch holds and their root, and the verifier recomputes it from the records in the bundle. */
function cycleFinding(c: VerdictView["cycle"]): Finding {
  if (c.complete && c.batch_size !== null) {
    return {
      ok: true,
      text: `The whole batch its cycle committed is here: ${count(c.batch_size, "record")} and the step that committed ${c.batch_size === 1 ? "it" : "them"}, together making the root that step names.`,
    };
  }
  if (c.steps > 1)
    return {
      ok: false,
      text: "The bundle holds more than one committing step; a bundle shows one cycle, so it shows none as complete.",
    };
  if (!c.declared)
    return {
      ok: false,
      text: "Nothing in the bundle says what its cycle committed, so it cannot show that no call was taken out.",
    };
  if (c.batch_size === null)
    return {
      ok: false,
      text: "The cycle's committing step does not name a batch the bundle can check.",
    };
  return {
    ok: false,
    text: `The bundle does not hold the whole batch its cycle committed: ${c.members_present} of ${c.batch_size} records found, or they do not make the root the committing step names.`,
  };
}

/** What the records after the stamped checkpoint show, in a sentence, or nothing when the bundle holds none (or the verifier does not read
 * them): each call's eligible time and start held to what the time-stamps allow. It never says a settlement is right. */
function laterFinding(c: VerdictView["cycle"]): Finding | null {
  const later = c.later;
  if (later === undefined || !later.shown) return null;
  const calls = Object.values(later.forecasts);
  if (later.failures > 0) {
    return {
      ok: false,
      text: `The later steps of ${later.failures} of ${count(calls.length, "call")} do not follow from the time-stamps or from their own settlement: an eligible time or a start that is too early or not the next minute close, a settlement that is missing, written twice, written before the window closed or that does not agree with its own prices, or a step that is missing, malformed or written twice.`,
    };
  }
  const started = calls.filter((x) => x.origin === "ok").length;
  const ended =
    calls.filter((x) => x.settlement === "ok").length === 0
      ? ""
      : ` ${count(calls.filter((x) => x.settlement === "ok").length, "call")} ended with a settlement that agrees with its own two prices and was written after the call's window closed; those prices are not checked against a market here.`;
  const start =
    started === 0
      ? "none has started yet"
      : `${count(started, "call")} started at the next minute close after that`;
  return {
    ok: true,
    text: `The later steps of ${count(calls.length, "call")} are here, under a later checkpoint that extends the first: each call became eligible no earlier than its time-stamps allow, and ${start}.${ended}`,
  };
}

export function findings(v: VerdictView): Finding[] {
  const records = Object.keys(v.integrity.items).length;
  const out: Finding[] = [];
  out.push(
    v.integrity.ok
      ? {
          ok: true,
          text: `Every record is intact: ${count(records, "record")}, each signed and in the log.`,
        }
      : {
          ok: false,
          text: `The bundle is not intact (${v.integrity.errors.length > 0 ? v.integrity.errors.join(", ") : "a record failed its checks"}).`,
        },
  );
  out.push(
    v.root_of_trust.state === "PINNED"
      ? {
          ok: true,
          text: `Checked against a trusted root (version ${v.root_of_trust.version ?? "unknown"}).`,
        }
      : {
          ok: false,
          text: "No trusted root accepted: the bundle is only checked against itself, so nothing says it came from here.",
        },
  );
  const valid = v.timing.tokens.filter((t) => t.valid).length;
  const phrase = TRUST_PHRASES[v.timing.level];
  out.push(
    phrase === undefined
      ? {
          ok: false,
          text: `${NO_FORECAST}: no time-stamp reaches a level a person should rely on (${valid} of ${v.timing.tokens.length} tokens valid).`,
        }
      : {
          ok: v.timing.level === "C3" || v.timing.level === "C4",
          text: `${phrase} (${valid} of ${count(v.timing.tokens.length, "time-stamp")} valid).`,
        },
  );
  out.push(cycleFinding(v.cycle));
  const later = laterFinding(v.cycle);
  if (later !== null) out.push(later);
  out.push(
    v.eligibility.scorable
      ? {
          ok: true,
          text: "It meets every condition a scored period asks for. A call counts only in a scored period; an exploratory run never does.",
        }
      : {
          ok: false,
          text: `It cannot count toward a score${v.eligibility.reasons.length > 0 ? `: ${v.eligibility.reasons.map(notScorable).join("; ")}` : ""}.`,
        },
  );
  return out;
}

// --- a receipt: what a model agent answered, and how the call settled ------------------------------------------------------------------
/** A model agent's answer in words (the codes of arm_run_record): for the codes that are also states of a slot, the words the league gives
 * those states (hoshitori.labels STEP_WORDS), and for a call the word the Calls page heads its calls with. A test keeps them equal. */
export const ANSWER_WORDS: Record<string, string> = {
  CALL: "Call",
  NO_CALL: "Abstained",
  FAILED: "Failed",
  FAILED_EVIDENCE: "Failed for lack of evidence",
  FAILED_LATE: "Too late",
};

/** "Call", or "Abstained, too little edge to call": an answer and its reason; an unknown answer is an error, never shown as its code. */
export function answerWords(verdict: string, reason: string | null): string {
  const word = ANSWER_WORDS[verdict];
  if (word === undefined) throw new Error(`not an answer: ${verdict}`);
  return reason === null ? word : `${word}, ${reason.toLowerCase()}`;
}

/** "settled, rose": a settlement's state, then what happened in the words of the row's result; the outcome's code ("1") is never shown. */
export function settlementWords(state: string, result: string | null): string {
  return result === null
    ? state.toLowerCase()
    : `${state.toLowerCase()}, ${result.toLowerCase()}`;
}

/** What a receipt says of an evidence item's digest: the first sixteen hex digits of its raw hash; for a derived item, which has no raw hash
 * of its own (hoshitori.evidence), that it was derived; for anything else without one, the view model's own words ("not in this ledger"). */
export function digestWords(
  raw: string | null,
  kind: string,
  shown: string,
): string {
  if (raw !== null) return raw.slice(0, 16);
  return kind === "DERIVED" ? "derived from the others" : shown;
}

// --- the state of the ledger ------------------------------------------------------------------------------------------------------------
/** What every page says when the ledger the documents were exported from is not in its OK state (ADR-026: INTEGRITY_FAILURE is visible on
 * every surface; LEDGER_SPEC section 8: the product shows NOT RECONSTRUCTIBLE for affected records, never a pass); nothing when it is OK,
 * or when the index does not say (one exported before the state was). */
export function ledgerFailure(
  ledger: { state: string; detail: string } | undefined,
): { heading: string; line: string; detail: string } | null {
  if (ledger === undefined || ledger.state === "OK") return null;
  return {
    heading: `The league's ledger is in ${ledger.state}.`,
    line: "Its records are NOT RECONSTRUCTIBLE, so no cycle, call, score or bundle from it is shown.",
    detail: ledger.detail,
  };
}

// --- evidence of quality ----------------------------------------------------------------------------------------------------------------
/** The words a part of the product can have, from least to most shown (docs/STATUS.md says what each means). */
export const STATUS_WORDS = [
  "DESIGNED",
  "IMPLEMENTED",
  "TESTED",
  "INTEGRATED",
  "INDEPENDENTLY REVIEWED",
] as const;

/** How many parts have each status word, in the order of STATUS_WORDS, leaving out the words nothing has. */
export function statusCounts(rows: { word: string }[]): [string, number][] {
  return STATUS_WORDS.map((w): [string, number] => [
    w,
    rows.filter((r) => r.word === w).length,
  ]).filter(([, n]) => n > 0);
}

/** The parts of the status table grouped by status word, least proven first (the order of STATUS_WORDS); a group nothing is in is left out, and a
 * word the table does not know is an error, so that no part is ever dropped from the page. */
export function qualityGroups<T extends { word: string }>(
  rows: T[],
): { word: string; rows: T[] }[] {
  for (const r of rows)
    if (!(STATUS_WORDS as readonly string[]).includes(r.word))
      throw new Error(`not a status word: ${r.word}`);
  return STATUS_WORDS.map((word) => ({
    word,
    rows: rows.filter((r) => r.word === word),
  })).filter((g) => g.rows.length > 0);
}

/** Whether the group of a status word is open when the page loads: the groups below TESTED, the gaps, are; the rest are one click away. */
export function groupStartsOpen(word: string): boolean {
  return word === "DESIGNED" || word === "IMPLEMENTED";
}

/** The sentence above the table: how many parts, and how many of them anyone but the author has reviewed. */
export function qualityLine(rows: { word: string }[]): string {
  const reviewed = rows.filter(
    (r) => r.word === "INDEPENDENTLY REVIEWED",
  ).length;
  const tested = statusCounts(rows)
    .map(([w, n]) => `${n} ${w.toLowerCase()}`)
    .join(", ");
  return `${count(rows.length, "part")}: ${tested}. ${reviewed === 0 ? "None has been reviewed by anyone but its author." : `${reviewed} reviewed by someone else.`}`;
}

/** Text with `code` spans (as the status table writes them) as pieces to show, each marked as code or not; an unclosed backtick is plain. */
export function codeSpans(text: string): { text: string; code: boolean }[] {
  const out: { text: string; code: boolean }[] = [];
  let rest = text;
  for (;;) {
    const open = rest.indexOf("`");
    const close = open === -1 ? -1 : rest.indexOf("`", open + 1);
    if (open === -1 || close === -1) break;
    if (open > 0) out.push({ text: rest.slice(0, open), code: false });
    out.push({ text: rest.slice(open + 1, close), code: true });
    rest = rest.slice(close + 1);
  }
  if (rest !== "") out.push({ text: rest, code: false });
  return out;
}

// --- sources read ------------------------------------------------------------------------------------------------------------------------
// How a reader meets a source: an id of ours (hoshitori.*) is never shown raw.
const SOURCE_NAMES: Record<string, string> = {
  ryo: "RYO",
  "hoshitori.features": "Computed measures",
  "hoshitori.crosscheck": "Price cross-check",
  "hoshitori.placebo": "Neutral text",
  okx: "OKX",
};

export function sourceName(source: string): string {
  const known = SOURCE_NAMES[source];
  if (known !== undefined) return known;
  const spaced = source.replace(/[-_.]+/g, " ");
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** What each source brought into the latest cycle and where one brought nothing, from the snapshot's own counts; a source that brought nothing
 * for some calls is named first. Null when the cycle has no snapshot. */
export function sourceLine(
  sources: { source: string; items: number; unread: number }[],
): string | null {
  if (sources.length === 0) return null;
  const ordered = [...sources].sort(
    (a, b) =>
      Number(b.unread > 0 && b.items === 0) -
      Number(a.unread > 0 && a.items === 0),
  );
  const parts = ordered.map((s) => {
    const name = sourceName(s.source);
    const brought = s.items > 0 ? ` ${count(s.items, "item")}` : "";
    const missing =
      s.unread > 0
        ? `${s.items > 0 ? ", nothing" : " brought nothing"} for ${count(s.unread, "call")}`
        : "";
    return `${name}${brought}${missing}`;
  });
  return `Read this cycle: ${parts.join("; ")}.`;
}

// --- drills ----------------------------------------------------------------------------------------------------------------------------
/** A sentence's first letter in lower case, unless the word is an acronym (RYO, ATR): its second letter is a capital too. */
function lowerFirst(text: string): string {
  return /^[A-Z]{2}/.test(text)
    ? text
    : text.charAt(0).toLowerCase() + text.slice(1);
}

/** What a drill's run produced, as a sentence: "Committed at C3: 3 open, 3 failed. Why: ...". */
export function drillSentence(o: {
  cycle: string;
  level: string | null;
  slots: [string, number][];
  reasons: string[];
  forecasts: number;
}): string {
  const slots =
    o.slots.length === 0
      ? "no calls"
      : o.slots.map(([state, n]) => `${n} ${state.toLowerCase()}`).join(", ");
  const level = o.level === null ? "" : ` at ${o.level}`;
  const why =
    o.reasons.length === 0
      ? ""
      : ` Why: ${o.reasons.map(lowerFirst).join("; ")}.`;
  return `${o.cycle}${level}: ${slots}. ${count(o.forecasts, "forecast")} made.${why}`;
}

/** The line above the list: how many of the drills held. */
export function drillLine(held: boolean[]): string {
  const n = held.filter(Boolean).length;
  return n === held.length
    ? `All ${held.length} drills held.`
    : `${n} of ${held.length} drills held. The others did not.`;
}

// --- states of the data: old, partial, empty -------------------------------------------------------------------------------------------
/** How long ago, in the largest whole unit: "5 minutes", "1 hour", "3 days". */
export function ageWords(nowMs: number, iso: string): string {
  const then = Date.parse(iso);
  if (Number.isNaN(then)) throw new Error(`not a time: ${iso}`);
  const minutes = Math.max(0, Math.floor((nowMs - then) / 60_000));
  if (minutes < 1) return "under a minute";
  if (minutes < 60) return count(minutes, "minute");
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return count(hours, "hour");
  return count(Math.floor(hours / 24), "day");
}

/** Documents older than this many hours are called old (a prior: a cycle is hourly, so three missed ones is a sign). */
export const OLD_AFTER_HOURS = 3;

/** A warning when the exported documents are old; nothing when they are not. */
export function oldLine(generatedAt: string, nowMs: number): string | null {
  const then = Date.parse(generatedAt);
  if (Number.isNaN(then)) throw new Error(`not a time: ${generatedAt}`);
  if (nowMs - then <= OLD_AFTER_HOURS * 3_600_000) return null;
  return `These documents were exported ${ageWords(nowMs, generatedAt)} ago. Nothing newer is shown, so the league may have moved on.`;
}

/** A warning when part of the latest cycle did not finish; nothing when all of it did. */
export function partialLine(byState: [string, number][]): string | null {
  const n = (state: string): number =>
    byState.find(([s]) => s === state)?.[1] ?? 0;
  const broken = n("Failed") + n("Too late");
  if (broken === 0) return null;
  return `${count(broken, "call")} failed or came too late; the rest stands.`;
}

// --- claims ------------------------------------------------------------------------------------------------------------------------------
const KIND_WORDS: Record<string, string> = {
  OBSERVATION: "Observation",
  INTERPRETATION: "Interpretation",
  CAUSAL_LINK: "Causal link",
  FORECAST: "Forecast",
  CONDITION: "Condition",
  OPINION: "Opinion",
  PROMOTION_SIGNAL: "Promotion signal",
  CORRECTION_REF: "Correction",
  ASSUMPTION_INFERRED: "Implied step (ours)",
};

/** What kind of part a part is, in words; an unknown kind is an error, never a guess. */
export function kindWord(kind: string): string {
  const word = KIND_WORDS[kind];
  if (word === undefined) throw new Error(`not a kind of part: ${kind}`);
  return word;
}

/** The words between two offsets of a claim, as the claims file counts them: in code points (Python's ``text[start:end]``), not in the UTF-16
 * units a JavaScript string is sliced by, so a character outside the Basic Multilingual Plane before them moves nothing. */
export function wordsAt(text: string, start: number, end: number): string {
  return Array.from(text).slice(start, end).join("");
}

/** How far the readings of one claim agree, in a sentence that also says whether the rules were ever checked on labelled claims. */
export function readingLine(
  state: string,
  runs: number,
  score: string,
  validated: boolean,
): string {
  const head =
    state === "STABLE"
      ? `The ${runs} readings agree on the parts (agreement ${score}).`
      : state === "UNSTABLE"
        ? `The ${runs} readings disagree on what the parts are (agreement ${score}).`
        : state === "DISPUTED"
          ? `A plain reading of the text disagrees with the parts (agreement ${score}).`
          : "";
  if (head === "") throw new Error(`not a state: ${state}`);
  return validated ? head : `${head} Not yet checked against labelled claims.`;
}

/** What a draft of a forecast contract is, in a sentence. */
export function sketchLine(status: string, reason: string | null): string {
  if (status === "RESOLVABLE")
    return "Could be scored as a direction call over the time stated.";
  if (status === "NEEDS_CHOICES")
    return "Needs your choices before it could be scored.";
  if (status === "UNRESOLVED")
    return `Names nothing a price series could settle: ${reason ?? "no reason given"}.`;
  throw new Error(`not a status: ${status}`);
}

/** The next check as a sentence a person can act on; a part nothing can check says why, and never invents a step. */
export function nextCheckLine(
  status: string,
  words: string | null,
  instruction: string | null,
  source: string | null,
  reason: string | null,
): string {
  if (status === "CHECK") {
    if (words === null || instruction === null || source === null)
      throw new Error("a check needs its part, its words and its source");
    return `Next check, on “${words}”: ${instruction} Source: ${source}.`;
  }
  if (status === "NO_AVAILABLE_CHECK") {
    if (words === null || reason === null)
      throw new Error("no available check needs its part and its reason");
    return `No check is available for “${words}”: ${reason}.`;
  }
  if (status === "NOTHING_LEFT")
    return `No further check: ${reason ?? "none was given"}.`;
  throw new Error(`not a status: ${status}`);
}

// --- challenges -----------------------------------------------------------------------------------------------------------------------
const SOURCE_WORDS: Record<string, string> = {
  "binance-spot": "Binance spot candles",
  "binance-funding": "Binance perpetual funding",
  "coinbase-spot": "Coinbase candles",
  "kraken-spot": "Kraken candles",
  "hoshitori.features": "The measures computed from the candles",
  "hoshitori.crosscheck": "The price cross-check of RYO against the exchange",
  ryo: "RYO",
  coinbase: "Coinbase candles",
  kraken: "Kraken candles",
  okx: "OKX candles",
  hyperliquid: "Hyperliquid candles",
};

/** A source as a person would name it; a source with no name here is shown as it is written, never dropped. */
/** A chance written as a decimal between 0 and 1 (as the records write it: at most 18 places) as a whole percentage, half to even and never
 * 0% or 100%, the way the league's own wording rounds. Done on the digits, not on a float, so a half is a half. */
export function chanceWords(chance: string): string {
  const m = /^0\.(\d{1,18})$/.exec(chance);
  if (m === null || /^0+$/.test(m[1] ?? ""))
    throw new Error(`not a chance: ${JSON.stringify(chance)}`);
  const digits = m[1] ?? "";
  const scaled = BigInt(digits); // the chance times 10^places
  const unit = 10n ** BigInt(digits.length);
  let points = (scaled * 100n) / unit;
  const twice = ((scaled * 100n) % unit) * 2n;
  if (twice > unit || (twice === unit && points % 2n === 1n)) points += 1n;
  const shown = points < 1n ? 1n : points > 99n ? 99n : points;
  return `${shown}%`;
}

/** The forgery's one change, in a sentence. Only the chance of a rise is ever changed, so a document that names another field is refused. */
export function forgeryWords(edit: {
  record: string;
  field: string;
  was: string;
  now: string;
}): string {
  if (edit.field !== "p")
    throw new Error(
      `not a field the forgery changes: ${JSON.stringify(edit.field)}`,
    );
  return `The chance given to a rise in one call was changed from ${chanceWords(edit.was)} to ${chanceWords(edit.now)}.`;
}

/** What the rules agent's "without each source" questions say, when they all say the same. A fixed rule that requires every source gives
 * one identical row for each call and source, which a reader should be told once rather than read forty times. Rows are collapsed only
 * when their outcome and their words agree (what the rule said without the source is not part of the statement); anything else is shown
 * row by row, with `outcomes` giving how many rows say each thing. Groups and sources are in the order they first appear. */
export function challengeSummary(rows: ChallengeRow[]): {
  uniform: boolean;
  words: string | null;
  questions: number;
  calls: number;
  outcomes: { outcome: string; words: string; questions: number }[];
  sources: {
    source: string;
    questions: number;
    itemsLow: number;
    itemsHigh: number;
  }[];
} {
  const first = rows[0];
  const uniform =
    first !== undefined &&
    rows.every((r) => r.outcome === first.outcome && r.words === first.words);
  const byOutcome = new Map<
    string,
    { outcome: string; words: string; questions: number }
  >();
  for (const r of rows) {
    const key = `${r.outcome}\u0000${r.words}`;
    const o = byOutcome.get(key);
    if (o === undefined)
      byOutcome.set(key, { outcome: r.outcome, words: r.words, questions: 1 });
    else o.questions += 1;
  }
  const bySource = new Map<
    string,
    { source: string; questions: number; itemsLow: number; itemsHigh: number }
  >();
  for (const r of rows) {
    const s = bySource.get(r.source);
    if (s === undefined)
      bySource.set(r.source, {
        source: r.source,
        questions: 1,
        itemsLow: r.items,
        itemsHigh: r.items,
      });
    else {
      s.questions += 1;
      s.itemsLow = Math.min(s.itemsLow, r.items);
      s.itemsHigh = Math.max(s.itemsHigh, r.items);
    }
  }
  return {
    uniform,
    words: uniform && first !== undefined ? first.words : null,
    questions: rows.length,
    calls: new Set(rows.map((r) => `${r.cycle_id}|${r.symbol}|${r.hours}`))
      .size,
    outcomes: [...byOutcome.values()],
    sources: [...bySource.values()],
  };
}

export function sourceWords(source: string): string {
  return SOURCE_WORDS[source] ?? source;
}

/** A source's words in the middle of a sentence: a leading "The" loses its capital, a name ("Binance spot candles", "RYO") keeps its own. */
function inSentence(words: string): string {
  return words.startsWith("The ") ? `the ${words.slice(4)}` : words;
}

/** The source name of a placebo challenge: nothing was taken out; a neutral item of the size of the evidence was added. */
export const PLACEBO_SOURCE = "hoshitori.placebo";

/** The title of one challenge on the What if page. */
export function challengeTitle(
  symbol: string,
  hours: number,
  source: string,
): string {
  const what =
    source === PLACEBO_SOURCE
      ? "with neutral text added"
      : `without ${inSentence(sourceWords(source))}`;
  return `${symbol}, ${horizon(hours)}: ${what}`;
}

/** What was done to the evidence, in a sentence. */
export function challengeAction(source: string, items: number): string {
  return source === PLACEBO_SOURCE
    ? "Neutral text with no content was added to the evidence."
    : `${count(items, "item")} taken out.`;
}

const CHECK_WORDS: Record<string, string> = {
  SUPPORTED: "Agrees with the evidence",
  CONTRADICTED: "Does not agree with the evidence",
  NOT_CHECKABLE: "Cannot be checked here",
  NOT_FOUND_IN_SOURCES_CHECKED: "Not in the evidence held",
  SOURCE_UNAVAILABLE: "Unknown in the evidence",
  UNRESOLVED: "Words too open to check",
};

/** What a check's status says, in words; an unknown status is an error, never shown as a result. */
export function checkWords(status: string): string {
  const word = CHECK_WORDS[status];
  if (word === undefined) throw new Error(`not a check status: ${status}`);
  return word;
}

// --- challenges of the model agent ----------------------------------------------------------------------------------------------------
// Closed sets of sentences (EVIDENCE_CHALLENGE_SPEC sections 1, 4 and 5, in its own words where it gives them). They say what moved, never
// why: decision sensitivity only, never that the model drew on the source, never that a market moved. Every code a challenge file can hold
// has its sentence here and no other (tests/model_challenge_file_checks.py, check_page_words); an unknown code is an error, never shown.

/** What the result of a challenge says, by its label (hoshitori.paired_stats and hoshitori.model_challenge). */
export const MODEL_OUTCOME_WORDS: Record<string, string> = {
  DECISION_CHANGED:
    "Taking it out changed the decision, beyond the agent's own variation.",
  DECISION_STABLE:
    "Taking it out did not change the decision: any shift is within 15 points either way.",
  EFFECT_UNCERTAIN:
    "Too few runs to say whether taking it out changed the decision. This does not mean the source made no difference.",
  "NOT_EVALUATED(unstable_baseline)":
    "Not judged: asked again on the same evidence, the agent already gave another call often enough to hide any effect.",
  "NOT_EVALUATED(structural_failure)":
    "Not judged: more than one run in five failed.",
  EVIDENCE_REQUIRED:
    "The agent requires this source: without it, it does not decide at all. This is a result, not a failure.",
  NOT_EVALUATED:
    "Not judged: something left in the evidence was still computed from the source taken out.",
};

/** The same labels for a placebo challenge, where nothing was taken out: a neutral item of no content was added. Other labels read as before. */
const PLACEBO_OUTCOME_WORDS: Record<string, string> = {
  DECISION_CHANGED:
    "Adding text with no content changed the decision, beyond the agent's own variation: this agent moves when its evidence is only longer.",
  DECISION_STABLE:
    "Adding text with no content did not change the decision: any shift is within 15 points either way.",
  EFFECT_UNCERTAIN:
    "Too few runs to say whether adding text with no content changed the decision.",
};

/** Who changed the decision, when it changed (EVIDENCE_CHALLENGE_SPEC section 5): only a moved proposal is the agent changing its mind. */
export const MECHANISM_WORDS: Record<string, string> = {
  MODEL_REVISED:
    "The agent changed its mind: its own proposal moved without the source more often than when it was asked again.",
  GATE_BLOCKED:
    "A safety rule stopped it: the agent's proposal stayed within its own variation, and a check on its answer changed the final call.",
  BOTH: "Both moved: the agent's own proposal, and a safety rule that changed the final call.",
  VARIATION_ONLY:
    "Neither the agent's proposal nor a safety rule moved beyond its own variation alone, so the change cannot be put on either.",
};

/** The committed call a challenge counts against, by its action class (a call's chance of a rise above, below or at 50%). */
export const COMMITTED_WORDS: Record<string, string> = {
  UP: "up, more likely to rise than to fall",
  DOWN: "down, more likely to fall than to rise",
  FLAT: "even, as likely to rise as to fall",
};

/** Said beside every challenge of the model agent (EVIDENCE_CHALLENGE_SPEC section 1). */
export const MODEL_CHALLENGE_NOTE =
  "This measures decision sensitivity: whether this agent's call depended on the source, on this evidence. It does not say how the model reasons, that the source is true, or what the market will do.";

/** How a challenge is asked (EVIDENCE_CHALLENGE_SPEC section 2). */
export const MODEL_CHALLENGE_METHOD =
  "Each pair asks the agent three times about the call it committed: with every source, without one source and everything computed from it, and with every source again, as a control. The control shows how much the agent varies on its own.";

/** What the result says, with who changed it when it changed; a mechanism on any other result, or none on a change, is an error. */
export function modelOutcome(
  outcome: string,
  mechanism: string | null,
  source?: string,
): string {
  const head =
    (source === PLACEBO_SOURCE ? PLACEBO_OUTCOME_WORDS[outcome] : undefined) ??
    MODEL_OUTCOME_WORDS[outcome];
  if (head === undefined) throw new Error(`not a challenge result: ${outcome}`);
  if ((outcome === "DECISION_CHANGED") !== (mechanism !== null))
    throw new Error("a mechanism comes with a change, and only with one");
  if (mechanism === null) return head;
  const who = MECHANISM_WORDS[mechanism];
  if (who === undefined) throw new Error(`not a mechanism: ${mechanism}`);
  return `${head} ${who}`;
}

/** "Committed call: up, more likely to rise than to fall." */
export function committedLine(original: string): string {
  const words = COMMITTED_WORDS[original];
  if (words === undefined) throw new Error(`not a committed call: ${original}`);
  return `Committed call: ${words}.`;
}

/** How often each run gave the committed call, from the two paired tables (they share the full runs). */
export function modelCounts(
  source: string,
  pairs: number,
  treated: PairCounts,
  control: PairCounts,
): string {
  const full = treated.same_same + treated.same_changed;
  if (control.same_same + control.same_changed !== full)
    throw new Error("the two tables do not share their full runs");
  const without = treated.same_same + treated.changed_same;
  const again = control.same_same + control.changed_same;
  const what =
    source === PLACEBO_SOURCE
      ? "with neutral text added"
      : `without ${inSentence(sourceWords(source))}`;
  return `With every source the agent made this call in ${full} of ${pairs} runs; ${what}, in ${without} of ${pairs}; asked again with every source (the control), in ${again} of ${pairs}.`;
}

/** The control's line when no pair was asked: the answer was known first. */
export const NO_CONTROL =
  "No control was needed: the answer was known before any pair was asked.";

/** A signed number of points as a page shows it: a minus sign, not a hyphen. */
function signed(points: string): string {
  return points.startsWith("-") ? `\u2212${points.slice(1)}` : points;
}

/** The shift and its interval, in percentage points, with the exact test. */
export function shiftLine(
  shift: string,
  low: string,
  high: string,
  p: string,
  source?: string,
): string {
  const between =
    source === PLACEBO_SOURCE
      ? "with the evidence minus with neutral text added"
      : "with every source minus without it";
  return `The share of runs making this call, ${between}: ${signed(shift)} points (95% interval ${signed(low)} to ${signed(high)}); exact test p = ${p}.`;
}

/** The failed runs, always shown: out of the runs asked, and kept in their pairs. */
export function failedLine(
  failed: number,
  attempted: number,
  outcome: string,
): string {
  if (outcome === "EVIDENCE_REQUIRED")
    return "No model was asked: the agent stops before asking when a source it requires is missing, so no run failed.";
  if (attempted === 0) return "No run was asked, so none failed.";
  return `${failed} of ${count(attempted, "run")} failed. A failed run stays in its pair and counts against the evidence.`;
}

/** How many pairs, of which agent, at what temperature. */
export function pairsLine(
  pairs: number,
  armName: string,
  model: string,
  temperature: string | null,
): string {
  const heat =
    temperature === null
      ? "temperature left to the provider"
      : `temperature ${temperature}`;
  return `${count(pairs, "pair")} of ${armName} (${model}, ${heat}).`;
}

/** The first-layer words for the steps of a call's life, in the order they happen; a step this page does not know is shown as it was
 * recorded, so a new state is visible and never hidden. */
export const STEP_WORDS: Record<string, string> = {
  SCHEDULED: "Planned",
  CREATED: "Started",
  EVIDENCE_FROZEN: "Evidence fixed",
  DECIDED: "Decided",
  COMMITTED: "Committed",
  AWAITING_ORIGIN: "Waiting for its window to start",
  OPEN: "Window open",
  SETTLING: "Being settled",
  SETTLED: "Settled",
  AMBIGUOUS: "Could not be settled",
  VOID: "Void",
  UNSETTLEABLE: "Could not be settled",
  NO_CALL: "No call",
  FAILED: "Failed",
  FAILED_EVIDENCE: "Failed for lack of evidence",
  FAILED_LATE: "Too late",
  FAILED_STALE: "Evidence went stale",
  FAILED_LOST: "Lost with the process",
  LEVEL: "Commitment level changed",
};

export function stepWords(state: string): string {
  return STEP_WORDS[state] ?? state;
}

/** Whether a Today document has what the first card needs (an older export does not); the page says so instead of printing "undefined". */
export function hasChanges(today: {
  changes?: unknown;
  product?: unknown;
  rule?: unknown;
}): boolean {
  return (
    Array.isArray(today.changes) &&
    typeof today.product === "string" &&
    typeof today.rule === "string"
  );
}

/** Changes that say the same thing about different tokens, as one card: the same kind, sentence, horizon and agent. The order of the first
 * of each group is kept, so the ranking is unchanged; each token keeps its own link. */
export function groupChanges<
  C extends {
    kind: string;
    sentence: string;
    hours: number;
    arm_name: string;
    symbol: string;
    forecast_id: string;
  },
>(changes: readonly C[]): { first: C; members: C[] }[] {
  const groups: { first: C; members: C[] }[] = [];
  for (const c of changes) {
    const same = groups.find(
      (g) =>
        g.first.kind === c.kind &&
        g.first.sentence === c.sentence &&
        g.first.hours === c.hours &&
        g.first.arm_name === c.arm_name,
    );
    if (same !== undefined) same.members.push(c);
    else groups.push({ first: c, members: [c] });
  }
  return groups;
}

/** The one sentence a person can take away from Today: the first card, as a headline (tokens, horizon, agent, then what changed). It is the
 * first group of ``groupChanges``, so it is the card the page ranks first; none when nothing changed. */
export function topHeadline<
  C extends {
    kind: string;
    sentence: string;
    hours: number;
    arm_name: string;
    symbol: string;
    forecast_id: string;
  },
>(changes: readonly C[]): string | null {
  const first = groupChanges(changes)[0];
  if (first === undefined) return null;
  const symbols = first.members.map((m) => m.symbol).join(", ");
  return `${symbols}, ${horizon(first.first.hours)}, ${first.first.arm_name}: ${first.first.sentence}`;
}

/** One sentence on where a call's evidence came from: each source that was read, with how many items, then each source that gave nothing
 * and why. A source that was not read is said so, never left out, so a reader sees at once whether RYO was part of the call. */
export function readLine(
  evidence: readonly { source: string }[],
  unread: readonly { source: string; reason: string }[],
): string {
  const name = sourceName;
  const counts = new Map<string, number>();
  for (const e of evidence)
    counts.set(e.source, (counts.get(e.source) ?? 0) + 1);
  const read = [...counts.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .map(([s, n]) => `${name(s)} (${n} ${n === 1 ? "item" : "items"})`);
  const unreadBySource = new Map<string, string>();
  for (const u of unread)
    if (!unreadBySource.has(u.source)) unreadBySource.set(u.source, u.reason);
  const missed = [...unreadBySource.entries()]
    .sort((a, b) => a[0].localeCompare(b[0]))
    .filter(([s]) => !counts.has(s))
    .map(([s, why]) => `${name(s)}: ${why}`);
  const first =
    read.length === 0 ? "It read nothing." : `It read ${read.join(", ")}.`;
  return missed.length === 0
    ? first
    : `${first} Not read: ${missed.join("; ")}.`;
}

export type Disagreement = {
  symbol: string;
  hours: number;
  low: { arm_name: string; p: number; forecast_id: string };
  high: { arm_name: string; p: number; forecast_id: string };
  gap: number;
};

/** Smallest gap, in percentage points, between two agents' chances on the same call for it to count as a disagreement; both must be on
 * opposite sides of 50 (one at most 49, the other at least 51). */
export const DISAGREEMENT_GAP = 6;

/** The pair of agents furthest apart on one call (the same token and horizon, up or down), one on each side of a coin flip, among a cycle's
 * rows; none when there is none. Only up-or-down calls with a stated chance are paired; a tie goes to the earlier token, then horizon. */
export function largestDisagreement(
  rows: readonly {
    contract: string;
    symbol: string;
    hours: number;
    arm_name: string;
    chance: string | null;
    forecast_id: string;
  }[],
): Disagreement | null {
  let best: Disagreement | null = null;
  const groups = new Map<
    string,
    { arm_name: string; p: number; forecast_id: string }[]
  >();
  for (const r of rows) {
    if (r.contract !== "D" || r.chance === null) continue;
    const m = /^(\d+)% up$/.exec(r.chance);
    if (m === null) continue;
    const key = `${r.symbol}\u0000${r.hours}`;
    const list = groups.get(key) ?? [];
    list.push({
      arm_name: r.arm_name,
      p: Number(m[1]),
      forecast_id: r.forecast_id,
    });
    groups.set(key, list);
  }
  for (const [key, list] of [...groups.entries()].sort((a, b) => {
    const [sa = "", ha = ""] = a[0].split("\u0000");
    const [sb = "", hb = ""] = b[0].split("\u0000");
    return sa.localeCompare(sb) || Number(ha) - Number(hb); // the earlier token, then the shorter horizon, as numbers
  })) {
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

const WHY_IT_MATTERS: Record<string, string> = {
  FLIP: "An agent changed its mind: a practice position that follows it would change side.",
  MISS: "A call settled against what its agent said.",
  STOPPED: "A call could not be made this cycle; the gap is shown, not filled.",
  HIT: "A call settled as its agent said.",
  MOVE: "An agent's chance moved without changing side.",
  NEW: "These are the first calls on record.",
};

/** One sentence on why the first change matters, from its kind alone (the six kinds the league writes); none for a kind it does not know. */
export function whyItMatters(kind: string): string | null {
  return WHY_IT_MATTERS[kind] ?? null;
}

/** What the first card says when nothing changed; the league writes the same sentence (hoshitori.changes). */
export const NOTHING_CHANGED = "Nothing changed since the last cycle.";

/** An invalidation condition in plain words. The league writes them as a closed form (``ret_24h below 0``: the return over 24 hours); one
 * in any other form is shown as it was recorded, so nothing is hidden or guessed. */
export function invalidationWords(text: string): string {
  const atr =
    /^close (below|above) entry (minus|plus) (\d+(?:\.\d+)?) ATR(\d+)$/.exec(
      text,
    );
  if (atr !== null)
    return `A close ${atr[1]} the entry price by more than ${atr[3]} times its average hourly range over ${atr[4]} hours (ATR ${atr[4]}).`;
  const m = /^ret_(\d+)h (below|above) (-?\d+(?:\.\d+)?)$/.exec(text);
  if (m !== null)
    return `The price change over ${horizon(Number(m[1]))} is ${m[2]} ${percentOfShare(m[3] ?? "", 4)}.`;
  const f =
    /^(close|last_close|atr14|atr14_share) (below|above) (\d+(?:\.\d+)?)$/.exec(
      text,
    );
  if (f === null) return text;
  const [, field, op, level = ""] = f;
  if (field === "atr14")
    return `The average hourly range over 14 hours (ATR 14) is ${op} ${level}.`;
  if (field === "atr14_share")
    return `The average hourly range over 14 hours is ${op} ${percentOfShare(level, 4).replace("+", "")} of the price.`;
  return `The price closes ${op} ${level}.`;
}

// --- what a verifier's pass means, exactly ----------------------------------------------------------------------------------------------
/** What a pass shows, what it does not, and who must be honest for it to mean what it says. Closed
 * sentences: the Verify page prints them as they are, and a test keeps their main claims. */
export const PASS_SHOWS: readonly string[] = [
  "Every record in the bundle is exactly as it was signed, and sits in the log that the signed checkpoint describes.",
  "That checkpoint was time-stamped by the authorities the trusted root names, as many as the root asks for, close enough to the moment of commitment.",
  "So each call in the bundle existed, as written, no later than the time-stamps say.",
  "When the findings say the whole batch is here: no record the cycle committed was taken out, added or changed since, because their digests make the root the committing step names.",
  "When the findings list the later steps: the log's later checkpoint extends the time-stamped one, so those steps are as the log wrote them, and each call's first eligible moment and its start are not earlier than the time-stamps allow.",
];
export const PASS_DOES_NOT_SHOW: readonly string[] = [
  "That the bundle holds every call of its cycle, unless the findings say so. A bundle without the cycle's committing step shows nothing about what was taken out of it.",
  "That a call's window began after the time-stamps, unless the findings list the later steps. Without them the start of the window and the settlement, which the operator writes after the time-stamped checkpoint, are not in the bundle. Even with them, a settlement is never shown to be right.",
  "That the evidence was true or complete. A call is only as good as what it read.",
  "That the call was good. This is a check of what was fixed and when, never of how well.",
];
export const PASS_NEEDS_HONEST: readonly string[] = [
  "The operator: to start each call's window after the time-stamps, to settle it fairly, to commit every call of a cycle in the one batch (a batch the operator left a call out of is complete as written), to put them on these pages, and to run one writer for the log.",
  "Whoever wrote the trusted root: a root with loose timing rules gives a looser pass, and the verdict prints them (the line under the findings), so a reader can see which rules the pass was judged by.",
  "The time-stamp authorities: not both of them at once.",
  "Whoever produced each piece of evidence: for saying whether it was live. Nothing offline can check that.",
];

/** The distance to a different call of the rules agent for one receipt, or none: matched on the cycle, the arm, the symbol and the horizon. */
export function boundaryFor(
  boundaries: Boundary[],
  row: { cycle_id: string; arm: string; symbol: string; hours: number },
): Boundary | null {
  return (
    boundaries.find(
      (b) =>
        b.cycle_id === row.cycle_id &&
        b.arm === row.arm &&
        b.symbol === row.symbol &&
        b.hours === row.hours,
    ) ?? null
  );
}

const NO_CALL_WORDS =
  "it made no call, because there are too few past windows in that state";

/** Why a call of the rules agent was what it was, in sentences: the state of the market it saw, what it said, and how far the data was from a
 * different call. The return itself and the returns at which it would have changed are not printed: the page says
 * where the call stood and how far from changing, and shows no value of the exchanges' data. */
export function whyWords(b: Boundary): string[] {
  const said = b.says.startsWith("no call")
    ? NO_CALL_WORDS
    : `it said ${b.says}`;
  const out = [`The state of the market the rule saw was ${b.state}: ${said}.`];
  if (b.moves.length === 0) {
    out.push(
      "No move of the return, up or down, would have changed the call: the rule says the same in every state.",
    );
  }
  for (const m of b.moves) {
    if (m.direction !== "UP" && m.direction !== "DOWN")
      throw new Error(`not a direction: ${m.direction}`);
    const how = m.direction === "UP" ? "A rise" : "A fall";
    out.push(
      `${how} of ${m.distance.replace(/^[+-]/, "")} points would have put it in ${m.state}, and it would have said ${m.says}.`,
    );
  }
  return out;
}

// --- the Desk's decision log ------------------------------------------------------------------------------------------------------------
// One sentence for every code the Desk's records can hold (src/hoshitori/desk_record.py, desk_gates.py, desk_menu.py, falsify.py) and for none
// other: a code outside these tables is an error, never shown. They say what a step was and how it ended. They never say a claim is true: a check
// that found support says it found support in the sources checked, and an attempt to disprove that failed says only that it did not succeed.

export const DESK_TABLES: Record<string, Record<string, string>> = {
  state: {
    RUNNING: "Running",
    STOPPED: "Stopped",
    COMMITTED: "Reached a practice call: recorded here, not time-stamped",
    ABSTAINED: "Abstained",
    FAILED: "Failed",
    FAILED_FINAL: "Failed and closed",
  },
  reason: {
    UTILITY_BELOW_THRESHOLD: "no remaining check was worth its cost",
    CANNOT_FLIP_DECISION: "no remaining check could change the decision",
    BUDGET_EXHAUSTED: "the budget for calls, time or tokens was spent",
    ALL_MENU_OPENED: "every check on the menu had been made",
    BELOW_MINIMUM_EDGE: "the edge it saw was below the minimum",
    ESSENTIAL_PREMISE_UNPROVEN: "a premise the call needs could not be shown",
    EVIDENCE_CONFLICT_UNRESOLVED:
      "the evidence disagreed and no check settled it",
    OUT_OF_SCOPE:
      "outside what the Desk reads: the claim, or a token whose contract a screen lists",
    FAILED: "a step failed",
    FAILED_EVIDENCE: "a source it needed was not available",
    FAILED_STALE: "the evidence was too old to use",
    MODEL_OUTPUT_INVALID: "the model's answer did not fit the required form",
    MODEL_UNAVAILABLE: "the model could not be reached",
    RUN_INTERRUPTED: "the run was cut off and closed on restart",
  },
  route: {
    RYO_SCAN: "RYO market scan",
    EVENT: "an event trigger",
    WATCHLIST: "a watchlist",
  },
  check: {
    INDEPENDENT_PRICE_CHECK: "Price on other exchanges",
    DERIVATIVES_CONTEXT: "Funding and derivatives",
    RYO_TOKEN_READ: "RYO token read",
    RYO_DEEP_ANALYSIS: "RYO deep analysis",
    MARKET_CONTEXT_OVERVIEW: "RYO market overview",
    MARKET_SENTIMENT_SHIFT: "RYO sentiment shift",
    ADDRESS_FLAG_CHECK: "Screen of the token's contract",
  },
  status: {
    UNCHECKED: "Not checked",
    MIXED: "Sources disagree",
    SUPPORTED: "Supported in the sources checked",
    CONTRADICTED: "Contradicted in the sources checked",
    NOT_FOUND_IN_SOURCES_CHECKED: "Not found in the sources checked",
    NOT_CHECKABLE: "Not checkable here",
    SOURCE_UNAVAILABLE: "Source unavailable",
    FLAGGED: "Listed by a source",
    NO_FLAG_FOUND: "No flag found (not a safety rating)",
    INCOMPLETE: "Some sources did not answer",
    NOT_SCREENED: "Not screened",
  },
  gate: {
    HAZARD_SCREEN: "No source lists the token's contract",
    GROUNDING: "Every number traces to a stored source",
    REQUIRED_EVIDENCE: "The evidence the call needs is present",
    STALENESS: "The evidence is recent enough",
    MINIMUM_EDGE: "The edge is above the minimum",
    FALSIFICATION: "What was tried to disprove it",
    MINIMUM_EDGE_AFTER_CUT: "The edge is still above the minimum after any cut",
    RISK_KERNEL: "The size fits the risk limits",
  },
  gateStatus: {
    PASS: "Passed",
    CUT: "Cut the size",
    STOP: "Stopped the call",
  },
  falsification: {
    PASSED: "It survived the tests that could be run. That does not prove it.",
    FAILED: "The alternative explanation fitted better, so the size was cut",
    NOT_FALSIFIED_ATTEMPTED:
      "The tests could not all be run, so the size was cut",
  },
  outcome: {
    OK: "Done",
    FAILED: "Failed",
    NOT_STARTED: "Not started",
  },
  error: {
    DEADLINE_UNREACHABLE: "there was no time left to make it",
    NO_ADAPTER: "no source is wired for it",
    NO_ASSET: "the token could not be identified",
    NO_INSTRUMENT: "the token has no instrument on that venue",
    BAD_REQUEST: "the request was refused as malformed",
    CALL_FAILED: "the source did not answer",
    TIMED_OUT: "the source took too long",
    UNREADABLE: "the answer could not be read",
    NOT_LIVE: "the source was not live",
    BINDING_MISMATCH:
      "the source did not match the run's recorded or live setting",
    UNKNOWN_CHECK_TYPE: "the check is not one the Desk knows",
  },
  layer: {
    NONE: "no check was made",
    DETERMINISTIC: "checked by a rule on the source's numbers",
    MOVE_CHECK: "checked against a price move",
    PERSON: "checked by a person",
  },
  label: {
    live: "Live",
    recorded: "Recorded",
    synthetic: "Synthetic",
  },
  test: {
    T_ONSET: "Did the move begin before the event?",
    T_MARKET: "Did the whole market move the same way?",
    T_SOURCE: "Does an independent source agree?",
    T_PLACEBO_TIME: "Does an unrelated time look the same?",
  },
  result: {
    FAVOURS_ORIGINAL: "Favours the original explanation",
    FAVOURS_ALTERNATIVE: "Favours the alternative",
    INCONCLUSIVE: "Inconclusive",
    NOT_TESTABLE: "Not testable",
  },
  why: {
    series_too_short: "the price series was too short",
    series_misaligned: "the price series did not line up",
    non_finite_value: "a value was missing or not a number",
    no_volatility: "the price did not move enough to measure",
    proxy_has_no_variation: "the reference series did not vary",
    no_event_time: "the event has no recorded time",
    uncalibrated: "the test has not been shown to be reliable yet",
    no_source: "no second source was available",
  },
  stance: {
    LONG: "Long",
    FLAT: "Flat",
    SHORT: "Short",
  },
  final: {
    COMMIT: "Commit a practice call",
    ABSTAIN: "Abstain",
    FAILED: "Fail",
  },
};

const DESK_NAMES: Record<string, string> = {
  state: "Desk state",
  reason: "Desk reason",
  route: "Desk route",
  check: "Desk check type",
  status: "Desk check status",
  gate: "Desk gate",
  gateStatus: "Desk gate status",
  falsification: "Desk falsification outcome",
  outcome: "Desk check outcome",
  error: "Desk error class",
  layer: "Desk check layer",
  label: "Desk run label",
  test: "Desk test",
  result: "Desk test result",
  why: "Desk test reason",
  stance: "Desk stance",
  final: "Desk final action",
};

/** The sentence for one code of the Desk's records; a table or a code outside the closed sets is an error. */
// The falsification sentence: an outcome only when every test could run; otherwise the label that says the tests were not all run, so a
// run whose tests were all "not testable" never reads as if an alternative explanation had won.
export function falsificationKey(f: {
  outcome: string;
  completed: boolean;
  label: string | null;
}): string {
  return f.completed ? f.outcome : (f.label ?? f.outcome);
}

// A gate can only shrink a call. A gate that changed nothing says so; on a proposal of no size there is nothing it could have cut.
export function gateWord(status: string, proposalWeight: string): string {
  if (status !== "PASS") return deskWord("gateStatus", status);
  return Number(proposalWeight) === 0 ? "Nothing to cut" : "No change";
}

export function deskWord(table: string, code: string): string {
  const words = DESK_TABLES[table];
  if (words === undefined) throw new Error(`not a Desk word table: ${table}`);
  const word = words[code];
  if (word === undefined)
    throw new Error(`not a ${DESK_NAMES[table] ?? table}: ${code}`);
  return word;
}

const ENDINGS: Record<string, [string, string[]]> = {
  COMMITTED: ["Reached a practice call: recorded here, not time-stamped", []],
  ABSTAINED: [
    "Abstained",
    [
      "BELOW_MINIMUM_EDGE",
      "ESSENTIAL_PREMISE_UNPROVEN",
      "EVIDENCE_CONFLICT_UNRESOLVED",
      "OUT_OF_SCOPE",
    ],
  ],
  FAILED: [
    "Failed",
    [
      "FAILED",
      "FAILED_EVIDENCE",
      "FAILED_STALE",
      "MODEL_OUTPUT_INVALID",
      "MODEL_UNAVAILABLE",
      "RUN_INTERRUPTED",
    ],
  ],
  FAILED_FINAL: [
    "Failed",
    [
      "RUN_INTERRUPTED",
      "FAILED",
      "FAILED_EVIDENCE",
      "FAILED_STALE",
      "MODEL_OUTPUT_INVALID",
      "MODEL_UNAVAILABLE",
    ],
  ],
  STOPPED: [
    "Stopped",
    [
      "UTILITY_BELOW_THRESHOLD",
      "CANNOT_FLIP_DECISION",
      "BUDGET_EXHAUSTED",
      "ALL_MENU_OPENED",
    ],
  ],
};

/** How a run ended, as one sentence: the state and, where the record gives one, the reason. A reason that does not belong to the state is an error. */
export function endingLine(
  state: string,
  reason: string | null,
  open: boolean,
): string {
  if (state === "RUNNING") {
    if (reason !== null)
      throw new Error(`not a reason for a running run: ${reason}`);
    return open ? "Still running: no ending has been recorded." : "Running.";
  }
  const ending = ENDINGS[state];
  if (ending === undefined) throw new Error(`not a Desk state: ${state}`);
  const [head, reasons] = ending;
  if (reason === null) return `${head}.`;
  if (!reasons.includes(reason))
    throw new Error(`not a reason for ${state}: ${reason}`);
  return `${head}: ${deskWord("reason", reason)}.`;
}

/** What a run spent against its budget. */
export function spentLine(
  spent: { calls: number; seconds: number; tokens: number },
  budget: { calls: number; seconds: number; tokens: number },
): string {
  return `${spent.calls} of ${count(budget.calls, "call")}, ${spent.seconds} of ${count(budget.seconds, "second")}, ${spent.tokens} of ${count(budget.tokens, "token")}.`;
}

/** A source that was asked for the evidence of a call and brought nothing, as one sentence: who, why and, where one failed, which window. */
export function unreadLine(u: {
  source: string;
  reason: string;
  interval_s: number | null;
}): string {
  if (u.interval_s === null) return `${sourceName(u.source)}: ${u.reason}.`;
  const s = u.interval_s;
  if (!Number.isInteger(s) || s <= 0) throw new Error(`not a window: ${s}`);
  const window =
    s % 3600 === 0
      ? `${s / 3600}-hour`
      : s % 60 === 0
        ? `${s / 60}-minute`
        : `${s}-second`;
  return `${sourceName(u.source)}: ${u.reason}, for the ${window} window.`;
}

/** What one check did to how its part stands: ``before`` is the status the part had, ``own`` what this check found alone and ``after`` what the
 * part stands at once the check is combined with the ones before (a disagreement is MIXED, never averaged). */
export function changeWords(
  before: string,
  own: string,
  after: string,
  checkType?: string,
): string {
  if (checkType === "ADDRESS_FLAG_CHECK") return "screened before the claim";
  if (before === "UNCHECKED") return "first check of this part";
  if (after === "MIXED" && before !== "MIXED")
    return "disagrees with what was found before";
  if (own === "SOURCE_UNAVAILABLE") return "could not be read: no change";
  if (own === "NOT_CHECKABLE") return "could not read this part: no change";
  if (
    before === after &&
    own === after &&
    (own === "SUPPORTED" || own === "CONTRADICTED")
  )
    return "agrees with what was found before";
  if (before === after) return "no change";
  return `before: ${deskWord("status", before).toLowerCase()}`;
}

/** The independent origins that read the part a run checked (contract 40), in a sentence: RYO is one origin whatever tool it was read through
 * and an exchange is one whatever its products, so three RYO calls never read "3 sources". It says when only one origin read the part. */
export function originsLine(o: {
  read: string[];
  supporting: string[];
  contradicting: string[];
  calls: number;
}): string {
  const name = (family: string): string => {
    if (family === "RYO") return "RYO";
    if (family.startsWith("EXCHANGE:")) return family.slice("EXCHANGE:".length);
    throw new Error(`not an origin family a check can have: ${family}`);
  };
  const calls = count(o.calls, "call");
  if (o.read.length === 0)
    return `No independent origin read this part. ${calls}.`;
  const names = o.read.map(name).join(", ");
  const yes = o.supporting.length;
  const no = o.contradicting.length;
  const supports = `${yes} ${yes === 1 ? "supports" : "support"} it`;
  const contradicts =
    no === 0
      ? "none contradicts it"
      : `${no} ${no === 1 ? "contradicts" : "contradict"} it`;
  const alone = o.read.length === 1 ? "; no second origin read it" : "";
  return `${count(o.read.length, "independent origin")} read this part (${names}): ${supports} and ${contradicts}${alone}. ${calls}.`;
}

/** A decimal string of a record without its trailing zeros ("0.030000" is "0.03"); the value is never changed, and anything but a plain decimal is an error. */
export function plainDecimal(text: string): string {
  if (!/^\d+(\.\d+)?$/.test(text)) throw new Error(`not a decimal: ${text}`);
  return text.includes(".") ? text.replace(/0+$/, "").replace(/\.$/, "") : text;
}

/** The name of a page as a link to it reads ("Open the Desk"). */
export const WHERE_WORDS: Record<string, string> = {
  today: "Today",
  calls: "the calls",
  record: "the track record",
  verify: "the record check",
  quality: "the evidence of quality",
  drill: "the failure drills",
  claims: "the claim check",
  challenge: "the source test",
  desk: "the Desk",
  judges: "this page",
};

// --- the practice portfolio -------------------------------------------------------------------------------------------------------------
// Every amount arrives as exact decimal text. It is shown from the text, never through a float: the digits are the book's own.

const DECIMAL = /^(-?)(\d+)(?:\.(\d+))?$/;

function decimalParts(text: string): {
  negative: boolean;
  whole: string;
  fraction: string;
} {
  const m = DECIMAL.exec(text);
  if (m === null) throw new Error(`not a decimal: ${text}`);
  return { negative: m[1] === "-", whole: m[2] ?? "0", fraction: m[3] ?? "" };
}

/** An amount of money with thousands separators and its own digits: "101398.5" as "101,398.5". */
export function groupedMoney(text: string): string {
  const { negative, whole, fraction } = decimalParts(text);
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${negative ? "-" : ""}${grouped}${fraction === "" ? "" : `.${fraction}`}`;
}

/** The decimal point moved right by `places` (negative: left), with trailing zeros dropped: "0.0095" by 2 is "0.95". */
function shifted(text: string, places: number): string {
  const { negative, whole, fraction } = decimalParts(text);
  let digits = whole + fraction;
  let point = whole.length + places;
  if (point <= 0) {
    digits = "0".repeat(1 - point) + digits;
    point = 1;
  } else if (point > digits.length) {
    digits += "0".repeat(point - digits.length);
  }
  const head = digits.slice(0, point).replace(/^0+(?=\d)/, "");
  const tail = digits.slice(point).replace(/0+$/, "");
  const out = tail === "" ? head : `${head}.${tail}`;
  return out === "0" ? "0" : `${negative ? "-" : ""}${out}`;
}

/** A decimal rounded half away from zero to `places` places, from its digits (no float): "98892.5875" to 2 places is "98892.59". */
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

/** An amount of money for a headline: cents and thousands separators, "98892.5875" as "98,892.59". The exact digits are in the numbers behind it. */
export function money2(text: string): string {
  return groupedMoney(roundDecimal(text, 2));
}

/** A share (0.01 is one percent) as a signed percentage rounded to `places` places, trailing zeros dropped: "0.009811" as "+0.98%". */
export function percentOfShare(text: string, places = 2): string {
  const rounded = roundDecimal(shifted(text, 2), places);
  const percent = rounded.includes(".")
    ? rounded.replace(/0+$/, "").replace(/\.$/, "")
    : rounded;
  if (percent === "0") return "0%";
  return `${percent.startsWith("-") ? "" : "+"}${percent}%`;
}

/** A chance as a whole percentage, a half rounded up as the alerts write it: "0.535519" as "54%", "0.545" as "55%". */
export function chancePercent(p: string): string {
  return `${roundDecimal(shifted(p, 2), 0)}%`;
}

/** Basis points as a percentage: "10" as "0.1%". */
export function bpsAsPercent(text: string): string {
  return `${shifted(text, -2)}%`;
}

const LEVEL_WORDS: Record<string, string> = {
  BASE: "Base costs",
  STRESS: "Stress costs",
  DOUBLE_FEES: "Double fees",
};

/** The name of a cost level of the practice portfolio. */
export function practiceLevel(level: string): string {
  const word = LEVEL_WORDS[level];
  if (word === undefined) throw new Error(`not a cost level: ${level}`);
  return word;
}

const EXIT_WORDS: Record<string, string> = {
  STOP: "the stop was reached",
  TARGET: "the target was reached",
  TIME: "the time ran out",
  GAP_EXIT: "taken at the next bar there was",
};

/** Why a practice position was closed, as it reads after "closed because". */
export function exitWords(kind: string | null): string {
  if (kind === null) return "not recorded";
  const word = EXIT_WORDS[kind];
  if (word === undefined) throw new Error(`not an exit kind: ${kind}`);
  return word;
}

const MODE_WORDS: Record<string, string> = {
  ACTIVE: "Sizing as planned",
  HALVED: "Sizes halved after a fall",
  FLAT: "Closed after a fall",
  HALTED: "Stopped after a deep fall",
};

/** The state of a practice book: the risk ladder's rung, or that the book was wiped out. */
export function modeWords(mode: string, ruined: boolean): string {
  const word = MODE_WORDS[mode];
  if (word === undefined) throw new Error(`not a mode: ${mode}`);
  return ruined ? "Wiped out" : word;
}

type CurvePoint = { at: string; equity: string };

/** The stretches of a curve where the marks stop for much longer than they usually do, as the indices of the mark before and the mark after:
 * a spacing of more than ten times the median spacing. Judged from four marks on, since fewer have no usual spacing, and not at all when the
 * usual spacing is nothing (marks at one time). */
export function curveGaps(points: CurvePoint[]): [number, number][] {
  if (points.length < 4) return [];
  const times = points.map((p) => Date.parse(p.at));
  const spacing = times.slice(1).map((time, i) => time - (times[i] ?? time));
  const ordered = [...spacing].sort((a, b) => a - b);
  const mid = ordered.length >> 1;
  const usual =
    ordered.length % 2 === 1
      ? (ordered[mid] ?? 0)
      : ((ordered[mid - 1] ?? 0) + (ordered[mid] ?? 0)) / 2;
  if (!(usual > 0)) return [];
  const gaps: [number, number][] = [];
  spacing.forEach((s, i) => {
    if (s > 10 * usual) gaps.push([i, i + 1]);
  });
  return gaps;
}

/** What the equity curve shows, in words, from the same marks that draw it. `total` is how many marks the book has when the curve draws only some of
 * them (a long record is thinned evenly): the words then say so, since the highest and lowest of the drawn marks are not the book's own peak. */
export function curveSummary(points: CurvePoint[], total?: number): string {
  const first = points[0];
  const last = points[points.length - 1];
  if (first === undefined || last === undefined) return "No mark yet.";
  if (points.length === 1)
    return `One mark so far: equity ${groupedMoney(first.equity)}.`;
  const order = [...points].sort((a, b) => Number(a.equity) - Number(b.equity));
  const lowest = order[0] ?? first;
  const highest = order[order.length - 1] ?? last;
  const gaps = curveGaps(points);
  const told = gaps.slice(0, 3).flatMap(([a, b]) => {
    const from = points[a];
    const to = points[b];
    return from !== undefined && to !== undefined
      ? [` No mark is drawn between ${when(from.at)} and ${when(to.at)}.`]
      : [];
  });
  const more =
    gaps.length > 3
      ? ` ${gaps.length - 3} more ${gaps.length - 3 === 1 ? "stretch has" : "stretches have"} no mark drawn.`
      : "";
  const thinned =
    total !== undefined && total > points.length
      ? ` The curve draws ${points.length} of the book's ${total} marks, chosen evenly, so its highest and lowest can be less extreme than the book's own peak and largest fall.`
      : "";
  return `Equity went from ${groupedMoney(first.equity)} to ${groupedMoney(last.equity)} over ${points.length} marks; lowest ${groupedMoney(lowest.equity)}, highest ${groupedMoney(highest.equity)}.${told.join("")}${more}${thinned}`;
}

/** The marks as points of a drawing `width` by `height` with `pad` around it: time runs right, higher equity is higher up; a flat run is in the middle. */
export function curvePath(
  points: CurvePoint[],
  width: number,
  height: number,
  pad: number,
): { points: [number, number][]; d: string; dashed: string } {
  if (points.length === 0) return { points: [], d: "", dashed: "" };
  const times = points.map((p) => Date.parse(p.at));
  const values = points.map((p) => Number(p.equity));
  const t0 = Math.min(...times);
  const t1 = Math.max(...times);
  const v0 = Math.min(...values);
  const v1 = Math.max(...values);
  const drawn: [number, number][] = points.map((_, i) => {
    const t = times[i] ?? t0;
    const v = values[i] ?? v0;
    const x =
      t1 === t0 ? width / 2 : pad + ((t - t0) / (t1 - t0)) * (width - 2 * pad);
    const y =
      v1 === v0
        ? height / 2
        : height - pad - ((v - v0) / (v1 - v0)) * (height - 2 * pad);
    return [Math.round(x), Math.round(y)];
  });
  // a stretch with no mark is not joined in the solid line; it is drawn on its own, to be shown dashed
  const gapAfter = new Set(curveGaps(points).map(([i]) => i));
  const d = drawn
    .map(
      ([x, y], i) => `${i === 0 || gapAfter.has(i - 1) ? "M" : "L"}${x} ${y}`,
    )
    .join("");
  const dashed = [...gapAfter]
    .flatMap((i) => {
      const a = drawn[i];
      const b = drawn[i + 1];
      return a !== undefined && b !== undefined
        ? [`M${a[0]} ${a[1]}L${b[0]} ${b[1]}`]
        : [];
    })
    .join("");
  return { points: drawn, d, dashed };
}

// --- what the requirements rest on ----------------------------------------------------------------------------------------------------

function coverageCount(counts: CoverageCount[], verdict: string): number {
  return counts.find((c) => c.verdict === verdict)?.count ?? 0;
}

function joinParts(parts: string[]): string {
  return parts.length <= 1
    ? (parts[0] ?? "")
    : `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

/** What has a check behind it, for the organisers' rubric and the contract, in two sentences from the counts the matrices state and nothing else. */
export function coverageSentence(c: Coverage): string {
  const rFound = coverageCount(c.rubric, "CHECKS FOUND");
  const rMissing = coverageCount(c.rubric, "CHECK NOT FOUND");
  const rUnnamed = coverageCount(c.rubric, "NO CHECK NAMED");
  const rLead =
    rFound === c.rubric_total
      ? `all ${c.rubric_total} name checks that exist`
      : `${whole(rFound)} ${rFound === 1 ? "names" : "name"} checks that exist`;
  const rOthers: string[] = [];
  if (rMissing > 0)
    rOthers.push(
      `${whole(rMissing)} ${rMissing === 1 ? "names" : "name"} a check that cannot be found`,
    );
  if (rUnnamed > 0)
    rOthers.push(
      `${whole(rUnnamed)} ${rUnnamed === 1 ? "has no check named, so it is" : "have no check named, so they are"} not verified`,
    );
  const found = coverageCount(c.contract, "CHECKS FOUND");
  const missing = coverageCount(c.contract, "CHECK NOT FOUND");
  const partly = coverageCount(c.contract, "PARTLY BUILT");
  const unbuilt = coverageCount(c.contract, "NOT BUILT");
  const outside = coverageCount(c.contract, "OUTSIDE THIS REPOSITORY");
  const unnamed = coverageCount(c.contract, "NO CHECK NAMED");
  const lead =
    found === c.contract_total
      ? `all ${c.contract_total} do`
      : found === 0
        ? "none do"
        : `${whole(found)} do`;
  const others: string[] = [];
  if (missing > 0)
    others.push(
      `${whole(missing)} ${missing === 1 ? "names" : "name"} a check that cannot be found`,
    );
  if (partly > 0)
    others.push(
      `${whole(partly)} ${partly === 1 ? "is" : "are"} only partly built`,
    );
  if (unbuilt > 0)
    others.push(`${whole(unbuilt)} ${unbuilt === 1 ? "is" : "are"} not built`);
  if (outside > 0)
    others.push(
      `${whole(outside)} ${outside === 1 ? "is" : "are"} checked outside this repository`,
    );
  if (unnamed > 0)
    others.push(
      `${whole(unnamed)} ${unnamed === 1 ? "has no check named, so it is" : "have no check named, so they are"} not verified`,
    );
  return [
    `Of the ${whole(c.rubric_total)} lines of the published rubric, ${rLead}${rOthers.length === 0 ? "." : `; ${joinParts(rOthers)}.`}`,
    `Of the ${whole(c.contract_total)} sections of the contract, ${lead}${others.length === 0 ? "." : `; ${joinParts(others)}.`}`,
    "A check that exists is not a check that passes.",
  ].join(" ");
}

/** A section's title without the contract's version tag, "(v0.8)" at its end. */
export function coverageGapTitle(title: string): string {
  return title.replace(/\s*\(v\d+(\.\d+)?\)$/, "");
}

/** A section's reason as a sentence: a capital letter and a full stop; the dash the matrix writes for none is no reason. */
export function coverageGapReason(reason: string): string {
  const r = reason.trim();
  if (r === "" || r === "-") return "";
  const capital = r.charAt(0).toUpperCase() + r.slice(1);
  return capital.endsWith(".") ? capital : `${capital}.`;
}

/** A section's verdict in the reader's words. */
export function coverageVerdictWords(verdict: string): string {
  switch (verdict) {
    case "PARTLY BUILT":
      return "only partly built";
    case "NOT BUILT":
      return "not built";
    case "OUTSIDE THIS REPOSITORY":
      return "checked outside this repository";
    case "CHECK NOT FOUND":
      return "names a check that cannot be found";
    case "NO CHECK NAMED":
      return "no check named, so not verified";
    default:
      return "has checks";
  }
}

// --- the mutation sweeps ----------------------------------------------------------------------------------------------------------------

/** A whole number with thousands separators: 26640 as "26,640". */
function whole(n: number): string {
  return groupedMoney(String(n));
}

/** What the sweeps found, in one paragraph, from the summary of the sweep data and nothing else. */
export function sweepSentence(s: SweepSummary): string {
  if (s.modules === 0) return "No module has a sweep.";
  const percent = s.places === 0 ? 0 : Math.floor((100 * s.noticed) / s.places);
  const others: string[] = [];
  if (s.unbuilt > 0) others.push(`${whole(s.unbuilt)} could not be built`);
  if (s.raised > 0)
    others.push(`${whole(s.raised)} made a check raise an error`);
  if (s.clock > 0) {
    others.push(
      `${whole(s.clock)} ${s.clock === 1 ? "was" : "were"} noticed only because a check ran out of time or the machine failed, which shows nothing about the checks`,
    );
  }
  const joined =
    others.length <= 1
      ? (others[0] ?? "")
      : `${others.slice(0, -1).join(", ")} and ${others[others.length - 1]}`;
  const parts = [
    `${whole(s.modules)} ${s.modules === 1 ? "module was" : "modules were"} each changed in every small way their code allows: ${whole(s.places)} changes in all.`,
    `A check noticed ${whole(s.noticed)} of them (${percent}%)${joined === "" ? "." : `; ${joined}.`}`,
    s.survived === 0
      ? "Nothing went unnoticed."
      : `${whole(s.survived)} went unnoticed; ${s.survived === 1 ? "it is" : "each is"} listed as harmless, with its reason, in its module's test.`,
  ];
  if (s.stale > 0) {
    parts.push(
      `${whole(s.stale)} ${s.stale === 1 ? "module changed after its sweep" : "modules changed after their sweep"}, so what the sweep found says nothing about the code as it is now.`,
    );
  }
  if (s.unchecked > 0) {
    parts.push(
      `${whole(s.unchecked)} ${s.unchecked === 1 ? "module" : "modules"} could not be compared with the code here.`,
    );
  }
  return parts.join(" ");
}

/** Whether the files that were swept are still the files there are, as a table cell reads. */
export function currencyWords(
  current: boolean | null,
  reason: string | null,
): string {
  if (current === null) return "not compared here";
  return current ? "the swept files" : `no: ${reason ?? "they changed"}`;
}

/** What a book's closed positions come to without their best few (contract section 10, the concentration test): the share removed is the best
 * 5% of positions, at least one. A result that is positive and no longer positive without them rests on a few positions. Cents are whole numbers. */
export interface Concentration {
  count: number;
  removed: number;
  total: string;
  without: string;
  flagged: boolean;
}

function cents(text: string): bigint {
  return BigInt(roundDecimal(text, 2).replace(".", ""));
}

function fromCents(value: bigint): string {
  const sign = value < 0n ? "-" : "";
  const digits = (value < 0n ? -value : value).toString().padStart(3, "0");
  return `${sign}${digits.slice(0, -2)}.${digits.slice(-2)}`;
}

export function concentration(nets: string[]): Concentration | null {
  if (nets.length < 2) return null; // one position cannot be asked what it was without its best
  const sorted = nets.map(cents).sort((a, b) => (a < b ? 1 : a > b ? -1 : 0));
  const removed = Math.max(1, Math.ceil(nets.length * 0.05));
  const total = sorted.reduce((a, b) => a + b, 0n);
  const without = sorted.slice(removed).reduce((a, b) => a + b, 0n);
  return {
    count: nets.length,
    removed,
    total: fromCents(total),
    without: fromCents(without),
    flagged: total > 0n && without <= 0n,
  };
}

// --- the agents ---------------------------------------------------------------------------------------------------------------------------
const AGENT_WORDS: Record<string, string> = {
  L0: "A fixed rule on the measured numbers, written down and fixed before a scored period begins. It uses no model.",
  L1: "One model reads the evidence once and gives one answer, which code then checks against that evidence.",
  L2: "A bull and a bear each argue from the same evidence and a judge decides; the judge's answer is checked in the same way.",
  L1x: "The single model asked several times, spending about as much as the debate did, to show whether the debate won only by spending more.",
};

/** What an agent is, in one sentence; none for an agent this page has no words for (it is not guessed at). */
export function agentWords(arm: string): string | null {
  return Object.prototype.hasOwnProperty.call(AGENT_WORDS, arm)
    ? (AGENT_WORDS[arm] ?? null)
    : null;
}

// --- the Hypothesis Laboratory ---------------------------------------------------------------------------------------------------------
/** A time in milliseconds since the epoch, in the page's one way of writing a time. */
export function whenMs(ms: number): string {
  return when(new Date(ms).toISOString());
}

const ASSUMPTION_KIND_WORDS: Record<string, string> = {
  OBSERVATION: "Observation",
  INTERPRETATION: "Interpretation",
  CAUSAL: "Cause",
  FORECAST: "Forecast",
};

/** What kind of statement an assumption is, in a word. */
export function assumptionKind(kind: string): string {
  const word = ASSUMPTION_KIND_WORDS[kind];
  if (word === undefined) throw new Error(`not an assumption kind: ${kind}`);
  return word;
}

/** What a hypothesis expects, in a sentence. */
export function expectsWords(
  direction: string,
  symbol: string,
  hours: number,
): string {
  if (direction !== "UP" && direction !== "DOWN")
    throw new Error(`not a direction: ${direction}`);
  return `${symbol} to ${direction === "UP" ? "rise" : "fall"} within ${horizon(hours)} of the entry`;
}

/** A share as an unsigned percentage: "0.02" as "2%". */
export function shareWords(text: string): string {
  return percentOfShare(text).replace(/^\+/, "");
}

const TRADE_WORDS: Record<string, string> = {
  PENDING: "Waiting for the minute it would be bought in",
  MISSING: "The minute it would be bought in never came",
  OPEN: "Bought and not yet sold",
  EXIT_MISSING: "The minute it would be sold in never came",
  CLOSED: "Closed",
};

/** Where a practice trade of a hypothesis stands. */
export function tradeStatus(status: string): string {
  const word = TRADE_WORDS[status];
  if (word === undefined) throw new Error(`not a trade status: ${status}`);
  return word;
}

const SELL_WORDS: Record<string, string> = {
  INVALIDATED: "the invalidation condition held",
  HORIZON: "the horizon ended",
};

/** Why a trade was sold. */
export function sellReason(reason: string): string {
  const word = SELL_WORDS[reason];
  if (word === undefined) throw new Error(`not a reason to sell: ${reason}`);
  return word;
}

/** What a preview found, in a sentence: how often the entry condition held on the stored candles, and on how many it could not be told. */
export function previewWords(p: {
  bars: number;
  true_bars: number;
  false_bars: number;
  undecided_bars: number;
}): string {
  const undecided =
    p.undecided_bars === 0
      ? ""
      : `, and could not be decided on ${p.undecided_bars} (too few earlier candles for its indicators)`;
  return `The entry condition held on ${p.true_bars} of ${count(p.bars, "closed hourly candle")}, did not on ${p.false_bars}${undecided}.`;
}

const STATE_WORDS: Record<string, string> = {
  INVALIDATED: "On the last candle the invalidation condition held.",
  HOLDS: "On the last candle the invalidation condition did not hold.",
  UNKNOWN: "On the last candle the invalidation condition could not be decided",
};

/** What the invalidation condition said on the last stored candle, with the reason when it could not be told. */
export function invalidationState(
  state: string,
  reason: string | null,
): string {
  const word = STATE_WORDS[state];
  if (word === undefined)
    throw new Error(`not an invalidation state: ${state}`);
  if (state !== "UNKNOWN") return word;
  return reason === null ? `${word}.` : `${word}: ${reason}.`;
}

/** How old the evidence was when the call's window began: "never" is not a word here; an origin not yet set is said so. */
export function evidenceAgeWords(seconds: number | null): string {
  if (seconds === null) return "Not yet known";
  if (seconds < 90) return `${seconds} seconds`;
  return `${Math.round(seconds / 60)} minutes`;
}

/** How far the price moved between the evidence and the start, in units of the token's typical hourly range (ATR); none until it settles. */
export function driftWords(atr: string | null): string {
  return atr === null
    ? "Not known until the call settles"
    : `${atr} times its usual hourly range`;
}
