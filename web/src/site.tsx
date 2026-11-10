import {
  Component,
  Suspense,
  lazy,
  useCallback,
  useEffect,
  useRef,
  useState,
  type MouseEvent,
  type ReactNode,
  Fragment,
} from "react";
import {
  BARRIER_NOTE,
  CHANCE_NOTE,
  costWords,
  defaultCycle,
  driftWords,
  emptyCycleWords,
  evidenceAgeWords,
  inProgressLine,
  sourceLine,
  NOTHING_CHANGED,
  PASS_DOES_NOT_SHOW,
  PASS_NEEDS_HONEST,
  PASS_SHOWS,
  ageWords,
  allSettled,
  answerWords,
  basisWords,
  boundaryFor,
  changed,
  codeSpans,
  count,
  digestWords,
  findings,
  forgeryWords,
  hasChanges,
  horizon,
  ledgerFailure,
  nextLine,
  oldLine,
  partialLine,
  invalidationWords,
  settlementWords,
  settling,
  share,
  score,
  skill,
  stepWords,
  summary,
  when,
  whenShort,
  type Finding,
  type TimingBasis,
  sourceName,
  unreadLine,
  whyWords,
  groupChanges,
  largestDisagreement,
  readLine,
  topHeadline,
  whyItMatters,
} from "./format.ts";
import {
  LoadError,
  loadBytes,
  loadDocument,
  loadIndex,
  sha256Hex,
} from "./load.ts";
import { GROUPS } from "./nav.ts";
import { href, parse, type Route } from "./route.ts";
import { Mark, type Tokens } from "./brand.tsx";

// The pages the first screen does not need are loaded when they are opened, so that the code before interaction on Today stays inside its budget
// (UX_SPEC section 7; web/tools/budget_test.py holds it).
const QualityPage = lazy(() =>
  import("./reviewers.tsx").then((m) => ({ default: m.QualityPage })),
);
const DrillPage = lazy(() =>
  import("./reviewers.tsx").then((m) => ({ default: m.DrillPage })),
);
const BrandPage = lazy(() =>
  import("./brand.tsx").then((m) => ({ default: m.BrandPage })),
);
const SourcesSection = lazy(() =>
  import("./challenge.tsx").then((m) => ({ default: m.SourcesSection })),
);
const ChallengeView = lazy(() =>
  import("./challenge.tsx").then((m) => ({ default: m.ChallengeView })),
);
const ClaimsView = lazy(() =>
  import("./claims.tsx").then((m) => ({ default: m.ClaimsView })),
);
const DeskView = lazy(() =>
  import("./desk.tsx").then((m) => ({ default: m.DeskView })),
);
const PracticeView = lazy(() =>
  import("./practice.tsx").then((m) => ({ default: m.PracticeView })),
);
const LabView = lazy(() =>
  import("./lab.tsx").then((m) => ({ default: m.LabView })),
);
const ComparisonsSection = lazy(() =>
  import("./comparisons.tsx").then((m) => ({ default: m.ComparisonsSection })),
);
const RyoSection = lazy(() =>
  import("./ryo.tsx").then((m) => ({ default: m.RyoSection })),
);
const RyoStrip = lazy(() =>
  import("./ryo.tsx").then((m) => ({ default: m.RyoStrip })),
);
const RyoOnThisToken = lazy(() =>
  import("./ryo.tsx").then((m) => ({ default: m.RyoOnThisToken })),
);
const ReceiptHub = lazy(() =>
  import("./receipt_hub.tsx").then((m) => ({ default: m.ReceiptHub })),
);
const RecordPage = lazy(() =>
  import("./record.tsx").then((m) => ({ default: m.RecordPage })),
);
const CallsBy = lazy(() =>
  import("./calls_by.tsx").then((m) => ({ default: m.CallsBy })),
);
const AlertsPanel = lazy(() =>
  import("./alerts.tsx").then((m) => ({ default: m.AlertsPanel })),
);
const ReceiptLead = lazy(() =>
  import("./receipt_hub.tsx").then((m) => ({ default: m.ReceiptLead })),
);
const JudgesView = lazy(() =>
  import("./judges.tsx").then((m) => ({ default: m.JudgesView })),
);
const LiveClaim = lazy(() =>
  import("./claims.tsx").then((m) => ({ default: m.LiveClaim })),
);
const FeedView = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.FeedView })),
);
const VoicesView = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.VoicesView })),
);
const Developments = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.Developments })),
);
const UncheckedRow = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.UncheckedRow })),
);
const ChallengeThis = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.ChallengeThis })),
);
const ReviewBlock = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.ReviewBlock })),
);
const LearningLedger = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.LearningLedger })),
);
const ChallengeBlock = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.ChallengeBlock })),
);
const ShareCard = lazy(() =>
  import("./social.tsx").then((m) => ({ default: m.ShareCard })),
);
import type {
  SourceValue,
  DecisionAlert,
  ArmRecord,
  Comparisons,
  RyoRecord,
  Boundary,
  BundleEntry,
  ChallengeRow,
  Claim,
  ClaimCheck,
  Coverage,
  CycleSummary,
  DeskRun,
  Drill,
  LabDocument,
  ForgeryDoc,
  PracticeBook,
  QualityRow,
  Sweeps,
  Receipt,
  Row,
  SiteIndex,
  Today,
} from "./types.ts";

export const DATA = "./data";

// --- loading states -------------------------------------------------------------------------------------------------------------------
type Loaded<T> =
  | { state: "loading" }
  | { state: "failed"; error: LoadError }
  | { state: "ready"; value: T };

function useLoad<T>(
  load: () => Promise<T>,
  key: string,
): [Loaded<T>, () => void] {
  const [result, setResult] = useState<{ key: string; loaded: Loaded<T> }>({
    key,
    loaded: { state: "loading" },
  });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setResult({ key, loaded: { state: "loading" } });
    load().then(
      (value) => live && setResult({ key, loaded: { state: "ready", value } }),
      (error: unknown) =>
        live &&
        setResult({
          key,
          loaded: {
            state: "failed",
            error:
              error instanceof LoadError
                ? error
                : new LoadError("unreadable", key, String(error)),
          },
        }),
    );
    return () => {
      live = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, attempt]);
  // what was loaded for another key (the receipt before this one) is never drawn under this one, not even once: the keyboard would go to its heading
  return [
    result.key === key ? result.loaded : { state: "loading" },
    useCallback(() => setAttempt((n) => n + 1), []),
  ];
}

export function Waiting({ what }: { what: string }): ReactNode {
  return (
    <p role="status" className="muted">
      Loading {what}…
    </p>
  );
}

function Failed({
  error,
  retry,
}: {
  error: LoadError;
  retry: () => void;
}): ReactNode {
  const words = {
    missing: "A document of this site could not be fetched.",
    changed:
      "A document does not match its digest in the index, so it is not shown.",
    unreadable: "A document of this site could not be read.",
  } as const;
  return (
    <div className="notice error" role="alert">
      <p>
        <strong>{words[error.kind]}</strong>
      </p>
      <p className="mono">{error.message}</p>
      <p>
        <button type="button" onClick={retry}>
          Try again
        </button>
      </p>
    </div>
  );
}

