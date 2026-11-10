import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { counter } from "../format.ts";
import { B, COUNTER_SECONDS, FPS } from "../timing.ts";
import { box, layout, shape, spacing } from "../theme.ts";
import { Appear, ClockCorner, Crop, Kicker, Page, Recording, Row, Stage, pan, useFilm } from "../ui/kit.tsx";

/** Scene 6: Track 2. Real screens of the interface, a clock that is honest about screen time, and the keyboard run. */
export function Scene6(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s6;
  const elapsed = Math.min(Math.max(frame - b.changes, layout.origin) / FPS, COUNTER_SECONDS);
  return (
    <>
      <Page>
        <Appear at={b.kicker}>
          <Kicker>{COPY.kicker.t2}</Kicker>
        </Appear>
        <Stage at={b.top} out={b.changes} dir="left">
          <Crop asset={film.images.todayTop} w={box.wide.w} h={box.wide.h} {...pan(b.top, b.changes)} />
        </Stage>
        <Stage at={b.changes} out={b.keyboard}>
          <Crop asset={film.images.todayChanges} w={box.wide.w} h={box.wide.h} {...pan(b.changes, b.keyboard)} />
        </Stage>
        <Stage at={b.keyboard} out={b.phones} centred>
          <Recording asset={film.videos.todayKeyboard} w={box.video.w} h={box.video.h} startFrom={Math.max(layout.origin, Math.round(film.videos.todayKeyboard.seconds * FPS) - (b.phones - b.keyboard))} />
        </Stage>
        <Stage at={b.phones} out={b.judges} centred>
          <Row gap={shape.phoneGap} style={{ alignItems: "flex-start" }}>
            <Crop asset={film.images.todayPhoneLight} w={box.phone.w} h={box.phone.h} fit="contain" />
            <Crop asset={film.images.todayPhoneDark} w={box.phone.w} h={box.phone.h} fit="contain" />
          </Row>
        </Stage>
        <Stage at={b.judges} dir="left">
          <Crop asset={film.images.judgesPage} w={box.wide.w} h={box.wide.h} {...pan(b.judges, b.stop)} />
        </Stage>
      </Page>
      {frame >= b.changes ? <ClockCorner text={counter(elapsed)} /> : null}
    </>
  );
}
