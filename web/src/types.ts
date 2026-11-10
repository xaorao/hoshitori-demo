// The documents the league exports (hoshitori.view_models, written by hoshitori.site_export). Every number is already text.

export type Row = {
  forecast_id: string;
  cycle_id: string;
  arm: string;
  arm_name: string;
  symbol: string;
  hours: number;
  contract: "D" | "B";
  state: string;
  chance: string | null;
  result: string | null;
  reason: string | null;
  reason_code: string | null;
  trust: string;
  level: string | null;
  origin: string | null;
  settled_at: string | null;
  due: string | null; // the earliest the call can settle
};

export type CycleSummary = {
  cycle_id: string;
  planned_for: string;
  state: string;
  committed_at: string | null;
  slots: number;
  rows_by_state: [string, number][];
  sources: { source: string; items: number; unread: number }[];
};

export type TodayChange = {
  kind: "FLIP" | "MISS" | "STOPPED" | "HIT" | "MOVE" | "NEW";
  forecast_id: string;
  arm_name: string;
  symbol: string;
  hours: number;
  sentence: string;
  action: string;
  size: string;
};

export type Today = {
  mode: string;
  generated_at: string;
  latest: CycleSummary | null;
  rows: Row[];
  settled_lately: Row[];
  since: string | null;
  settled_since: [string, number][];
  product: string;
  rule: string;
  changes: TodayChange[];
  in_progress: string | null;
  scheduled_cycles?: number; // cycles the schedule has put due since it began
  missed_cycles?: string[]; // the times of those that left no record at all
};

export type ArmRecord = {
  arm: string;
  arm_name: string;
  contract: "D" | "B";
  scheduled: number;
  calls: number;
  settled: number;
  open: number;
  not_settled: number;
  abstained: number;
  failed: number;
  deciding: number;
  coverage: string | null;
  availability: string | null;
  mean_brier: string | null; // the settled calls that count toward a score (independently time-stamped)
  brier_n: number;
  all_mean_brier: string | null; // every settled call, whatever its level
  all_brier_n: number;
  missed_c3: string | null; // the share of every settled call that does not count
  label: string;
  strata?: {
    by: string; // "age" or "drift"
    group: number; // 0 the lowest third, 1 the middle, 2 the highest
    low: string | null;
    high: string | null;
    n: number;
    mean_brier: string | null;
  }[];
  skill?: string | null; // one minus the score over the score of a fixed reference (a coin; three equal parts for a barrier call)
  log_score?: string | null; // up or down: the mean logarithmic score of the calls that count toward a score
  climate_skill?: string | null; // up or down: one minus the score over the score of the share of rises in the 30 days before each call
  climate_n?: number; // the calls that had such a reference
  reliability?: {
    group: number;
    n: number;
    mean_p: string;
    frequency: string;
    low: string;
    high: string;
  }[]; // three groups by the probability said
  exclusions?: { reason: string; words: string; n: number }[]; // the calls that were void or unsettleable, by reason
  bounds?: {
    excluded: number;
    brier_low: string;
    brier_high: string;
    skill_low: string;
    skill_high: string;
    sensitive: boolean;
  } | null; // the score and its skill if every excluded call had gone either way
};

export type EvidenceLine = {
  evidence_id: string;
  source: string;
  path: string;
  kind: string;
  event_time: string | null; // none for evidence the ledger does not hold
  data_mode: string;
  origin_family: string;
  raw_sha256: string | null;
  shown: string;
};

export type TimelineLine = { state: string; at: string; reason: string | null };

export type SettlementLine = {
  state: string;
  outcome: string | null;
  reference_price: string | null;
  outcome_price: string | null;
  oracles: string;
  rule_version: string;
  flags: string[];
};

export type AgentChallenge = {
  bear_only: EvidenceLine[];
  judge_cited: string[];
  shared: number;
  bull_only: number;
};

export type RunLine = {
  verdict: string;
  reason: string | null;
  gate: string;
  proposed_chance: string | null;
  final_chance: string | null;
  calls: number;
  tokens: number;
  cutoff_s: number;
  challenge: AgentChallenge | null;
};

