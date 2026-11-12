import assert from "node:assert/strict";
import { test } from "node:test";
import {
  chancePercent,
  basisWords,
  challengeSummary,
  chanceWords,
  count,
  forgeryWords,
  horizon,
  notScorable,
  score,
  share,
  summary,
  when,
} from "../src/format.ts";
import {
  concentration,
  largestDisagreement,
  readLine,
  topHeadline,
  whyItMatters,
} from "../src/format.ts";
import type { ChallengeRow } from "../src/types.ts";

test("a time in words", () => {
  assert.equal(when("2026-10-30T22:40:24.966Z"), "30 Oct 2026, 22:40 UTC");
  assert.equal(when("2026-01-05T00:00:00.000Z"), "5 Jan 2026, 00:00 UTC");
  assert.equal(when("2026-12-31T23:59:59Z"), "31 Dec 2026, 23:59 UTC");
});

test("a text that is not a time is refused, not shown", () => {
  assert.throws(() => when("yesterday"), /not a time/);
  assert.throws(() => when("2026-13-01T00:00:00Z"), /not a month/);
  assert.throws(() => when("2026-00-01T00:00:00Z"), /not a month/);
});

test("a horizon", () => {
  assert.equal(horizon(1), "1 hour");
  assert.equal(horizon(4), "4 hours");
  assert.equal(horizon(24), "24 hours");
});

test("a count with its noun", () => {
  assert.equal(count(1, "call"), "1 call");
  assert.equal(count(0, "call"), "0 calls");
  assert.equal(count(2, "call"), "2 calls");
  assert.equal(count(3, "request"), "3 requests");
  assert.equal(count(1, "category", "categories"), "1 category");
  assert.equal(count(2, "category", "categories"), "2 categories");
});

test("the sentence at the top of Today", () => {
  assert.equal(summary([["Open", 6]]), "6 calls waiting to settle.");
  assert.equal(summary([["Open", 1]]), "1 call waiting to settle.");
  assert.equal(
    summary([
      ["Open", 7],
      ["Settled", 3],
      ["Abstained", 4],
      ["Failed", 2],
      ["Too late", 1],
    ]),
    "7 calls waiting to settle, 3 settled, 4 made no call, 3 did not finish.",
  );
  assert.equal(
    summary([
      ["Settled", 2],
      ["Void", 1],
      ["Not settled", 2],
    ]),
    "2 settled, 3 could not be settled.",
  );
  assert.equal(summary([["Deciding", 5]]), "5 still deciding.");
  assert.equal(summary([]), "Nothing was scheduled in this cycle.");
  assert.equal(
    summary(
      [
        ["Open", 7],
        ["Failed", 2],
        ["Too late", 1],
      ],
      true,
    ),
    "7 calls waiting to settle.",
  );
  assert.equal(summary([["Failed", 2]], true), null);
  assert.equal(summary([], true), "Nothing was scheduled in this cycle.");
});

test("a missing score is a dash and never zero", () => {
  assert.equal(score(null), "–");
  assert.equal(score("0.2481"), "0.2481");
  assert.equal(share(null), "–");
  assert.equal(share("67%"), "67%");
});

test("hours added to a time", async () => {
  const { plusHours } = await import("../src/format.ts");
  assert.equal(
    plusHours("2026-10-30T22:40:00.000Z", 1),
    "2026-10-30T23:40:00.000Z",
  );
  assert.equal(
    plusHours("2026-10-30T22:40:00.000Z", 4),
    "2026-10-31T02:40:00.000Z",
  );
  assert.equal(
    plusHours("2026-12-31T23:30:00Z", 24),
    "2027-01-01T23:30:00.000Z",
  );
  assert.throws(() => plusHours("later", 1), /not a time/);
});

test("when the open calls settle, by horizon", async () => {
  const { settling } = await import("../src/format.ts");
  const rows = [
    { hours: 4, state: "Open", origin: "2026-10-30T10:00:00.000Z" },
    { hours: 1, state: "Open", origin: "2026-10-30T10:01:00.000Z" },
    { hours: 1, state: "Open", origin: "2026-10-30T10:00:00.000Z" },
    { hours: 1, state: "Settled", origin: "2026-10-30T09:00:00.000Z" },
    { hours: 24, state: "Open", origin: null },
    { hours: 24, state: "Abstained", origin: null },
  ];
  assert.deepEqual(settling(rows), [
    [1, 2, "2026-10-30T11:00:00.000Z"],
    [4, 1, "2026-10-30T14:00:00.000Z"],
    [24, 1, null],
  ]);
  assert.deepEqual(settling([]), []);
  assert.deepEqual(
    settling([
      { hours: 1, state: "Settled", origin: "2026-10-30T10:00:00.000Z" },
    ]),
    [],
  );
});

test("what changed since the previous cycle", async () => {
  const { changed } = await import("../src/format.ts");
  const since = "2026-10-30T15:06:00.000Z";
  assert.equal(changed(null, [["Rose", 3]]), null);
  assert.equal(
    changed(since, []),
    "Since the last cycle (30 Oct, 15:06 UTC): nothing has settled.",
  );
  assert.equal(
    changed(since, [
      ["Rose", 9],
      ["Fell", 12],
      ["Neither", 3],
      ["Target first", 1],
      ["Stop first", 2],
    ]),
    "Since the last cycle (30 Oct, 15:06 UTC): 27 calls settled. 9 rose, 12 fell, 1 reached the target, 2 reached the stop, 3 reached neither.",
  );
  assert.equal(
    changed(since, [["Rose", 1]]),
    "Since the last cycle (30 Oct, 15:06 UTC): 1 call settled. 1 rose.",
  );
});

test("what a score says so far: the calls it counts are those independently time-stamped", async () => {
  const { skill, LEAST_SETTLED } = await import("../src/format.ts");
  assert.equal(LEAST_SETTLED, 20);
  assert.equal(
    skill("Rules", null, 0),
    "Rules: no settled up-or-down calls independently time-stamped yet.",
  );
  assert.equal(
    skill("Rules", "0.2000", 0),
    "Rules: no settled up-or-down calls independently time-stamped yet.",
  );
  assert.equal(
    skill("Rules", "0.2400", 1),
    "Rules: 1 settled call independently time-stamped, too few for a score to mean anything.",
  );
  assert.equal(
    skill("Rules", "0.2400", 19),
    "Rules: 19 settled calls independently time-stamped, too few for a score to mean anything.",
  );
  assert.equal(
    skill("Rules", "0.2496", 20),
    "Rules: 0.2496 over 20 settled calls independently time-stamped (always saying 50% scores 0.25).",
  );
});

test("beside the score: the result on every settled call, and the share that was not independently time-stamped in time", async () => {
  const format = await import("../src/format.ts");
  const allSettled = (format as Record<string, unknown>)["allSettled"] as
    | ((m: string | null, n: number, missed: string | null) => string | null)
    | undefined;
  assert.equal(
    typeof allSettled,
    "function",
    "format.ts words the second line",
  );
  if (allSettled === undefined) return;
  assert.equal(allSettled(null, 0, null), null); // nothing settled: the line above says so
  assert.equal(
    allSettled("0.0520", 2, "100%"),
    "All settled calls: 2, too few for a score to mean anything; 100% were not independently time-stamped.",
  );
  assert.equal(
    allSettled("0.2400", 1, "0%"),
    "All settled calls: 1, too few for a score to mean anything; 0% were not independently time-stamped.",
  );
  assert.equal(
    allSettled("0.2510", 25, "20%"),
    "All settled calls: 0.2510 over 25; 20% were not independently time-stamped.",
  );
  assert.equal(
    allSettled("0.2510", 25, null),
    "All settled calls: 0.2510 over 25; – were not independently time-stamped.",
  );
});

test("the words a dispute names are counted in code points, as the league counts them, not in UTF-16 units", async () => {
  const format = await import("../src/format.ts");
  const wordsAt = (format as Record<string, unknown>)["wordsAt"] as
    ((text: string, start: number, end: number) => string) | undefined;
  assert.equal(typeof wordsAt, "function", "format.ts takes a dispute's words");
  if (wordsAt === undefined) return;
  // a character outside the Basic Multilingual Plane is one code point and two UTF-16 units; the offsets are the claims file's (Python's)
  const text =
    "\u{1F680} Whales are accumulating SOL, therefore institutions are entering, and SOL will rise 15% this week.";
  assert.equal(
    text.slice(31, 40),
    " therefor",
    "a plain slice of the string is one unit off after the rocket",
  );
  assert.equal(wordsAt(text, 31, 40), "therefore");
  assert.equal(wordsAt(text, 2, 29), "Whales are accumulating SOL");
  assert.equal(wordsAt(text, 0, 1), "\u{1F680}");
  assert.equal(wordsAt("\u{1D538}\u{1D539} C \u{1F680}\u{1F680} D", 8, 9), "D"); // code points: 𝔸 𝔹 ␠ C ␠ 🚀 🚀 ␠ D
  assert.equal(wordsAt("plain words", 6, 11), "words");
});

test("a receipt's evidence: a digest, words for derived evidence, and the view model's own words for evidence it does not hold", async () => {
  const format = await import("../src/format.ts");
  const digestWords = (format as Record<string, unknown>)["digestWords"] as
    ((raw: string | null, kind: string, shown: string) => string) | undefined;
  assert.equal(
    typeof digestWords,
    "function",
    "format.ts words the digest cell",
  );
  if (digestWords === undefined) return;
  assert.equal(
    digestWords(
      "efd829de216d6061c95ace229ac6f8175419defb4b8e1ad16be6179bcc167e5b",
      "OBSERVATION",
      "digest only",
    ),
    "efd829de216d6061",
  );
  assert.equal(
    digestWords(null, "DERIVED", "digest only"),
    "derived from the others",
  );
  assert.equal(
    digestWords(null, "unknown", "not in this ledger"),
    "not in this ledger",
  );
});

test("a ledger that is not in its OK state is said on every page, in the words of the specification", async () => {
  const format = await import("../src/format.ts");
  type Said = { heading: string; line: string; detail: string } | null;
  const ledgerFailure = (format as Record<string, unknown>)["ledgerFailure"] as
    ((l: { state: string; detail: string } | undefined) => Said) | undefined;
  assert.equal(
    typeof ledgerFailure,
    "function",
    "format.ts words a ledger failure",
  );
  if (ledgerFailure === undefined) return;
  assert.equal(ledgerFailure(undefined), null); // an index written before the state was exported says nothing about it
  assert.equal(ledgerFailure({ state: "OK", detail: "" }), null);
  const detail =
    "segment segment-000000000000.jsonl: leaf hash does not match the record at offset 3256; not repaired";
  assert.deepEqual(ledgerFailure({ state: "INTEGRITY_FAILURE", detail }), {
    heading: "The league's ledger is in INTEGRITY_FAILURE.",
    line: "Its records are NOT RECONSTRUCTIBLE, so no cycle, call, score or bundle from it is shown.",
    detail,
  });
  assert.equal(
    ledgerFailure({ state: "FENCED", detail: "x" })?.heading,
    "The league's ledger is in FENCED.",
  );
});

test("a short time, without its year", async () => {
  const { whenShort } = await import("../src/format.ts");
  assert.equal(whenShort("2026-10-30T16:07:00.000Z"), "30 Oct, 16:07 UTC");
  assert.equal(whenShort("2026-01-05T00:00:00.000Z"), "5 Jan, 00:00 UTC");
});

test("the line of what settles next", async () => {
  const { nextLine } = await import("../src/format.ts");
  assert.equal(nextLine([]), null);
  assert.equal(
    nextLine([
      [1, 14, "2026-10-30T16:07:00.000Z"],
      [4, 1, "2026-10-30T19:07:00.000Z"],
      [24, 5, null],
    ]),
    "Next to settle: 14 calls by 30 Oct, 16:07 UTC (1 hour); 1 call by 30 Oct, 19:07 UTC (4 hours); 5 calls after they start (24 hours).",
  );
});

