import { Fragment, type ReactNode } from "react";
import { InShort } from "./inshort";
import {
  assumptionKind,
  count,
  expectsWords,
  groupedMoney,
  invalidationState,
  percentOfShare,
  practiceLevel,
  previewWords,
  sellReason,
  shareWords,
  tradeStatus,
  whenMs,
} from "./format.ts";
import { Scroll } from "./scroll.tsx";
import type { LabDocument, LabEntry, LabForward, LabPreview } from "./types.ts";

/** A reason that the document writes in lower case, as a sentence. */
function sentence(text: string | null): string {
  return text === null
    ? ""
    : `${text.charAt(0).toUpperCase()}${text.slice(1)}${/[.!?]$/.test(text) ? "" : "."}`;
}

function Assumptions({ entry }: { entry: LabEntry }): ReactNode {
  return (
    <>
      <h3>What it assumes</h3>
      <ul>
        {entry.assumptions.map((a) => (
          <li key={a.id}>
            <strong>{assumptionKind(a.kind)}.</strong> {a.text}
          </li>
        ))}
      </ul>
    </>
  );
}

function Preview({ entry }: { entry: LabEntry }): ReactNode {
  const p: LabPreview | null = entry.preview;
  if (p === null) {
    return (
      <>
        <h3>Its rule on real candles</h3>
        <p className="muted">{sentence(entry.preview_reason)}</p>
      </>
    );
  }
  return (
    <>
      <h3>Its rule on real candles</h3>
      <p className="notice">{p.label}.</p>
      <p>{previewWords(p)}</p>
      <p>{invalidationState(p.invalidation, p.invalidation_reason)}</p>
      <p className="muted">
        {p.instrument} on {p.venue}, from {whenMs(p.first_at_ms)} to the candle
        opening {whenMs(p.last_at_ms)}.
        {p.last_true_at_ms === null
          ? " It held on none of them."
          : ` The last candle on which it held opened ${whenMs(p.last_true_at_ms)}.`}
      </p>
    </>
  );
}

