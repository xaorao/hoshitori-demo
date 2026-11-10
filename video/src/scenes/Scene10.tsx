import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { B, FPS, STEP } from "../timing.ts";
import { box, colour, size, spacing } from "../theme.ts";
import { Appear, Body, Centre, Col, Mono, MarkLockup, Typed, useFilm } from "../ui/kit.tsx";
import { Terminal } from "../ui/Terminal.tsx";
import { resettleRows } from "../rows.ts";

/** Scene 10: the one command that re-derives every outcome with none of our code, then the mark and the way to the repository. */
export function Scene10(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s10;
  if (frame >= b.closing) {
    return (
      <Centre gap={spacing.l}>
        <MarkLockup at={b.closing} />
        <Appear at={b.closing + STEP.fade}>
          <Col gap={spacing.m}>
            <Mono style={{ fontSize: size.lead }}>
              <Typed text={COPY.s10.repo} at={b.closing + STEP.fade} style={{ fontFamily: "inherit" }} />
            </Mono>
            {COPY.s10.closing.map((line) => (
              <Body key={line} fontSize={size.lead} style={{ color: colour.graphite }}>
                {line}
              </Body>
            ))}
          </Col>
        </Appear>
      </Centre>
    );
  }
  const rows = resettleRows(film);
  return (
    <Centre>
      <Terminal rows={rows} at={b.terminal} seconds={(b.closing - b.terminal) / FPS} width={box.wide.w} fontSize={size.mono} />
    </Centre>
  );
}