test("a bundle check in words", async () => {
  const { findings } = await import("../src/format.ts");
  const honest = {
    root_of_trust: { state: "PINNED", version: 1, sha256: "ab" },
    integrity: { ok: true, items: { a: {}, b: {} }, errors: [] },
    timing: {
      level: "C3",
      tokens: [{ valid: true }, { valid: true }],
      basis: null,
    },
    cycle: {
      declared: true,
      steps: 1,
      batch_size: 5,
      members_present: 5,
      complete: true,
    },
    eligibility: { scorable: true, reasons: [] },
    not_proven: ["that the forecast is good"],
  };
  assert.deepEqual(findings(honest), [
    {
      ok: true,
      text: "Every record is intact: 2 records, each signed and in the log.",
    },
    { ok: true, text: "Checked against a trusted root (version 1)." },
    {
      ok: true,
      text: "Independently time-stamped (2 of 2 time-stamps valid).",
    },
    {
      ok: true,
      text: "The whole batch its cycle committed is here: 5 records and the step that committed them, together making the root that step names.",
    },
    {
      ok: true,
      text: "It meets every condition a scored period asks for. A call counts only in a scored period; an exploratory run never does.",
    },
  ]);
  const broken = {
    root_of_trust: { state: "ASSERTED", version: null, sha256: null },
    integrity: { ok: false, items: { a: {} }, errors: ["TOO_MANY_ITEMS"] },
    timing: {
      level: "C1",
      tokens: [{ valid: true }, { valid: false }],
      basis: null,
    },
    cycle: {
      declared: false,
      steps: 0,
      batch_size: null,
      members_present: 0,
      complete: false,
    },
    eligibility: {
      scorable: false,
      reasons: ["ROOT_NOT_CURRENT", "LEVEL_BELOW_C3", "COMPLETENESS_NOT_SHOWN"],
    },
    not_proven: [],
  };
  assert.deepEqual(findings(broken), [
    { ok: false, text: "The bundle is not intact (TOO_MANY_ITEMS)." },
    {
      ok: false,
      text: "No trusted root accepted: the bundle is only checked against itself, so nothing says it came from here.",
    },
    { ok: false, text: "Recorded by us only (1 of 2 time-stamps valid)." },
    {
      ok: false,
      text: "Nothing in the bundle says what its cycle committed, so it cannot show that no call was taken out.",
    },
    {
      ok: false,
      text: "It cannot count toward a score: the trusted root is not a current one; the time-stamps do not reach independent time-stamping; the bundle does not show that it holds the whole batch its cycle committed.",
    },
  ]);
  const cycleOf = (cycle: object) =>
    findings({ ...honest, cycle: { ...honest.cycle, ...cycle } })[3];
  assert.deepEqual(cycleOf({ batch_size: 1, members_present: 1 }), {
    ok: true,
    text: "The whole batch its cycle committed is here: 1 record and the step that committed it, together making the root that step names.",
  });
  assert.deepEqual(cycleOf({ complete: false, members_present: 3 }), {
    ok: false,
    text: "The bundle does not hold the whole batch its cycle committed: 3 of 5 records found, or they do not make the root the committing step names.",
  });
  assert.deepEqual(cycleOf({ complete: false, members_present: 5 }), {
    ok: false,
    text: "The bundle does not hold the whole batch its cycle committed: 5 of 5 records found, or they do not make the root the committing step names.",
  });
  assert.deepEqual(
    cycleOf({ complete: false, batch_size: null, members_present: 0 }),
    {
      ok: false,
      text: "The cycle's committing step does not name a batch the bundle can check.",
    },
  );
  assert.deepEqual(
    cycleOf({
      complete: false,
      steps: 2,
      batch_size: null,
      members_present: 0,
    }),
    {
      ok: false,
      text: "The bundle holds more than one committing step; a bundle shows one cycle, so it shows none as complete.",
    },
  );
  const noLevel = {
    ...honest,
    timing: { level: "C0", tokens: [], basis: null },
    integrity: { ok: false, items: {}, errors: [] },
    eligibility: { scorable: false, reasons: [] },
  };
  const text = findings(noLevel).map((f) => f.text);
  assert.equal(
    text[0],
    "The bundle is not intact (a record failed its checks).",
  );
  assert.equal(
    text[2],
    "No forecast recorded: no time-stamp reaches a level a person should rely on (0 of 0 tokens valid).",
  );
  assert.equal(text[4], "It cannot count toward a score.");
});

test("the trust phrases are the ones the league writes", async () => {
  const { TRUST_PHRASES, NO_FORECAST } = await import("../src/format.ts");
  const { readFileSync } = await import("node:fs");
  const python = readFileSync(
    new URL("../../src/hoshitori/labels.py", import.meta.url),
    "utf8",
  );
  for (const phrase of [NO_FORECAST, ...Object.values(TRUST_PHRASES)])
    assert.ok(python.includes(`"${phrase}"`), phrase);
  assert.deepEqual(Object.keys(TRUST_PHRASES), ["C1", "C2", "C3", "C4"]);
});

test("the quality sentence", async () => {
  const { qualityLine, statusCounts, STATUS_WORDS } =
    await import("../src/format.ts");
  assert.deepEqual(
    [...STATUS_WORDS],
    [
      "DESIGNED",
      "IMPLEMENTED",
      "TESTED",
      "INTEGRATED",
      "INDEPENDENTLY REVIEWED",
    ],
  );
  const rows = [
    { word: "TESTED" },
    { word: "TESTED" },
    { word: "DESIGNED" },
    { word: "INTEGRATED" },
  ];
  assert.deepEqual(statusCounts(rows), [
    ["DESIGNED", 1],
    ["TESTED", 2],
    ["INTEGRATED", 1],
  ]);
  assert.equal(
    qualityLine(rows),
    "4 parts: 1 designed, 2 tested, 1 integrated. None has been reviewed by anyone but its author.",
  );
  assert.equal(
    qualityLine([{ word: "TESTED" }, { word: "INDEPENDENTLY REVIEWED" }]),
    "2 parts: 1 tested, 1 independently reviewed. 1 reviewed by someone else.",
  );
  assert.equal(
    qualityLine([]),
    "0 parts: . None has been reviewed by anyone but its author.",
  );
});

test("code spans", async () => {
  const { codeSpans } = await import("../src/format.ts");
  assert.deepEqual(codeSpans(""), []);
  assert.deepEqual(codeSpans("plain words"), [
    { text: "plain words", code: false },
  ]);
  assert.deepEqual(codeSpans("Time (`timeutil`, `clock`) values"), [
    { text: "Time (", code: false },
    { text: "timeutil", code: true },
    { text: ", ", code: false },
    { text: "clock", code: true },
    { text: ") values", code: false },
  ]);
  assert.deepEqual(codeSpans("`all of it`"), [
    { text: "all of it", code: true },
  ]);
  assert.deepEqual(codeSpans("an `unclosed span"), [
    { text: "an `unclosed span", code: false },
  ]);
  assert.deepEqual(codeSpans("a `` b"), [
    { text: "a ", code: false },
    { text: "", code: true },
    { text: " b", code: false },
  ]);
});

test("a drill in words", async () => {
  const { drillSentence, drillLine } = await import("../src/format.ts");
  assert.equal(
    drillSentence({
      cycle: "Committed",
      level: "C3",
      slots: [
        ["Open", 3],
        ["Failed", 3],
      ],
      reasons: ["No evidence could be built for the token"],
      forecasts: 3,
    }),
    "Committed at C3: 3 open, 3 failed. 3 forecasts made. Why: no evidence could be built for the token.",
  );
  assert.equal(
    drillSentence({
      cycle: "Abandoned",
      level: null,
      slots: [],
      reasons: [],
      forecasts: 0,
    }),
    "Abandoned: no calls. 0 forecasts made.",
  );
  assert.equal(
    drillSentence({
      cycle: "Committed",
      level: null,
      slots: [["Open", 1]],
      reasons: ["A", "B c"],
      forecasts: 1,
    }),
    "Committed: 1 open. 1 forecast made. Why: a; b c.",
  );
  assert.equal(drillLine([true, true]), "All 2 drills held.");
  assert.equal(
    drillLine([true, false, false]),
    "1 of 3 drills held. The others did not.",
  );
  assert.equal(drillLine([]), "All 0 drills held.");
});

test("how old", async () => {
  const { ageWords } = await import("../src/format.ts");
  const at = "2026-10-30T12:00:00.000Z";
  const t = Date.parse(at);
  assert.equal(ageWords(t, at), "under a minute");
  assert.equal(ageWords(t - 5000, at), "under a minute");
  assert.equal(ageWords(t + 59_999, at), "under a minute");
  assert.equal(ageWords(t + 60_000, at), "1 minute");
  assert.equal(ageWords(t + 59 * 60_000, at), "59 minutes");
  assert.equal(ageWords(t + 60 * 60_000, at), "1 hour");
  assert.equal(ageWords(t + 47 * 3_600_000, at), "47 hours");
  assert.equal(ageWords(t + 48 * 3_600_000, at), "2 days");
  assert.equal(ageWords(t + 100 * 3_600_000, at), "4 days");
  assert.throws(() => ageWords(t, "soon"), /not a time/);
});

test("old documents and partial cycles are said", async () => {
  const { oldLine, partialLine, OLD_AFTER_HOURS } =
    await import("../src/format.ts");
  const at = "2026-10-30T12:00:00.000Z";
  const t = Date.parse(at);
  assert.equal(OLD_AFTER_HOURS, 3);
  assert.equal(oldLine(at, t), null);
  assert.equal(oldLine(at, t + 3 * 3_600_000), null);
  assert.equal(
    oldLine(at, t + 3 * 3_600_000 + 1),
    "A snapshot of the record from 3 hours ago. Newer cycles are not shown.",
  );
  assert.equal(
    oldLine(at, t + 72 * 3_600_000),
    "A snapshot of the record from 3 days ago. Newer cycles are not shown.",
  );
  assert.throws(() => oldLine("x", t), /not a time/);
  assert.equal(partialLine([["Open", 5]]), null);
  assert.equal(partialLine([]), null);
  assert.equal(
    partialLine([
      ["Failed", 2],
      ["Too late", 1],
      ["Open", 9],
    ]),
    "3 calls failed or came too late; the rest stands.",
  );
  assert.equal(
    partialLine([["Too late", 1]]),
    "1 call failed or came too late; the rest stands.",
  );
});

test("claims in words", async () => {
  const { kindWord, readingLine, sketchLine } =
    await import("../src/format.ts");
  assert.equal(kindWord("OBSERVATION"), "Observation");
  assert.equal(kindWord("ASSUMPTION_INFERRED"), "Implied step (ours)");
  assert.throws(() => kindWord("VERDICT"), /not a kind of part/);
  assert.equal(
    readingLine("STABLE", 3, "1.00", false),
    "The 3 readings agree on the parts (agreement 1.00). Not yet checked against labelled claims.",
  );
  assert.equal(
    readingLine("STABLE", 3, "1.00", true),
    "The 3 readings agree on the parts (agreement 1.00).",
  );
  assert.equal(
    readingLine("UNSTABLE", 2, "0.50", true),
    "The 2 readings disagree on what the parts are (agreement 0.50).",
  );
  assert.equal(
    readingLine("DISPUTED", 3, "0.75", true),
    "A plain reading of the text disagrees with the parts (agreement 0.75).",
  );
  assert.throws(() => readingLine("GREAT", 3, "1.00", true), /not a state/);
  assert.equal(
    sketchLine("RESOLVABLE", null),
    "Could be scored as a direction call over the time stated.",
  );
  assert.equal(
    sketchLine("NEEDS_CHOICES", null),
    "Needs your choices before it could be scored.",
  );
  assert.equal(
    sketchLine("UNRESOLVED", "x"),
    "Names nothing a price series could settle: x.",
  );
  assert.throws(() => sketchLine("MAYBE", null), /not a status/);
});

