import type { ReactNode } from "react";
import { CoverageView } from "./coverage.tsx";
import {
  count,
  drillLine,
  drillSentence,
  groupStartsOpen,
  qualityGroups,
  qualityLine,
} from "./format.ts";
import { Doc, Spans, StatePill } from "./site.tsx";
import { SweepsView } from "./sweeps.tsx";
import type {
  Coverage,
  Drill,
  QualityRow,
  SiteIndex,
  Sweeps,
} from "./types.ts";

// The two reviewer pages: loaded when opened, so their code (and the coverage and sweep tables) stays out of the first screen's budget.

export function QualityPage({ index }: { index: SiteIndex }): ReactNode {
  if (index.files["quality.json"] === undefined) {
    return (
      <>
        <p className="kicker">Evidence of quality</p>
        <h1>This site was exported without its status table.</h1>
        <p>
          Export it again with{" "}
          <span className="mono">
            hoshitori-league site SETTINGS OUT --status docs/STATUS.md
          </span>
          .
        </p>
      </>
    );
  }
  return (
    <Doc<QualityRow[]>
      index={index}
      path="quality.json"
      what="the evidence of quality"
    >
      {(rows) => (
        <>
          <p className="kicker">Evidence of quality</p>
          <h1>What has been shown, and what has not.</h1>
          <p className="lead">{qualityLine(rows)}</p>
          <p>
            Tested means a part passes checks written for it. Most modules are
            also checked against small changes made one at a time to their code,
            and the sweep table says how each change was noticed. It does not
            mean anyone but the author has read it. A dash under "Not shown"
            means the table says nothing there, not that nothing is missing.
          </p>
          <p>
            The parts are grouped by status, the least proven first. Open a part
            to see what shows it and what does not.
          </p>
          {qualityGroups(rows).map((g) => (
            <details
              key={g.word}
              className="status-group"
              open={groupStartsOpen(g.word)}
            >
              <summary>
                {count(g.rows.length, "part")}: {g.word.toLowerCase()}
              </summary>
              <ul className="parts-list">
                {g.rows.map((r) => (
                  <li key={r.unit}>
                    <details>
                      <summary>
                        <Spans text={r.unit} />
                      </summary>
                      <dl className="facts">
                        <dt>What shows it</dt>
                        <dd>
                          <Spans text={r.evidence} />
                        </dd>
                        <dt>Not shown</dt>
                        <dd>
                          {r.limit === null ? "–" : <Spans text={r.limit} />}
                        </dd>
                      </dl>
                    </details>
                  </li>
                ))}
              </ul>
            </details>
          ))}
          {index.files["coverage.json"] === undefined ? null : (
            <Doc<Coverage>
              index={index}
              path="coverage.json"
              what="the requirements coverage"
            >
              {(coverage) => <CoverageView coverage={coverage} />}
            </Doc>
          )}
          {index.files["sweeps.json"] === undefined ? null : (
            <Doc<Sweeps>
              index={index}
              path="sweeps.json"
              what="the mutation sweeps"
            >
              {(sweeps) => <SweepsView sweeps={sweeps} />}
            </Doc>
          )}
        </>
      )}
    </Doc>
  );
}

export function DrillPage({ index }: { index: SiteIndex }): ReactNode {
  if (index.files["drill.json"] === undefined) {
    return (
      <>
        <p className="kicker">Failure drills</p>
        <h1>This site was exported without its drills.</h1>
        <p>
          Run <span className="mono">scripts/make_drill.py drill.json</span>,
          then export again with{" "}
          <span className="mono">--drill drill.json</span>.
        </p>
      </>
    );
  }
  return (
    <Doc<Drill[]> index={index} path="drill.json" what="the drills">
      {(drills) => (
        <>
          <p className="kicker">Failure drills</p>
          <h1>What happens when something breaks.</h1>
          <p className="lead">{drillLine(drills.map((d) => d.held))}</p>
          <p>
            Each drill runs the league's own code on a test exchange, a test
            model or a test time-stamp authority with one thing broken. Nothing
            here touches a live service or a key. A drill holds when what the
            league did is exactly what the drill's script says it should do. The
            three drills that say nothing is broken are baselines: the same
            world with no fault, so that each failure is read against what a
            normal run looks like.
          </p>
          <p>
            To run them yourself, from the repository's top folder after the
            install in the README:{" "}
            <span className="mono">
              .venv/bin/python scripts/make_drill.py drill.json
            </span>
            . It takes about a minute and a half, prints whether each drill
            held, and writes this page's data.
          </p>
          <ol className="drills">
            {drills.map((d) => (
              <li key={d.id}>
                <h2>
                  {d.title}{" "}
                  <StatePill state={d.held ? "Held" : "Did not hold"} />
                </h2>
                <dl className="facts">
                  <dt>Broken</dt>
                  <dd>{d.injected}</dd>
                  <dt>Meant to happen</dt>
                  <dd>{d.expected}</dd>
                  <dt>What it did</dt>
                  <dd>{drillSentence(d.observed)}</dd>
                </dl>
              </li>
            ))}
          </ol>
          <p className="muted">
            Not drilled here: a crash while a cycle's records are being written
            and a lost command are covered by the recovery checks of the ledger,
            which are not recorded on this page.
          </p>
        </>
      )}
    </Doc>
  );
}
