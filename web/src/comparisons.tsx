import type { ReactNode } from "react";
import { Scroll } from "./scroll.tsx";
import type { Comparisons } from "./types.ts";

/** Who is who: the agents' names from the record, and the fixed forecast every agent is held against. */
function name(arm: string, names: Map<string, string>): string {
  if (arm === "HALF") return "Always 50%";
  return names.get(arm) ?? arm;
}

export function ComparisonsSection({
  doc,
  names,
}: {
  doc: Comparisons;
  names: Map<string, string>;
}): ReactNode {
  const shown = doc.lines.filter((l) => l.mean !== null);
  const few = doc.lines.filter((l) => l.mean === null && l.shared > 0);
  const most = few.reduce((m, l) => Math.max(m, l.shared), 0);
  return (
    <section aria-labelledby="pairs">
      <h2 id="pairs">Two agents on the same calls</h2>
      <p className="lead">
        Each pair is compared only on the calls both made about the same token
        and window, by how close each was to what happened. The interval stays
        valid however often this page is read; until it leaves zero the pair is
        not distinguishable, and nothing is ranked.
      </p>
      {shown.length === 0 ? (
        <p>
          No pair has {doc.min_shown} settled calls in common yet, the number
          from which a pair is shown.
        </p>
      ) : (
        <Scroll label="Pairs of agents compared on the same calls">
          <table className="stack">
            <caption>Pairs compared on the same calls</caption>
            <thead>
              <tr>
                <th scope="col">Pair</th>
                <th scope="col">Horizon</th>
                <th scope="col" className="num">
                  Shared calls
                </th>
                <th scope="col">Standing</th>
                <th scope="col">Difference (95% interval)</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((l) => (
                <tr key={`${l.first}${l.second}${l.hours}`}>
                  <th scope="row">
                    {name(l.first, names)} against {name(l.second, names)}
                  </th>
                  <td data-label="Horizon">
                    {l.hours} {l.hours === 1 ? "hour" : "hours"}
                  </td>
                  <td data-label="Shared calls" className="num">
                    {l.shared}
                  </td>
                  <td data-label="Standing">
                    {l.state}
                    {l.since === null ? "" : `, since call ${l.since}`}
                  </td>
                  <td data-label="Difference">
                    {`${l.mean} (${l.lower} to ${l.upper})`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Scroll>
      )}
      {few.length > 0 ? (
        <p>
          {few.length} more {few.length === 1 ? "pair has" : "pairs have"} too
          few shared calls to show yet (at most {most}; a pair is shown from{" "}
          {doc.min_shown}).
        </p>
      ) : null}
      <p className="muted">
        A positive difference means the first did better (its Brier score was
        lower). The interval stays valid however often it is checked: a
        confidence sequence at level {doc.alpha}, after Choe and Ramdas. Calls
        made every hour about longer windows overlap, so a horizon of h hours is
        compared only on cycles planned at an hour divisible by h.
      </p>
    </section>
  );
}