test("the next check in words", async () => {
  const { nextCheckLine } = await import("../src/format.ts");
  assert.equal(
    nextCheckLine("CHECK", "A", "Do it.", "a chart", null),
    "Next check, on “A”: Do it. Source: a chart.",
  );
  assert.equal(
    nextCheckLine("NO_AVAILABLE_CHECK", "B", null, null, "no source"),
    "No check is available for “B”: no source.",
  );
  assert.equal(
    nextCheckLine("NOTHING_LEFT", null, null, null, "all done"),
    "No further check: all done.",
  );
  assert.equal(
    nextCheckLine("NOTHING_LEFT", null, null, null, null),
    "No further check: none was given.",
  );
  assert.throws(
    () => nextCheckLine("CHECK", null, "x", "y", null),
    /needs its part/,
  );
  assert.throws(
    () => nextCheckLine("NO_AVAILABLE_CHECK", "B", null, null, null),
    /needs its part and its reason/,
  );
  assert.throws(
    () => nextCheckLine("GO", null, null, null, null),
    /not a status/,
  );
});

test("a source in words", async () => {
  const { sourceWords } = await import("../src/format.ts");
  assert.equal(sourceWords("binance-spot"), "Binance spot candles");
  assert.equal(sourceWords("coinbase-spot"), "Coinbase candles");
  assert.equal(sourceWords("kraken-spot"), "Kraken candles");
  assert.equal(
    sourceWords("hoshitori.features"),
    "The measures computed from the candles",
  );
  assert.equal(sourceWords("ryo-deep"), "ryo-deep");
});

test("a check in words", async () => {
  const { checkWords } = await import("../src/format.ts");
  assert.equal(checkWords("SUPPORTED"), "Agrees with the evidence");
  assert.equal(checkWords("CONTRADICTED"), "Does not agree with the evidence");
  assert.equal(checkWords("NOT_CHECKABLE"), "Cannot be checked here");
  assert.equal(
    checkWords("NOT_FOUND_IN_SOURCES_CHECKED"),
    "Not in the evidence held",
  );
  assert.equal(checkWords("SOURCE_UNAVAILABLE"), "Unknown in the evidence");
  assert.equal(checkWords("UNRESOLVED"), "Words too open to check");
  assert.throws(() => checkWords("GREAT"), /not a check status/);
});

test("a model agent's answer in words, never its code", async () => {
  const { answerWords } = await import("../src/format.ts");
  assert.equal(typeof answerWords, "function", "format.ts words an answer");
  assert.equal(answerWords("CALL", null), "Call");
  assert.equal(
    answerWords("NO_CALL", "Too little edge to call"),
    "Abstained, too little edge to call",
  );
  assert.equal(
    answerWords("FAILED", "The model did not answer in time"),
    "Failed, the model did not answer in time",
  );
  assert.equal(
    answerWords("FAILED_EVIDENCE", "Evidence was missing"),
    "Failed for lack of evidence, evidence was missing",
  );
  assert.equal(
    answerWords("FAILED_LATE", "The answer came after the deadline"),
    "Too late, the answer came after the deadline",
  );
  assert.throws(() => answerWords("MAYBE", null), /not an answer: MAYBE/);
});

test("the answers are the league's own, and their words the ones it gives the same states", async () => {
  const { ANSWER_WORDS } = await import("../src/format.ts");
  assert.ok(ANSWER_WORDS !== undefined, "format.ts has the answer words");
  const { readFileSync } = await import("node:fs");
  const runs = readFileSync(
    new URL("../../src/hoshitori/arm_run_record.py", import.meta.url),
    "utf8",
  );
  const codes = Object.keys(ANSWER_WORDS);
  assert.ok(
    runs.includes(
      `VERDICTS: Final = (${codes.map((c) => `"${c}"`).join(", ")})`,
    ),
    codes.join(" "),
  );
  const labels = readFileSync(
    new URL("../../src/hoshitori/labels.py", import.meta.url),
    "utf8",
  );
  for (const code of codes.filter((c) => c !== "CALL"))
    assert.ok(labels.includes(`"${code}": "${ANSWER_WORDS[code]}"`), code);
  assert.equal(ANSWER_WORDS["CALL"], "Call"); // the word of the Calls page's column, where a call is shown
});

test("a settlement in words, never its outcome code", async () => {
  const { settlementWords } = await import("../src/format.ts");
  assert.equal(
    typeof settlementWords,
    "function",
    "format.ts words a settlement",
  );
  assert.equal(settlementWords("SETTLED", "Rose"), "settled, rose");
  assert.equal(settlementWords("SETTLED", "Fell"), "settled, fell");
  assert.equal(
    settlementWords("SETTLED", "Target first"),
    "settled, target first",
  );
  assert.equal(settlementWords("SETTLED", "Stop first"), "settled, stop first");
  assert.equal(settlementWords("SETTLED", "Neither"), "settled, neither");
  assert.equal(settlementWords("AMBIGUOUS", null), "ambiguous");
  assert.equal(settlementWords("VOID", null), "void");
  assert.equal(settlementWords("UNSETTLEABLE", null), "unsettleable");
});

test("the first card's copy is the league's", async () => {
  const { NOTHING_CHANGED, hasChanges, stepWords } =
    await import("../src/format.ts");
  const { readFileSync } = await import("node:fs");
  const python = readFileSync(
    new URL("../../src/hoshitori/changes.py", import.meta.url),
    "utf8",
  );
  assert.ok(python.includes(`"${NOTHING_CHANGED}"`));
  assert.equal(hasChanges({ changes: [], product: "p", rule: "r" }), true);
  assert.equal(
    hasChanges({ product: "p", rule: "r" }),
    false,
    "an older export has no changes",
  );
  assert.equal(hasChanges({ changes: [], product: 3, rule: "r" }), false);
  assert.equal(stepWords("AWAITING_ORIGIN"), "Waiting for its window to start");
  assert.equal(
    stepWords("SOMETHING_NEW"),
    "SOMETHING_NEW",
    "a state the page does not know is shown as recorded",
  );
});

test("an invalidation in plain words", async () => {
  const { invalidationWords } = await import("../src/format.ts");
  assert.equal(
    invalidationWords("ret_24h below 0"),
    "The price change over 24 hours is below 0%.",
  );
  assert.equal(
    invalidationWords("ret_1h above 0.5"),
    "The price change over 1 hour is above +50%.",
  );
  assert.equal(
    invalidationWords("ret_4h below -0.017394"),
    "The price change over 4 hours is below -1.7394%.",
  );
  assert.equal(
    invalidationWords("close below 87.56"),
    "The price closes below 87.56.",
  );
  assert.equal(
    invalidationWords("last_close above 90"),
    "The price closes above 90.",
  );
  assert.equal(
    invalidationWords("atr14 above 1.077142"),
    "The average hourly range over 14 hours (ATR 14) is above 1.077142.",
  );
  assert.equal(
    invalidationWords("atr14_share above 0.012"),
    "The average hourly range over 14 hours is above 1.2% of the price.",
  );
  assert.equal(invalidationWords("count above 3"), "count above 3");
  assert.equal(
    invalidationWords("something else entirely"),
    "something else entirely",
  );
  assert.equal(invalidationWords("ret_1h below"), "ret_1h below");
  assert.equal(
    invalidationWords("close below entry minus 1.5 ATR14"),
    "A close below the entry price by more than 1.5 times its average hourly range over 14 hours (ATR 14).",
  );
});

test("what a pass means is said exactly", async () => {
  const { PASS_SHOWS, PASS_DOES_NOT_SHOW, PASS_NEEDS_HONEST } =
    await import("../src/format.ts");
  assert.equal(PASS_SHOWS.length, 5);
  assert.equal(PASS_DOES_NOT_SHOW.length, 4);
  assert.equal(PASS_NEEDS_HONEST.length, 4);
  const all = [...PASS_SHOWS, ...PASS_DOES_NOT_SHOW, ...PASS_NEEDS_HONEST].join(
    "\n",
  );
  for (const must of [
    "every call of its cycle, unless the findings say so",
    "window began after the time-stamps, unless the findings list the later steps",
    "later checkpoint extends",
    "true or complete",
    "never of how well",
    "the operator",
    "trusted root",
    "not both",
  ]) {
    assert.ok(all.toLowerCase().includes(must.toLowerCase()), must);
  }
  assert.ok(!/\bC[0-4]\b/.test(all), "no commitment code on the first layer");
  assert.ok(
    !/does not print those numbers/.test(all),
    "the verdict prints the root's rules now, so no text says it does not",
  );
  assert.ok(
    /the verdict prints them/.test(all),
    "and the text for whoever wrote the root says where to read the rules",
  );
  assert.ok(
    !/before the outcome window/.test(all),
    "a pass does not claim a window it cannot show",
  );
});

const row = (over: Partial<ChallengeRow>): ChallengeRow => ({
  cycle_id: "c1",
  arm: "L0",
  symbol: "BTC",
  hours: 1,
  source: "binance-spot",
  items: 6,
  outcome: "EVIDENCE_REQUIRED",
  words: "The rule cannot decide without it: it is evidence the rule requires.",
  original: "55% up",
  changed: "No call: the evidence it needs was missing",
  ...over,
});

test("rows that all say the same collapse into one statement with the counts behind it", () => {
  const rows = [
    row({}),
    row({ source: "hoshitori.features", items: 2 }),
    row({ hours: 4 }),
    row({ hours: 4, source: "hoshitori.features", items: 3 }),
    row({ symbol: "ETH", cycle_id: "c1" }),
    row({ symbol: "ETH", source: "hoshitori.features", items: 2 }),
  ];
  const s = challengeSummary(rows);
  assert.equal(s.uniform, true);
  assert.equal(
    s.words,
    "The rule cannot decide without it: it is evidence the rule requires.",
  );
  assert.equal(s.questions, 6);
  assert.equal(s.calls, 3); // BTC 1 hour, BTC 4 hours, ETH 1 hour
  assert.deepEqual(s.outcomes, [
    { outcome: "EVIDENCE_REQUIRED", words: s.words, questions: 6 },
  ]);
  assert.deepEqual(s.sources, [
    { source: "binance-spot", questions: 3, itemsLow: 6, itemsHigh: 6 },
    { source: "hoshitori.features", questions: 3, itemsLow: 2, itemsHigh: 3 },
  ]);
});

test("rows that differ in outcome or in words are not collapsed", () => {
  assert.equal(
    challengeSummary([row({}), row({ outcome: "DECISION_CHANGED" })]).uniform,
    false,
  );
  assert.equal(
    challengeSummary([row({}), row({ words: "Something else." })]).uniform,
    false,
  );
  assert.equal(
    challengeSummary([row({}), row({ changed: null })]).uniform,
    true,
  ); // what it said without is not part of the statement
});

test("rows that differ are grouped by what they say, in the order each first appears, with their counts", () => {
  const changed = {
    outcome: "SAME_DECISION",
    words: "Removing it did not change the decision.",
  };
  const rows = [
    row({}),
    row({ ...changed, hours: 4 }),
    row({ hours: 24 }),
    row({ ...changed, hours: 24, source: "hoshitori.features" }),
    row({ ...changed, symbol: "ETH" }),
  ];
  const s = challengeSummary(rows);
  assert.equal(s.uniform, false);
  assert.equal(s.words, null);
  assert.deepEqual(s.outcomes, [
    {
      outcome: "EVIDENCE_REQUIRED",
      words:
        "The rule cannot decide without it: it is evidence the rule requires.",
      questions: 2,
    },
    {
      outcome: "SAME_DECISION",
      words: "Removing it did not change the decision.",
      questions: 3,
    },
  ]);
  assert.equal(
    s.outcomes.reduce((n, o) => n + o.questions, 0),
    s.questions,
  );
});

