import type { ReactNode } from "react";
import { whenShort } from "./format.ts";
import { Scroll } from "./scroll.tsx";
import type { RyoOutcome, RyoRecord } from "./types.ts";

const MOVED: Record<string, string> = {
  ROSE: "Rose",
  FELL: "Fell",
  TIE: "Unchanged",
};
const SCORED_AS: Record<string, string> = {
  UP: "Right if the price rose",
  DOWN: "Right if the price fell",
  NOT_LONG: "Right if the price did not rise",
  NONE: "Not scored",
  UNREAD: "Not scored",
};
const SHOWN = 12;
const PLAN: Record<string, string> = {
  TARGET: "Target first",
  STOP: "Stop first",
  TIMEOUT: "Neither in 24 hours",
  AMBIGUOUS: "Both in one minute",
  ALREADY_PAST: "Already past at the start",
  NOT_SETTLED: "Could not be settled",
  WAITING: "Waiting",
  NO_PLAN: "No plan",
};

function count(record: RyoRecord, code: string): number {
  return record.summary.plans.find(([p]) => p === code)?.[1] ?? 0;
}

function happened(o: RyoOutcome): string {
  if (o.state === "Settled" && o.direction !== null)
    return MOVED[o.direction] ?? o.direction;
  if (o.state === "Waiting") return "Waiting for the 24 hours";
  if (o.state === "No window") return "No 24-hour call on this token then";
  return "Could not be settled";
}

function verdict(o: RyoOutcome): string {
  if (o.hit === true) return "Right";
  if (o.hit === false) return "Wrong";
  return o.state === "Waiting" ? "Not yet" : "Not scored";
}

/** The receipt's sentence: what the price did over the 24 hours and what that makes of RYO's read. */
function sentence(o: RyoOutcome): string {
  if (o.state === "Waiting") return "The 24 hours have not passed yet.";
  if (o.state === "No window")
    return "No agent made a 24-hour call on this token then, so it is not scored.";
  if (o.state === "Not settled")
    return "The 24 hours could not be settled, so it is not scored.";
  const moved =
    o.direction === "TIE"
      ? "ended where it started"
      : o.direction === "ROSE"
        ? "rose"
        : "fell";
  const so =
    o.hit === true
      ? "so the read was right"
      : o.hit === false
        ? "so the read was wrong"
        : "and this read is not scored";
  return `Over the 24 hours the price ${moved}, ${so}.`;
}

/** RYO's read for a receipt: what RYO said on the same token in the same cycle, and how it did. */
export function RyoOnThisToken({
  record,
  cycleId,
  symbol,
}: {
  record: RyoRecord;
  cycleId: string;
  symbol: string;
}): ReactNode {
  const read = record.outcomes.find(
    (o) => o.cycle_id === cycleId && o.symbol === symbol,
  );
  const words = read
    ? record.table.find(([kind]) => kind === read.reading)?.[1]
    : undefined;
  return (
    <section aria-labelledby="ryo-here">
      <h2 id="ryo-here">RYO on this token</h2>
      {read === undefined ? (
        <p>RYO gave no verdict on this token in this cycle.</p>
      ) : (
        <p>
          RYO said “{read.call}” ({words}). {sentence(read)} Its plan:{" "}
          {(PLAN[read.plan] ?? read.plan).toLowerCase()}. RYO never saw the
          agents’ calls, and no agent saw RYO’s verdict.
        </p>
      )}
    </section>
  );
}

export function RyoSection({ record }: { record: RyoRecord }): ReactNode {
  const s = record.summary;
  const waiting = s.states.find(([state]) => state === "Waiting")?.[1] ?? 0;
  const newest = [...record.outcomes].reverse().slice(0, SHOWN);
  const times = new Map(record.cycle_times);
  return (
    <section aria-labelledby="ryo-record">
      <h2 id="ryo-record">RYO’s own read</h2>
      <p className="lead">
        RYO’s verdict on each token it read, scored over the same 24 hours as
        the agents’ calls. RYO gives a word, not a chance, so it gets a count of
        right and wrong, never a score.
      </p>
      {s.scored === 0 ? (
        <p>
          No read has settled yet: {s.reads} {s.reads === 1 ? "read" : "reads"},{" "}
          {waiting} waiting for the 24 hours.
        </p>
      ) : (
        <p>
          {s.hits} of {s.scored} scored{" "}
          {s.scored === 1 ? "read was" : "reads were"} right. Over the same
          24-hour windows the price rose in {s.rose} of {s.scored}, so a run of
          rights can be the market’s own drift.
        </p>
      )}
      {count(record, "TARGET") +
        count(record, "STOP") +
        count(record, "TIMEOUT") >
      0 ? (
        <p>
          RYO’s own plans, over the same windows: target first{" "}
          {count(record, "TARGET")}, stop first {count(record, "STOP")}, neither{" "}
          {count(record, "TIMEOUT")}
          {count(record, "ALREADY_PAST") > 0
            ? `; ${count(record, "ALREADY_PAST")} already past when the window opened`
            : ""}
          .
        </p>
      ) : null}
      <Scroll label="How RYO's words are read">
        <table className="stack">
          <caption>How RYO’s words are read ({record.table_version})</caption>
          <thead>
            <tr>
              <th scope="col">RYO says</th>
              <th scope="col">Read as</th>
              <th scope="col">Scored</th>
            </tr>
          </thead>
          <tbody>
            {record.table.map(([kind, words, said]) => (
              <tr key={kind}>
                <th scope="row">
                  {said.length > 0 ? said.join(", ") : "any other word"}
                </th>
                <td data-label="Read as">{words}</td>
                <td data-label="Scored">{SCORED_AS[kind]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Scroll>
      {s.unread_words.length > 0 ? (
        <p className="muted">
          Words our table does not read, kept as RYO gave them:{" "}
          {s.unread_words.join(", ")}.
        </p>
      ) : null}
      {newest.length > 0 ? (
        <Scroll label="RYO's latest reads">
          <table className="stack">
            <caption>RYO’s latest reads, newest first</caption>
            <thead>
              <tr>
                <th scope="col">Token</th>
                <th scope="col">When</th>
                <th scope="col">RYO said</th>
                <th scope="col">What happened</th>
                <th scope="col">Result</th>
                <th scope="col">Plan</th>
              </tr>
            </thead>
            <tbody>
              {newest.map((o) => {
                const at = times.get(o.cycle_id);
                return (
                  <tr key={`${o.cycle_id}${o.candidate_id}`}>
                    <th scope="row">{o.symbol}</th>
                    <td data-label="When">
                      {at === undefined ? "–" : whenShort(at)}
                    </td>
                    <td data-label="RYO said">{o.call}</td>
                    <td data-label="Happened">{happened(o)}</td>
                    <td data-label="Result">{verdict(o)}</td>
                    <td data-label="Plan">{PLAN[o.plan] ?? o.plan}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </Scroll>
      ) : null}
    </section>
  );
}

/** On Today, once RYO's own reads have settled: how often its verdict was right against how often the price simply rose on the same
 * windows, with a link to the full panel. Nothing before a read has settled, so an empty strip never stands in for a result. */
export function RyoStrip({ record }: { record: RyoRecord }): ReactNode {
  const { scored, hits, rose } = record.summary;
  if (scored === 0) return null;
  return (
    <p>
      <strong>RYO&apos;s own read:</strong> right on {hits} of {scored} settled
      24-hour windows; the price rose in {rose} of them.{" "}
      <a href="#/record">RYO beside the agents</a>
    </p>
  );
}
