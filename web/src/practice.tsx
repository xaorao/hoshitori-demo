import { Fragment, type ReactNode } from "react";
import { useOpenOn } from "./open_on.ts";
import {
  agentWords,
  bpsAsPercent,
  concentration,
  count,
  curvePath,
  curveSummary,
  exitWords,
  groupedMoney,
  modeWords,
  money2,
  percentOfShare,
  practiceLevel,
  when,
} from "./format.ts";
import { href } from "./route.ts";
import { Scroll } from "./scroll.tsx";
import type { PracticeBook } from "./types.ts";

const W = 640;
const H = 160;
const PAD = 12;

/** Which book: the agent and its cost level, so that two books never share a name. */
function which(b: PracticeBook): string {
  return `${b.arm_name}, ${practiceLevel(b.level).toLowerCase()}`;
}

/** Who the agents are, once, for the books on the page. */
function Agents({ books }: { books: PracticeBook[] }): ReactNode {
  const named = new Map<string, string>();
  for (const b of books) if (!named.has(b.arm)) named.set(b.arm, b.arm_name);
  return (
    <details>
      <summary>Who the agents are</summary>
      <dl className="facts">
        {[...named].map(([arm, name]) => (
          <Fragment key={arm}>
            <dt>{name}</dt>
            <dd>
              {agentWords(arm) ?? "No description is written for this agent."}
            </dd>
          </Fragment>
        ))}
      </dl>
    </details>
  );
}

function costLine(b: PracticeBook): string {
  const slip = `${bpsAsPercent(b.slippage_bps)} slippage${b.adverse ? ", taken at the worst price of the minute" : ""}`;
  return `${bpsAsPercent(b.fee_bps)} fee on each side, ${slip}.`;
}