test("the same outcome with different words is two groups, never merged", () => {
  const s = challengeSummary([row({}), row({ words: "Another sentence." })]);
  assert.equal(s.outcomes.length, 2);
});

test("no rows is not a uniform answer", () => {
  const s = challengeSummary([]);
  assert.equal(s.uniform, false);
  assert.equal(s.questions, 0);
  assert.deepEqual(s.sources, []);
  assert.deepEqual(s.outcomes, []);
});

test("one row is a statement about one question", () => {
  const s = challengeSummary([row({})]);
  assert.equal(s.uniform, true);
  assert.equal(s.questions, 1);
  assert.equal(s.calls, 1);
});

test("a chance written as a decimal is a whole percentage, half to even, never 0 or 100", () => {
  assert.equal(chanceWords("0.488462"), "49%");
  assert.equal(chanceWords("0.990000"), "99%");
  assert.equal(chanceWords("0.010000"), "1%");
  assert.equal(chanceWords("0.995"), "99%"); // a chance that is not 1 is never shown as 100
  assert.equal(chanceWords("0.004"), "1%"); // nor one that is not 0 as 0
  assert.equal(chanceWords("0.505"), "50%"); // half to even, as the records' own wording rounds
  assert.equal(chanceWords("0.515"), "52%");
  assert.equal(chanceWords("0.5"), "50%");
  assert.equal(chanceWords("0.12"), "12%");
  assert.equal(chanceWords("0.9"), "90%");
});

test("a chance that is not one is refused, not shown", () => {
  for (const bad of [
    "",
    "abc",
    "1",
    "0",
    "-0.2",
    "1.5",
    "0.",
    ".5",
    "0.5x",
    " 0.5",
    "NaN",
    "0.1234567890123456789",
  ]) {
    assert.throws(() => chanceWords(bad), /not a chance/, bad);
  }
});

test("the forgery in a sentence: which call, from what, to what", () => {
  const doc = { record: "fcabc", field: "p", was: "0.488462", now: "0.990000" };
  assert.equal(
    forgeryWords(doc),
    "The chance given to a rise in one call was changed from 49% to 99%.",
  );
  assert.equal(
    forgeryWords({ ...doc, was: "0.97", now: "0.010000" }),
    "The chance given to a rise in one call was changed from 97% to 1%.",
  );
  assert.throws(() => forgeryWords({ ...doc, field: "q" }), /not a field/);
});

test("each reason a bundle cannot be scored has words, and an unknown one is refused", () => {
  assert.equal(
    notScorable("ROOT_NOT_CURRENT"),
    "the trusted root is not a current one",
  );
  assert.equal(notScorable("INTEGRITY"), "a record failed its checks");
  assert.equal(
    notScorable("LEVEL_BELOW_C3"),
    "the time-stamps do not reach independent time-stamping",
  );
  assert.equal(
    notScorable("UNRESOLVED_REFERENCE"),
    "a record that a call cites is not in the bundle",
  );
  assert.equal(
    notScorable("COMPLETENESS_NOT_SHOWN"),
    "the bundle does not show that it holds the whole batch its cycle committed",
  );
  assert.equal(notScorable("NO_FORECAST"), "the bundle holds no call to score");
  assert.equal(
    notScorable("DATA_MODE"),
    "the evidence is not all of the one kind of data a score needs",
  );
  assert.throws(() => notScorable("NO_ROOT"), /not a reason/);
});

test("the rules a pass was judged by are said in one sentence, and none is said when no root was used", () => {
  const basis = {
    required_independent: 2,
    window_before_commit_s: 1,
    window_after_commit_s: 60,
    close_after_commit_s: 90,
    max_spread_s: 5,
    u_constant_s: 5,
  };
  assert.equal(
    basisWords(basis),
    "Judged by the root's rules: 2 independent time-stamps, each from 1 s before to 60 s after the commit, no more than 5 s apart, with 5 s of clock uncertainty allowed.",
  );
  assert.equal(
    basisWords({
      ...basis,
      required_independent: 1,
      window_after_commit_s: 3600,
      max_spread_s: 600,
      u_constant_s: 0,
    }),
    "Judged by the root's rules: 1 independent time-stamp, each from 1 s before to 3600 s after the commit, no more than 600 s apart, with 0 s of clock uncertainty allowed.",
  );
  assert.equal(basisWords(null), null);
});

test("why a call was made: the state in words and the distance to a different call, no return values", async () => {
  const { boundaryFor, whyWords } = await import("../src/format.ts");
  const up = {
    direction: "UP",
    state: "rising hard",
    at: "+0.47",
    distance: "+0.23",
    says: "46% up, flat",
  };
  const down = {
    direction: "DOWN",
    state: "flat",
    at: "+0.12",
    distance: "-0.12",
    says: "48% up, flat",
  };
  const b = {
    cycle_id: "c1",
    arm: "L0",
    symbol: "SOL",
    hours: 1,
    ret: "+0.24",
    state: "rising",
    says: "49% up, flat",
    moves: [up, down],
  };
  const row = { cycle_id: "c1", arm: "L0", symbol: "SOL", hours: 1 };
  assert.equal(boundaryFor([b], row)?.symbol, "SOL");
  assert.equal(boundaryFor([b], { ...row, arm: "L1" }), null);
  assert.equal(boundaryFor([b], { ...row, hours: 4 }), null);
  assert.equal(boundaryFor([b], { ...row, cycle_id: "c2" }), null);
  assert.equal(boundaryFor([], row), null);
  assert.deepEqual(whyWords(b), [
    "The state of the market the rule saw was rising: it said 49% up, flat.",
    "A rise of 0.23 points would have put it in rising hard, and it would have said 46% up, flat.",
    "A fall of 0.12 points would have put it in flat, and it would have said 48% up, flat.",
  ]);
  const text = whyWords(b).join(" ");
  assert.ok(
    !text.includes("+0.24") &&
      !text.includes("+0.47") &&
      !text.includes("+0.12"),
    "no return value of the data is printed",
  );
  assert.deepEqual(whyWords({ ...b, moves: [] }), [
    "The state of the market the rule saw was rising: it said 49% up, flat.",
    "No move of the return, up or down, would have changed the call: the rule says the same in every state.",
  ]);
  assert.deepEqual(
    whyWords({
      ...b,
      says: "no call: too few past windows in that state",
      moves: [up],
    }),
    [
      "The state of the market the rule saw was rising: it made no call, because there are too few past windows in that state.",
      "A rise of 0.23 points would have put it in rising hard, and it would have said 46% up, flat.",
    ],
  );
  assert.throws(
    () => whyWords({ ...b, moves: [{ ...up, direction: "SIDEWAYS" }] }),
    /not a direction/,
  );
});

test("the status table is grouped by status, least proven first, and every part is in exactly one group", async () => {
  const { qualityGroups } = await import("../src/format.ts");
  const rows = [
    { unit: "a", word: "TESTED" },
    { unit: "b", word: "DESIGNED" },
    { unit: "c", word: "INTEGRATED" },
    { unit: "d", word: "TESTED" },
    { unit: "e", word: "DESIGNED" },
  ];
  const groups = qualityGroups(rows);
  assert.deepEqual(
    groups.map((g) => [g.word, g.rows.map((r) => r.unit)]),
    [
      ["DESIGNED", ["b", "e"]],
      ["TESTED", ["a", "d"]],
      ["INTEGRATED", ["c"]],
    ],
  );
  assert.deepEqual(qualityGroups([]), []);
  assert.equal(groups.flatMap((g) => g.rows).length, rows.length);
});

test("a status word the table does not know is an error and a part is never dropped", async () => {
  const { qualityGroups } = await import("../src/format.ts");
  assert.throws(
    () => qualityGroups([{ unit: "x", word: "DONE" }]),
    /not a status word: DONE/,
  );
});

test("only the groups below TESTED start open, so the first screen shows the gaps", async () => {
  const { groupStartsOpen } = await import("../src/format.ts");
  assert.deepEqual(
    [
      "DESIGNED",
      "IMPLEMENTED",
      "TESTED",
      "INTEGRATED",
      "INDEPENDENTLY REVIEWED",
    ].map(groupStartsOpen),
    [true, true, false, false, false],
  );
});

test("the later steps of a bundle, in words", async () => {
  const { findings, notScorable } = await import("../src/format.ts");
  const base = {
    root_of_trust: { state: "PINNED", version: 1, sha256: "ab" },
    integrity: { ok: true, items: { a: {}, b: {} }, errors: [] },
    timing: {
      level: "C3",
      tokens: [{ valid: true }, { valid: true }],
      basis: null,
    },
    cycle: {
      declared: true,
      steps: 1,
      batch_size: 5,
      members_present: 5,
      complete: true,
    },
    eligibility: { scorable: true, reasons: [] },
    not_proven: [],
  };
  const withLater = (later: {
    shown: boolean;
    failures: number;
    forecasts: Record<
      string,
      { awaiting: string; origin: string; settlement?: string }
    >;
  }) => findings({ ...base, cycle: { ...base.cycle, later } });
  const calls = {
    a: { awaiting: "ok", origin: "absent" },
    b: { awaiting: "ok", origin: "ok" },
  };
  const good = withLater({ shown: true, failures: 0, forecasts: calls });
  assert.equal(good.length, 6);
  assert.deepEqual(good[4], {
    ok: true,
    text: "The later steps of 2 calls are here, under a later checkpoint that extends the first: each call became eligible no earlier than its time-stamps allow, and 1 call started at the next minute close after that.",
  });
  assert.equal(
    good[5]?.text,
    "It meets every condition a scored period asks for. A call counts only in a scored period; an exploratory run never does.",
  );
  assert.equal(
    withLater({ shown: true, failures: 0, forecasts: { a: calls.a } })[4]?.text,
    "The later steps of 1 call are here, under a later checkpoint that extends the first: each call became eligible no earlier than its time-stamps allow, and none has started yet.",
  );
  assert.deepEqual(
    withLater({
      shown: true,
      failures: 1,
      forecasts: { a: { awaiting: "early", origin: "wrong" }, b: calls.b },
    })[4],
    {
      ok: false,
      text: "The later steps of 1 of 2 calls do not follow from the time-stamps or from their own settlement: an eligible time or a start that is too early or not the next minute close, a settlement that is missing, written twice, written before the window closed or that does not agree with its own prices, or a step that is missing, malformed or written twice.",
    },
  );
  const settled = {
    a: { awaiting: "ok", origin: "ok", settlement: "ok" },
    b: { awaiting: "ok", origin: "ok", settlement: "none" },
  };
  assert.equal(
    withLater({ shown: true, failures: 0, forecasts: settled })[4]?.text,
    "The later steps of 2 calls are here, under a later checkpoint that extends the first: each call became eligible no earlier than its time-stamps allow, and 2 calls started at the next minute close after that. 1 call ended with a settlement that agrees with its own two prices and was written after the call's window closed; those prices are not checked against a market here.",
  );
  assert.equal(
    withLater({
      shown: true,
      failures: 1,
      forecasts: {
        a: { awaiting: "ok", origin: "ok", settlement: "mismatch" },
        b: settled.b,
      },
    })[4]?.text,
    "The later steps of 1 of 2 calls do not follow from the time-stamps or from their own settlement: an eligible time or a start that is too early or not the next minute close, a settlement that is missing, written twice, written before the window closed or that does not agree with its own prices, or a step that is missing, malformed or written twice.",
  );
  assert.equal(
    withLater({ shown: false, failures: 0, forecasts: {} }).length,
    5,
    "a bundle with no later part says nothing about one",
  );
  assert.equal(
    findings(base).length,
    5,
    "nor does a verdict from a verifier that does not know the later part",
  );
  assert.equal(
    notScorable("ORIGIN_BEFORE_ATTESTATION"),
    "a call's later steps do not show it becoming eligible and starting after its time-stamps",
  );
});

