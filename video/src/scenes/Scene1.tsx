import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { B, STEP } from "../timing.ts";
import { colour, size, spacing, weight } from "../theme.ts";
import { Appear, Body, Centre, Col, Head, Mono, useFilm } from "../ui/kit.tsx";

/** Scene 1: the problem, in the user's three questions, and then the product's own promise. */
export function Scene1(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s1;
  if (frame >= b.promise) {
    return (
      <Centre>
        <Appear at={b.promise} dir="right">
          <Head fontSize={size.head}>{film.today.product}</Head>
        </Appear>
      </Centre>
    );
  }
  return (
    <Centre gap={spacing.xl}>
      <Body fontSize={size.title} style={{ fontWeight: weight.head }}>
        {COPY.s1.claim}
      </Body>
      <Col gap={spacing.m}>
        {COPY.s1.questions.map((q, i) => (
          <Appear key={q} at={b.questions + i * STEP.questionGap} dir="up">
            <Mono style={{ color: colour.graphite, fontSize: size.mono }}>{q}</Mono>
          </Appear>
        ))}
      </Col>
    </Centre>
  );
}
