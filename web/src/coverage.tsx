import type { ReactNode } from "react";
import { coverageGapReason, coverageGapTitle, coverageSentence, coverageVerdictWords } from "./format.ts";
import type { Coverage } from "./types.ts";

/** What the requirements rest on, from the two generated matrices: the counts in words, then every section that has no check, with the reason.
 * The sections are a list and not a table, so the reason stays beside its section at every width. */
export function CoverageView({ coverage }: { coverage: Coverage }): ReactNode {
  return (
    <section aria-labelledby="coverage-title">
      <h2 id="coverage-title">What the requirements rest on</h2>
      <p>{coverageSentence(coverage)}</p>
      {coverage.gaps.length === 0 ? null : (
        <details>
          <summary>Sections of the contract with no check ({coverage.gaps.length})</summary>
          <ul className="findings">
            {coverage.gaps.map((g) => (
              <li key={g.number}>
                <span className="mono">{g.number}</span> <strong>{coverageGapTitle(g.title)}</strong>, <span className="muted">{coverageVerdictWords(g.verdict)}</span>.{" "}
                {coverageGapReason(g.reason)}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