export type Receipt = {
  row: Row;
  invalidations: string[];
  evidence: EvidenceLine[];
  timeline: TimelineLine[];
  committed_at: string | null;
  as_of: string;
  oracles: string;
  forecast_digest: string;
  settlement: SettlementLine | null;
  run: RunLine | null;
  unread: {
    source: string;
    reason_code: string;
    reason: string;
    interval_s: number | null;
  }[];
  evidence_age_s?: number | null; // the origin less the evidence time, in seconds
  gap_drift_atr?: string | null; // |reference price - close| over ATR(14), once the call has settled
  links: ReceiptLinks; // what the receipt leads to
};

/** The same opportunity's other calls, the practice trades that followed this call, the Desk's runs on its token (`receipt_links`). */
export type ReceiptLinks = {
  others: {
    forecast_id: string;
    arm: string;
    arm_name: string;
    chance: string | null;
    state: string;
    result: string | null;
  }[];
  followed: {
    book: string;
    arm_name: string;
    level: string;
    entry_price: string;
    exit_price: string;
    spent: string;
    net: string;
    exit_kind: string | null;
    closed_at: string;
  }[];
  desk: {
    run_id: string;
    label: string;
    state: string;
    forecast_id: string | null;
  }[];
  revisions?: Revision[]; // none in a receipt exported before reversals were linked
};

/** The same agent pointing the other way on the same token and horizon while its earlier call is open (`call_revisions`). */
export type Revision = {
  earlier: string;
  later: string;
  arm: string;
  symbol: string;
  hours: number;
  earlier_p: string;
  later_p: string;
  earlier_origin: string;
  later_origin: string;
  earlier_reading: { close: string | null; ret: string | null };
  later_reading: { close: string | null; ret: string | null };
  decisive: boolean;
};

export type LedgerState = { state: string; detail: string };

export type SiteIndex = {
  format: number;
  generated_at: string;
  mode: string;
  ledger?: LedgerState; // the state of the ledger the documents were exported from; none in an index exported before it was written
  counts: { cycles: number; rows: number; receipts: number };
  files: Record<string, string>;
};

export type QualityRow = {
  unit: string;
  status: string;
  word: string;
  evidence: string;
  limit: string | null;
};

export type DrillObserved = {
  cycle: string;
  level: string | null;
  slots: [string, number][];
  reasons: string[];
  forecasts: number;
};
export type Drill = {
  id: string;
  title: string;
  injected: string;
  expected: string;
  observed: DrillObserved;
  expected_observed: DrillObserved;
  held: boolean;
};

export type ClaimPart = {
  node_id: string;
  kind: string;
  status: string;
  quote: string;
  line: string;
};
export type ClaimPage = {
  banner: string;
  headline: string;
  parts: ClaimPart[];
  next_step: string;
  triage: string;
};
export type ClaimReading = {
  state: string;
  score: string;
  validated: boolean;
  unmatched: string[];
  type_conflicts: string[];
};
export type ClaimDispute = {
  kind: string;
  start: number;
  end: number;
  detail: string;
};
export type ClaimChoice = {
  field: string;
  question: string;
  options: string[];
};
export type ClaimSketch = {
  node_id: string;
  status: string;
  contract: string | null;
  asset: string | null;
  direction: string | null;
  magnitude: string | null;
  horizon_hours: number | null;
  choices: ClaimChoice[];
  reason: string | null;
};
export type ClaimNextCheck = {
  status: string;
  node_id: string | null;
  words: string | null;
  check_id: string | null;
  instruction: string | null;
  source: string | null;
  reason: string | null;
};
export type ChallengeRow = {
  cycle_id: string;
  arm: string;
  symbol: string;
  hours: number;
  source: string;
  items: number;
  outcome: string;
  words: string;
  original: string;
  changed: string | null;
};
export type BoundaryMove = {
  direction: string;
  state: string;
  at: string;
  distance: string;
  says: string;
};
export type Boundary = {
  cycle_id: string;
  arm: string;
  symbol: string;
  hours: number;
  ret: string;
  state: string;
  says: string;
  moves: BoundaryMove[];
};
export type ClaimCheck = {
  claim_id: string;
  node_id: string;
  words: string;
  status: string;
  reason: string;
  evidence_id: string | null;
  cycle_id: string | null;
  snapshot_at: string | null;
};
export type Claim = {
  id: string;
  text: string;
  model: string;
  read_at: string;
  runs: number;
  page: ClaimPage | null;
  message: string | null;
  reading: ClaimReading | null;
  disputes: ClaimDispute[];
  sketches: ClaimSketch[];
  next_check: ClaimNextCheck | null;
  assets: string[];
  assumptions: ClaimAssumption[];
};

