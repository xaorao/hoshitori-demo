import type { ReactNode } from "react";
import { currencyWords, groupedMoney, sweepSentence } from "./format.ts";
import { Scroll } from "./scroll.tsx";
import type { Sweeps } from "./types.ts";

function whole(n: number): string {
  return groupedMoney(String(n));
}

/** What the mutation sweeps found, from the sweep page's own data: the summary in words, then every module, the least proven first. */
export function SweepsView({ sweeps }: { sweeps: Sweeps }): ReactNode {
  return (
    <section aria-labelledby="sweeps-title">
      <h2 id="sweeps-title">How hard the checks were tried</h2>
      <p>{sweepSentence(sweeps.summary)}</p>
      {sweeps.rows.length === 0 ? null : (
        <details>
          <summary>Every module, the least proven first ({whole(sweeps.rows.length)})</summary>
          <Scroll label="Mutation sweep of every module">
            <table>
              <caption>Changes made to each module&apos;s code, and what noticed them</caption>
              <thead>
                <tr>
                  <th scope="col">Module</th>
                  <th scope="col">Changes</th>
                  <th scope="col">Noticed</th>
                  <th scope="col">Not noticed</th>
                  <th scope="col">Build or check error</th>
                  <th scope="col">Machine only</th>
                  <th scope="col">Still the files swept</th>
                </tr>
              </thead>
              <tbody>
                {sweeps.rows.map((r) => (
                  <tr key={r.module}>
                    <th scope="row" className="mono nowrap">
                      {r.module}
                    </th>
                    <td>{whole(r.places)}</td>
                    <td>{whole(r.noticed)}</td>
                    <td>{whole(r.survived)}</td>
                    <td>{whole(r.unbuilt + r.raised)}</td>
                    <td>{whole(r.clock)}</td>
                    <td>
                      <span className={r.current === false ? "pill warn" : "muted"}>{currencyWords(r.current, r.reason)}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Scroll>
        </details>
      )}
    </section>
  );
}
