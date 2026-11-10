import type { ReactNode } from "react";
import { COPY } from "../copy.ts";
import { disagreementLine, dots } from "../format.ts";
import { B } from "../timing.ts";
import { box, colour, layout, size } from "../theme.ts";
import type { Asset } from "../data.ts";
import { Appear, Centre, Crop, Head, Kicker, Mono, Page, Stage, pan, useFilm } from "../ui/kit.tsx";
import type { Dir } from "../ui/kit.tsx";

/** Scene 3: Track 1, the receipt. Real crops of one call's receipt, one idea each, and the text the call recorded. */
export function Scene3(): ReactNode {
  const film = useFilm();
  const b = B.s3;
  const sources = dots(...new Set(film.hero.evidence.map((e) => e.source)));
  const crop = (asset: Asset, at: number, out: number): ReactNode => <Crop asset={asset} w={box.wide.w} h={box.wide.h} {...pan(at, out)} />;
  const steps: { at: number; out: number; node: ReactNode; under?: string; dir: Dir }[] = [
    { at: b.head, out: b.read, node: crop(film.images.receiptHead, b.head, b.read), dir: "left" },
    { at: b.read, out: b.prove, node: crop(film.images.receiptRead, b.read, b.prove), under: sources, dir: "up" },
    { at: b.prove, out: b.model, node: crop(film.images.receiptProve, b.prove, b.model), under: dots(...film.hero.invalidations), dir: "up" },
    { at: b.model, out: b.disagree, node: crop(film.images.receiptModel, b.model, b.disagree), dir: "up" },
    { at: b.disagree, out: b.snapshot, node: crop(film.images.todayDisagree, b.disagree, b.snapshot), under: film.disagreement === null ? undefined : disagreementLine(film.disagreement), dir: "up" },
  ];
  return (
    <>
      <Page>
        <Appear at={b.kicker}>
          <Kicker>{COPY.kicker.t1}</Kicker>
        </Appear>
        {steps.map((s) => (
          <Stage key={s.at} at={s.at} out={s.out} dir={s.dir}>
            {s.node}
            {s.under === undefined ? null : <Mono style={{ color: colour.ink, fontSize: size.mono }}>{s.under}</Mono>}
          </Stage>
        ))}
      </Page>
      <Appear at={b.snapshot} dir="up" distance={layout.nudge} fill>
        <Centre>
          <Head fontSize={size.title}>{COPY.s3.snapshot}</Head>
        </Centre>
      </Appear>
    </>
  );
}
