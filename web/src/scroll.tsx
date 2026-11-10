import type { ReactNode } from "react";

/** A table that may scroll sideways on a small screen: a labelled region a keyboard can reach and scroll. */
export function Scroll({ label, children }: { label: string; children: ReactNode }): ReactNode {
  return (
    <div className="table-wrap" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}