export function Doc<T>({
  index,
  path,
  what,
  children,
}: {
  index: SiteIndex;
  path: string;
  what: string;
  children: (value: T) => ReactNode;
}): ReactNode {
  const [result, retry] = useLoad<T>(
    () => loadDocument<T>(DATA, index, path),
    path,
  );
  if (result.state === "loading") return <Waiting what={what} />;
  if (result.state === "failed")
    return <Failed error={result.error} retry={retry} />;
  return <>{children(result.value)}</>;
}

// --- small parts ----------------------------------------------------------------------------------------------------------------------
export function Time({ at }: { at: string }): ReactNode {
  return <time dateTime={at}>{when(at)}</time>;
}

export function StatePill({ state }: { state: string }): ReactNode {
  const kind =
    state === "Settled" || state === "Held"
      ? "ok"
      : state === "Failed" ||
          state === "Too late" ||
          state === "Void" ||
          state === "Not settled" ||
          state === "Did not hold"
        ? "warn"
        : "";
  return (
    <span className={`pill ${kind}`.trim()}>
      {state === "Abstained" ? "No call" : state}
    </span>
  );
}

function ModeNotice({ mode }: { mode: string }): ReactNode {
  const exploratory = mode.startsWith("Exploratory");
  return (
    <p className="mode">
      <strong>{mode}.</strong>{" "}
      {exploratory
        ? "Practice only; nothing is ranked."
        : "Made under a protocol fixed before the period began. Practice, not advice."}
    </p>
  );
}

function callText(row: Row): string {
  if (row.chance !== null) return row.chance;
  return row.reason ?? "–";
}

