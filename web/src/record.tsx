// The Track record page, loaded on demand: what each agent did, the other ways its score is read, the pairs compared on the same
// calls, RYO's own reads and the cycles that left no record.

import { Fragment, type ReactNode, Suspense } from "react";
import { ComparisonsSection } from "./comparisons.tsx";
import { BARRIER_NOTE, score, share } from "./format.ts";
import { href } from "./route.ts";
import { RyoSection } from "./ryo.tsx";
import { Scroll } from "./scroll.tsx";
import { Doc, Time, Waiting } from "./site.tsx";
import { LearningLedger } from "./social.tsx";
import type {
  ArmRecord,
  Comparisons,
  RyoRecord,
  SiteIndex,
  Today,
} from "./types.ts";

const GROUP_WORDS = ["Lowest probabilities", "Middle", "Highest probabilities"];

/** The other ways the score is read, and the calls that were left out of it: shown for every arm that has them, never merged into one number. */
function MoreScores({ arms }: { arms: ArmRecord[] }): ReactNode {
  const shown = arms.filter(
    (a) => a.log_score != null || (a.exclusions ?? []).length > 0,
  );
  return (
    <>
      <h2>The score read other ways, and what was left out</h2>
      <p className="muted">
        The logarithmic score punishes a confident miss more than the Brier
        score does. The reference is the share of rises in the 30 days before
        each call, and it needs 20 outcomes in that time. Counts, not a ranking.
      </p>
      <dl className="facts">
        {shown.map((a) => (
          <Fragment key={`${a.arm}${a.contract}`}>
            <dt>
              {a.arm_name} ({a.contract === "D" ? "up or down" : "barrier"})
            </dt>
            <dd>
              {a.log_score != null
                ? `Logarithmic score ${a.log_score} (lower is better). `
                : ""}
              {a.log_score != null
                ? a.climate_skill != null
                  ? `Against the recent rate of rises: skill ${a.climate_skill} over ${a.climate_n ?? 0} calls. `
                  : "No recent rate of rises yet: fewer than 20 outcomes in the 30 days before its calls. "
                : ""}
              {(a.exclusions ?? []).length > 0
                ? `Left unscored: ${(a.exclusions ?? []).map((x) => `${x.n} (${x.words.toLowerCase()})`).join(", ")}.`
                : "Nothing left unscored."}
              {a.bounds != null
                ? ` If those ${a.bounds.excluded} had gone either way the Brier score would lie between ${a.bounds.brier_low} and ${a.bounds.brier_high}, and the skill against a coin between ${a.bounds.skill_low} and ${a.bounds.skill_high}.${a.bounds.sensitive ? " This result depends on what was left out." : ""}`
                : ""}
            </dd>
          </Fragment>
        ))}
      </dl>
      {shown.some((a) => (a.reliability ?? []).length > 0) ? (
        <Scroll label="What each arm said against how often the price rose">
          <table className="dense">
            <caption>
              What each arm said, in three groups of equal size, against how
              often the price rose
            </caption>
            <thead>
              <tr>
                <th scope="col">Agent</th>
                <th scope="col">Group</th>
                <th scope="col">Said</th>
                <th scope="col" className="num">
                  Calls
                </th>
                <th scope="col" className="num">
                  Mean said
                </th>
                <th scope="col" className="num">
                  Rose
                </th>
              </tr>
            </thead>
            <tbody>
              {shown.flatMap((a) =>
                (a.reliability ?? []).map((g) => (
                  <tr key={`${a.arm}${a.contract}${g.group}`}>
                    <th scope="row">{a.arm_name}</th>
                    <td>{GROUP_WORDS[g.group] ?? ""}</td>
                    <td>
                      {g.low} to {g.high}
                    </td>
                    <td className="num">{g.n}</td>
                    <td className="num">{g.mean_p}</td>
                    <td className="num">{g.frequency}</td>
                  </tr>
                )),
              )}
            </tbody>
          </table>
        </Scroll>
      ) : null}
    </>
  );
}

const STRATUM_SPLIT: Record<string, string> = {
  age: "Age of the evidence (minutes)",
  drift: "Price move since (times the hourly range)",
};
const STRATUM_GROUP = ["Lowest third", "Middle third", "Highest third"];

