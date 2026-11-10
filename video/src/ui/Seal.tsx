import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { B, STEP } from "../timing.ts";
import { colour, font, layout, seal, size, stampLeft, weight } from "../theme.ts";
import { Abs, Appear, Mono, Page, ease, linear, useFilm } from "./kit.tsx";

const LINE = layout.hair * 3;

function StampMark({ name, at }: { name: string; at: number }): ReactNode {
  const frame = useCurrentFrame();
  const p = ease(frame, at);
  const scale = seal.stampFrom + (1 - seal.stampFrom) * p;
  return (
    <div
      style={{
        width: seal.stampW,
        height: seal.stampH,
        boxSizing: "border-box",
        border: `${seal.stampBorder}px solid ${colour.amber}`,
        color: colour.amber,
        fontFamily: font.sans,
        fontWeight: weight.head,
        fontSize: size.lead,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        opacity: frame < at ? 0 : p,
        transform: `scale(${scale})`,
      }}
    >
      {name}
    </div>
  );
}

/** Scene 4's drawn diagram: a batch of squares gathers into one bracket (the checkpoint), then two operators stamp it, 0.3 s apart. */
export function SealDiagram({ at = B.s4.diagram, stampsAt = B.s4.stamps }: { at?: number; stampsAt?: number }): ReactNode {
  const frame = useCurrentFrame();
  const gather = ease(frame, at, STEP.squares);
  const bracket = linear(frame, at + STEP.squares, STEP.rule);
  const labelAt = at + STEP.squares + STEP.rule;
  const edge = `${LINE}px solid ${colour.ink}`;
  return (
    <Page>
      {seal.index.map((i) => {
        const from = seal.spread[i] ?? 0;
        const to = seal.packed[i] ?? 0;
        return (
          <Abs key={i} left={from + (to - from) * gather} top={seal.top} width={seal.size} height={seal.size}>
            <div style={{ width: seal.size, height: seal.size, boxSizing: "border-box", border: edge }} />
          </Abs>
        );
      })}
      <Abs left={seal.bracketLeft} top={seal.bracketTop} width={seal.bracketWidth * bracket} height={seal.bracketHeight}>
        <div style={{ width: "100%", height: "100%", boxSizing: "border-box", borderLeft: edge, borderBottom: edge, borderRight: edge }} />
      </Abs>
      <Abs left={layout.origin} top={seal.labelTop} width={layout.page} style={{ textAlign: "center" }}>
        <Appear at={labelAt}>
          <Mono>{COPY.s4.checkpoint}</Mono>
        </Appear>
      </Abs>
      <StampsRow at={stampsAt} top={seal.stampTop} />
    </Page>
  );
}

/** The two operators' amber marks, side by side, stamping down 0.3 s apart. */
export function StampsRow({ at, top }: { at: number; top: number }): ReactNode {
  const film = useFilm();
  return (
    <>
      {film.operators.map((name, i) => (
        <Abs key={name} left={stampLeft(film.operators.length, i)} top={top} width={seal.stampW} height={seal.stampH}>
          <StampMark name={name} at={at + i * STEP.stampGap} />
        </Abs>
      ))}
    </>
  );
}
