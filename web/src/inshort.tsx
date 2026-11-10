import type { ReactNode } from "react";

/** The four things a person needs from a page before reading it: what it does, what a result means, what is shown and what is not, and what
 * to do next. Short, in plain words, and only what the page itself shows; each page that has one says the same four things in the same order. */
export function InShort({
  does,
  means,
  sure,
  next,
}: {
  does: string;
  means: string;
  sure: string;
  next: string;
}): ReactNode {
  return (
    <dl className="inshort" aria-label="In short">
      <div>
        <dt>What it does</dt>
        <dd>{does}</dd>
      </div>
      <div>
        <dt>What a result means</dt>
        <dd>{means}</dd>
      </div>
      <div>
        <dt>Shown and not shown</dt>
        <dd>{sure}</dd>
      </div>
      <div>
        <dt>What to do next</dt>
        <dd>{next}</dd>
      </div>
    </dl>
  );
}