function Trades({ forward }: { forward: LabForward }): ReactNode {
  if (forward.trades.length === 0) return null;
  const level = forward.level === null ? "" : practiceLevel(forward.level);
  return (
    <Scroll
      label={`Practice trades since it was frozen, ${level.toLowerCase()}`}
    >
      <table>
        <caption>Practice trades since it was frozen</caption>
        <thead>
          <tr>
            <th scope="col">Decided</th>
            <th scope="col">Where it stands</th>
            <th scope="col">Bought</th>
            <th scope="col">Sold</th>
            <th scope="col">Result</th>
          </tr>
        </thead>
        <tbody>
          {forward.trades.map((t) => (
            <tr key={t.signal_at_ms}>
              <th scope="row">{whenMs(t.signal_at_ms)}</th>
              <td>{tradeStatus(t.status)}</td>
              <td className="nowrap">
                {t.entry_at_ms === null || t.entry_price === null
                  ? "–"
                  : `${groupedMoney(t.entry_price)}, ${whenMs(t.entry_at_ms)}`}
              </td>
              <td className="nowrap">
                {t.exit_at_ms === null ||
                t.exit_price === null ||
                t.exit_reason === null
                  ? t.exit_reason === null
                    ? "–"
                    : `not yet; ${sellReason(t.exit_reason)}`
                  : `${groupedMoney(t.exit_price)}, ${whenMs(t.exit_at_ms)}; ${sellReason(t.exit_reason)}`}
              </td>
              <td className="nowrap">
                {t.net_return === null ? "–" : percentOfShare(t.net_return)}
                {t.synthetic ? " (synthetic short)" : ""}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Scroll>
  );
}

function Forward({ forward }: { forward: LabForward }): ReactNode {
  if (!forward.run) {
    return (
      <>
        <h3>What its rule has done since it was frozen</h3>
        <p className="muted">{sentence(forward.reason)}</p>
      </>
    );
  }
  const waiting: string[] = [];
  if (forward.open > 0)
    waiting.push(`${count(forward.open, "trade")} bought and not yet sold`);
  if (forward.pending > 0)
    waiting.push(
      `${count(forward.pending, "entry", "entries")} waiting for a minute to close`,
    );
  if (forward.missing > 0)
    waiting.push(`${count(forward.missing, "trade")} whose minute never came`);
  return (
    <>
      <h3>What its rule has done since it was frozen</h3>
      <p>{sentence(forward.note)}</p>
      {forward.mean_net_return === null ? null : (
        <p>
          {forward.won} won and {forward.lost} lost after costs; the mean result
          is {percentOfShare(forward.mean_net_return)} per trade.
        </p>
      )}
      {waiting.length === 0 ? null : <p>{waiting.join("; ")}.</p>}
      {forward.as_of_ms === null ? null : (
        <p className="muted">
          As of {whenMs(forward.as_of_ms)}
          {forward.level === null
            ? ""
            : `, ${practiceLevel(forward.level).toLowerCase()}`}
          .
        </p>
      )}
      <Trades forward={forward} />
    </>
  );
}

function Version({ entry, n }: { entry: LabEntry; n: number }): ReactNode {
  return (
    <section aria-labelledby={`lab-${n}`}>
      <h2 id={`lab-${n}`}>{entry.statement}</h2>
      <dl className="facts">
        <dt>Expects</dt>
        <dd>{expectsWords(entry.direction, entry.symbol, entry.horizon_h)}</dd>
        <dt>Buys when</dt>
        <dd>{entry.enters_when}</dd>
        <dt>Stops when</dt>
        <dd>{entry.invalid_when}</dd>
        <dt>Size</dt>
        <dd>At most {shareWords(entry.max_weight)} of a practice portfolio</dd>
        <dt>Frozen</dt>
        <dd>
          {entry.frozen_at_ms === null
            ? "Before the moment was recorded"
            : whenMs(entry.frozen_at_ms)}
          {`, by ${entry.by}`}
          {entry.parent === null ? "" : "; an edit of an earlier version"}
        </dd>
      </dl>
      <Assumptions entry={entry} />
      <Preview entry={entry} />
      <Forward forward={entry.forward} />
    </section>
  );
}

/** The Hypothesis Laboratory: each frozen version in words, what it assumes, its rule on stored real candles and what it has done since. */
export function LabView({ lab }: { lab: LabDocument }): ReactNode {
  return (
    <>
      <p className="kicker">Hypothesis Laboratory</p>
      {lab.versions.length === 0 ? (
        <>
          <h1>No hypothesis has been frozen yet.</h1>
          <p>
            A hypothesis is written down in full before it is tried: what it
            expects, when it buys, when it stops and what it assumes. None has
            been.
          </p>
        </>
      ) : (
        <>
          <h1>
            {count(lab.versions.length, "hypothesis", "hypotheses")}, written
            down before they were tried.
          </h1>
          <InShort
            does="Turns a hypothesis written in words into a rule (when to buy, when to stop, how much) and freezes it before anything is tried."
            means="The preview shows what the rule would have done on past candles. Only what happened after the freeze is a forward result."
            sure="Shown: the frozen rule and when it was frozen. Not shown: any forecast score; nothing here is committed or time-stamped."
            next="Read each frozen rule, then check whether a forward run is stored for it."
          />
          <p className="lead">
            Each one buys when a condition holds and stops when another does.
            What a rule would have done on past candles is a preview and is
            labelled one; what it has done since the moment it was frozen is
            shown apart.
          </p>
          <p className="notice">
            Practice only: nothing here is committed or time-stamped, and
            nothing is a forecast score.
          </p>
        </>
      )}
      {lab.versions.map((entry, n) => (
        <Fragment key={entry.version}>
          <Version entry={entry} n={n} />
        </Fragment>
      ))}
    </>
  );
}
