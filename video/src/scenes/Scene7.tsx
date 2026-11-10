import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { B, STEP } from "../timing.ts";
import { box, colour, drills, font, layout, size, spacing } from "../theme.ts";
import { Appear, Centre, Col, Crop, Head, Mono, Page, Row, Stage, Typed, pan, useFilm } from "../ui/kit.tsx";

function DrillList({ at }: { at: number }): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  return (
    <Col gap={layout.hair} style={{ width: drills.listW }}>
      {film.drills.map((d, i) => {
        const lit = frame >= at + i * STEP.drillGap + STEP.fade;
        return (
          <div key={d.id} style={{ display: "flex", justifyContent: "space-between", height: drills.row, fontFamily: font.mono, fontSize: size.tiny, color: lit ? colour.ink : colour.fog, borderBottom: `${layout.hair}px solid ${colour.line}`, alignItems: "center" }}>
            <span>{d.id}</span>
            <span>{lit ? (d.held ? COPY.s7.held : "") : ""}</span>
          </div>
        );
      })}
    </Col>
  );
}

/** Scene 7: coping with failure, shown by the record's own drills, its own outage and the rule about nulls. */
export function Scene7(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s7;
  if (frame < b.drills) {
    return (
      <Centre>
        <Appear at={b.claim} dir="up" distance={layout.nudge}>
          <Head fontSize={size.title}>{COPY.s7.claim}</Head>
        </Appear>
      </Centre>
    );
  }
  if (frame < b.missed) {
    return (
      <Page>
        <Appear at={b.drills} dir="left">
          <Row gap={drills.gap} style={{ alignItems: "flex-start" }}>
            <Crop asset={film.images.drillPage} w={drills.cropW} h={box.tall.h} {...pan(b.drills, b.missed)} />
            <DrillList at={b.drills} />
          </Row>
        </Appear>
      </Page>
    );
  }
  if (frame < b.never) {
    return (
      <Page>
        <Stage at={b.missed} dir="left">
          <Crop asset={film.images.recordMissed} w={box.wide.w} h={box.tall.h} {...pan(b.missed, b.never)} />
        </Stage>
      </Page>
    );
  }
  return (
    <Centre gap={spacing.m}>
      <Mono style={{ fontSize: size.lead }}>
        <Typed text={COPY.s7.never} at={b.never} style={{ fontFamily: "inherit" }} />
      </Mono>
    </Centre>
  );
}
