import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { abbreviateCommand, commandOf, envelopeKeys } from "../logs.ts";
import { addressRows, indicatorRows } from "../rows.ts";
import { B, FPS, STEP } from "../timing.ts";
import { box, colour, size, spacing } from "../theme.ts";
import { Appear, Centre, Col, Kicker, Mono, Page, Stage, useFilm } from "../ui/kit.tsx";
import { Terminal } from "../ui/Terminal.tsx";

/** Scene 8: Track 3. Seven names, the gap in RYO's own words, then three recorded runs of the skills. */
export function Scene8(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s8;
  const seconds = (from: number, to: number): number => (to - from) / FPS;
  return (
    <>
      <Page>
        <Appear at={b.names}>
          <Kicker>{COPY.kicker.t3}</Kicker>
        </Appear>
        {frame < b.gap ? (
          <Stage at={b.names + STEP.fade}>
            <Col gap={spacing.s}>
              {COPY.s8.names.map((name, i) => (
                <Appear key={name} at={b.names + STEP.fade + i * STEP.skillGap} dir="up" distance={spacing.s}>
                  <Mono style={{ fontSize: size.lead }}>{name}</Mono>
                </Appear>
              ))}
            </Col>
          </Stage>
        ) : null}
        {frame >= b.address && frame < b.envelope ? (
          <Stage at={b.address} dir="left">
            <Terminal rows={addressRows(film.logs.skillAddress)} at={b.address} seconds={seconds(b.address, b.envelope) - STEP.holdSafety / FPS} width={box.wide.w} />
          </Stage>
        ) : null}
        {frame >= b.envelope && frame < b.indicator ? (
          <Stage at={b.envelope} dir="left">
            <Mono small style={{ color: colour.graphite }}>{abbreviateCommand(commandOf(film.logs.skillEnvelope) ?? "")}</Mono>
            <Col gap={spacing.s}>
              {envelopeKeys(film.logs.skillEnvelope).map((key, i) => (
                <Appear key={key} at={b.envelope + i * STEP.skillGap} dir="fade">
                  <Mono style={{ fontSize: size.mono }}>{key}</Mono>
                </Appear>
              ))}
            </Col>
          </Stage>
        ) : null}
        {frame >= b.indicator ? (
          <Stage at={b.indicator} dir="left">
            <Terminal rows={indicatorRows(film.logs.skillIndicator)} at={b.indicator} seconds={seconds(b.indicator, b.end)} width={box.wide.w} />
          </Stage>
        ) : null}
      </Page>
      {frame >= b.gap && frame < b.address ? (
        <Centre>
          <Appear at={b.gap} dir="up" distance={spacing.s}>
            <Mono style={{ fontSize: size.title }}>{COPY.s8.quote}</Mono>
          </Appear>
        </Centre>
      ) : null}
    </>
  );
}
