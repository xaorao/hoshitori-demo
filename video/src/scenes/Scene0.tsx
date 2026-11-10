import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { B } from "../timing.ts";
import { BigCall, Unknown } from "../ui/BigCall.tsx";
import { Centre, MarkLockup } from "../ui/kit.tsx";

/** Scene 0: the seal. One real call, one real time, one word. */
export function Scene0(): ReactNode {
  const frame = useCurrentFrame();
  const b = B.s0;
  if (frame >= b.mark) {
    return (
      <Centre>
        <MarkLockup at={b.mark} />
      </Centre>
    );
  }
  return (
    <Centre>
      <BigCall at={b.number} ruleAt={b.rule} after={<Unknown at={b.unknown} />} />
    </Centre>
  );
}
