// A map from each line of the published rubric to the place in the product that bears on it, and to what that place does not show. The lines and
// their points are the organiser's published rubric; test/judges.test.ts holds them to it. Nothing here scores the product: it says where to
// look and where the evidence stops.

import type { Route } from "./route.ts";

export type JudgeLine = {
  group: string;
  line: string;
  points: number;
  where: Route;
  look: string;
  limit: string;
};

/** The groups in the order the rubric gives them, with their headings and what each group is judged for. */
export const TRACKS: Record<string, { heading: string; note: string }> = {
  common: {
    heading: "Every track (40 points)",
    note: "Judged for each track entered.",
  },
  agents: {
    heading: "Autonomous Agents (60 points)",
    note: "Agents that read market evidence and record the practice calls they would make.",
  },
  interfaces: {
    heading: "Dashboards and Interfaces (60 points)",
    note: "Screens that say what changed and why it matters within 30 seconds.",
  },
  skills: {
    heading: "New Skills (60 points)",
    note: "A research tool RYO does not have, following the published tool specification.",
  },
};

export const JUDGE_MAP: JudgeLine[] = [
  {
    group: "common",
    line: "Combining sources",
    points: 20,
    where: { page: "calls", cycle: null },
    look: "Open any call, then its receipt. “What it read” lists each exchange source behind the call by its digest, and a call is settled against two venues.",
    limit:
      "A league reads RYO's tools when its settings turn them on, and each receipt lists what that call read: a receipt cites an RYO field only if RYO appears under “What it read”. The rules agent reads exchange data and the numbers computed from it. RYO's own interpretation is never given to the agents, and a price RYO states that the exchange contradicts stops the model agents before a model is asked.",
  },
  {
    group: "common",
    line: "Coping with failure",
    points: 20,
    where: { page: "drill" },
    look: "Each drill names the failure it injects, what was expected and what was observed, and says whether the expectation held.",
    limit:
      "The drills run on recorded data in a sandbox. They are not tests against live services.",
  },
  {
    group: "agents",
    line: "Real thinking",
    points: 25,
    where: { page: "calls", cycle: null },
    look: "Open a call of the single-judgment or the debate agent, then its receipt: what the model read, the chance it gave and why, which gate cut or kept it and, for the debate, what the Bear put against the Bull. The Desk page then shows, on labelled invented examples, each check it chose and what it tried to disprove.",
    limit:
      "The Desk's runs on its page are invented examples, labelled so; it has not yet committed a call of its own on a real event. The model agents' receipts are real. Whether any agent forecasts better than chance is not measured: too few calls have settled.",
  },
  {
    group: "agents",
    line: "Cause and effect",
    points: 20,
    where: { page: "challenge" },
    look: "For each call of the rules agent, the nearest return at which it would have said something different, and whether it could decide without each source.",
    limit:
      "This is how a call depends on its evidence. It does not say the evidence was true or that the market moved because of it.",
  },
  {
    group: "agents",
    line: "Repeatability",
    points: 10,
    where: { page: "verify" },
    look: "Check any cycle's record on the page: it is verified in the browser, against the site's trusted root or one you load yourself, and the page says what each result proves.",
    limit:
      "A pass shows the record existed unchanged and was independently time-stamped. It does not show the call was good.",
  },
  {
    group: "agents",
    line: "Advanced execution",
    points: 5,
    where: { page: "calls", cycle: null },
    look: "Each cycle runs from the candidate list to settlement, and every call lists the steps it went through, with the time of each. A call made outside a cycle, by the Desk or by a person, can be sealed, time-stamped and settled on a separate ledger.",
    limit:
      "Every call is a practice call. Nothing here is a trading result, and short-term profit is not scored. The separate ledger was run live once, for one call a person entered; the Desk has not yet produced a call of its own to commit, and that settlement is started by hand, not by the running league.",
  },
  {
    group: "interfaces",
    line: "Easy to read",
    points: 25,
    where: { page: "today" },
    look: "The first screen: what changed in the latest cycle, in a few words, with the next step. A page test counts the words fully in view.",
    limit:
      "No study with people has been run yet, so whether a stranger understands it in 30 seconds is not shown.",
  },
  {
    group: "interfaces",
    line: "Sorted by importance",
    points: 25,
    where: { page: "today" },
    look: "The changes are listed in an order set by a rule that is written above them, and each has a sentence saying what changed and a link to its receipt. The developments below them are ordered by a score whose parts are printed beside each item.",
    limit:
      "Both orders are fixed rules, and the development score is labelled provisional. Neither has been compared with labels from independent experts, so neither is shown to match what matters.",
  },
  {
    group: "interfaces",
    line: "Works for everyone",
    points: 10,
    where: { page: "quality" },
    look: "The page lists each unit of the build with its status and what was run, including the check of the accessible names of every page.",
    limit:
      "Automatic checks find only part of the problems, and no person using a screen reader has been through the pages.",
  },
  {
    group: "skills",
    line: "Fills a gap",
    points: 25,
    where: { page: "quality" },
    look: "Seven skills are in the repository under skills/: indicator-condition-check, address-flag-check, exit-liquidity-check, ryo-verdict-record, evidence-delta, evidence-sensitivity and research-integrity-check. The status page lists what was run against each.",
    limit:
      "Whether RYO lacks these tools is read from the published tool list (it states that token analysis is not wallet analysis). The skills are not part of this site.",
  },
  {
    group: "skills",
    line: "Follows the spec",
    points: 15,
    where: { page: "quality" },
    look: "The skill returns the envelope the tool guide describes and its catalogue entry follows the style of the live catalogue.",
    limit:
      "The organiser's skill contract is not published, so conformance to it cannot be shown.",
  },
  {
    group: "skills",
    line: "Could we use it",
    points: 20,
    where: { page: "quality" },
    look: "The repository holds the install steps and tests for the skill, and the status page says which were run.",
    limit:
      "Each was installed from a fresh environment by its author; no other person has installed any of them yet.",
  },
];