export type ClaimAssumption = {
  node_id: string;
  kind: "TRUE" | "FALSE" | "REMOVED";
  changed: { node_id: string; before: string; after: string }[];
  sentence: string;
};

export type BundleEntry = {
  cycle_id: string;
  path: string;
  files: string[];
  directories: string[];
};
/** The forged copy of a cycle's bundle (forgery.json): what was changed, the two roots, and where the copy is. */
export type ForgeryDoc = {
  format: number;
  cycle_id: string;
  edit: { record: string; field: string; was: string; now: string };
  standin_vkey: string;
  size: number;
  honest_root: string;
  forged_root: string;
  path: string;
  files: string[];
  directories: string[];
};

export type PairCounts = {
  same_same: number;
  same_changed: number;
  changed_same: number;
  changed_changed: number;
};

// A recorded challenge of the model agent (hoshitori.model_challenge_file.Row): codes and counts; the page words them (format.ts).
export type ModelChallenge = {
  cycle_id: string;
  forecast_id: string;
  arm_name: string;
  symbol: string;
  hours: number;
  source: string;
  items: number;
  original: string;
  outcome: string;
  mechanism: string | null;
  pairs: number;
  attempted: number;
  failed: number;
  treated: PairCounts | null;
  control: PairCounts | null;
  shift: string | null;
  low: string | null;
  high: string | null;
  p: string | null;
  model: string;
  temperature: string | null;
};

export type DeskCounts = { calls: number; seconds: number; tokens: number };
export type DeskHypothesis = {
  template_id: string;
  statement: string;
  invalidation: string;
  stance: string;
};
export type DeskCheck = {
  seq: number;
  node_id: string;
  check_type: string;
  provider: string;
  origin_family: string;
  operation: string;
  outcome: string;
  error_class: string | null;
  status_before: string;
  own_status: string;
  status_after: string;
  layer: string;
  alternatives: number;
  calls: number;
  evidence: number;
};
export type DeskOrigins = {
  read: string[];
  supporting: string[];
  contradicting: string[];
  calls: number;
};
export type DeskTest = {
  type: string;
  result: string;
  reason: string | null;
  calibrated: boolean;
};
export type DeskFalsification = {
  outcome: string;
  completed: boolean;
  label: string | null;
  target_assumption: string;
  proposed_explanation: string;
  tests: DeskTest[];
  unresolved: string[];
  pre_test: { stance: string; p: string; weight: string };
  ceiling: { weight: string; p_cap: string };
};
export type DeskGate = { gate: string; status: string; detail: string };
export type DeskDecision = {
  proposal: {
    stance: string;
    p: string;
    weight: string;
    cites: number;
    no_call_reason: string | null;
  };
  gates: DeskGate[];
  final: {
    kind: string;
    reason: string | null;
    stance: string | null;
    p: string | null;
    weight: string | null;
  };
};
export type DeskRun = {
  run_id: string;
  label: string;
  symbol: string;
  route: string;
  state: string;
  reason: string | null;
  forecast_id: string | null;
  open: boolean;
  hypothesis: DeskHypothesis | null;
  checks: DeskCheck[];
  origins: DeskOrigins;
  falsification: DeskFalsification | null;
  decision: DeskDecision | null;
  spent: DeskCounts;
  budget: DeskCounts;
  trigger_id: string | null;
};