// --- the practice portfolio -------------------------------------------------------------------------------------------------------------
import {
  bpsAsPercent,
  curvePath,
  curveSummary,
  exitWords,
  groupedMoney,
  modeWords,
  money2,
  percentOfShare,
  practiceLevel,
  roundDecimal,
} from "../src/format.ts";

test("an amount of money keeps its exact digits and gains thousands separators", () => {
  assert.equal(groupedMoney("101398.5"), "101,398.5");
  assert.equal(groupedMoney("100000"), "100,000");
  assert.equal(groupedMoney("-1398.5"), "-1,398.5");
  assert.equal(groupedMoney("999"), "999");
  assert.equal(groupedMoney("0"), "0");
  assert.equal(groupedMoney("1234567.12345678"), "1,234,567.12345678");
  assert.throws(() => groupedMoney("1e5"), /not a decimal/);
  assert.throws(() => groupedMoney(""), /not a decimal/);
});

test("a share is shown as a percentage without a float", () => {
  assert.equal(percentOfShare("0.0095"), "+0.95%");
  assert.equal(percentOfShare("0.013985"), "+1.4%");
  assert.equal(percentOfShare("0.009811"), "+0.98%");
  assert.equal(percentOfShare("-0.011074"), "-1.11%");
  assert.equal(percentOfShare("0.00001"), "0%");
  assert.equal(percentOfShare("-0.00001"), "0%");
  assert.equal(percentOfShare("0.0095", 4), "+0.95%");
  assert.equal(percentOfShare("0.013985", 4), "+1.3985%");
  assert.equal(percentOfShare("0"), "0%");
  assert.equal(percentOfShare("-0.012"), "-1.2%");
  assert.equal(percentOfShare("1"), "+100%");
  assert.equal(percentOfShare("0.5"), "+50%");
  assert.equal(percentOfShare("-1"), "-100%");
  assert.throws(() => percentOfShare("a lot"), /not a decimal/);
});

test("basis points are shown as a percentage", () => {
  assert.equal(bpsAsPercent("10"), "0.1%");
  assert.equal(bpsAsPercent("5"), "0.05%");
  assert.equal(bpsAsPercent("25"), "0.25%");
  assert.equal(bpsAsPercent("20"), "0.2%");
  assert.equal(bpsAsPercent("100"), "1%");
  assert.equal(bpsAsPercent("0"), "0%");
});

test("the words for a cost level, an exit and a mode", () => {
  assert.equal(practiceLevel("BASE"), "Base costs");
  assert.equal(practiceLevel("STRESS"), "Stress costs");
  assert.equal(practiceLevel("DOUBLE_FEES"), "Double fees");
  assert.throws(() => practiceLevel("ODD"), /cost level/);
  assert.equal(exitWords("STOP"), "the stop was reached");
  assert.equal(exitWords("TARGET"), "the target was reached");
  assert.equal(exitWords("TIME"), "the time ran out");
  assert.equal(exitWords("GAP_EXIT"), "taken at the next bar there was");
  assert.equal(exitWords(null), "not recorded");
  assert.equal(modeWords("ACTIVE", false), "Sizing as planned");
  assert.equal(modeWords("HALVED", false), "Sizes halved after a fall");
  assert.equal(modeWords("FLAT", false), "Closed after a fall");
  assert.equal(modeWords("HALTED", false), "Stopped after a deep fall");
  assert.equal(modeWords("HALTED", true), "Wiped out");
  assert.throws(() => modeWords("ODD", false), /mode/);
});

test("the curve says in words what it draws", () => {
  const points = [
    { at: "2026-10-26T07:35:20.000Z", equity: "100950" },
    { at: "2026-10-26T07:36:20.000Z", equity: "100100" },
    { at: "2026-10-26T07:37:20.000Z", equity: "101398.5" },
  ];
  assert.equal(
    curveSummary(points),
    "Equity went from 100,950 to 101,398.5 over 3 marks; lowest 100,100, highest 101,398.5.",
  );
  assert.equal(curveSummary([points[0]!]), "One mark so far: equity 100,950.");
  assert.equal(curveSummary([]), "No mark yet.");
});

test("the curve is drawn from its marks, higher equity higher on the screen, a flat run in the middle", () => {
  const two = curvePath(
    [
      { at: "2026-10-26T07:35:20.000Z", equity: "100" },
      { at: "2026-10-26T07:37:20.000Z", equity: "200" },
    ],
    200,
    100,
    10,
  );
  assert.deepEqual(two.points, [
    [10, 90],
    [190, 10],
  ]);
  assert.equal(two.d, "M10 90L190 10");
  const flat = curvePath(
    [
      { at: "2026-10-26T07:35:20.000Z", equity: "5" },
      { at: "2026-10-26T07:36:20.000Z", equity: "5" },
    ],
    200,
    100,
    10,
  );
  assert.deepEqual(
    flat.points.map(([, y]) => y),
    [50, 50],
  );
  const one = curvePath(
    [{ at: "2026-10-26T07:35:20.000Z", equity: "5" }],
    200,
    100,
    10,
  );
  assert.deepEqual(one.points, [[100, 50]]);
  assert.equal(one.d, "M100 50");
  assert.deepEqual(curvePath([], 200, 100, 10).points, []);
  // marks at uneven times keep their spacing in time
  const uneven = curvePath(
    [
      { at: "2026-10-26T07:30:00.000Z", equity: "1" },
      { at: "2026-10-26T07:31:00.000Z", equity: "2" },
      { at: "2026-10-26T07:40:00.000Z", equity: "3" },
    ],
    200,
    100,
    10,
  );
  assert.deepEqual(
    uneven.points.map(([x]) => x),
    [10, 28, 190],
  );
});

test("a decimal is rounded half away from zero, from its digits, to the places asked", () => {
  assert.equal(roundDecimal("98892.5875", 2), "98892.59");
  assert.equal(roundDecimal("98892.5849", 2), "98892.58");
  assert.equal(roundDecimal("0.005", 2), "0.01");
  assert.equal(roundDecimal("-0.005", 2), "-0.01");
  assert.equal(roundDecimal("-0.004", 2), "0.00");
  assert.equal(roundDecimal("100", 2), "100.00");
  assert.equal(roundDecimal("1.5", 0), "2");
  assert.equal(roundDecimal("0.999", 2), "1.00");
  assert.equal(
    roundDecimal("123456789012345678.995", 2),
    "123456789012345679.00",
  );
  assert.throws(() => roundDecimal("1e3", 2), /not a decimal/);
});

test("an amount of money for a headline has cents and separators", () => {
  assert.equal(money2("98892.5875"), "98,892.59");
  assert.equal(money2("100000"), "100,000.00");
  assert.equal(money2("101398.5"), "101,398.50");
  assert.equal(money2("-1107.4125"), "-1,107.41");
  assert.equal(money2("-0.001"), "0.00");
  assert.equal(money2("0"), "0.00");
});

// --- the mutation sweeps ----------------------------------------------------------------------------------------------------------------
import { currencyWords, sweepSentence } from "../src/format.ts";

const SWEEP = {
  modules: 110,
  places: 26640,
  noticed: 23545,
  unbuilt: 253,
  raised: 2597,
  clock: 70,
  survived: 175,
  stale: 0,
  unchecked: 0,
};

test("the sweep in one honest paragraph", () => {
  assert.equal(
    sweepSentence(SWEEP),
    "110 modules were each changed in every small way their code allows: 26,640 changes in all. A check noticed 23,545 of them (88%); 253 could not be built, 2,597 made a check raise an error and 70 were noticed only because a check ran out of time or the machine failed, which shows nothing about the checks. 175 went unnoticed; each is listed as harmless, with its reason, in its module's test.",
  );
});

test("the sweep paragraph says what it could not compare, and what changed after its sweep", () => {
  assert.match(
    sweepSentence({ ...SWEEP, stale: 3 }),
    /3 modules changed after their sweep, so what the sweep found says nothing about the code as it is now\.$/,
  );
  assert.match(
    sweepSentence({ ...SWEEP, stale: 1 }),
    /1 module changed after its sweep, so what the sweep found says nothing about the code as it is now\.$/,
  );
  assert.match(
    sweepSentence({ ...SWEEP, unchecked: 110 }),
    /110 modules could not be compared with the code here\.$/,
  );
  assert.doesNotMatch(sweepSentence(SWEEP), /compared|changed after/);
  assert.match(
    sweepSentence({ ...SWEEP, clock: 0 }),
    /253 could not be built and 2,597 made a check raise an error\. 175 went unnoticed/,
  );
  assert.match(
    sweepSentence({ ...SWEEP, unbuilt: 0, raised: 0, clock: 0 }),
    /A check noticed 23,545 of them \(88%\)\. 175 went unnoticed/,
  );
  assert.match(
    sweepSentence({ ...SWEEP, survived: 0 }),
    /Nothing went unnoticed\./,
  );
  assert.match(
    sweepSentence({ ...SWEEP, survived: 1 }),
    /1 went unnoticed; it is listed as harmless/,
  );
  assert.equal(
    sweepSentence({
      ...SWEEP,
      modules: 0,
      places: 0,
      noticed: 0,
      unbuilt: 0,
      raised: 0,
      clock: 0,
      survived: 0,
    }),
    "No module has a sweep.",
  );
});

test("whether a swept file is still the one that was swept", () => {
  assert.equal(currencyWords(true, null), "the swept files");
  assert.equal(
    currencyWords(false, "the module changed after its sweep"),
    "no: the module changed after its sweep",
  );
  assert.equal(currencyWords(null, null), "not compared here");
});

test("a source that was asked and not read, in words", async () => {
  const { unreadLine } = await import("../src/format.ts");
  assert.equal(
    unreadLine({
      source: "kraken",
      reason: "no answer came",
      interval_s: 3600,
    }),
    "Kraken: no answer came, for the 1-hour window.",
  );
  assert.equal(
    unreadLine({
      source: "kraken",
      reason: "no answer came",
      interval_s: 14400,
    }),
    "Kraken: no answer came, for the 4-hour window.",
  );
  assert.equal(
    unreadLine({
      source: "binance",
      reason: "the venue did not tell its time",
      interval_s: 900,
    }),
    "Binance: the venue did not tell its time, for the 15-minute window.",
  );
  assert.equal(
    unreadLine({ source: "binance", reason: "no answer came", interval_s: 45 }),
    "Binance: no answer came, for the 45-second window.",
  );
  assert.equal(
    unreadLine({
      source: "ryo",
      reason: "no answer came in time",
      interval_s: null,
    }),
    "RYO: no answer came in time.",
  );
  assert.throws(
    () => unreadLine({ source: "ryo", reason: "x", interval_s: 0 }),
    /not a window/,
  );
});

// --- what the requirements rest on ------------------------------------------------------------------------------------------------------
import {
  coverageGapReason,
  coverageGapTitle,
  coverageSentence,
  coverageVerdictWords,
} from "../src/format.ts";

const COVERAGE = {
  rubric: [
    { verdict: "CHECKS FOUND", count: 12 },
    { verdict: "CHECK NOT FOUND", count: 0 },
    { verdict: "NO CHECK NAMED", count: 0 },
  ],
  rubric_total: 12,
  contract: [
    { verdict: "CHECKS FOUND", count: 46 },
    { verdict: "CHECK NOT FOUND", count: 0 },
    { verdict: "NOT BUILT", count: 11 },
    { verdict: "OUTSIDE THIS REPOSITORY", count: 1 },
    { verdict: "NO CHECK NAMED", count: 0 },
  ],
  contract_total: 58,
  gaps: [],
};

