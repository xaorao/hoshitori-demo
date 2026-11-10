import type { ReactNode } from "react";
import { count } from "./format.ts";
import type { Row } from "./types.ts";

const SHOWN = 60;

/** Every call on one token, or of one agent, newest first: the lens a side page opens when it names a token or an agent. The table is the
 * Calls page's own (passed in), so a call reads the same wherever it is listed. Nothing is ranked. */
export function CallsBy({
  rows,
  by,
  value,
  table,
}: {
  rows: Row[];
  by: "token" | "agent";
  value: string;
  table: (rows: Row[], caption: string) => ReactNode;
}): ReactNode {
  const mine = rows
    .filter((r) => (by === "token" ? r.symbol === value : r.arm === value))
    .sort(
      (a, b) =>
        (b.origin ?? b.due ?? "").localeCompare(a.origin ?? a.due ?? "") ||
        a.forecast_id.localeCompare(b.forecast_id),
    );
  const name = by === "agent" ? (mine[0]?.arm_name ?? value) : value;
  const title =
    by === "token"
      ? `Every call on ${value}`
      : `Every call of the ${name.toLowerCase()} agent`;
  const settled = mine.filter((r) => r.state === "Settled").length;
  return (
    <>
      <p className="kicker">Calls</p>
      <h1>{title}</h1>
      {mine.length === 0 ? (
        <p>
          No call {by === "token" ? `on ${value}` : "of that agent"} is in this
          export.
        </p>
      ) : (
        <>
          <p className="lead">
            {count(mine.length, "call")}, {settled} settled. Each opens its
            receipt; none is ranked.
          </p>
          {table(
            mine.slice(0, SHOWN),
            mine.length > SHOWN
              ? `The latest ${SHOWN} of ${mine.length}`
              : title,
          )}
        </>
      )}
    </>
  );
}