export type PracticePosition = {
  symbol: string;
  quantity: string;
  cost: string;
  mark: string | null;
  value: string | null;
};
export type PracticeTrade = {
  symbol: string;
  opened_at: string | null;
  closed_at: string;
  quantity: string;
  entry_price: string;
  exit_price: string;
  fees: string;
  net: string;
  exit_kind: string | null;
  forecast_id: string | null;
};
export type PracticePoint = { at: string; equity: string };
export type PracticeBook = {
  book: string;
  arm: string;
  arm_name: string;
  level: string;
  fee_bps: string;
  slippage_bps: string;
  adverse: boolean;
  opened_at: string;
  last_at: string;
  opening_equity: string;
  equity: string;
  peak: string;
  cash: string;
  return_share: string;
  drawdown: string;
  mode: string;
  ruined: boolean;
  fees: string;
  buys: number;
  sells: number;
  marks: number;
  positions: PracticePosition[];
  trades: PracticeTrade[];
  curve: PracticePoint[];
};

export type SweepRow = {
  module: string;
  checks: string;
  places: number;
  noticed: number;
  unbuilt: number;
  raised: number;
  clock: number;
  survived: number;
  current: boolean | null;
  reason: string | null;
};
export type SweepSummary = {
  modules: number;
  places: number;
  noticed: number;
  unbuilt: number;
  raised: number;
  clock: number;
  survived: number;
  stale: number;
  unchecked: number;
};
export type Sweeps = { summary: SweepSummary; rows: SweepRow[] };

export type CoverageCount = { verdict: string; count: number };
export type CoverageGap = {
  number: string;
  title: string;
  verdict: string;
  reason: string;
};
export type Coverage = {
  rubric: CoverageCount[];
  rubric_total: number;
  contract: CoverageCount[];
  contract_total: number;
  gaps: CoverageGap[];
};

export interface SignalsEcho {
  uri: string;
  author: string;
  echoes: string;
  reason: string;
}

export interface SignalsToken {
  symbol: string;
  voices: {
    mentions: number;
    authors: number;
    independent: number;
    up: number;
    down: number;
    neutral: number;
    local_mentions: number;
    echoes: SignalsEcho[];
    posts: string[];
  };
  whales: {
    wallets: number;
    holding: number;
    long: number;
    short: number;
    gross_long: string;
    gross_short: string;
    net: string;
    top_share: string;
  };
}

export interface Signals {
  format: number;
  collected_at: string;
  took_ms: number;
  window_hours: number;
  accounts: {
    actor: string;
    posts: number;
    state: string;
    reason: string | null;
  }[];
  wallets: {
    address: string;
    state: string;
    reason: string | null;
    positions: number;
  }[];
  tokens: SignalsToken[];
  not_known: string[];
}

export interface ReviewObservation {
  code: string;
  text: string;
}

export interface Review {
  forecast_id: string;
  cycle_id: string;
  symbol: string;
  hours: number;
  arm_name: string;
  chance: string;
  outcome: string;
  move: string;
  agreed: string;
  observations: ReviewObservation[];
}

export interface Reviews {
  reviews: Review[];
  ledger: {
    reviewed: number;
    not_reviewed: number;
    proposed: number;
    tested: number;
    promoted: number;
    rejected: number;
    n_support: number;
    statement: string;
    patterns: {
      arm_name: string;
      code: string;
      support: number;
      against_the_call: number;
      change: Record<string, string> | null;
      status: string;
    }[];
  };
}

/** The Hypothesis Laboratory's document (`lab.json`): its versions in words, previews on stored candles and what its rule did since. */
export interface LabAssumption {
  id: string;
  kind: string;
  text: string;
}

export interface LabPreview {
  label: string;
  venue: string;
  instrument: string;
  bars: number;
  true_bars: number;
  false_bars: number;
  undecided_bars: number;
  last_true_at_ms: number | null;
  first_at_ms: number;
  last_at_ms: number;
  invalidation: string;
  invalidation_reason: string | null;
}

