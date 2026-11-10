import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { verifierRows } from "../rows.ts";
import { B, FPS } from "../timing.ts";
import { box, size, spacing } from "../theme.ts";
import { Appear, Centre, Crop, Page, Row, pan, useFilm } from "../ui/kit.tsx";
import { ForgeryOverlay } from "../ui/Forgery.tsx";
import { SealDiagram } from "../ui/Seal.tsx";
import { Terminal } from "../ui/Terminal.tsx";

/** Scene 4: the seal, the browser check, the two verifiers, and the one forged digit that is refused. */
export function Scene4(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s4;
  if (frame < b.verify) return <SealDiagram />;
  if (frame < b.logs) {
    return (
      <Centre>
        <Appear at={b.verify} dir="left">
          <Crop asset={film.images.verifyPage} w={box.wide.w} h={box.tall.h} {...pan(b.verify, b.logs)} />
        </Appear>
      </Centre>
    );
  }
  if (frame < b.forged) {
    const rows = verifierRows(film);
    return (
      <Centre>
        <Appear at={b.logs} dir="left">
          <Terminal rows={rows} at={b.logs} seconds={(b.forged - b.logs) / FPS} width={box.wide.w} fontSize={size.mono} />
        </Appear>
      </Centre>
    );
  }
  return (
    <Page>
      <Row gap={spacing.l} style={{ alignItems: "flex-start" }}>
        <Appear at={b.forged} dir="left">
          <Crop asset={film.images.verifyForgery} w={box.half.w} h={box.tall.h} {...pan(b.forged, b.end)} />
        </Appear>
        <ForgeryOverlay at={b.forged} />
      </Row>
    </Page>
  );
}
