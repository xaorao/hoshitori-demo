import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { ARROW } from "../format.ts";
import { STEP } from "../timing.ts";
import { colour, layout, size } from "../theme.ts";
import { Appear, Col, Mono, linear, useFilm } from "./kit.tsx";

/** The forged digit as the record states it: the edited field, the honest value, and the forged value struck through by the accent. */
export function ForgeryOverlay({ at, lead = STEP.strikeLead, fontSize = size.mono }: { at: number; lead?: number; fontSize?: number }): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const strikeAt = at + lead;
  const strike = linear(frame, strikeAt, STEP.strike);
  const exit = film.logs.verifyForgery.exit;
  return (
    <Col gap={layout.rule * 3}>
      <Appear at={at + STEP.fade}>
        <Mono style={{ fontSize }}>
          {film.forgery.field}: {film.forgery.was} {ARROW}{" "}
          <span style={{ position: "relative", display: "inline-block" }}>
            {film.forgery.now}
            <span style={{ position: "absolute", left: 0, top: "50%", height: layout.rule, width: `${strike * 100}%`, background: colour.amber, transform: "translateY(-50%)" }} />
          </span>
        </Mono>
      </Appear>
      <Appear at={strikeAt + STEP.strike}>
        <Mono style={{ color: colour.graphite, fontSize }}>verify: exit {exit}</Mono>
      </Appear>
    </Col>
  );
}