function Curve({ book }: { book: PracticeBook }): ReactNode {
  const drawn = curvePath(book.curve, W, H, PAD);
  const summary = curveSummary(book.curve, book.marks);
  if (book.curve.length === 0) return <p className="muted">{summary}</p>;
  return (
    <>
      <svg
        className="curve"
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={summary}
      >
        <path d={drawn.d} fill="none" stroke="currentColor" strokeWidth="2" />
        {drawn.dashed !== "" ? (
          <path
            d={drawn.dashed}
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeDasharray="4 6"
          />
        ) : null}
        {drawn.points.map(([x, y], i) => (
          <circle
            key={book.curve[i]?.at ?? i}
            cx={x}
            cy={y}
            r="3"
            fill="currentColor"
          />
        ))}
      </svg>
      <p className="muted">{summary}</p>
      {drawn.dashed !== "" ? (
        <p className="muted">
          A dashed stretch is a time with no mark drawn. The line there only
          joins the marks on either side of it; it does not show how the equity
          moved in between.
        </p>
      ) : null}
      <details>
        <summary>The numbers behind the curve</summary>
        <Scroll label={`Equity at each mark, ${which(book)}`}>
          <table>
            <caption>Equity at each mark</caption>
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Equity</th>
              </tr>
            </thead>
            <tbody>
              {book.curve.map((p) => (
                <tr key={p.at}>
                  <th scope="row">{when(p.at)}</th>
                  <td className="nowrap">{groupedMoney(p.equity)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      </details>
    </>
  );
}

function Held({ book }: { book: PracticeBook }): ReactNode {
  if (book.positions.length === 0)
    return <p className="muted">Nothing is held.</p>;
  return (
    <Scroll label={`Open positions, ${which(book)}`}>
      <table>
        <caption>What is held now</caption>
        <thead>
          <tr>
            <th scope="col">Asset</th>
            <th scope="col">Units</th>
            <th scope="col">Cost with fees</th>
            <th scope="col">Last mark</th>
            <th scope="col">Worth at that mark</th>
          </tr>
        </thead>
        <tbody>
          {book.positions.map((p) => (
            <tr key={p.symbol}>
              <th scope="row">{p.symbol}</th>
              <td>{p.quantity}</td>
              <td className="nowrap">{money2(p.cost)}</td>
              <td className="nowrap">
                {p.mark === null ? "never marked" : groupedMoney(p.mark)}
              </td>
              <td className="nowrap">
                {p.value === null ? "–" : money2(p.value)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </Scroll>
  );
}

function Concentrated({ book }: { book: PracticeBook }): ReactNode {
  const c = concentration(book.trades.map((t) => t.net));
  if (c === null) return null;
  const best =
    c.removed === 1 ? "its best position" : `its best ${c.removed} positions`;
  return (
    <p className={c.flagged ? "notice" : "muted"}>
      Without {best} of {c.count}, the closed positions come to{" "}
      {money2(c.without)} (with them, {money2(c.total)}).
      {c.flagged ? " The result rests on a few positions." : ""}
    </p>
  );
}

function Trips({ book }: { book: PracticeBook }): ReactNode {
  if (book.trades.length === 0)
    return <p className="muted">No position has been closed yet.</p>;
  return (
    <>
      <Concentrated book={book} />
      <Scroll label={`Closed positions, ${which(book)}`}>
        <table>
          <caption>Positions that were closed</caption>
          <thead>
            <tr>
              <th scope="col">Asset</th>
              <th scope="col">Closed</th>
              <th scope="col">Why</th>
              <th scope="col">Units</th>
              <th scope="col">In at</th>
              <th scope="col">Out at</th>
              <th scope="col">Fees</th>
              <th scope="col">Result</th>
              <th scope="col">Call</th>
            </tr>
          </thead>
          <tbody>
            {book.trades.map((t, i) => (
              <tr key={`${t.closed_at}-${t.symbol}-${t.forecast_id ?? ""}`}>
                <th scope="row">{t.symbol}</th>
                <td className="nowrap">{when(t.closed_at)}</td>
                <td>{exitWords(t.exit_kind)}</td>
                <td>{t.quantity}</td>
                <td className="nowrap">{groupedMoney(t.entry_price)}</td>
                <td className="nowrap">{groupedMoney(t.exit_price)}</td>
                <td className="nowrap">{money2(t.fees)}</td>
                <td className="nowrap">{money2(t.net)}</td>
                <td>
                  {t.forecast_id === null ? (
                    "–"
                  ) : (
                    <a
                      href={href({ page: "receipt", id: t.forecast_id })}
                      aria-label={`the call for position ${i + 1} of ${book.trades.length} (${t.symbol}, closed ${when(t.closed_at)}) in the book of ${which(book)}`}
                    >
                      the call
                    </a>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Scroll>
    </>
  );
}

function BookView({ book }: { book: PracticeBook }): ReactNode {
  return (
    <section className="practice-book" aria-labelledby={`b-${book.book}`}>
      <h2 id={`b-${book.book}`} tabIndex={-1}>
        {which(book)}
      </h2>
      <p className="muted">
        {costLine(book)} Opened {when(book.opened_at)} with{" "}
        {money2(book.opening_equity)}; last event {when(book.last_at)}.
      </p>
      <p>
        <strong>{modeWords(book.mode, book.ruined)}.</strong> Equity{" "}
        {money2(book.equity)} ({percentOfShare(book.return_share)} on the
        start); the largest fall from its peak of {money2(book.peak)} was{" "}
        {percentOfShare(book.drawdown).replace("+", "")}. Cash{" "}
        {money2(book.cash)}.
      </p>
      <Curve book={book} />
      <h3>What is held</h3>
      <Held book={book} />
      <h3>What was closed</h3>
      <Trips book={book} />
    </section>
  );
}

export function PracticeView({
  books,
  focus,
}: {
  books: PracticeBook[];
  focus?: string;
}): ReactNode {
  const opened = books.find((b) => b.book === focus) ?? null;
  useOpenOn(opened === null ? null : `b-${opened.book}`);
  return (
    <>
      <p className="kicker">Practice trades</p>
      <h1>
        What the practice portfolio would have done, at three levels of cost.
      </h1>
      <p className="lead">
        A practice portfolio follows an agent&apos;s calls with simulated buys
        and sells. No order was sent anywhere; a fill is a price the model takes
        from the exchange&apos;s one-minute bars, plus the fee and slippage
        shown.
      </p>
      <p className="notice">
        This is a simulation under stated costs, not something anyone traded,
        and short-term profit is not what this product is judged on. A small
        number of closed positions says little about skill; the counts are
        beside every figure.
      </p>
      {books.some((b) => b.arm.endsWith(".shadow")) ? (
        <p className="notice">
          A book that begins &ldquo;If &hellip; had followed the rules
          agent&rdquo; is hypothetical: it shows what passing on those calls
          cost or saved, using the rules agent&apos;s long calls on the slots
          another agent chose to make no call, at the same size and costs. It is
          never part of that agent&apos;s own result.
        </p>
      ) : null}
      {books.length === 0 ? (
        <p className="notice">
          No practice portfolio is recorded in this export. A league runs one
          when its settings turn practice on.
        </p>
      ) : (
        <>
          <Agents books={books} />
          <Scroll label="Every practice book">
            <table>
              <caption>Every practice book</caption>
              <thead>
                <tr>
                  <th scope="col">Agent and costs</th>
                  <th scope="col">Return</th>
                  <th scope="col">Equity</th>
                  <th scope="col">Largest fall</th>
                  <th scope="col">Closed</th>
                  <th scope="col">Fees paid</th>
                  <th scope="col">State</th>
                </tr>
              </thead>
              <tbody>
                {books.map((b) => (
                  <tr key={b.book}>
                    <th scope="row">{which(b)}</th>
                    <td className="nowrap">{percentOfShare(b.return_share)}</td>
                    <td className="nowrap">{money2(b.equity)}</td>
                    <td className="nowrap">
                      {percentOfShare(b.drawdown).replace("+", "")}
                    </td>
                    <td>{count(b.sells, "position")}</td>
                    <td className="nowrap">{money2(b.fees)}</td>
                    <td>{modeWords(b.mode, b.ruined)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
          {books.every((b) => b.buys === 0 && b.positions.length === 0) ? (
            <p className="notice">
              No book has opened a position yet: a position opens only when an
              agent&apos;s call leans far enough, and the first calls have not.
              Each book starts at its opening equity, which the table shows.
            </p>
          ) : (
            books.map((b) => <BookView key={b.book} book={b} />)
          )}
        </>
      )}
    </>
  );
}