export interface LabTrade {
  status: string;
  direction: string;
  synthetic: boolean;
  signal_at_ms: number;
  entry_at_ms: number | null;
  entry_price: string | null;
  exit_reason: string | null;
  exit_signal_at_ms: number | null;
  exit_at_ms: number | null;
  exit_price: string | null;
  amount: string | null;
  net_return: string | null;
}

export interface LabForward {
  run: boolean;
  reason: string | null;
  level: string | null;
  as_of_ms: number | null;
  trades: LabTrade[];
  closed: number;
  open: number;
  pending: number;
  missing: number;
  won: number;
  lost: number;
  mean_net_return: string | null;
  note: string;
}

export interface LabEntry {
  version: string;
  statement: string;
  symbol: string;
  direction: string;
  horizon_h: number;
  max_weight: string;
  frozen_at_ms: number | null;
  by: string;
  parent: string | null;
  enters_when: string;
  invalid_when: string;
  assumptions: LabAssumption[];
  preview: LabPreview | null;
  preview_reason: string | null;
  forward: LabForward;
}

export interface LabDocument {
  format: number;
  versions: LabEntry[];
}

/** One development on Today: what happened, whether it was checked, and where the check stands (``today_items``). */
export type TodayItem = {
  item_id: string;
  state: string;
  symbol: string;
  route: string;
  headline: string;
  first_seen: string | null;
  reason: string | null;
  link: string | null;
  unknown: string;
  label: string | null;
  contradicting: number;
  origins_read: number;
  score: number;
  reasons: string[];
};

/** Arm R (`ryo.json`): RYO's own verdict on each token it read, scored over the same 24 hours as the agents' calls. */
export type RyoOutcome = {
  cycle_id: string;
  candidate_id: string;
  symbol: string;
  call: string;
  reading: "UP" | "DOWN" | "NOT_LONG" | "NONE" | "UNREAD";
  state: "Settled" | "Waiting" | "Not settled" | "No window";
  direction: "ROSE" | "FELL" | "TIE" | null;
  hit: boolean | null;
  reason: string | null;
  plan:
    | "TARGET"
    | "STOP"
    | "TIMEOUT"
    | "AMBIGUOUS"
    | "ALREADY_PAST"
    | "NOT_SETTLED"
    | "WAITING"
    | "NO_PLAN";
};

export type RyoRecord = {
  table_version: string;
  table: [string, string, string[]][];
  summary: {
    reads: number;
    by_reading: [string, number][];
    states: [string, number][];
    scored: number;
    hits: number;
    rose: number;
    unread_words: string[];
    plans: [string, number][];
  };
  outcomes: RyoOutcome[];
  cycle_times: [string, string][];
};

/** Pairs of forecasters compared on the same opportunities (`comparisons.json`). */
export type ComparisonLine = {
  first: string;
  second: string;
  hours: number;
  shared: number;
  mean: string | null;
  lower: string | null;
  upper: string | null;
  state: string;
  since: number | null;
};

export type Comparisons = {
  alpha: string;
  v_opt: string;
  min_shown: number;
  lines: ComparisonLine[];
};

/** A change that affects a decision (`decision_alerts`): never a price alone. */
export type DecisionAlert = {
  kind: string; // INVALIDATED, THESIS_CHANGED, RISK_STEP or SETTLED
  at: string;
  text: string;
  forecast_id: string | null;
  symbol: string | null;
  arm: string | null;
  book: string | null;
};

/** Whether each source made the rules arm's settled calls better (`source_value`): exploratory. */
export type SourceLine = {
  arm: string;
  arm_name: string;
  source: string;
  hours: number;
  shared: number;
  mean: string | null;
  lower: string | null;
  upper: string | null;
  state: string;
  since: number | null;
  required: number;
  not_evaluated: number;
  differs: number;
};
export type SourceValue = { alpha: string; v_opt: string; min_shown: number; lines: SourceLine[] };
