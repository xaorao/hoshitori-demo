import { useCallback, useEffect, useState, type ReactNode } from "react";
import {
  sourceName,
  MODEL_CHALLENGE_METHOD,
  MODEL_CHALLENGE_NOTE,
  NO_CONTROL,
  challengeSummary,
  committedLine,
  PLACEBO_SOURCE,
  challengeAction,
  challengeTitle,
  count,
  failedLine,
  horizon,
  modelCounts,
  modelOutcome,
  pairsLine,
  shiftLine,
  sourceWords,
} from "./format.ts";
import { LoadError, loadModelChallenges } from "./load.ts";
import { href } from "./route.ts";
import { DATA } from "./site.tsx";
import { Scroll } from "./scroll.tsx";
import type { Boundary, ChallengeRow, ModelChallenge, PairCounts, SourceValue } from "./types.ts";

export function ChallengeView({ rows, boundaries }: { rows: ChallengeRow[]; boundaries: Boundary[] }): ReactNode {
  const symbols = [...new Set(rows.map((r) => r.symbol))];
  const summary = challengeSummary(rows);
  const tables = (
    <>
      {symbols.map((symbol) => (
        <section key={symbol}>
          <h3>{symbol}</h3>
          <p>
            <a href={href({ page: "callsby", by: "token", value: symbol })}>Every call on {symbol}</a>
          </p>
          <div className="table-wrap" tabIndex={0} role="region" aria-label={`What changes for ${symbol} without each source`}>
            <table>
              <caption>{count(rows.filter((r) => r.symbol === symbol).length, "question")} asked of the rule</caption>
              <thead>
                <tr>
                  <th scope="col">Horizon</th>
                  <th scope="col">Taken out</th>
                  <th scope="col">The rule said</th>
                  <th scope="col">Without it</th>
                  <th scope="col">So</th>
                </tr>
              </thead>
              <tbody>
                {rows
                  .filter((r) => r.symbol === symbol)
                  .map((r) => (
                    <tr key={`${r.cycle_id}-${r.hours}-${r.source}`}>
                      <th scope="row">{r.hours} {r.hours === 1 ? "hour" : "hours"}</th>
                      <td>
                        {sourceWords(r.source)} <span className="muted">({count(r.items, "item")} taken out)</span>
                      </td>
                      <td>{r.original}</td>
                      <td>{r.changed ?? "Not evaluated"}</td>
                      <td>{r.words}</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </>
  );
  return (
    <>
      <p className="kicker">Test a source</p>
      <h1>What would change the agents' calls?</h1>
      <p className="lead">
        Two exact questions for the rules agent: how far each call was from a different one, and whether the rule could decide without each source. One measured question for the model
        agent, which answers differently each time: asked many times with and without one source, did its call change beyond its own variation?
      </p>
      <p>
        The rules agent is a fixed rule, so each of its answers is exact and needs no repeated runs. The answers say how a call depends on this evidence, not whether the evidence was true or
        whether the market moved because of it.
      </p>
      <ModelChallenges />
      <h2>The rules agent: how far from a different call</h2>
      <p>
        The rule turns the move so far into one of five states, each with its own stated chance. So for each call, the nearest return, up and down, at which the rule would have said
        something different is exact. A state in between that says the same is passed over, so that return can lie more than one state away. These are returns that did not happen: a
        what-if, not a forecast.
      </p>
      {boundaries.length === 0 ? <p className="muted">No call of the rules agent in a committed cycle has a state to measure from.</p> : null}
      {boundaries.length > 0 ? (
        <details>
          <summary>Every call, one row each ({count(boundaries.length, "call")})</summary>
        <div className="table-wrap" tabIndex={0} role="region" aria-label="How far each call was from changing">
          <table>
            <caption>{count(boundaries.length, "call")} of the rules agent</caption>
            <thead>
              <tr>
                <th scope="col">Call</th>
                <th scope="col">Return seen</th>
                <th scope="col">The rule said</th>
                <th scope="col">It would have changed at</th>
              </tr>
            </thead>
            <tbody>
              {boundaries.map((b) => (
                <tr key={`${b.cycle_id}-${b.symbol}-${b.hours}`}>
                  <th scope="row">
                    {b.symbol}, {b.hours} {b.hours === 1 ? "hour" : "hours"}
                  </th>
                  <td>
                    {b.ret}% <span className="muted">({b.state})</span>
                  </td>
                  <td>{b.says}</td>
                  <td>
                    {b.moves.length === 0 ? (
                      <span className="muted">No return, up or down, changes the call: the rule says the same in every state.</span>
                    ) : (
                      b.moves.map((m) => (
                        <div key={m.direction}>
                          {m.at}% ({m.distance} points, {m.state}): {m.says}
                        </div>
                      ))
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        </details>
      ) : null}
      <h2>The rules agent without each source</h2>
      <p>For each call, one source was taken out, together with everything computed from it, and the same rule was asked again.</p>
      {rows.length === 0 ? <p className="notice">No committed cycle has a snapshot to ask this of yet.</p> : null}
      {rows.length > 0 ? (
        <>
          <p>
            {count(summary.questions, "question")} over {count(summary.calls, "call")}:
          </p>
          <ul>
            {summary.outcomes.map((o) => (
              <li key={`${o.outcome}-${o.words}`}>
                {summary.uniform ? "Every question" : `${o.questions} of ${summary.questions}`}: {o.words}
              </li>
            ))}
          </ul>
        </>
      ) : null}
      {summary.uniform ? (
        <>
          <ul>
            {summary.sources.map((s) => (
              <li key={s.source}>
                {sourceWords(s.source)}: taken out in {count(s.questions, "question")}, {s.itemsLow === s.itemsHigh ? count(s.itemsLow, "item") : `${s.itemsLow} to ${count(s.itemsHigh, "item")}`} each time
              </li>
            ))}
          </ul>
          <details>
            <summary>Every question, one row each</summary>
            {tables}
          </details>
        </>
      ) : (
        <details>
          <summary>Every question, one row each</summary>
          {tables}
        </details>
      )}
    </>
  );
}

// --- the model agent ------------------------------------------------------------------------------------------------------------------
type Loaded = { state: "loading" } | { state: "failed"; error: LoadError } | { state: "ready"; value: ModelChallenge[] | null };

const FAILED_WORDS = {
  missing: "A document of this site could not be fetched.",
  changed: "A document does not match its digest in the index, so it is not shown.",
  unreadable: "A document of this site could not be read.",
} as const;

/** The section of the model agent's recorded challenges. It loads its own document (model_challenges.json, checked against the digest its
 * index holds), and says so when the site was exported without one. */
function ModelChallenges(): ReactNode {
  const [loaded, setLoaded] = useState<Loaded>({ state: "loading" });
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    setLoaded({ state: "loading" });
    loadModelChallenges(DATA).then(
      (value) => live && setLoaded({ state: "ready", value }),
      (error: unknown) =>
        live && setLoaded({ state: "failed", error: error instanceof LoadError ? error : new LoadError("unreadable", "model_challenges.json", String(error)) }),
    );
    return () => {
      live = false;
    };
  }, [attempt]);
  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return (
    <section aria-labelledby="model-challenges">
      <h2 id="model-challenges">The model agent without one source</h2>
      <p>{MODEL_CHALLENGE_METHOD}</p>
      <p>{MODEL_CHALLENGE_NOTE}</p>
      {loaded.state === "loading" ? (
        <p role="status" className="muted">
          Loading the model agent's challenges…
        </p>
      ) : null}
      {loaded.state === "failed" ? (
        <div className="notice error" role="alert">
          <p>
            <strong>{FAILED_WORDS[loaded.error.kind]}</strong>
          </p>
          <p className="mono">{loaded.error.message}</p>
          <p>
            <button type="button" onClick={retry}>
              Try again
            </button>
          </p>
        </div>
      ) : null}
      {loaded.state === "ready" && (loaded.value === null || loaded.value.length === 0) ? (
        <div className="notice">
          <p>No challenge of the model agent has been run for this copy of the site yet, so only the rules agent can be challenged here.</p>
          <details>
            <summary>For the operator</summary>
            <p>
              Run <span className="mono">scripts/challenge_live.py</span>, then export again with{" "}
              <span className="mono">--model-challenge FILE</span>.
            </p>
          </details>
        </div>
      ) : null}
      {loaded.state === "ready" && loaded.value !== null ? loaded.value.map((c) => <OneChallenge key={`${c.forecast_id}-${c.source}`} c={c} />) : null}
    </section>
  );
}

const TABLE_ROWS: [string, (t: PairCounts) => number][] = [
  ["Both runs", (t) => t.same_same],
  ["Only with every source", (t) => t.same_changed],
  ["Only the other run", (t) => t.changed_same],
  ["Neither run", (t) => t.changed_changed],
];

function OneChallenge({ c }: { c: ModelChallenge }): ReactNode {
  const id = `mc-${c.forecast_id}-${c.source}`.replace(/[^a-z0-9-]/gi, "-");
  const title = challengeTitle(c.symbol, c.hours, c.source);
  const counted = c.treated !== null && c.control !== null;
  return (
    <section aria-labelledby={id}>
      <h3 id={id}>{title}</h3>
      <p>
        <strong>{modelOutcome(c.outcome, c.mechanism, c.source)}</strong>
      </p>
      <p>
        {committedLine(c.original)} {challengeAction(c.source, c.items)}{" "}
        <a href={href({ page: "receipt", id: c.forecast_id })}>
          Receipt of the committed call, {c.symbol} {horizon(c.hours)}
        </a>
      </p>
      <p>{c.treated !== null && c.control !== null ? modelCounts(c.source, c.pairs, c.treated, c.control) : NO_CONTROL}</p>
      {counted ? (
        <div className="table-wrap" tabIndex={0} role="region" aria-label={`Pairs of ${title}`}>
          <table>
            <caption>Of {count(c.pairs, "pair")}, which runs made the committed call</caption>
            <thead>
              <tr>
                <th scope="col">In the pair</th>
                <th scope="col">{c.source === PLACEBO_SOURCE ? "With text added" : "Without it"}</th>
                <th scope="col">Asked again</th>
              </tr>
            </thead>
            <tbody>
              {TABLE_ROWS.map(([label, of]) => (
                <tr key={label}>
                  <th scope="row">{label}</th>
                  <td>{c.treated === null ? "–" : of(c.treated)}</td>
                  <td>{c.control === null ? "–" : of(c.control)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
      {c.shift !== null && c.low !== null && c.high !== null && c.p !== null ? <p>{shiftLine(c.shift, c.low, c.high, c.p, c.source)}</p> : null}
      <p>{failedLine(c.failed, c.attempted, c.outcome)}</p>
      <p className="muted">{pairsLine(c.pairs, c.arm_name, c.model, c.temperature)}</p>
    </section>
  );
}

const SOURCE_STATE: Record<string, string> = {
  "First better on average": "Better with it, on average",
  "Second better on average": "Better without it, on average",
  "Not distinguishable yet": "Not distinguishable yet",
  "Too few to show": "Too few to show",
};

/** Whether each source made the rules agent's settled calls better: the call with the source against the call without it, on the same
 * outcomes, by the anytime-valid comparison the Track record uses. Exploratory, and said so. */
export function SourcesSection({ doc }: { doc: SourceValue }): ReactNode {
  return (
    <section aria-labelledby="sources-title">
      <h2 id="sources-title">Does a source make the calls better?</h2>
      <p>
        Changing a call is not the same as improving it. Here settled calls are made again without each source and both are scored
        against what happened: the rules agent exactly, the single model by asking it twice with one seed, once with the source and once
        without. Exploratory: this test was not fixed before the record began.
      </p>
      {doc.lines.length === 0 ? (
        <p className="muted">No settled call has been made again without a source in this export yet.</p>
      ) : (
        <Scroll label="Each source, with and without">
          <table className="stack">
            <caption>Each source, with and without, on settled calls</caption>
            <thead>
              <tr>
                <th scope="col">Source</th>
                <th scope="col">Agent</th>
                <th scope="col">Horizon</th>
                <th scope="col">Scored</th>
                <th scope="col">Standing</th>
                <th scope="col">No call without it</th>
              </tr>
            </thead>
            <tbody>
              {doc.lines.map((l) => (
                <tr key={`${l.arm}-${l.source}-${l.hours}`}>
                  <th scope="row">{sourceName(l.source)}</th>
                  <td data-label="Agent">{l.arm_name}</td>
                  <td data-label="Horizon">{horizon(l.hours)}</td>
                  <td data-label="Scored">{l.shared}</td>
                  <td data-label="Standing">
                    {l.shared === 0 && l.required > 0 && l.not_evaluated === 0
                      ? "Required: the agent makes no call without it"
                      : (SOURCE_STATE[l.state] ?? l.state)}
                    {l.mean !== null ? ` (${l.mean}, ${l.lower} to ${l.upper})` : ""}
                  </td>
                  <td data-label="No call without it">{l.required}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      )}
      <p className="muted">
        A positive difference means the call was closer to what happened with the source. A source the agent cannot call without is counted,
        not scored. Interval at {doc.alpha}; fewer than {doc.min_shown} scored calls show none.
      </p>
    </section>
  );
}