test("the coverage in plain words, with what a check is and is not", () => {
  assert.equal(
    coverageSentence(COVERAGE),
    "Of the 12 lines of the published rubric, all 12 name checks that exist. Of the 58 sections of the contract, 46 do; 11 are not built and 1 is checked outside this repository. A check that exists is not a check that passes.",
  );
});

test("the coverage sentence counts what is missing, in the singular and the plural", () => {
  const some = {
    ...COVERAGE,
    rubric: [
      { verdict: "CHECKS FOUND", count: 10 },
      { verdict: "CHECK NOT FOUND", count: 1 },
      { verdict: "NO CHECK NAMED", count: 1 },
    ],
    contract: [
      { verdict: "CHECKS FOUND", count: 0 },
      { verdict: "CHECK NOT FOUND", count: 2 },
      { verdict: "NOT BUILT", count: 1 },
      { verdict: "OUTSIDE THIS REPOSITORY", count: 0 },
      { verdict: "NO CHECK NAMED", count: 3 },
    ],
    contract_total: 6,
  };
  assert.equal(
    coverageSentence(some),
    "Of the 12 lines of the published rubric, 10 name checks that exist; 1 names a check that cannot be found and 1 has no check named, so it is not verified. Of the 6 sections of the contract, none do; 2 name a check that cannot be found, 1 is not built and 3 have no check named, so they are not verified. A check that exists is not a check that passes.",
  );
  assert.match(
    coverageSentence({
      ...COVERAGE,
      contract: [{ verdict: "CHECKS FOUND", count: 58 }],
      contract_total: 58,
    }),
    /the contract, all 58 do\. A check/,
  );
});

test("a gap's verdict in words", () => {
  assert.equal(coverageVerdictWords("NOT BUILT"), "not built");
  assert.equal(
    coverageVerdictWords("OUTSIDE THIS REPOSITORY"),
    "checked outside this repository",
  );
  assert.equal(
    coverageVerdictWords("CHECK NOT FOUND"),
    "names a check that cannot be found",
  );
  assert.equal(
    coverageVerdictWords("NO CHECK NAMED"),
    "no check named, so not verified",
  );
  assert.equal(coverageVerdictWords("CHECKS FOUND"), "has checks");
});

test("a gap's title without the contract's version tag, and its reason as a sentence", () => {
  assert.equal(
    coverageGapTitle("Visibility, privacy and publishing (v0.8)"),
    "Visibility, privacy and publishing",
  );
  assert.equal(
    coverageGapTitle("The test registry (v0.13)"),
    "The test registry",
  );
  assert.equal(
    coverageGapTitle("Whale evidence rules"),
    "Whale evidence rules",
  );
  assert.equal(coverageGapTitle("A (v1) thing (v0.9)"), "A (v1) thing");
  assert.equal(
    coverageGapReason("the whale collector is not built"),
    "The whale collector is not built.",
  );
  assert.equal(coverageGapReason("already a sentence."), "Already a sentence.");
  assert.equal(coverageGapReason("-"), "");
  assert.equal(coverageGapReason(""), "");
});

// --- what a model arm's run cost ---------------------------------------------------------------------------------------------------------
import { costWords } from "../src/format.ts";

test("the cost of a model run says the time is a limit, not what it took", () => {
  assert.equal(
    costWords(1, 7251, 580),
    "1 request, 7251 tokens; it was allowed up to 580 seconds",
  );
  assert.equal(
    costWords(2, 100, 200),
    "2 requests, 100 tokens; it was allowed up to 200 seconds",
  );
  assert.doesNotMatch(costWords(1, 1, 1), /with \d+ seconds to answer/);
});

test("a drill's reasons keep an acronym whole when they follow a colon", async () => {
  const { drillSentence } = await import("../src/format.ts");
  const s = drillSentence({
    cycle: "Committed",
    level: "C3",
    slots: [["Open", 6]],
    reasons: [
      "RYO said no",
      "A partial answer was kept",
      "No deep analysis was invented",
    ],
    forecasts: 6,
  });
  assert.equal(
    s,
    "Committed at C3: 6 open. 6 forecasts made. Why: RYO said no; a partial answer was kept; no deep analysis was invented.",
  );
});

test("what each source brought into the latest cycle is one short line", async () => {
  const { sourceLine } = await import("../src/format.ts");
  assert.equal(
    sourceLine([
      { source: "binance-spot", items: 6, unread: 0 },
      { source: "hoshitori.features", items: 3, unread: 0 },
      { source: "ryo", items: 8, unread: 0 },
    ]),
    "Read this cycle: Binance spot 6 items; Computed measures 3 items; RYO 8 items.",
  );
  assert.equal(
    sourceLine([
      { source: "kraken-spot", items: 1, unread: 1 },
      { source: "ryo", items: 0, unread: 3 },
    ]),
    "Read this cycle: RYO brought nothing for 3 calls; Kraken spot 1 item, nothing for 1 call.",
  );
  assert.equal(sourceLine([]), null);
});

test("a placebo challenge is worded as text added, never as a source taken out", async () => {
  const {
    modelCounts,
    shiftLine,
    challengeTitle,
    challengeAction,
    PLACEBO_SOURCE,
  } = await import("../src/format.ts");
  const t = {
    same_same: 29,
    same_changed: 6,
    changed_same: 3,
    changed_changed: 2,
  };
  const k = {
    same_same: 27,
    same_changed: 8,
    changed_same: 4,
    changed_changed: 1,
  };
  assert.equal(PLACEBO_SOURCE, "hoshitori.placebo");
  assert.equal(
    challengeTitle("ETH", 24, "hoshitori.placebo"),
    "ETH, 24 hours: with neutral text added",
  );
  assert.equal(challengeTitle("ETH", 24, "ryo"), "ETH, 24 hours: without RYO");
  assert.equal(
    challengeAction("hoshitori.placebo", 1),
    "Neutral text with no content was added to the evidence.",
  );
  assert.equal(challengeAction("ryo", 2), "2 items taken out.");
  assert.equal(
    modelCounts("hoshitori.placebo", 40, t, k),
    "With every source the agent made this call in 35 of 40 runs; with neutral text added, in 32 of 40; asked again with every source (the control), in 31 of 40.",
  );
  assert.equal(
    shiftLine("+8", "-8", "+23", "0.87", "hoshitori.placebo"),
    "The share of runs making this call, with the evidence minus with neutral text added: +8 points (95% interval −8 to +23); exact test p = 0.87.",
  );
  assert.equal(
    shiftLine("+8", "-8", "+23", "0.87"),
    "The share of runs making this call, with every source minus without it: +8 points (95% interval −8 to +23); exact test p = 0.87.",
  );
});

test("the result of a placebo challenge says what adding text did, and the other results are as before", async () => {
  const { modelOutcome } = await import("../src/format.ts");
  assert.equal(
    modelOutcome("EFFECT_UNCERTAIN", null, "hoshitori.placebo"),
    "Too few runs to say whether adding text with no content changed the decision.",
  );
  assert.equal(
    modelOutcome("DECISION_STABLE", null, "hoshitori.placebo"),
    "Adding text with no content did not change the decision: any shift is within 15 points either way.",
  );
  assert.equal(
    modelOutcome("DECISION_CHANGED", "MODEL_REVISED", "hoshitori.placebo"),
    "Adding text with no content changed the decision, beyond the agent's own variation: this agent moves when its evidence is only longer. The agent changed its mind: its own proposal moved without the source more often than when it was asked again.",
  );
  assert.equal(
    modelOutcome("EFFECT_UNCERTAIN", null, "ryo"),
    modelOutcome("EFFECT_UNCERTAIN", null),
  );
  assert.equal(
    modelOutcome(
      "NOT_EVALUATED(structural_failure)",
      null,
      "hoshitori.placebo",
    ),
    modelOutcome("NOT_EVALUATED(structural_failure)", null),
  );
});

test("Today says that another cycle is being collected, and for when it was planned", async () => {
  const { inProgressLine } = await import("../src/format.ts");
  assert.equal(
    inProgressLine("2026-11-04T14:00:00.000Z"),
    "The cycle planned for 4 Nov, 14:00 UTC is being collected; its calls will replace these.",
  );
  assert.equal(inProgressLine(null), null);
});

test("a cycle with no calls says why, and the page opens on the last cycle that was committed", async () => {
  const { emptyCycleWords, defaultCycle } = await import("../src/format.ts");
  assert.equal(
    emptyCycleWords(undefined),
    "No cycle of that name is in this site.",
  );
  assert.equal(
    emptyCycleWords("ABANDONED"),
    "This cycle was abandoned before it made any call.",
  );
  assert.equal(
    emptyCycleWords("COMMITTED"),
    "Nothing was scheduled in this cycle.",
  );
  assert.equal(
    emptyCycleWords("COLLECTING"),
    "This cycle is being collected; its calls appear when it commits.",
  );
  const c = (id: string, state: string) => ({ cycle_id: id, state }) as never;
  assert.equal(
    defaultCycle([
      c("a", "COMMITTED"),
      c("b", "COMMITTED"),
      c("c", "COLLECTING"),
    ]),
    "b",
  );
  assert.equal(defaultCycle([c("a", "ABANDONED"), c("b", "COLLECTING")]), "b");
  assert.equal(defaultCycle([]), null);
});

test("the concentration test removes the best 5% of positions, at least one, in exact cents", () => {
  assert.equal(concentration(["5"]), null);
  assert.deepEqual(concentration(["10", "-2", "-3"]), {
    count: 3,
    removed: 1,
    total: "5.00",
    without: "-5.00",
    flagged: true,
  });
  const twenty = [...Array(19).fill("1.50"), "30"];
  assert.deepEqual(concentration(twenty), {
    count: 20,
    removed: 1,
    total: "58.50",
    without: "28.50",
    flagged: false,
  });
  assert.deepEqual(concentration(Array(21).fill("0.10")), {
    count: 21,
    removed: 2,
    total: "2.10",
    without: "1.90",
    flagged: false,
  });
  assert.deepEqual(concentration(["-4", "-1"]), {
    count: 2,
    removed: 1,
    total: "-5.00",
    without: "-4.00",
    flagged: false,
  });
  assert.equal(concentration(["3", "0.004", "-3"])?.total, "0.00");
});

test("a source is named for a reader, and an id of ours never reaches one raw", async () => {
  const { sourceName } = await import("../src/format.ts");
  assert.equal(sourceName("ryo"), "RYO");
  assert.equal(sourceName("hoshitori.features"), "Computed measures");
  assert.equal(sourceName("hoshitori.crosscheck"), "Price cross-check");
  assert.equal(sourceName("hoshitori.placebo"), "Neutral text");
  assert.equal(sourceName("binance-spot"), "Binance spot");
  assert.equal(sourceName("kraken"), "Kraken");
});

test("our own price cross-check is named for a reader in a challenge too", async () => {
  const { sourceWords } = await import("../src/format.ts");
  assert.equal(
    sourceWords("hoshitori.crosscheck"),
    "The price cross-check of RYO against the exchange",
  );
});

