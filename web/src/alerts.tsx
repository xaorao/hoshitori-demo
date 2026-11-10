import { type ReactNode, useState } from "react";
import { type Follows, followed, since, useFollows, visit, writeFollows } from "./follows.ts";
import { whenShort } from "./format.ts";
import { href } from "./route.ts";
import type { DecisionAlert } from "./types.ts";

const KIND_WORDS: Record<string, string> = {
  INVALIDATED: "Proved wrong by its own terms",
  THESIS_CHANGED: "A premise changed",
  RISK_STEP: "A practice risk step",
  SETTLED: "Settled",
};

/** Follow or stop following one call, its token or its agent: three switches on a receipt. */
export function FollowControls({
  forecastId,
  symbol,
  arm,
  armName,
}: {
  forecastId: string;
  symbol: string;
  arm: string;
  armName: string;
}): ReactNode {
  const f = useFollows();
  const toggle = (list: keyof Follows, value: string): void => {
    const now = f[list];
    writeFollows({
      ...f,
      [list]: now.includes(value)
        ? now.filter((v) => v !== value)
        : [...now, value],
    });
  };
  const switches: [keyof Follows, string, string][] = [
    ["calls", forecastId, "This call"],
    ["tokens", symbol, `Every call on ${symbol}`],
    ["agents", arm, `The ${armName.toLowerCase()} agent`],
  ];
  return (
    <>
      <h2>Follow</h2>
      <p className="muted">
        Alerts on Today only when something changes a decision: a call proved
        wrong by its own terms, a changed premise, a practice risk step, a
        settlement. Kept in this browser only.
      </p>
      <p className="follow">
        {switches.map(([list, value, label]) => (
          <button
            key={list}
            type="button"
            className="pill"
            aria-pressed={f[list].includes(value)}
            onClick={() => toggle(list, value)}
          >
            {f[list].includes(value)
              ? `Following: ${label.toLowerCase()}`
              : `Follow ${label.toLowerCase()}`}
          </button>
        ))}
      </p>
    </>
  );
}

const SHOWN = 5;

/** Today's alerts for what this browser follows, the most consequential first; a hint when nothing is followed. */
function AlertLine({ a }: { a: DecisionAlert }): ReactNode {
  return (
    <li>
      <strong>{KIND_WORDS[a.kind] ?? a.kind}.</strong> {a.text}{" "}
      <span className="muted">{whenShort(a.at)}</span>{" "}
      {a.forecast_id !== null ? (
        <a href={href({ page: "receipt", id: a.forecast_id })}>
          Open the receipt
        </a>
      ) : a.book !== null ? (
        <a href={href({ page: "practice", book: a.book })}>Open the book</a>
      ) : null}
    </li>
  );
}

const key = (a: DecisionAlert): string =>
  `${a.kind}-${a.forecast_id ?? a.book}-${a.at}`;

export function AlertsPanel({
  alerts,
  generated,
}: {
  alerts: DecisionAlert[];
  generated: string;
}): ReactNode {
  const f = useFollows();
  const [visits] = useState(() => visit(generated)); // once per page: a reload of the same export changes nothing
  const following = f.calls.length + f.tokens.length + f.agents.length;
  const mine = followed(alerts, f);
  const fresh = since(mine, visits.before);
  const shownFresh = new Set(fresh.map(key));
  const older = mine.filter((a) => !shownFresh.has(key(a)));
  return (
    <section aria-labelledby="alerts-title">
      <h2 id="alerts-title">Your alerts</h2>
      {following === 0 ? (
        <p className="muted">
          Follow a call, a token or an agent from any receipt to see here what
          changes a decision about it.
        </p>
      ) : (
        <>
          {visits.before !== null ? (
            <>
              <h3>Since your last visit</h3>
              {fresh.length === 0 ? (
                <p className="muted">
                  Nothing that changes a decision has happened to what you
                  follow since the export you last opened (
                  {whenShort(visits.before)}).
                </p>
              ) : (
                <ul className="findings">
                  {fresh.slice(0, SHOWN).map((a) => (
                    <AlertLine key={key(a)} a={a} />
                  ))}
                </ul>
              )}
              {fresh.length > SHOWN ? (
                <p className="muted">
                  {fresh.length - SHOWN} more since your last visit.
                </p>
              ) : null}
              {older.length > 0 ? <h3>Earlier</h3> : null}
            </>
          ) : null}
          {mine.length === 0 ? (
            <p className="muted">
              Nothing that changes a decision has happened to what you follow in
              this export.
            </p>
          ) : older.length === 0 ? null : (
            <ul className="findings">
              {older.slice(0, SHOWN).map((a) => (
                <AlertLine key={key(a)} a={a} />
              ))}
            </ul>
          )}
          {older.length > SHOWN ? (
            <p className="muted">
              {older.length - SHOWN} more, older or less consequential.
            </p>
          ) : null}
        </>
      )}
    </section>
  );
}
