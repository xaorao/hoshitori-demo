import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { planTerminal, type Row } from "../logs.ts";
import { FPS, STEP, TYPE_CPS } from "../timing.ts";
import { colour, font, layout, size, spacing, weight } from "../theme.ts";
import { Typed } from "./kit.tsx";

/** The longest line the terminal prints before the end is clipped, in characters (mono, at the terminal's size, inside the full width). */
export const TERMINAL_COLUMNS = 82;

/**
 * A recorded terminal. Commands are typed at 40 characters a second from the log's own text, never retyped by hand; output is typed too
 * while the scene's time allows it and printed line by line when it does not. `suffix` lands (in ink 800) once its line is done.
 */
export function Terminal({
  rows,
  at = 0,
  seconds,
  width,
  fontSize = size.monoSmall,
  minHeight,
  hold = STEP.holdSafety,
}: {
  rows: readonly Row[];
  at?: number;
  seconds: number;
  width: number | string;
  fontSize?: number;
  minHeight?: number;
  hold?: number;
}): ReactNode {
  const frame = useCurrentFrame() - at;
  const plan = planTerminal(rows, seconds, TYPE_CPS, FPS, STEP.cmdPause, STEP.printLead, hold);
  return (
    <div
      style={{
        width,
        minHeight,
        boxSizing: "border-box",
        background: colour.halo,
        border: `${layout.hair}px solid ${colour.line}`,
        padding: `${spacing.l}px ${spacing.l}px`,
        fontFamily: font.mono,
        fontSize,
        lineHeight: 1.5,
        color: colour.ink,
        whiteSpace: "pre-wrap",
        wordBreak: "break-word",
        overflow: "hidden",
      }}
    >
      {rows.map((row, i) => {
        const start = plan.starts[i] ?? 0;
        const done = start + (plan.durations[i] ?? 0);
        const typed = plan.typed[i] === true;
        const colourOf = row.kind === "cmd" ? colour.ink : row.kind === "out" ? colour.graphite : colour.ink;
        return (
          <div key={`${i}-${row.text}`} style={{ opacity: frame < start ? 0 : 1, minHeight: fontSize * 1.5 }}>
            {row.kind === "cmd" ? <span style={{ color: colour.fog }}>$ </span> : null}
            {typed ? <Typed text={row.text} at={start + at} cps={TYPE_CPS * plan.speed} style={{ color: colourOf, fontWeight: row.kind === "strong" ? weight.head : weight.text }} /> : <span style={{ color: colourOf, fontWeight: row.kind === "strong" ? weight.head : weight.text }}>{row.text}</span>}
            {row.suffix !== undefined && frame >= done ? <span style={{ color: colour.ink, fontWeight: weight.head }}> {row.suffix}</span> : null}
          </div>
        );
      })}
    </div>
  );
}
