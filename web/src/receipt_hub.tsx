import type { ReactNode } from "react";
import {
  chancePercent,
  deskWord,
  exitWords,
  horizon,
  groupedMoney,
  money2,
  percentOfShare,
  practiceLevel,
  whenShort,
} from "./format.ts";
import { href } from "./route.ts";
import { Scroll } from "./scroll.tsx";
import { FollowControls } from "./alerts.tsx";
import { receiptSummary } from "./summary.ts";
import type { Receipt, ReceiptLinks, Row } from "./types.ts";

/** What a receipt leads to: the other agents asked the same thing, the practice trades that followed this call, and the Desk's
 * runs on its token. Loaded on demand, so the first screen does not carry it. */
export function ReceiptHub({
  links,
  row,
  voices = false,
}: {
  links: ReceiptLinks;
  row: Row;
  voices?: boolean;
}): ReactNode {
  return (
    <>
      <FollowControls
        forecastId={row.forecast_id}
        symbol={row.symbol}
        arm={row.arm}
        armName={row.arm_name}
      />
      <p>
        <a href={href({ page: "callsby", by: "token", value: row.symbol })}>
          Every call on {row.symbol}
        </a>
        {" · "}
        <a href={href({ page: "callsby", by: "agent", value: row.arm })}>
          Every call of the {row.arm_name.toLowerCase()} agent
        </a>
        {voices ? (
          <>
            {" · "}
            <a href={href({ page: "voices" })}>
              What voices and whales said
            </a>{" "}
            <span className="muted">(context the call did not use)</span>
          </>
        ) : null}
      </p>
      <h2>Other agents on the same call</h2>
      {links.others.length === 0 ? (
        <p className="muted">
          No other agent was asked about {row.symbol} for {horizon(row.hours)}{" "}
          in this cycle.
        </p>
      ) : (
        <>
          <p className="muted">
            The same question, the same frozen evidence, the same start. Not
            ranked: one call settles nothing about skill.
          </p>
          <Scroll label="Other agents on the same call">
            <table className="stack">
              <caption>Other agents on the same call</caption>
              <thead>
                <tr>
                  <th scope="col">Agent</th>
                  <th scope="col">Said</th>
                  <th scope="col">What happened</th>
                  <th scope="col">Receipt</th>
                </tr>
              </thead>
              <tbody>
                {links.others.map((o) => (
                  <tr key={o.forecast_id}>
                    <th scope="row">{o.arm_name}</th>
                    <td data-label="Said">{o.chance ?? "No call"}</td>
                    <td data-label="What happened">{o.result ?? o.state}</td>
                    <td data-label="Receipt">
                      <a href={href({ page: "receipt", id: o.forecast_id })}>
                        Open
                        <span className="visually-hidden">
                          {" "}
                          the {o.arm_name.toLowerCase()} agent’s receipt
                        </span>
                      </a>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
        </>
      )}
      {row.chance !== null ? (
        <>
          <h2>What following it did</h2>
          {links.followed.length === 0 ? (
            <p className="muted">
              No practice trade has closed on this call in this export.
            </p>
          ) : (
            <>
              <p className="muted">
                A simulated portfolio, at stated costs: no order was sent
                anywhere. A small amount means the book already held this
                token, or tokens that move with it, close to a limit, so the
                call could add only a little.
              </p>
              <Scroll label="Practice trades that followed this call">
                <table className="stack">
                  <caption>Practice trades that followed this call</caption>
                  <thead>
                    <tr>
                      <th scope="col">Book</th>
                      <th scope="col">Bought</th>
                      <th scope="col">Bought at</th>
                      <th scope="col">Sold at</th>
                      <th scope="col">Net</th>
                      <th scope="col">Why it was sold</th>
                    </tr>
                  </thead>
                  <tbody>
                    {links.followed.map((t) => (
                      <tr key={`${t.book}-${t.closed_at}`}>
                        <th scope="row">
                          <a href={href({ page: "practice", book: t.book })}>
                            {t.arm_name}, {practiceLevel(t.level).toLowerCase()}
                          </a>
                        </th>
                        <td data-label="Bought" className="mono num">
                          {money2(t.spent)}
                        </td>
                        <td data-label="Bought at" className="mono num">
                          {groupedMoney(t.entry_price)}
                        </td>
                        <td data-label="Sold at" className="mono num">
                          {groupedMoney(t.exit_price)}
                        </td>
                        <td data-label="Net" className="mono num">
                          {money2(t.net)}
                        </td>
                        <td data-label="Why it was sold">
                          {exitWords(t.exit_kind)}, {whenShort(t.closed_at)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Scroll>
            </>
          )}
        </>
      ) : null}
      {(links.revisions ?? []).length > 0 ? (
        <>
          <h2>The agent changed its mind</h2>
          <p className="muted">
            The same agent called this token over the same window the other way while the earlier call was still open. Both calls stay as
            they were recorded; what each was made on is shown side by side, not explained.
          </p>
          <ul>
            {(links.revisions ?? []).map((v) => {
              const mine = v.earlier === row.forecast_id;
              const other = mine ? v.later : v.earlier;
              const ret = (r: { ret: string | null }) => (r.ret === null ? "not recorded" : percentOfShare(r.ret));
              return (
                <li key={`${v.earlier}-${v.later}`}>
                  {mine ? "Later reversed" : "This reversed an earlier call"}:{" "}
                  {chancePercent(v.earlier_p)} at {whenShort(v.earlier_origin)}, then {chancePercent(v.later_p)} at{" "}
                  {whenShort(v.later_origin)}. The {v.hours}-hour return was {ret(v.earlier_reading)}, then{" "}
                  {ret(v.later_reading)}.{v.decisive ? "" : " A small swing across an even chance."}{" "}
                  <a href={href({ page: "receipt", id: other })}>Open the {mine ? "later" : "earlier"} call</a>
                </li>
              );
            })}
          </ul>
        </>
      ) : null}
      {links.desk.length > 0 ? (
        <>
          <h2>The Desk on {row.symbol}</h2>
          <p className="muted">
            The Desk investigates the token, not this call: its runs are its own
            and never change this receipt.
          </p>
          <ul>
            {links.desk.map((d) => (
              <li key={d.run_id}>
                <a href={href({ page: "desk", run: d.run_id })}>
                  {deskWord("state", d.state)}
                </a>{" "}
                <span className="muted">({deskWord("label", d.label)})</span>
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

/** The receipt in five seconds, at the top of the page; until this part has loaded the page shows the call's words. */
export function ReceiptLead({ receipt }: { receipt: Receipt }): ReactNode {
  return <>{receiptSummary(receipt)}</>;
}