test("a stretch with no mark is drawn dashed and never as a smooth move", () => {
  const minute = (m: number) =>
    new Date(Date.UTC(2026, 9, 26, 7, 0, 0) + m * 60_000).toISOString();
  const series = (minutes: number[]) =>
    minutes.map((m, i) => ({ at: minute(m), equity: String(i + 1) }));
  const gapped = curvePath(series([0, 1, 2, 3, 63, 64]), 200, 100, 10);
  assert.deepEqual(gapped.points, [
    [10, 90],
    [13, 74],
    [16, 58],
    [18, 42],
    [187, 26],
    [190, 10],
  ]);
  assert.equal(gapped.d, "M10 90L13 74L16 58L18 42M187 26L190 10");
  assert.equal(gapped.dashed, "M18 42L187 26");
  const even = curvePath(series([0, 1, 2, 3]), 200, 100, 10);
  assert.equal(even.d, "M10 90L70 63L130 37L190 10");
  assert.equal(even.dashed, "");
  assert.equal(
    curvePath(series([0, 1, 60]), 200, 100, 10).dashed,
    "",
    "three marks have no usual spacing to judge a gap by",
  );
  assert.equal(
    curvePath(series([0, 1, 2, 3, 13]), 200, 100, 10).dashed,
    "",
    "ten times the usual spacing is not yet a gap",
  );
  assert.notEqual(
    curvePath(series([0, 1, 2, 3, 14]), 200, 100, 10).dashed,
    "",
    "more than ten times is",
  );
  assert.equal(
    curvePath(series([0, 0, 0, 5]), 200, 100, 10).dashed,
    "",
    "marks at one time have no spacing",
  );
  assert.equal(curvePath([], 200, 100, 10).dashed, "");
});

test("a source reads inside a sentence, and no id of ours or lower-case brand reaches a reader", async () => {
  const { sourceWords, sourceName, challengeTitle } =
    await import("../src/format.ts");
  assert.equal(sourceWords("ryo"), "RYO");
  assert.equal(
    challengeTitle("ETH", 24, "hoshitori.features"),
    "ETH, 24 hours: without the measures computed from the candles",
  );
  assert.equal(
    challengeTitle("ETH", 24, "hoshitori.crosscheck"),
    "ETH, 24 hours: without the price cross-check of RYO against the exchange",
  );
  assert.equal(
    challengeTitle("ETH", 24, "binance-spot"),
    "ETH, 24 hours: without Binance spot candles",
  );
  assert.equal(sourceName("okx"), "OKX");
  assert.equal(sourceName("coinbase"), "Coinbase");
  assert.equal(sourceName("hyperliquid"), "Hyperliquid");
  assert.equal(sourceName("binance-funding"), "Binance funding");
});

test("a stretch with no mark is told in words as well as drawn, three at most", () => {
  const minute = (m: number) =>
    new Date(Date.UTC(2026, 9, 26, 7, 0, 0) + m * 60_000).toISOString();
  const series = (minutes: number[]) =>
    minutes.map((m, i) => ({ at: minute(m), equity: String(i + 1) }));
  assert.equal(
    curveSummary(series([0, 1, 2, 3, 63, 64])),
    "Equity went from 1 to 6 over 6 marks; lowest 1, highest 6. No mark is drawn between 26 Oct 2026, 07:03 UTC and 26 Oct 2026, 08:03 UTC.",
  );
  assert.equal(
    curveSummary(series([0, 1, 2, 3])),
    "Equity went from 1 to 4 over 4 marks; lowest 1, highest 4.",
  );
  const many = series([
    0, 1, 2, 3, 100, 101, 102, 103, 200, 201, 202, 203, 300, 301, 302, 303, 400,
  ]);
  assert.equal(
    curveSummary(many),
    "Equity went from 1 to 17 over 17 marks; lowest 1, highest 17. " +
      "No mark is drawn between 26 Oct 2026, 07:03 UTC and 26 Oct 2026, 08:40 UTC. " +
      "No mark is drawn between 26 Oct 2026, 08:43 UTC and 26 Oct 2026, 10:20 UTC. " +
      "No mark is drawn between 26 Oct 2026, 10:23 UTC and 26 Oct 2026, 12:00 UTC. " +
      "1 more stretch has no mark drawn.",
  );
});

test("each agent is explained in one sentence, and an agent we do not know is not explained", async () => {
  const { agentWords } = await import("../src/format.ts");
  assert.equal(
    agentWords("L0"),
    "A fixed rule on the measured numbers, written down and fixed before a scored period begins. It uses no model.",
  );
  assert.equal(
    agentWords("L1"),
    "One model reads the evidence once and gives one answer, which code then checks against that evidence.",
  );
  assert.equal(
    agentWords("L2"),
    "A bull and a bear each argue from the same evidence and a judge decides; the judge's answer is checked in the same way.",
  );
  assert.equal(
    agentWords("L1x"),
    "The single model asked several times, spending about as much as the debate did, to show whether the debate won only by spending more.",
  );
  assert.equal(agentWords("L9"), null);
});

test("a curve that draws only some of a book's marks says so, and what that does to its highest and lowest", () => {
  const points = [
    { at: "2026-10-26T07:35:20.000Z", equity: "100" },
    { at: "2026-10-26T07:36:20.000Z", equity: "90" },
    { at: "2026-10-26T07:37:20.000Z", equity: "110" },
  ];
  const thinned =
    " The curve draws 3 of the book's 9 marks, chosen evenly, so its highest and lowest can be less extreme than the book's own peak and largest fall.";
  assert.equal(
    curveSummary(points, 9),
    `Equity went from 100 to 110 over 3 marks; lowest 90, highest 110.${thinned}`,
  );
  assert.equal(
    curveSummary(points, 3),
    curveSummary(points),
    "every mark drawn: nothing to say",
  );
  assert.equal(
    curveSummary(points, 2),
    curveSummary(points),
    "a total below the marks drawn is not a thinning",
  );
  const minute = (m: number) =>
    new Date(Date.UTC(2026, 9, 26, 7, 0, 0) + m * 60_000).toISOString();
  const gapped = [0, 1, 2, 3, 63, 64].map((m, i) => ({
    at: minute(m),
    equity: String(i + 1),
  }));
  assert.equal(
    curveSummary(gapped, 20),
    "Equity went from 1 to 6 over 6 marks; lowest 1, highest 6. No mark is drawn between 26 Oct 2026, 07:03 UTC and 26 Oct 2026, 08:03 UTC. The curve draws 6 of the book's 20 marks, chosen evenly, so its highest and lowest can be less extreme than the book's own peak and largest fall.",
  );
});

import {
  assumptionKind,
  expectsWords,
  invalidationState,
  previewWords,
  sellReason,
  shareWords,
  tradeStatus,
  whenMs,
} from "../src/format.ts";

test("a time in milliseconds is written as the page writes every time", () => {
  assert.equal(whenMs(0), "1 Jan 1970, 00:00 UTC");
  assert.equal(whenMs(1_700_000_000_000), "14 Nov 2023, 22:13 UTC");
});

test("an assumption's kind, in a word", () => {
  assert.equal(assumptionKind("OBSERVATION"), "Observation");
  assert.equal(assumptionKind("INTERPRETATION"), "Interpretation");
  assert.equal(assumptionKind("CAUSAL"), "Cause");
  assert.equal(assumptionKind("FORECAST"), "Forecast");
  assert.throws(() => assumptionKind("HUNCH"), /not an assumption kind: HUNCH/);
});

test("what a hypothesis expects", () => {
  assert.equal(
    expectsWords("UP", "BTC", 4),
    "BTC to rise within 4 hours of the entry",
  );
  assert.equal(
    expectsWords("DOWN", "SOL", 1),
    "SOL to fall within 1 hour of the entry",
  );
  assert.throws(
    () => expectsWords("SIDEWAYS", "BTC", 4),
    /not a direction: SIDEWAYS/,
  );
});

test("a share without its sign", () => {
  assert.equal(shareWords("0.02"), "2%");
  assert.equal(shareWords("0.005"), "0.5%");
});

test("where a practice trade stands and why it was sold", () => {
  assert.equal(
    tradeStatus("PENDING"),
    "Waiting for the minute it would be bought in",
  );
  assert.equal(
    tradeStatus("MISSING"),
    "The minute it would be bought in never came",
  );
  assert.equal(tradeStatus("OPEN"), "Bought and not yet sold");
  assert.equal(
    tradeStatus("EXIT_MISSING"),
    "The minute it would be sold in never came",
  );
  assert.equal(tradeStatus("CLOSED"), "Closed");
  assert.throws(() => tradeStatus("LOST"), /not a trade status: LOST/);
  assert.equal(sellReason("INVALIDATED"), "the invalidation condition held");
  assert.equal(sellReason("HORIZON"), "the horizon ended");
  assert.throws(() => sellReason("BORED"), /not a reason to sell: BORED/);
});

test("what a preview found", () => {
  assert.equal(
    previewWords({
      bars: 300,
      true_bars: 31,
      false_bars: 255,
      undecided_bars: 14,
    }),
    "The entry condition held on 31 of 300 closed hourly candles, did not on 255, and could not be decided on 14 (too few earlier candles for its indicators).",
  );
  assert.equal(
    previewWords({ bars: 1, true_bars: 1, false_bars: 0, undecided_bars: 0 }),
    "The entry condition held on 1 of 1 closed hourly candle, did not on 0.",
  );
});

test("what the invalidation condition said on the last candle", () => {
  assert.equal(
    invalidationState("HOLDS", null),
    "On the last candle the invalidation condition did not hold.",
  );
  assert.equal(
    invalidationState("INVALIDATED", null),
    "On the last candle the invalidation condition held.",
  );
  assert.equal(
    invalidationState("UNKNOWN", null),
    "On the last candle the invalidation condition could not be decided.",
  );
  assert.equal(
    invalidationState("UNKNOWN", "too few bars"),
    "On the last candle the invalidation condition could not be decided: too few bars.",
  );
  assert.throws(
    () => invalidationState("MAYBE", null),
    /not an invalidation state: MAYBE/,
  );
});

test("the age of the evidence and the price move are said in words, and unknown is not zero", async () => {
  const { evidenceAgeWords, driftWords } = await import("../src/format.ts");
  assert.equal(evidenceAgeWords(null), "Not yet known");
  assert.equal(evidenceAgeWords(0), "0 seconds");
  assert.equal(evidenceAgeWords(89), "89 seconds");
  assert.equal(evidenceAgeWords(90), "2 minutes");
  assert.equal(evidenceAgeWords(3600), "60 minutes");
  assert.equal(driftWords(null), "Not known until the call settles");
  assert.equal(driftWords("0.0000"), "0.0000 times its usual hourly range");
  assert.equal(driftWords("1.2500"), "1.2500 times its usual hourly range");
});

test("changes that say the same thing about different tokens become one card, in the ranked order", async () => {
  const { groupChanges } = await import("../src/format.ts");
  const c = (id: string, symbol: string, sentence: string, hours = 1) => ({
    kind: "NEW",
    sentence,
    hours,
    arm_name: "Rules",
    symbol,
    forecast_id: id,
  });
  const groups = groupChanges([
    c("a", "ADA", "New call: 60% up over 1 hour."),
    c("b", "BTC", "New call: 52% up over 1 hour."),
    c("d", "DOGE", "New call: 60% up over 1 hour."),
    c("e", "ETH", "New call: 60% up over 1 hour.", 4),
  ]);
  assert.deepEqual(
    groups.map((g) => g.members.map((m) => m.symbol)),
    [["ADA", "DOGE"], ["BTC"], ["ETH"]],
  );
});

test("which way a call leaned", async () => {
  const { leanOf } = await import("../src/summary.ts");
  assert.equal(leanOf("54% up"), "up");
  assert.equal(leanOf("46% up"), "down");
  assert.equal(leanOf("50% up"), "even");
  assert.equal(leanOf("1% up"), "down");
  assert.equal(leanOf("99% up"), "up");
  assert.equal(leanOf(null), null);
  assert.equal(leanOf("target first 4%, stop first 6%, neither 90%"), null);
  assert.equal(leanOf("100% up"), null);
});

