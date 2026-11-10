import type { ReactNode } from "react";
import { COPY } from "../copy.ts";
import { callKicker, stamp } from "../format.ts";
import { STEP } from "../timing.ts";
import { colour, layout, size, spacing } from "../theme.ts";
import { Appear, Body, Col, Head, Mono, Row, SealRule, useFilm } from "./kit.tsx";

/**
 * The frame scenes 0 and 5 share: the call's kicker, its chance, the amber rule that means "fixed in time" and the commit time.
 * Scene 0 puts "Outcome unknown." right of the rule; scene 5 puts the settled word right of the number and its prices under it.
 */
export function BigCall({
  at,
  ruleAt,
  after,
  rightOfNumber,
  underRule,
}: {
  at: number;
  ruleAt: number;
  after?: ReactNode;
  rightOfNumber?: ReactNode;
  underRule?: ReactNode;
}): ReactNode {
  const film = useFilm();
  const { row } = film.hero;
  return (
    <Col gap={spacing.m}>
      <Appear at={at}>
        <Mono style={{ color: colour.graphite }}>{callKicker(row)}</Mono>
      </Appear>
      <Row gap={spacing.l} style={{ alignItems: "baseline" }}>
        <Appear at={at + STEP.numberLead} dir="up" distance={layout.nudge}>
          <Head fontSize={size.giant} style={{ whiteSpace: "nowrap" }}>
            {row.chance}
          </Head>
        </Appear>
        {rightOfNumber}
      </Row>
      <Row gap={spacing.l}>
        <SealRule at={ruleAt} width={layout.ruleWide} />
        {after}
      </Row>
      <Appear at={ruleAt}>
        <Mono>
          {COPY.committedPrefix} {stamp(film.hero.committed_at)}
        </Mono>
      </Appear>
      {underRule}
    </Col>
  );
}

export function Unknown({ at }: { at: number }): ReactNode {
  return (
    <Appear at={at}>
      <Body style={{ color: colour.graphite }}>{COPY.unknown}</Body>
    </Appear>
  );
}