/** A table that may scroll sideways on a small screen: a labelled region a keyboard can reach and scroll. */
function Scroll({
  label,
  children,
}: {
  label: string;
  children: ReactNode;
}): ReactNode {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

function RowsTable({
  rows,
  caption,
  dated = false,
}: {
  rows: Row[];
  caption: string;
  dated?: boolean; // the rows come from several cycles: each says when it started, on screen and in its link's name
}): ReactNode {
  // the columns a stranger needs first (the asset, the call, what became of it) lead, so a phone's first screen holds them; the rest follow
  return (
    <Scroll label={caption}>
      <table className="stack">
        <caption>{caption}</caption>
        <thead>
          <tr>
            <th scope="col">Asset</th>
            <th scope="col">Call</th>
            <th scope="col">State</th>
            <th scope="col">Horizon</th>
            <th scope="col">Agent</th>
            <th scope="col">Kind</th>
            {dated ? <th scope="col">Started</th> : null}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.forecast_id}>
              <th scope="row">{row.symbol}</th>
              <td className="nowrap" data-label="Call">
                {row.chance !== null ? (
                  <a href={href({ page: "receipt", id: row.forecast_id })}>
                    <span className="visually-hidden">
                      {row.symbol}, {horizon(row.hours)}, {row.arm_name}
                      {dated && (row.origin ?? row.due) !== null
                        ? `, ${whenShort(row.origin ?? row.due ?? "")}, ${row.state.toLowerCase()}`
                        : ""}
                      :{" "}
                    </span>
                    {callText(row)}
                  </a>
                ) : (
                  callText(row)
                )}
                {row.result !== null ? (
                  <span className="muted">
                    <span aria-hidden="true"> → </span>
                    <span className="visually-hidden"> Result: </span>
                    {row.result}
                  </span>
                ) : null}
              </td>
              <td data-label="State">
                <StatePill state={row.state} />
              </td>
              <td data-label="Horizon">{horizon(row.hours)}</td>
              <td data-label="Agent">{row.arm_name}</td>
              <td data-label="Kind">
                {row.contract === "D" ? "Up or down" : "Barrier"}
              </td>
              {dated ? (
                <td data-label="Started">
                  {row.origin !== null ? whenShort(row.origin) : "Not yet"}
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </Scroll>
  );
}

// --- pages ----------------------------------------------------------------------------------------------------------------------------
/** The latest cycle's bundle, checked here and now: the same check as on the Check a record page, with the root this site publishes. */
function NewestCheck({
  index,
  cycleId,
}: {
  index: SiteIndex;
  cycleId: string;
}): ReactNode {
  return (
    <Doc<BundleEntry[]> index={index} path="bundles.json" what="the bundles">
      {(bundles) => {
        const bundle = bundles.find((b) => b.cycle_id === cycleId);
        if (bundle === undefined) return null;
        return (
          <section aria-labelledby="newest-check">
            <h2 id="newest-check">Check the newest record</h2>
            <p>
              Check this cycle's record in your browser, without taking our word
              for it.{" "}
              <a href={href({ page: "verify" })}>
                What the check shows and does not show
              </a>
              .
            </p>
            <BundleCheck index={index} bundle={bundle} own={null} />
          </section>
        );
      }}
    </Doc>
  );
}

function TodayPage({ index }: { index: SiteIndex }): ReactNode {
  return (
    <Doc<Today> index={index} path="today.json" what="today">
      {(today) => (
        // one boundary for the whole page: the parts loaded on demand arrive with the rest, so nothing below them moves (layout shift)
        <Suspense fallback={<Waiting what="today" />}>
          {today.latest === null ? (
            <>
              <h1>No cycle has run yet.</h1>
              {hasChanges(today) ? (
                <p className="lead">{today.product}</p>
              ) : null}
            </>
          ) : hasChanges(today) ? (
            // the product sentence stays in the page title and in "How this works"; the first screen's words go to the day's news
            <h1>{topHeadline(today.changes) ?? today.product}</h1>
          ) : (
            <h1>{summary(today.latest.rows_by_state)}</h1>
          )}
          <p className="lead">
            {(today.changes.length > 0 &&
              whyItMatters(today.changes[0]?.kind ?? "")) || (
              <a href={href({ page: "verify" })}>
                Check any call's record yourself.
              </a>
            )}
          </p>
          <ModeNotice mode={today.mode} />
          <HowItWorks />
          {today.latest === null ? (
            <p>
              The league records its first cycle when it commits it. Until then
              there is nothing to show, and nothing here is made up.
            </p>
          ) : null}
          {today.latest !== null ? <TopChanges today={today} /> : null}
          <UncheckedRow index={index} />
          <Developments index={index} />
          {today.latest !== null &&
          hasChanges(today) &&
          summary(today.latest.rows_by_state, true) !== null ? (
            <p>{summary(today.latest.rows_by_state, true)}</p>
          ) : null}
          {inProgressLine(today.in_progress) !== null ? (
            <p>{inProgressLine(today.in_progress)}</p>
          ) : null}
          {today.latest !== null &&
          sourceLine(today.latest.sources) !== null ? (
            <p>{sourceLine(today.latest.sources)}</p>
          ) : null}
          {today.latest !== null &&
          partialLine(today.latest.rows_by_state) !== null ? (
            <p className="notice">
              {partialLine(today.latest.rows_by_state)}{" "}
              <a href={href({ page: "calls", cycle: null })}>See which</a>.
            </p>
          ) : null}
          {today.latest !== null ? (
            <Changes today={today} index={index} />
          ) : null}
          <Recent rows={today.settled_lately} />
          {today.latest !== null ? <Disagreement rows={today.rows} /> : null}
          {index.files["ryo.json"] !== undefined ? (
            <Doc<RyoRecord> index={index} path="ryo.json" what="RYO’s read">
              {(record) => (
                <Suspense fallback={null}>
                  <RyoStrip record={record} />
                </Suspense>
              )}
            </Doc>
          ) : null}
          {index.files["alerts.json"] !== undefined ? (
            <Doc<DecisionAlert[]>
              index={index}
              path="alerts.json"
              what="the alerts"
            >
              {(alerts) => (
                <Suspense fallback={null}>
                  <AlertsPanel alerts={alerts} generated={index.generated_at} />
                </Suspense>
              )}
            </Doc>
          ) : null}
          {today.latest !== null ? (
            <p>
              <a
                href={href({ page: "calls", cycle: null })}
              >{`All ${count(today.latest.slots, "call")}`}</a>
            </p>
          ) : null}
          {today.latest !== null &&
          index.files["bundles.json"] !== undefined ? (
            <NewestCheck index={index} cycleId={today.latest.cycle_id} />
          ) : null}
        </Suspense>
      )}
    </Doc>
  );
}

/** What the product does, for a person who has just arrived: four steps and the few words the pages use. Closed by default, so the first
 * screen keeps its word budget; a link to each place to start. */
function HowItWorks(): ReactNode {
  return (
    <details className="how">
      <summary>How this works</summary>
      <ol>
        <li>
          Each cycle, every agent reads real exchange prices and states a
          chance, such as 51% up over one hour. Every call is practice: nothing
          is traded.
        </li>
        <li>
          Before the hour starts, two independent time-stamp authorities sign
          the whole batch, so no call can be rewritten afterwards.
        </li>
        <li>
          When the hour is over, each call is settled against real candles from
          two exchanges and added to the track record.
        </li>
        <li>
          Anyone can check a cycle's record in a browser without trusting this
          site.
        </li>
      </ol>
      <p>
        Words used here. A <strong>call</strong> is one agent's stated chance
        for one token over one horizon. A <strong>cycle</strong> is one round of
        collecting, deciding and committing. <strong>Rules</strong> is the
        fixed-rule agent; any other agent uses a language model. A{" "}
        <strong>receipt</strong> is one call with what it read. An{" "}
        <strong>origin</strong> is where a piece of information first came from:
        two reports of one announcement count once.
      </p>
      <p>
        Start with <a href={href({ page: "calls", cycle: null })}>Calls</a>,{" "}
        <a href={href({ page: "verify" })}>Check a record</a>,{" "}
        <a href={href({ page: "desk" })}>The Desk</a> or{" "}
        <a href={href({ page: "judges" })}>For judges</a>.
      </p>
    </details>
  );
}

/** The first card: the top changes since the last cycle, each with its asset, a sentence and one thing to do, in the order printed below
 * them. An export that does not carry them says so; it never prints a blank. */
function TopChanges({ today }: { today: Today }): ReactNode {
  if (!hasChanges(today)) {
    return (
      <p className="notice">
        These documents were exported by an older version, which did not list
        what changed. Export the site again.
      </p>
    );
  }
  return (
    <section aria-labelledby="top-changes">
      <h2 id="top-changes">What changed</h2>
      <details className="how">
        <summary>How these are ordered</summary>
        <p className="muted rule">{today.rule}</p>
      </details>
      {today.changes.length === 0 ? (
        <p>{NOTHING_CHANGED}</p>
      ) : (
        <ol className="changes">
          {groupChanges(today.changes).map(({ first: c, members }) => (
            <li key={c.forecast_id}>
              <p className="change-head">
                <strong>{members.map((m) => m.symbol).join(", ")}</strong>,{" "}
                {horizon(c.hours)}, {c.arm_name}
              </p>
              <p>{c.sentence}</p>
              <p>
                {members.map((m, i) => (
                  <span key={m.forecast_id}>
                    {i > 0 ? " · " : null}
                    <a href={href({ page: "receipt", id: m.forecast_id })}>
                      {members.length > 1 ? m.symbol : c.action}
                      <span className="visually-hidden">
                        {members.length > 1
                          ? `: ${c.action.toLowerCase()}, ${horizon(c.hours)}, ${c.arm_name}`
                          : `: ${c.symbol}, ${horizon(c.hours)}, ${c.arm_name}`}
                      </span>
                    </a>
                  </span>
                ))}
              </p>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

/** The two agents furthest apart on one call in the latest cycle, with a link to each receipt: where the evidence was read two ways. */
function Disagreement({ rows }: { rows: Row[] }): ReactNode {
  const d = largestDisagreement(rows);
  if (d === null) return null;
  return (
    <section aria-labelledby="disagree-h">
      <h2 id="disagree-h">Where the agents disagree</h2>
      <p>
        On {d.symbol} over {horizon(d.hours)}, {d.low.arm_name} says {d.low.p}%
        up and {d.high.arm_name} says {d.high.p}% up, {d.gap} points apart. Both
        read the same evidence; the outcome will say who read it better.
      </p>
      <p>
        <a href={href({ page: "receipt", id: d.low.forecast_id })}>
          {d.low.arm_name}'s receipt
        </a>
        <span aria-hidden="true"> · </span>
        <a href={href({ page: "receipt", id: d.high.forecast_id })}>
          {d.high.arm_name}'s receipt
        </a>
      </p>
    </section>
  );
}

function Changes({
  today,
  index,
}: {
  today: Today;
  index: SiteIndex;
}): ReactNode {
  const line = changed(today.since, today.settled_since);
  return (
    <>
      {line !== null ? <p>{line}</p> : null}
      <Skill index={index} />
    </>
  );
}

/** What the scores say so far, from the record; read on its own page too. */
function Skill({ index }: { index: SiteIndex }): ReactNode {
  return (
    <Doc<ArmRecord[]> index={index} path="record.json" what="the scores">
      {(arms) => (
        <>
          {arms
            .filter((a) => a.contract === "D")
            .map((a) => {
              const beside =
                a.all_brier_n === undefined
                  ? null
                  : allSettled(a.all_mean_brier, a.all_brier_n, a.missed_c3);
              return (
                <p key={a.arm}>
                  {skill(a.arm_name, a.mean_brier, a.brier_n)}
                  {beside !== null ? ` ${beside}` : null}
                </p>
              );
            })}
        </>
      )}
    </Doc>
  );
}

/** The last few results as plain lines, each a link to its receipt. */
function Recent({ rows }: { rows: Row[] }): ReactNode {
  const last = rows.slice(-2).reverse();
  if (last.length === 0) return null;
  return (
    <>
      <h2>Results</h2>
      <ul>
        {last.map((r) => (
          <li key={r.forecast_id}>
            <a href={href({ page: "receipt", id: r.forecast_id })}>
              {r.symbol}, {horizon(r.hours)}, {r.arm_name}: {r.chance}
            </a>
            <span aria-hidden="true"> → </span>
            <span className="visually-hidden"> Result: </span>
            {r.result}
          </li>
        ))}
      </ul>
    </>
  );
}

function Settling({ rows }: { rows: Row[] }): ReactNode {
  const line = nextLine(settling(rows));
  return line === null ? null : <p>{line}</p>;
}

/** The table's caption: which cycle, when it was committed, how many calls. */
function cycleCaption(cycle: CycleSummary, latest: boolean): string {
  const when_ =
    cycle.committed_at === null
      ? `planned for ${when(cycle.planned_for)}, not committed`
      : `committed ${when(cycle.committed_at)}`;
  return `${latest ? "Latest cycle" : "Cycle"}, ${when_}: ${count(cycle.slots, "call")}`;
}

function CallsPage({
  index,
  cycle,
}: {
  index: SiteIndex;
  cycle: string | null;
}): ReactNode {
  return (
    <Doc<CycleSummary[]> index={index} path="cycles.json" what="the cycles">
      {(cycles) => (
        <Doc<Row[]> index={index} path="rows.json" what="the calls">
          {(rows) => {
            const chosen = cycle ?? defaultCycle(cycles);
            const mine = rows.filter((r) => r.cycle_id === chosen);
            const summary_ = cycles.find((c) => c.cycle_id === chosen);
            return (
              <>
                <p className="kicker">Calls</p>
                <h1>
                  {chosen === null
                    ? "No cycle has run yet."
                    : cycleHeading(summary_)}
                </h1>
                {cycles.length > 1 ? (
                  <nav aria-label="Cycles">
                    <ul className="chips">
                      {[...cycles].reverse().map((c) => (
                        <li key={c.cycle_id}>
                          <a
                            className="pill"
                            href={href({ page: "calls", cycle: c.cycle_id })}
                            aria-current={
                              c.cycle_id === chosen ? "page" : undefined
                            }
                          >
                            {whenShort(c.planned_for)}
                          </a>
                        </li>
                      ))}
                    </ul>
                  </nav>
                ) : null}
                <Settling rows={mine} />
                {chosen !== null && mine.length === 0 ? (
                  <p>{emptyCycleWords(summary_?.state)}</p>
                ) : null}
                {mine.length > 0 ? (
                  <p className="muted">
                    {mine.some((r) => r.contract === "B")
                      ? `${CHANCE_NOTE} ${BARRIER_NOTE}`
                      : CHANCE_NOTE}
                  </p>
                ) : null}
                {mine.length > 0 ? (
                  <RowsTable
                    rows={mine}
                    caption={
                      summary_ === undefined
                        ? "Calls"
                        : cycleCaption(
                            summary_,
                            summary_.cycle_id === defaultCycle(cycles),
                          )
                    }
                  />
                ) : null}
              </>
            );
          }}
        </Doc>
      )}
    </Doc>
  );
}

function cycleHeading(cycle: CycleSummary | undefined): string {
  return cycle === undefined
    ? "That cycle is not in this site."
    : `The cycle of ${whenShort(cycle.planned_for)}`;
}
/** RYO's own verdict on the token of a 24-hour call, from the same cycle, when the site holds RYO's reads. */
function RyoOnReceipt({
  index,
  row,
}: {
  index: SiteIndex;
  row: Row;
}): ReactNode {
  if (index.files["ryo.json"] === undefined) return null;
  return (
    <Doc<RyoRecord> index={index} path="ryo.json" what="RYO’s read">
      {(record) => (
        <Suspense fallback={null}>
          <RyoOnThisToken
            record={record}
            cycleId={row.cycle_id}
            symbol={row.symbol}
          />
        </Suspense>
      )}
    </Doc>
  );
}

function resultSentence(result: string): string {
  switch (result) {
    case "Rose":
      return "The price rose.";
    case "Fell":
      return "The price fell.";
    case "Target first":
      return "The target was reached first.";
    case "Stop first":
      return "The stop was reached first.";
    default:
      return "Neither level was reached.";
  }
}

/** Why a call of the rules agent was what it was: the state it saw and how far the data was from a different call. Nothing is
 * shown for a call that has no such measure (a model arm, a barrier call, or a site made without it). */
function WhyThisCall({
  index,
  row,
}: {
  index: SiteIndex;
  row: Row;
}): ReactNode {
  if (index.files["boundaries.json"] === undefined) return null;
  return (
    <Doc<Boundary[]> index={index} path="boundaries.json" what="the boundaries">
      {(boundaries) => {
        const found = boundaryFor(boundaries, row);
        if (found === null) return null;
        return (
          <>
            <h2>Why this call</h2>
            <ul>
              {whyWords(found).map((sentence) => (
                <li key={sentence}>{sentence}</li>
              ))}
            </ul>
            <p>
              <a href={href({ page: "challenge" })}>
                See what else would change this call
              </a>
            </p>
          </>
        );
      }}
    </Doc>
  );
}

function ReceiptPage({
  index,
  id,
}: {
  index: SiteIndex;
  id: string;
}): ReactNode {
  return (
    <Doc<Receipt> index={index} path={`receipts/${id}.json`} what="the receipt">
      {(r) => {
        const row = r.row;
        return (
          <>
            <p className="kicker">Receipt</p>
            <h1>
              {row.symbol}, {horizon(row.hours)}: {row.arm_name}
            </h1>
            <p className="lead">
              <Suspense fallback={row.chance ?? "No call"}>
                <ReceiptLead receipt={r} />
              </Suspense>
            </p>
            <p>
              <StatePill state={row.state} />
              {row.result !== null ? (
                <>
                  <span className="visually-hidden">: </span>{" "}
                  {resultSentence(row.result)}
                </>
              ) : null}
            </p>
            {row.state === "Open" &&
            row.due !== undefined &&
            row.due !== null ? (
              Date.parse(row.due) > Date.parse(index.generated_at) ? (
                <p>
                  Settles at <Time at={row.due} /> at the earliest, after these
                  pages were made; a later export shows the outcome.
                </p>
              ) : (
                <p>
                  Due to settle from <Time at={row.due} />; it had not settled
                  when these pages were made.
                </p>
              )
            ) : null}
            {row.contract === "B" ? (
              <p className="muted">{BARRIER_NOTE}</p>
            ) : null}
            <p>{readLine(r.evidence, r.unread)}</p>
            <h2>What would prove it wrong</h2>
            <ul>
              {r.invalidations.map((x) => (
                <li key={x}>{invalidationWords(x)}</li>
              ))}
            </ul>
            <h2>When it was fixed</h2>
            <dl className="facts">
              <dt>In words</dt>
              <dd>{row.trust}</dd>
              <dt>Committed</dt>
              <dd>
                {r.committed_at === null ? "–" : <Time at={r.committed_at} />}
              </dd>
              <dt>Evidence as of</dt>
              <dd>
                <Time at={r.as_of} />
              </dd>
              <dt>Started</dt>
              <dd>
                {row.origin === null ? "Not yet" : <Time at={row.origin} />}
              </dd>
              <dt>Age of the evidence at the start</dt>
              <dd>{evidenceAgeWords(r.evidence_age_s ?? null)}</dd>
              <dt>Price move since the evidence</dt>
              <dd>{driftWords(r.gap_drift_atr ?? null)}</dd>
              <dt>Settled by</dt>
              <dd>{r.oracles}</dd>
            </dl>
            {row.contract === "B" ? null : (
              // the boundaries are those of the up-or-down rule; a barrier call is read from another mapping, so they would explain the wrong call
              <WhyThisCall index={index} row={row} />
            )}
            <Suspense fallback={null}>
              <ReceiptHub
                links={r.links}
                row={row}
                voices={index.files["signals.json"] !== undefined}
              />
            </Suspense>
            {row.contract === "D" && row.hours === 24 ? (
              <RyoOnReceipt index={index} row={row} />
            ) : null}
            <Suspense fallback={null}>
              <ChallengeBlock run={r.run} />
              <ReviewBlock index={index} id={row.forecast_id} />
              <ChallengeThis id={row.forecast_id} />
              <ShareCard r={r} />
            </Suspense>
            <h2>What it read</h2>
            <p className="muted">
              Exchange data is held for private use, so each item is listed by
              its digest only.
            </p>
            <Scroll label="Evidence behind this call">
              <table>
                <caption>Evidence behind this call</caption>
                <thead>
                  <tr>
                    <th scope="col">Source</th>
                    <th scope="col">Kind</th>
                    <th scope="col">Time</th>
                    <th scope="col">Digest</th>
                  </tr>
                </thead>
                <tbody>
                  {r.evidence.map((e) => (
                    <tr key={e.evidence_id}>
                      <th scope="row">{sourceName(e.source)}</th>
                      <td>
                        {e.kind.toLowerCase()}, {e.data_mode}
                      </td>
                      <td>
                        {e.event_time === null ? (
                          "–"
                        ) : (
                          <Time at={e.event_time} />
                        )}
                      </td>
                      <td className="mono">
                        {digestWords(e.raw_sha256, e.kind, e.shown)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </Scroll>
            {r.unread.length > 0 ? (
              <>
                <h3>Asked for and not read</h3>
                <ul className="findings">
                  {r.unread.map((u) => (
                    <li key={`${u.source}-${u.reason_code}-${u.interval_s}`}>
                      {unreadLine(u)}
                    </li>
                  ))}
                </ul>
              </>
            ) : null}
            {r.run !== null ? (
              <>
                <h2>What the model did</h2>
                <dl className="facts">
                  <dt>Answer</dt>
                  <dd>{answerWords(r.run.verdict, r.run.reason)}</dd>
                  <dt>Said</dt>
                  <dd>
                    {r.run.proposed_chance ?? "–"} up; kept{" "}
                    {r.run.final_chance ?? "–"}
                    {r.run.gate !== "NONE" ? " after a safety rule" : ""}
                  </dd>
                  <dt>Cost</dt>
                  <dd>
                    {costWords(r.run.calls, r.run.tokens, r.run.cutoff_s)}
                  </dd>
                </dl>
              </>
            ) : null}
            <h2>What happened</h2>
            <ol className="timeline">
              {r.timeline.map((t, i) => (
                <li key={`${t.state}${t.at}${i}`}>
                  {stepWords(t.state)}{" "}
                  <span className="muted">
                    · <Time at={t.at} />
                    {t.reason !== null ? ` · ${t.reason}` : ""}
                  </span>
                </li>
              ))}
            </ol>
            {r.settlement !== null ? (
              <dl className="facts">
                <dt>Settlement</dt>
                <dd>{settlementWords(r.settlement.state, row.result)}</dd>
                <dt>Start price</dt>
                <dd className="mono">{r.settlement.reference_price ?? "–"}</dd>
                <dt>End price</dt>
                <dd className="mono">{r.settlement.outcome_price ?? "–"}</dd>
              </dl>
            ) : null}
            <h2>Check it</h2>
            <p>
              Anyone can check this record without trusting this page.{" "}
              <a href={href({ page: "verify" })}>How to check it yourself</a>.
            </p>
            <details className="for-checking">
              <summary>For checking</summary>
              <dl className="facts">
                <dt>Commitment level</dt>
                <dd className="mono">{row.level ?? "none"}</dd>
                <dt>Digest of the record</dt>
                <dd className="mono">{r.forecast_digest}</dd>
                {r.settlement !== null ? (
                  <>
                    <dt>Settlement rule</dt>
                    <dd className="mono">
                      {r.settlement.rule_version}
                      {r.settlement.flags.length > 0
                        ? `, ${r.settlement.flags.join(", ")}`
                        : ""}
                    </dd>
                  </>
                ) : null}
              </dl>
            </details>
          </>
        );
      }}
    </Doc>
  );
}

function VerifyPage({ index }: { index: SiteIndex }): ReactNode {
  const [own, setOwn] = useState<{
    name: string;
    bytes: Uint8Array<ArrayBuffer>;
    sha256: string;
  } | null>(null);
  const [rootProblem, setRootProblem] = useState<string | null>(null);
  const chooseRoot = async (file: File | undefined): Promise<void> => {
    setRootProblem(null);
    if (file === undefined) return setOwn(null);
    if (file.size > 1_000_000)
      return setRootProblem("That file is too large to be a trusted root.");
    const bytes = new Uint8Array(await file.arrayBuffer());
    setOwn({ name: file.name, bytes, sha256: await sha256Hex(bytes) });
  };
  return (
    <Doc<BundleEntry[]> index={index} path="bundles.json" what="the bundles">
      {(bundles) => (
        <>
          <p className="kicker">Check a record</p>
          <h1>Check a cycle in your browser.</h1>
          <p className="lead">
            Each committed cycle has a bundle: its calls, the evidence they cite
            and the time-stamps that fixed them. This page checks one here, in
            your browser, with the same verifier that runs on the command line.
          </p>
          <h2>Bundles</h2>
          {bundles.length === 0 ? (
            <p className="muted">No cycle has been committed yet.</p>
          ) : (
            <ul className="bundles">
              {bundles.map((b) => (
                <li key={b.cycle_id}>
                  <BundleCheck index={index} bundle={b} own={own} />
                </li>
              ))}
            </ul>
          )}
          {index.files["forgery.json"] !== undefined ? (
            <ForgedCopy index={index} own={own} />
          ) : null}
          <h2>Which trusted root</h2>
          <p>
            A check is only as good as the root it starts from. By default this
            page uses the root this site publishes, so a pass shows the files
            are intact and consistent; it cannot show they come from someone you
            trust, because the same place served both. For that, pick a
            trusted-root file you got from somewhere else.
          </p>
          <p>
            <label>
              Your trusted-root file{" "}
              <input
                type="file"
                accept=".json,application/json"
                onChange={(e) => void chooseRoot(e.target.files?.[0])}
              />
            </label>
          </p>
          {own !== null ? (
            <p>
              Using <span className="mono">{own.name}</span>, digest{" "}
              <span className="mono">{own.sha256.slice(0, 16)}</span>.
            </p>
          ) : null}
          {rootProblem !== null ? <p role="alert">{rootProblem}</p> : null}
          <h2>On the command line</h2>
          <p>
            <span className="mono">
              hoshitori-verify BUNDLE --trusted-root ROOT
            </span>{" "}
            gives the same verdict from Python, and the Node program in{" "}
            <span className="mono">verifier-js</span> from a second
            implementation.
          </p>
          <h2>What a pass shows</h2>
          <ul>
            {PASS_SHOWS.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <h2>What it does not show</h2>
          <ul>
            {PASS_DOES_NOT_SHOW.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
          <h2>Who must be honest</h2>
          <ul>
            {PASS_NEEDS_HONEST.map((s) => (
              <li key={s}>{s}</li>
            ))}
          </ul>
        </>
      )}
    </Doc>
  );
}

type Check =
  | { state: "idle" }
  | { state: "running" }
  | { state: "failed"; message: string }
  | {
      state: "done";
      findings: Finding[];
      notProven: string[];
      verdict: unknown;
      root: string;
      basis: TimingBasis | null;
    };

/** The copy of a cycle's bundle that was forged for this page, and what to read in its check: the records pass and the time-stamps do not. */
function ForgedCopy({
  index,
  own,
}: {
  index: SiteIndex;
  own: { name: string; bytes: Uint8Array<ArrayBuffer>; sha256: string } | null;
}): ReactNode {
  return (
    <Doc<ForgeryDoc> index={index} path="forgery.json" what="the forged copy">
      {(doc) => (
        <>
          <h2>A forged copy</h2>
          <p>
            Intact records are not the whole check. Someone who held the
            league's signing key could rewrite a call after the fact: change it,
            rewrite the step that commits the cycle's batch, rebuild every hash
            and sign the new log. This is such a copy of cycle{" "}
            <span className="mono">{doc.cycle_id}</span>. It was made for this
            page from the real ledger, and signed with a stand-in key (
            <span className="mono">…{doc.standin_vkey.slice(-16)}</span>) that
            is published here and used for nothing else: the league's real key
            signs no forgery. {forgeryWords(doc.edit)}
          </p>
          <p>
            The copy is checked against a root that names the stand-in key as
            the log's, as the real root names the real one. Everything else in
            that root is the real root's, including the time-stamp authorities
            it trusts.
          </p>
          <BundleCheck
            index={index}
            bundle={{
              cycle_id: doc.cycle_id,
              path: doc.path,
              files: doc.files,
              directories: doc.directories,
            }}
            own={null}
            forged
          />
          <p>
            Read the lines the same way. The records pass, and so does the
            whole-batch line, because the forger signed the new log with the key
            the root names and wrote the committing step to match. The
            time-stamps fail: they were made by outside authorities, over the
            original log's checkpoint, and no one holding only the log's key can
            make them again for the new one. A copy that fails there cannot
            count toward a score.
          </p>
          <p className="muted">
            It does not show that the time-stamp authorities themselves cannot
            be fooled, or that no other history of the log exists. Those lines
            are under "What it does not show".
          </p>
        </>
      )}
    </Doc>
  );
}

function BundleCheck({
  index,
  bundle,
  own,
  forged = false,
}: {
  index: SiteIndex;
  bundle: BundleEntry;
  own: { name: string; bytes: Uint8Array<ArrayBuffer>; sha256: string } | null;
  forged?: boolean;
}): ReactNode {
  const [check, setCheck] = useState<Check>({ state: "idle" });
  const busy = useRef(false);
  const run = async (): Promise<void> => {
    // the button keeps the keyboard while the check runs (a disabled button would drop it, WCAG 2.4.3), so a second press is ignored here
    if (busy.current) return;
    busy.current = true;
    setCheck({ state: "running" });
    try {
      const rootBytes = forged
        ? await loadBytes(DATA, index, "forged_root.json")
        : own !== null
          ? own.bytes
          : await loadBytes(DATA, index, "trusted_root.json");
      const rootName = forged
        ? "the stand-in root published for this demonstration"
        : own !== null
          ? `your file ${own.name}`
          : "the root this site publishes";
      const { checkBundle } = await import("./verify/run.ts");
      const verdict = await checkBundle(DATA, bundle, rootBytes);
      setCheck({
        state: "done",
        findings: findings(verdict),
        notProven: verdict.not_proven,
        verdict,
        root: rootName,
        basis: verdict.timing.basis,
      });
    } catch (error) {
      setCheck({
        state: "failed",
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      busy.current = false;
    }
  };
  return (
    <div className="bundle">
      <p>
        {forged ? <strong>Forged copy of </strong> : null}
        <span className="mono">{bundle.cycle_id}</span>{" "}
        <span className="muted">({count(bundle.files.length, "file")})</span>
      </p>
      <p>
        <button
          type="button"
          onClick={() => void run()}
          aria-disabled={check.state === "running" ? true : undefined}
        >
          {check.state === "running"
            ? "Checking…"
            : forged
              ? "Check the forged copy"
              : "Check it here"}
          <span className="visually-hidden">
            , {forged ? "forged copy of " : ""}cycle {bundle.cycle_id}
          </span>
        </button>
      </p>
      {/* an alert of its own, outside the polite region below, so it is read once and at once (WCAG 4.1.3) */}
      {check.state === "failed" ? (
        <p className="notice error" role="alert">
          The check could not run: {check.message}
        </p>
      ) : null}
      <div aria-live="polite">
        {check.state === "done" ? (
          <>
            <p className="muted">Checked against {check.root}.</p>
            {basisWords(check.basis) !== null ? (
              <p className="muted">{basisWords(check.basis)}</p>
            ) : null}
            <ul className="findings">
              {check.findings.map((f) => (
                <li key={f.text}>
                  <strong className={`pill ${f.ok ? "ok" : "warn"}`}>
                    {f.ok ? "Passed:" : "Failed:"}
                  </strong>{" "}
                  {f.text}
                </li>
              ))}
            </ul>
            <details>
              <summary>What this does not show</summary>
              <ul>
                {check.notProven.map((t) => (
                  <li key={t}>
                    {t[0]?.toUpperCase()}
                    {t.slice(1)}.
                  </li>
                ))}
              </ul>
            </details>
            <details>
              <summary>The full verdict</summary>
              <pre className="mono">
                {JSON.stringify(check.verdict, null, 2)}
              </pre>
            </details>
          </>
        ) : null}
      </div>
    </div>
  );
}

/** Text with `code` spans shown in the monospace face. */
export function Spans({ text }: { text: string }): ReactNode {
  return (
    <>
      {codeSpans(text).map((p, i) =>
        p.code ? <code key={i}>{p.text}</code> : <span key={i}>{p.text}</span>,
      )}
    </>
  );
}

function ChallengePage({ index }: { index: SiteIndex }): ReactNode {
  if (index.files["challenges.json"] === undefined) {
    return (
      <>
        <p className="kicker">Test a source</p>
        <h1>This site was exported without its challenges.</h1>
        <p>
          Export the site again with the current command; the challenges are
          made from the ledger.
        </p>
      </>
    );
  }
  return (
    <Doc<ChallengeRow[]>
      index={index}
      path="challenges.json"
      what="the challenges"
    >
      {(rows) => (
        <>
          {index.files["boundaries.json"] === undefined ? (
            <ChallengeView rows={rows} boundaries={[]} />
          ) : (
            <Doc<Boundary[]>
              index={index}
              path="boundaries.json"
              what="the boundaries"
            >
              {(boundaries) => (
                <ChallengeView rows={rows} boundaries={boundaries} />
              )}
            </Doc>
          )}
          {index.files["sources.json"] !== undefined ? (
            <Doc<SourceValue>
              index={index}
              path="sources.json"
              what="what each source added"
            >
              {(doc) => (
                <Suspense fallback={null}>
                  <SourcesSection doc={doc} />
                </Suspense>
              )}
            </Doc>
          ) : null}
        </>
      )}
    </Doc>
  );
}

function ClaimsPage({ index }: { index: SiteIndex }): ReactNode {
  if (index.files["claims.json"] === undefined) {
    return (
      <>
        <p className="kicker">Check a claim</p>
        <h1>This site was exported without example claims.</h1>
        <p>
          Run <span className="mono">scripts/make_claims.py claims.json</span>,
          then export again with{" "}
          <span className="mono">--claims claims.json</span>.
        </p>
        <Suspense fallback={null}>
          <LiveClaim />
        </Suspense>
      </>
    );
  }
  return (
    <Doc<Claim[]> index={index} path="claims.json" what="the claims">
      {(claims) =>
        index.files["claim_checks.json"] === undefined ? (
          <ClaimsView claims={claims} checks={[]} />
        ) : (
          <Doc<ClaimCheck[]>
            index={index}
            path="claim_checks.json"
            what="the claim checks"
          >
            {(checks) => <ClaimsView claims={claims} checks={checks} />}
          </Doc>
        )
      }
    </Doc>
  );
}

function DeskPage({
  index,
  run,
}: {
  index: SiteIndex;
  run?: string;
}): ReactNode {
  if (index.files["desk.json"] === undefined) {
    return (
      <>
        <p className="kicker">The Desk</p>
        <h1>This site was exported without any Desk run.</h1>
        <p>
          Export it again with{" "}
          <span className="mono">--desk-runs desk_runs.json</span>.
        </p>
      </>
    );
  }
  return (
    <Doc<DeskRun[]> index={index} path="desk.json" what="the Desk runs">
      {(runs) => <DeskView runs={runs} focus={run} />}
    </Doc>
  );
}

function PracticePage({
  index,
  book,
}: {
  index: SiteIndex;
  book?: string;
}): ReactNode {
  if (index.files["practice.json"] === undefined) {
    return (
      <>
        <p className="kicker">Practice trades</p>
        <h1>This site was exported without a practice portfolio.</h1>
        <p>Export it again with a version that writes the practice books.</p>
      </>
    );
  }
  return (
    <Doc<PracticeBook[]>
      index={index}
      path="practice.json"
      what="the practice portfolio"
    >
      {(books) => <PracticeView books={books} focus={book} />}
    </Doc>
  );
}

function LabPage({ index }: { index: SiteIndex }): ReactNode {
  if (index.files["lab.json"] === undefined) {
    return (
      <>
        <p className="kicker">Hypothesis Laboratory</p>
        <h1>This site was exported without a Laboratory.</h1>
        <p>
          Export it again with the Laboratory log, so that its hypotheses are
          listed.
        </p>
      </>
    );
  }
  return (
    <Doc<LabDocument> index={index} path="lab.json" what="the Laboratory">
      {(lab) => (
        <Suspense fallback={<Waiting what="the Laboratory" />}>
          <LabView lab={lab} />
        </Suspense>
      )}
    </Doc>
  );
}

function FeedPage({ index }: { index: SiteIndex }): ReactNode {
  return (
    <Doc<CycleSummary[]> index={index} path="cycles.json" what="the cycles">
      {(cycles) => (
        <Doc<Row[]> index={index} path="rows.json" what="the calls">
          {(rows) => (
            <Suspense fallback={<Waiting what="the feed" />}>
              <FeedView rows={rows} cycles={cycles} index={index} />
            </Suspense>
          )}
        </Doc>
      )}
    </Doc>
  );
}

function Missing({ path }: { path: string }): ReactNode {
  return (
    <>
      <h1>That page does not exist.</h1>
      <p className="mono">{path}</p>
      <p>
        <a href={href({ page: "today" })}>Go to Today</a>
      </p>
    </>
  );
}

type LedgerSaid = { heading: string; line: string; detail: string };

/** The pages made from the ledger's records. When the ledger is not in its OK state they show that in place of their documents, which hold
 * none of its records; the other pages show it above themselves (ADR-026: visible on every surface). */
function fromLedger(route: Route): boolean {
  return [
    "today",
    "calls",
    "record",
    "verify",
    "receipt",
    "challenge",
  ].includes(route.page);
}

/** A page made from the ledger when the ledger is not in its OK state: the state, that its records are not shown, and the ledger's reason. */
function LedgerFailed({
  title,
  said,
}: {
  title: string;
  said: LedgerSaid;
}): ReactNode {
  return (
    <>
      <p className="kicker">{title}</p>
      <h1>{said.heading}</h1>
      <div className="notice error">
        <p>{said.line}</p>
        <p className="mono">{said.detail}</p>
      </div>
    </>
  );
}

/** The same, above a page that is not made from the ledger. */
function LedgerNotice({ said }: { said: LedgerSaid }): ReactNode {
  return (
    <div className="wrap">
      <div className="notice error" role="status">
        <p>
          <strong>{said.heading}</strong> {said.line}
        </p>
        <p className="mono">{said.detail}</p>
      </div>
    </div>
  );
}

/** What stands in for a page that cannot be drawn: that a document could not be read, the reason, and a way back to Today. */
function Broken({ error, today }: { error: Error; today: boolean }): ReactNode {
  return (
    <>
      <h1>This page could not be shown.</h1>
      <div className="notice error" role="alert">
        <p>
          <strong>A document of this site could not be read.</strong>
        </p>
        <p className="mono">{error.message}</p>
      </div>
      {today ? null : (
        <p>
          <a href={href({ page: "today" })}>Go to Today</a>
        </p>
      )}
    </>
  );
}

type Fault = { at: string; error: Error | null };

/** One page that cannot be drawn (a value the wording refuses, such as a time it cannot read) takes down that page only: the page bar and
 * the footer stay, and the next address draws its page afresh. */
class PageBoundary extends Component<
  { at: string; today: boolean; children: ReactNode },
  Fault
> {
  override state: Fault = { at: this.props.at, error: null };

  static getDerivedStateFromProps(
    props: { at: string },
    state: Fault,
  ): Fault | null {
    return props.at === state.at ? null : { at: props.at, error: null }; // before drawing, so a new page never shows the old failure
  }

  static getDerivedStateFromError(error: unknown): Partial<Fault> {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  override render(): ReactNode {
    return this.state.error === null ? (
      this.props.children
    ) : (
      <Broken error={this.state.error} today={this.props.today} />
    );
  }
}

// --- the shell ------------------------------------------------------------------------------------------------------------------------
/** Whether the screen is wide enough for every page link to share one row (a query on the viewport, read again when it changes). */
function titleOf(route: Route): string {
  switch (route.page) {
    case "today":
      return "Today";
    case "calls":
    case "callsby":
      return "Calls";
    case "record":
      return "Track record";
    case "verify":
      return "Check a record";
    case "quality":
      return "Evidence of quality";
    case "drill":
      return "Failure drills";
    case "brand":
      return "Brand";
    case "claims":
      return "Check a claim";
    case "challenge":
      return "Test a source";
    case "desk":
      return "The Desk";
    case "practice":
      return "Practice trades";
    case "lab":
      return "Laboratory";
    case "judges":
      return "For judges";
    case "feed":
      return "Feed";
    case "voices":
      return "Voices and whales";
    case "receipt":
      return "Receipt";
    case "missing":
      return "Not found";
  }
}

function NavLink({
  item,
  at,
}: {
  item: { route: Route; label: string };
  at: string;
}): ReactNode {
  return (
    <li>
      <a
        href={href(item.route)}
        aria-current={at === item.route.page ? "page" : undefined}
      >
        {item.label}
      </a>
    </li>
  );
}

export function Site(): ReactNode {
  const [hash, setHash] = useState(window.location.hash);
  const moved = useRef(false); // set by a page change, never by the first page (so not by a second run of an effect in development either)
  useEffect(() => {
    const on = () => {
      moved.current = true;
      setHash(window.location.hash);
    };
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);
  const route = parse(hash);
  const main = useRef<HTMLElement>(null);
  useEffect(() => {
    document.title = `${titleOf(route)} · Hoshitori`;
    if (!moved.current) return; // the first page keeps the keyboard at the top, so the skip link is the first stop
    window.scrollTo(0, 0);
    const area = main.current;
    if (area === null) return;
    // The keyboard goes to the new page's heading (WCAG 2.4.3). A page that loads its documents first has its heading only once they have
    // loaded, so the heading is waited for; someone who has moved the keyboard elsewhere in the meantime keeps it where they put it.
    const from = document.activeElement;
    const land = (): boolean => {
      const heading = area.querySelector("h1");
      if (heading === null) return false;
      const now = document.activeElement;
      if (now === from || now === null || now === document.body) {
        heading.setAttribute("tabindex", "-1");
        heading.focus({ preventScroll: true });
      }
      return true;
    };
    if (land()) return;
    const watch = new MutationObserver(() => {
      if (land()) watch.disconnect();
    });
    watch.observe(area, { childList: true, subtree: true });
    return () => watch.disconnect();
  }, [hash]); // eslint-disable-line react-hooks/exhaustive-deps
  const [index, retry] = useLoad<SiteIndex>(() => loadIndex(DATA), "index");
  const failure =
    index.state === "ready" ? ledgerFailure(index.value.ledger) : null;
  const moreOpen = GROUPS.some((g) =>
    g.items.some((n) => n.primary !== true && n.route.page === route.page),
  ); // the disclosure opens on one of its own pages
  // "#main" is not a page to the router, so the skip link moves the keyboard itself and leaves the address alone (WCAG 2.4.1)
  const skip = (event: MouseEvent<HTMLAnchorElement>): void => {
    event.preventDefault();
    main.current?.scrollIntoView();
    main.current?.focus({ preventScroll: true });
  };
  return (
    <>
      <a className="skip" href="#main" onClick={skip}>
        Skip to the page
      </a>
      <header className="site">
        <div className="wrap">
          <a className="mark" href={href({ page: "today" })}>
            <Mark size={18} decorative />
            Hoshitori<span lang="ja">星取</span>
          </a>
          <nav aria-label="Pages">
            <ul>
              {GROUPS.flatMap((g) => g.items)
                .filter((n) => n.primary === true)
                .map((n) => (
                  <NavLink key={n.label} item={n} at={route.page} />
                ))}
              <li>
                <details
                  className="more"
                  key={route.page}
                  onKeyDown={(event) => {
                    if (event.key === "Escape") {
                      event.currentTarget.open = false;
                      event.currentTarget.querySelector("summary")?.focus();
                    }
                  }}
                >
                  <summary data-current={moreOpen ? "true" : undefined}>
                    All pages
                  </summary>
                  <div className="menu">
                    {GROUPS.map((g) => (
                      <section className="group" key={g.name}>
                        <p className="group-name" id={`m-${g.name}`}>
                          {g.name}
                        </p>
                        <ul aria-labelledby={`m-${g.name}`}>
                          {g.items.map((n) => (
                            <NavLink key={n.label} item={n} at={route.page} />
                          ))}
                        </ul>
                      </section>
                    ))}
                  </div>
                </details>
              </li>
            </ul>
          </nav>
        </div>
      </header>
      {index.state === "ready" &&
      oldLine(index.value.generated_at, Date.now()) !== null ? (
        <div className="wrap">
          <p className="notice error" role="status">
            {oldLine(index.value.generated_at, Date.now())}
          </p>
        </div>
      ) : null}
      {failure !== null && !fromLedger(route) ? (
        <LedgerNotice said={failure} />
      ) : null}
      <main id="main" ref={main} className="wrap" tabIndex={-1}>
        <PageBoundary at={hash} today={route.page === "today"}>
          {route.page === "brand" ? <BrandRoute /> : null}
          {route.page !== "brand" && index.state === "loading" ? (
            <Waiting what="the site" />
          ) : null}
          {route.page !== "brand" && index.state === "failed" ? (
            <>
              <h1>{titleOf(route)}</h1>
              <Failed error={index.error} retry={retry} />
            </>
          ) : null}
          {route.page !== "brand" && index.state === "ready" ? (
            <Page route={route} index={index.value} />
          ) : null}
        </PageBoundary>
      </main>
      <footer className="site">
        <div className="wrap">
          <p>
            <a href={href({ page: "brand" })}>Brand</a>
          </p>
          <p>
            {index.state === "ready" ? (
              <>
                As of <Time at={index.value.generated_at} />,{" "}
                {ageWords(Date.now(), index.value.generated_at)} ago.
              </>
            ) : null}
          </p>
        </div>
      </footer>
    </>
  );
}

function BrandRoute(): ReactNode {
  const [tokens] = useLoad<Tokens>(async () => {
    const r = await fetch("tokens.json");
    if (!r.ok) throw new Error(`tokens.json ${r.status}`);
    return (await r.json()) as Tokens;
  }, "tokens.json");
  return <BrandPage tokens={tokens.state === "ready" ? tokens.value : null} />;
}

function Page({ route, index }: { route: Route; index: SiteIndex }): ReactNode {
  return (
    // keyed by the route: a boundary that suspends again keeps the old page in the document, hidden, and its heading would take the keyboard's focus
    <Suspense
      key={JSON.stringify(route)}
      fallback={<Waiting what="the page" />}
    >
      <PageOf route={route} index={index} />
    </Suspense>
  );
}

function PageOf({
  route,
  index,
}: {
  route: Route;
  index: SiteIndex;
}): ReactNode {
  const failure = ledgerFailure(index.ledger);
  if (failure !== null && fromLedger(route))
    return <LedgerFailed title={titleOf(route)} said={failure} />;
  switch (route.page) {
    case "today":
      return <TodayPage index={index} />;
    case "calls":
      return <CallsPage index={index} cycle={route.cycle} />;
    case "callsby":
      return (
        <Doc<Row[]> index={index} path="rows.json" what="the calls">
          {(rows) => (
            <Suspense fallback={null}>
              <CallsBy
                rows={rows}
                by={route.by}
                value={route.value}
                table={(r, caption) => (
                  <RowsTable rows={r} caption={caption} dated />
                )}
              />
            </Suspense>
          )}
        </Doc>
      );
    case "record":
      return <RecordPage index={index} />;
    case "verify":
      return <VerifyPage index={index} />;
    case "quality":
      return (
        <Suspense fallback={<Waiting what="the evidence of quality" />}>
          <QualityPage index={index} />
        </Suspense>
      );
    case "drill":
      return (
        <Suspense fallback={<Waiting what="the drills" />}>
          <DrillPage index={index} />
        </Suspense>
      );
    case "brand":
      return <BrandRoute />;
    case "claims":
      return <ClaimsPage index={index} />;
    case "challenge":
      return <ChallengePage index={index} />;
    case "desk":
      return <DeskPage index={index} run={route.run} />;
    case "practice":
      return <PracticePage index={index} book={route.book} />;
    case "lab":
      return <LabPage index={index} />;
    case "judges":
      return <JudgesView />;
    case "feed":
      return <FeedPage index={index} />;
    case "voices":
      return (
        <Suspense fallback={<Waiting what="the voices" />}>
          <VoicesView index={index} />
        </Suspense>
      );
    case "receipt":
      return <ReceiptPage index={index} id={route.id} />;
    case "missing":
      return <Missing path={route.path} />;
  }
}