test("the receipt in five seconds", async () => {
  const { receiptSummary } = await import("../src/summary.ts");
  const row = {
    arm_name: "Rules",
    symbol: "BTC",
    hours: 1,
    contract: "D",
    state: "Settled",
    chance: "54% up",
    result: "Rose",
  };
  const others = (...c: (string | null)[]) => ({
    others: c.map((chance) => ({ chance })),
  });
  assert.equal(
    receiptSummary({ row, links: others("51% up", "48% up", null) }),
    "The rules agent put the chance that BTC would be higher in 1 hour at 54%. It went up: the call leaned the right way. 1 of the 2 other agents on the same call leaned the same way.",
  );
  assert.equal(
    receiptSummary({ row: { ...row, result: "Fell" }, links: others() }),
    "The rules agent put the chance that BTC would be higher in 1 hour at 54%. It went down: the call leaned the wrong way.",
  );
  assert.equal(
    receiptSummary({
      row: { ...row, chance: "50% up", result: "Fell" },
      links: others("50% up"),
    }),
    "The rules agent put the chance that BTC would be higher in 1 hour at 50%. It went down; the call was even. The other agent on the same call leaned the same way.",
  );
  assert.equal(
    receiptSummary({
      row: { ...row, state: "Open", result: null, hours: 24 },
      links: others(),
    }),
    "The rules agent put the chance that BTC would be higher in 24 hours at 54%. Not settled yet.",
  );
  assert.equal(
    receiptSummary({
      row: { ...row, state: "Void", result: null },
      links: others(),
    }),
    "The rules agent put the chance that BTC would be higher in 1 hour at 54%. It could not be settled.",
  );
  assert.equal(
    receiptSummary({
      row: { ...row, state: "Abstained", chance: null, result: null },
      links: others("60% up"),
    }),
    "The rules agent made no call on BTC for 1 hour.",
  );
  assert.equal(
    receiptSummary({
      row: { ...row, state: "Failed", chance: null, result: null },
      links: others(),
    }),
    "The rules agent did not finish its call on BTC for 1 hour.",
  );
  assert.equal(
    receiptSummary({
      row: { ...row, state: "Too late", chance: null, result: null },
      links: others(),
    }),
    "The rules agent did not finish its call on BTC for 1 hour.",
  );
  assert.equal(
    receiptSummary({
      row: {
        ...row,
        contract: "B",
        chance: "target first 4%, stop first 6%, neither 90%",
        result: "Neither",
      },
      links: others(),
    }),
    "The rules agent, target or stop on BTC in 1 hour: target first 4%, stop first 6%, neither 90%. It ended: neither.",
  );
});

test("a lone other agent that leaned the other way", async () => {
  const { receiptSummary } = await import("../src/summary.ts");
  const row = {
    arm_name: "Debate",
    symbol: "ETH",
    hours: 4,
    contract: "D",
    state: "Settled",
    chance: "60% up",
    result: "Rose",
  };
  assert.equal(
    receiptSummary({ row, links: { others: [{ chance: "40% up" }] } }),
    "The debate agent put the chance that ETH would be higher in 4 hours at 60%. It went up: the call leaned the right way. The other agent on the same call leaned differently.",
  );
});

test("alerts follow what is followed, and storage that fails follows nothing", async () => {
  const { followed, readFollows } = await import("../src/follows.ts");
  const a = (
    kind: string,
    forecast_id: string | null,
    symbol: string | null,
    arm: string | null,
    book: string | null = null,
  ) => ({
    kind,
    at: "2026-11-09T12:00:00.000Z",
    text: "t",
    forecast_id,
    symbol,
    arm,
    book,
  });
  const all = [
    a("SETTLED", "fc1", "BTC", "L1"),
    a("INVALIDATED", "fc2", "ETH", "L0"),
    a("RISK_STEP", null, null, "L2", "L2:BASE"),
    a("SETTLED", "fc3", "SOL", "L1x"),
  ];
  assert.deepEqual(followed(all, { calls: [], tokens: [], agents: [] }), []);
  assert.deepEqual(
    followed(all, { calls: ["fc1"], tokens: [], agents: [] }).map(
      (x) => x.forecast_id,
    ),
    ["fc1"],
  );
  assert.deepEqual(
    followed(all, { calls: [], tokens: ["ETH"], agents: [] }).map(
      (x) => x.forecast_id,
    ),
    ["fc2"],
  );
  assert.deepEqual(
    followed(all, { calls: [], tokens: [], agents: ["L2"] }).map((x) => x.book),
    ["L2:BASE"],
  );
  assert.deepEqual(
    followed(all, { calls: ["fc3"], tokens: ["BTC"], agents: ["L2"] }).length,
    3,
  );
  assert.deepEqual(readFollows(), { calls: [], tokens: [], agents: [] }); // no window here: the reader follows nothing and does not throw
});

test("a chance reads as a whole percentage, a half rounded up as the alerts round it", () => {
  assert.equal(chancePercent("0.535519"), "54%");
  assert.equal(chancePercent("0.545"), "55%");
  assert.equal(chancePercent("0.42"), "42%");
  assert.equal(chancePercent("0.5"), "50%");
});

test("a visit is counted by the export it showed, and only what is newer than the last one counts as new", async () => {
  const { nextVisits, since, readVisits } = await import("../src/follows.ts");
  assert.deepEqual(
    nextVisits({ seen: null, before: null }, "2026-11-09T12:00:00.000Z"),
    { seen: "2026-11-09T12:00:00.000Z", before: null },
  );
  const once = {
    seen: "2026-11-09T12:00:00.000Z",
    before: "2026-11-08T12:00:00.000Z",
  };
  assert.deepEqual(nextVisits(once, "2026-11-09T12:00:00.000Z"), once);
  assert.deepEqual(nextVisits(once, "2026-11-10T12:00:00.000Z"), {
    seen: "2026-11-10T12:00:00.000Z",
    before: "2026-11-09T12:00:00.000Z",
  });
  assert.deepEqual(nextVisits(once, "2026-11-08T00:00:00.000Z"), once); // an older export opened again moves nothing back
  const a = (at: string) => ({
    kind: "SETTLED",
    at,
    text: "t",
    forecast_id: "fc",
    symbol: "BTC",
    arm: "L1",
    book: null,
  });
  const all = [
    a("2026-11-09T13:00:00.000Z"),
    a("2026-11-09T12:00:00.000Z"),
    a("2026-11-09T11:00:00.000Z"),
  ];
  assert.deepEqual(since(all, null), []);
  assert.deepEqual(since(all, "2026-11-09T12:00:00.000Z"), [all[0]]);
  assert.deepEqual(readVisits(), { seen: null, before: null }); // no browser storage here: a first visit
});

test("topHeadline: the first card as one sentence, grouped tokens together, none when nothing changed", () => {
  const flip = {
    kind: "FLIP",
    sentence: "Now leans down: 43% up, was 57% up.",
    hours: 4,
    arm_name: "Debate",
    symbol: "LINK",
    forecast_id: "a",
  };
  assert.equal(
    topHeadline([flip]),
    "LINK, 4 hours, Debate: Now leans down: 43% up, was 57% up.",
  );
  assert.equal(
    topHeadline([flip, { ...flip, symbol: "ADA", forecast_id: "b" }]),
    "LINK, ADA, 4 hours, Debate: Now leans down: 43% up, was 57% up.",
  );
  assert.equal(
    topHeadline([{ ...flip, kind: "MOVE", sentence: "Moved." }, flip]),
    "LINK, 4 hours, Debate: Moved.",
  );
  assert.equal(topHeadline([]), null);
});

test("readLine: what was read, and what was not and why", () => {
  const e = (source: string) => ({ source });
  assert.equal(
    readLine(
      [e("binance-spot"), e("binance-spot"), e("hoshitori.features")],
      [{ source: "ryo", reason: "no answer came" }],
    ),
    "It read Binance spot (2 items), Computed measures (1 item). Not read: RYO: no answer came.",
  );
  assert.equal(
    readLine([e("ryo")], [{ source: "ryo", reason: "x" }]),
    "It read RYO (1 item).",
  );
  assert.equal(readLine([], []), "It read nothing.");
  assert.equal(
    readLine(
      [e("odd-source")],
      [
        { source: "b", reason: "r" },
        { source: "b", reason: "r2" },
        { source: "a", reason: "q" },
      ],
    ),
    "It read Odd source (1 item). Not read: A: q; B: r.",
  );
});

test("largestDisagreement: the widest opposite-sided pair on one call, and none otherwise", () => {
  const row = (
    symbol: string,
    hours: number,
    arm_name: string,
    chance: string | null,
    id: string,
    contract = "D",
  ) => ({
    contract,
    symbol,
    hours,
    arm_name,
    chance,
    forecast_id: id,
  });
  const rows = [
    row("BTC", 1, "Rules", "43% up", "a"),
    row("BTC", 1, "Single model", "64% up", "b"),
    row("BTC", 1, "Debate", "50% up", "c"),
    row("ETH", 4, "Rules", "49% up", "d"),
    row("ETH", 4, "Debate", "55% up", "e"),
    row(
      "SOL",
      1,
      "Rules",
      "target first 4%, stop first 6%, neither 90%",
      "f",
      "B",
    ),
    row("SOL", 1, "Debate", null, "g"),
  ];
  const d = largestDisagreement(rows);
  assert.deepEqual(
    d && [d.symbol, d.hours, d.low.forecast_id, d.high.forecast_id, d.gap],
    ["BTC", 1, "a", "b", 21],
  );
  // a gap under the margin, a pair on one side of 50, and an exact flip are not disagreements
  assert.equal(
    largestDisagreement([
      row("ETH", 4, "Rules", "49% up", "d"),
      row("ETH", 4, "Debate", "54% up", "e"),
    ]),
    null,
  );
  assert.equal(
    largestDisagreement([
      row("ETH", 4, "Rules", "52% up", "d"),
      row("ETH", 4, "Debate", "70% up", "e"),
    ]),
    null,
  );
  assert.equal(
    largestDisagreement([
      row("ETH", 4, "Rules", "50% up", "d"),
      row("ETH", 4, "Debate", "70% up", "e"),
    ]),
    null,
  );
  // different horizons or tokens never pair; the margin is inclusive (49 and 55 is six points)
  assert.equal(
    largestDisagreement([
      row("ETH", 1, "Rules", "40% up", "d"),
      row("ETH", 4, "Debate", "70% up", "e"),
    ]),
    null,
  );
  assert.equal(
    largestDisagreement([
      row("ETH", 4, "Rules", "49% up", "d"),
      row("ETH", 4, "Debate", "55% up", "e"),
    ])?.gap,
    6,
  );
  assert.equal(largestDisagreement([]), null);
});

test("whyItMatters: a sentence for each of the six kinds, none for another", () => {
  for (const kind of ["FLIP", "MISS", "STOPPED", "HIT", "MOVE", "NEW"]) {
    const sentence = whyItMatters(kind);
    assert.ok(sentence !== null && sentence.endsWith("."), kind);
  }
  assert.equal(whyItMatters("NOTHING"), null);
  assert.match(whyItMatters("FLIP") ?? "", /change side/);
});

test("largestDisagreement: a tie goes to the shorter horizon as a number", () => {
  const row = (
    hours: number,
    arm_name: string,
    chance: string,
    id: string,
  ) => ({
    contract: "D",
    symbol: "BTC",
    hours,
    arm_name,
    chance,
    forecast_id: id,
  });
  const rows = [
    row(12, "Rules", "40% up", "a"),
    row(12, "Debate", "60% up", "b"),
    row(4, "Rules", "40% up", "c"),
    row(4, "Debate", "60% up", "d"),
  ];
  assert.equal(largestDisagreement(rows)?.hours, 4);
});
