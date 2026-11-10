import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { count, dots } from "../format.ts";
import { B, STEP } from "../timing.ts";
import { box, layout, size, spacing } from "../theme.ts";
import { Appear, Centre, Col, Crop, Mono, pan, useFilm } from "../ui/kit.tsx";
import { SettleFrame } from "../ui/SettleFrame.tsx";
import { IntervalBar } from "../ui/IntervalBar.tsx";

/** Scene 5: the call settles, the miss stays, and where the record is too short it says so instead of ranking. */
export function Scene5(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s5;
  if (frame < b.record) {
    return (
      <Centre>
        <SettleFrame at={b.tone} />
      </Centre>
    );
  }
  if (frame < b.compare) {
    return (
      <Centre>
        <Appear at={b.record} dir="left">
          <Crop asset={film.images.recordPage} w={box.wide.w} h={box.tall.h} {...pan(b.record, b.compare)} />
        </Appear>
      </Centre>
    );
  }
  const line = film.compare.line;
  return (
    <Centre gap={spacing.l}>
      <Appear at={b.compare} dir="up" distance={layout.nudge}>
        <Col gap={spacing.l}>
          <Mono style={{ fontSize: size.mono }}>{dots(`${film.compare.first} ${COPY.s5.versus} ${film.compare.second}`, count(line.shared, "shared call"), line.state)}</Mono>
          {line.lower !== null && line.upper !== null ? <IntervalBar lower={line.lower} upper={line.upper} at={b.compare + STEP.fade} /> : null}
        </Col>
      </Appear>
    </Centre>
  );
}
