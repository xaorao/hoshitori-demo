import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { signed2 } from "../format.ts";
import { STEP } from "../timing.ts";
import { bar, colour, layout } from "../theme.ts";
import { Mono, linear } from "./kit.tsx";

/** The shared-calls comparison as a picture: the interval's own `lower` and `upper` as a bar crossing a tick at zero. */
export function IntervalBar({ lower, upper, at }: { lower: string; upper: string; at: number }): ReactNode {
  const frame = useCurrentFrame();
  const lo = Number(lower);
  const hi = Number(upper);
  const reach = Math.max(Math.abs(lo), Math.abs(hi)) * 1.5;
  const x = (v: number): number => ((v + reach) / (reach * 2)) * bar.width;
  const draw = linear(frame, at, STEP.barDraw);
  const left = x(lo);
  const right = x(hi);
  return (
    <div style={{ position: "relative", width: bar.width, height: bar.label + bar.height }}>
      <div style={{ position: "absolute", left: 0, top: bar.top, width: bar.width, height: layout.hair, background: colour.line }} />
      <div style={{ position: "absolute", left: x(0) - layout.hair, top: bar.top - bar.tick / 2, width: layout.hair * 2, height: bar.tick, background: colour.ink }} />
      <div style={{ position: "absolute", left, top: bar.top - bar.height / 2, width: (right - left) * draw, height: bar.height, background: colour.ink }} />
      <Mono small style={{ position: "absolute", left, top: bar.top + bar.tick, color: colour.graphite, transform: "translateX(-50%)" }}>
        {signed2(lower)}
      </Mono>
      <Mono small style={{ position: "absolute", left: right, top: bar.top + bar.tick, color: colour.graphite, transform: "translateX(-50%)" }}>
        {signed2(upper)}
      </Mono>
    </div>
  );
}
