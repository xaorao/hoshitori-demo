// The film's fixed on-screen words (VIDEO.md section B). Anything that is a measurement, a time, an id or a program's output is NOT here:
// it comes through data.ts. Digits appear in this file only inside the labels listed in test/copy.test.ts.

export const COPY = {
  unknown: "Outcome unknown.",
  committedPrefix: "Committed",
  name: "Hoshitori 星取",
  nameLatin: "Hoshitori",
  nameJa: "星取",

  s1: {
    claim: "A market call usually arrives as a screenshot.",
    questions: ["When was it made?", "Was it edited?", "Were the misses dropped?"],
  },

  s2: {
    steps: ["Collect", "Decide", "Seal", "Settle", "Check"],
    labels: ["Evidence frozen", "Decided", "Committed", "Settled"],
    practice: "Nothing is traded. Every call is practice.",
    one: "Three tracks, one record.",
    tracks: ["Agents", "Interface", "Skills"],
  },

  kicker: {
    t1: "TRACK 1 · AUTONOMOUS AGENTS",
    t2: "TRACK 2 · DASHBOARDS AND INTERFACES",
    t3: "TRACK 3 · NEW SKILLS",
  },

  s3: {
    snapshot: "Every agent decides on the same frozen snapshot. None sees another's answer.",
    disagreeTail: "up",
  },

  s4: {
    checkpoint: "checkpoint",
    verifiers: ["Python", "Node"],
    identical: "reports identical except \"verifier\"",
  },

  s5: {
    versus: "vs",
    shared: "shared calls",
  },

  /** Caption lines that replace a beat's usual text when the data says the usual text would not be true. */
  override: {
    noOutageEn: ["Every cycle the schedule put due", "left a record."],
    noOutageJa: ["予定されたすべてのサイクルが", "記録を残した。"],
    noCountEn: ["run it: the count is printed live"],
    noCountJa: ["実行すると、件数がその場で表示される。"],
  },

  s7: {
    claim: "When a source goes down, it says so. Nothing is invented in its place.",
    never: "null is never zero. A failed read is a failed read.",
    held: "held",
  },

  s8: {
    names: [
      "address-flag-check",
      "exit-liquidity-check",
      "indicator-condition-check",
      "ryo-verdict-record",
      "evidence-delta",
      "evidence-sensitivity",
      "research-integrity-check",
    ],
    quote: "RYO's deep_analysis: \"not wallet analysis\".",
  },

  s9: {
    title: "What this film does not show",
    lines: [
      "No agent is shown to forecast better than chance.",
      "RYO's own read is built and tested, but RYO refused the key, so it is not in the record.",
      "The Desk, claim and Laboratory pages show invented examples, labelled on the page.",
      "The league runs on one machine; the site is a static export. No study with people has been run.",
    ],
    footerHead: "Every number in this film was read from docs/record/record.tar.gz at render time.",
    commit: "Commit",
    rendered: "Rendered",
    record: "Record sha256",
  },

  s10: {
    commands: ["tar -xzf docs/record/record.tar.gz -C ../record", "python3 scripts/resettle_published.py ../record"],
    closing: ["Built on RYO's read-only tools.", "Nothing is traded."],
    repo: "github.com/RYO-Digital/ryochan-hackathon_repository-388",
  },

  cut: {
    kickerCall: "BTC",
    promise: "Check any call yourself.",
    tagline: "Agents forecast crypto in public. Check any call yourself.",
    skills: "This is not a safety rating.",
  },

  dev: "DEV",
  devNote: "STAND-INS · NOT FOR RELEASE",
} as const;

/** The labels in this file that may carry digits. test/copy.test.ts rejects any other digit. */
export const DIGIT_ALLOWED: readonly string[] = [
  COPY.kicker.t1,
  COPY.kicker.t2,
  COPY.kicker.t3,
  COPY.s10.repo,
  COPY.s10.commands[1],
  COPY.s9.record,
];