/** The cycles the schedule put due that left no record: a cycle that never ran has no calls to count, so availability cannot show it. */
function MissedCycles({ index }: { index: SiteIndex }): ReactNode {
  return (
    <Doc<Today> index={index} path="today.json" what="the schedule">
      {(today) => {
        const missed = today.missed_cycles ?? [];
        const due = today.scheduled_cycles ?? 0;
        if (due === 0) return null;
        return (
          <>
            <h2>Cycles that did not run</h2>
            <p>
              {missed.length === 0
                ? `Every one of the ${due} cycles the schedule has put due left a record.`
                : `${missed.length} of the ${due} cycles the schedule has put due left no record. Availability above counts the calls of cycles that ran; these have no calls to count, so they are listed here.`}
            </p>
            {missed.length > 0 ? (
              <ul>
                {missed.slice(-10).map((at) => (
                  <li key={at}>
                    <Time at={at} />
                  </li>
                ))}
              </ul>
            ) : null}
            {missed.length > 10 ? (
              <p className="muted">
                The latest ten of {missed.length} are shown.
              </p>
            ) : null}
          </>
        );
      }}
    </Doc>
  );
}

export function RecordPage({ index }: { index: SiteIndex }): ReactNode {
  return (
    <Doc<ArmRecord[]> index={index} path="record.json" what="the record">
      {(arms) => (
        <>
          <p className="kicker">Track record</p>
          <h1>What each agent did.</h1>
          <p className="lead">
            Counts, not a ranking. A score appears only for settled calls, with
            how many there are.
          </p>
          {arms.length === 0 ? (
            <p>No agent has had a call yet, so there is nothing to count.</p>
          ) : null}
          <Scroll label="Every call, by what became of it">
            <table className="dense">
              <caption>Every call, by what became of it</caption>
              <thead>
                <tr>
                  <th scope="col">Agent</th>
                  <th scope="col">Kind</th>
                  <th scope="col" className="num">
                    Slots
                  </th>
                  <th scope="col" className="num">
                    Calls
                  </th>
                  <th scope="col" className="num">
                    Settled
                  </th>
                  <th scope="col" className="num">
                    Open
                  </th>
                  <th scope="col" className="num">
                    No call
                  </th>
                  <th scope="col" className="num">
                    Did not finish
                  </th>
                  <th scope="col" className="num">
                    Coverage
                  </th>
                  <th scope="col" className="num">
                    Availability
                  </th>
                  <th scope="col" className="num">
                    Score
                  </th>
                  <th scope="col" className="num">
                    Score, all settled
                  </th>
                  <th scope="col" className="num">
                    Left out
                  </th>
                  <th scope="col" className="num">
                    Skill
                  </th>
                </tr>
              </thead>
              <tbody>
                {arms.map((a) => (
                  <tr key={`${a.arm}${a.contract}`}>
                    <th scope="row">
                      {/^[A-Za-z0-9]{1,20}$/.test(a.arm) ? (
                        <a
                          href={href({
                            page: "callsby",
                            by: "agent",
                            value: a.arm,
                          })}
                        >
                          {a.arm_name}
                        </a>
                      ) : (
                        a.arm_name
                      )}
                    </th>
                    <td>{a.contract === "D" ? "Up or down" : "Barrier"}</td>
                    <td className="num">{a.scheduled}</td>
                    <td className="num">{a.calls}</td>
                    <td className="num">{a.settled}</td>
                    <td className="num">{a.open}</td>
                    <td className="num">{a.abstained}</td>
                    <td className="num">{a.failed}</td>
                    <td className="num">{share(a.coverage)}</td>
                    <td className="num">{share(a.availability)}</td>
                    <td className="num">
                      {score(a.mean_brier)}
                      <span className="muted"> ({a.brier_n})</span>
                    </td>
                    <td className="num">
                      {score(a.all_mean_brier)}
                      <span className="muted"> ({a.all_brier_n})</span>
                    </td>
                    <td className="num">{share(a.missed_c3)}</td>
                    <td className="num">{score(a.skill ?? null)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
          <dl className="facts">
            <dt>Coverage</dt>
            <dd>
              Calls made, out of calls made plus times the agent chose to stay
              out.
            </dd>
            <dt>Availability</dt>
            <dd>
              Calls that finished, out of calls that were due in the cycles that
              ran. A failure lowers this and never coverage; a cycle that never
              ran is counted in the line under the table, not here.
            </dd>
            <dt>Score</dt>
            <dd>
              The Brier score of the settled calls that were independently
              time-stamped, with their number in brackets. Lower is better. For
              an up-or-down call, always saying 50% scores 0.25; for a barrier
              call the score is over its three chances (target, stop, neither),
              and always saying one third each scores 0.6667.
            </dd>
            <dt>Score, all settled</dt>
            <dd>
              The same score over every settled call, with their number in
              brackets.
            </dd>
            <dt>Left out</dt>
            <dd>
              The share of every settled call that was not independently
              time-stamped, and so is not in the score.
            </dd>
            <dt>Skill</dt>
            <dd>
              One minus the score over the score of always saying the fixed
              answer above. Above zero beats it; below zero does worse. With few
              settled calls it says little.
            </dd>
          </dl>
          {arms
            .filter((a) => a.contract === "D")
            .map((a) => (
              <p key={a.arm} className="muted">
                {a.arm_name}: {a.label}.
              </p>
            ))}
          {arms.some((a) => a.contract === "B") ? (
            <p className="muted">{BARRIER_NOTE}</p>
          ) : null}
          {arms.some((a) => (a.strata ?? []).length > 0) ? (
            <>
              <h2>By how old the evidence was and how far the price moved</h2>
              <p className="muted">
                Each arm's settled calls in three groups of equal size, cut at
                the same values for every arm of a kind. Older or more-moved
                evidence may do worse; this shows whether it did. Exploratory:
                nothing here is ranked.
              </p>
              <Scroll label="Settled calls by the age of the evidence and by the price move">
                <table>
                  <thead>
                    <tr>
                      <th scope="col">Agent</th>
                      <th scope="col">Kind</th>
                      <th scope="col">Split</th>
                      <th scope="col">Group</th>
                      <th scope="col">Range</th>
                      <th scope="col" className="num">
                        Calls
                      </th>
                      <th scope="col" className="num">
                        Score
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {arms.flatMap((a) =>
                      (a.strata ?? []).map((x) => (
                        <tr key={`${a.arm}${a.contract}${x.by}${x.group}`}>
                          <th scope="row">{a.arm_name}</th>
                          <td>
                            {a.contract === "D" ? "Up or down" : "Barrier"}
                          </td>
                          <td>{STRATUM_SPLIT[x.by] ?? x.by}</td>
                          <td>{STRATUM_GROUP[x.group] ?? ""}</td>
                          <td>
                            {x.low === null ? "–" : `${x.low} to ${x.high}`}
                          </td>
                          <td className="num">{x.n}</td>
                          <td className="num">{score(x.mean_brier)}</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </Scroll>
            </>
          ) : null}
          {arms.some(
            (a) => a.log_score != null || (a.exclusions ?? []).length > 0,
          ) ? (
            <MoreScores arms={arms} />
          ) : null}
          <ComparisonsPart index={index} arms={arms} />
          <RyoRecordPart index={index} />
          <MissedCycles index={index} />
          <h2>Other ways to read these calls</h2>
          <ul>
            <li>
              Each agent&apos;s name above opens every call it made, each with
              its receipt.
            </li>
            {index.files["practice.json"] !== undefined ? (
              <li>
                <a href={href({ page: "practice" })}>Practice trades</a>: what
                following the calls would have done, under stated costs.
              </li>
            ) : null}
            {index.files["challenges.json"] !== undefined ? (
              <li>
                <a href={href({ page: "challenge" })}>Evidence challenge</a>:
                whether a call changes without one of its sources.
              </li>
            ) : null}
            {index.files["lab.json"] !== undefined ? (
              <li>
                <a href={href({ page: "lab" })}>Laboratory</a>: a person&apos;s
                own rule, frozen and run forward on the same candles.
              </li>
            ) : null}
            <li>
              <a href={href({ page: "feed" })}>Feed</a>: the same calls as a
              stream, newest first.
            </li>
          </ul>
          <Suspense fallback={null}>
            <LearningLedger index={index} />
          </Suspense>
        </>
      )}
    </Doc>
  );
}

/** The pairs of agents compared on the same calls, when the site holds them. */
function ComparisonsPart({
  index,
  arms,
}: {
  index: SiteIndex;
  arms: ArmRecord[];
}): ReactNode {
  if (index.files["comparisons.json"] === undefined) return null;
  const names = new Map(arms.map((a) => [a.arm, a.arm_name]));
  return (
    <Doc<Comparisons> index={index} path="comparisons.json" what="the pairs">
      {(doc) => (
        <Suspense fallback={<Waiting what="the pairs" />}>
          <ComparisonsSection doc={doc} names={names} />
        </Suspense>
      )}
    </Doc>
  );
}

/** Arm R on the Track record: RYO's own reads and how they did, or a line saying no cycle read RYO's verdict. */
function RyoRecordPart({ index }: { index: SiteIndex }): ReactNode {
  if (index.files["ryo.json"] === undefined) {
    return (
      <p className="muted">
        No cycle in this record read RYO’s own verdict, so RYO has no line here
        yet.
      </p>
    );
  }
  return (
    <Doc<RyoRecord> index={index} path="ryo.json" what="RYO’s reads">
      {(record) => (
        <Suspense fallback={<Waiting what="RYO’s reads" />}>
          <RyoSection record={record} />
        </Suspense>
      )}
    </Doc>
  );
}
