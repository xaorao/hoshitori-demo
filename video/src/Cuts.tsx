import type { ReactNode } from "react";
import { Sequence, useVideoConfig } from "remotion";
import { COPY } from "./copy.ts";
import { dots } from "./format.ts";
import { addressRows } from "./rows.ts";
import type { Film } from "./data.ts";
import { C10, C30, CUT10_FRAMES, CUT30_FRAMES, STEP, frames } from "./timing.ts";
import { cut10Events, cut30Events } from "./sound.ts";
import { box, colour, layout, seal, shape, size, spacing } from "./theme.ts";
import { BigCall } from "./ui/BigCall.tsx";
import { ForgeryOverlay } from "./ui/Forgery.tsx";
import { CaptionLayers } from "./ui/Layers.tsx";
import { ScaledStage } from "./ui/ScaledStage.tsx";
import { StampsRow } from "./ui/Seal.tsx";
import { SettleFrame } from "./ui/SettleFrame.tsx";
import { SoundTrack } from "./ui/Sound.tsx";
import { Terminal } from "./ui/Terminal.tsx";
import { Abs, Appear, Centre, Crop, DevStamp, Fill, FilmContext, Head, Mono, MarkLockup, Page, Row, Stage, useFilm } from "./ui/kit.tsx";
import { Unknown } from "./ui/BigCall.tsx";

const none = shape.none;

/** The seal frame of both cuts: the call, its chance, the amber rule and its commit time. */
function SealFrame(): ReactNode {
  return (
    <Centre>
      <BigCall at={none} ruleAt={frames(0.4)} after={<Unknown at={frames(1.4)} />} />
    </Centre>
  );
}

function Closing({ tagline }: { tagline: string }): ReactNode {
  return (
    <Centre gap={spacing.l}>
      <MarkLockup at={none} />
      <Appear at={STEP.fade}>
        <Mono style={{ fontSize: size.lead, color: colour.graphite }}>{tagline}</Mono>
      </Appear>
    </Centre>
  );
}

/** The cuts' version of scene 4: the two stamps, then both verifiers' exit codes under them. */
function StampsAndVerify({ stampsAt, verifyAt }: { stampsAt: number; verifyAt: number }): ReactNode {
  const film = useFilm();
  return (
    <Page>
      <StampsRow at={stampsAt} top={seal.cutTop} />
      <Abs left={layout.origin} top={seal.cutVerifyTop} width={layout.page} style={{ textAlign: "center" }}>
        <Appear at={verifyAt} dir="up" distance={layout.nudge}>
          <Mono style={{ fontSize: size.title }}>{dots(`verify: exit ${film.logs.verifyPython.exit}`, `exit ${film.logs.verifyNode.exit}`)}</Mono>
        </Appear>
      </Abs>
    </Page>
  );
}

function Piece({ from, to, children }: { from: number; to: number; children: ReactNode }): ReactNode {
  return (
    <Sequence from={frames(from)} durationInFrames={frames(to - from)} layout="none">
      {children}
    </Sequence>
  );
}

/** The 30-second cut (VIDEO.md section E). */
export function Cut30({ film }: { film: Film }): ReactNode {
  const c = C30;
  const verdicts = addressRows(film.logs.skillAddress).filter((r) => r.kind !== "cmd").slice(none, shape.pairCount);
  return (
    <FilmContext.Provider value={film}>
      <Fill style={{ background: colour.paper }}>
        <Piece from={c.call} to={c.promise}>
          <SealFrame />
        </Piece>
        <Piece from={c.promise} to={c.prove}>
          <Centre>
            <Appear at={none} dir="right">
              <Head fontSize={size.head}>{film.today.product}</Head>
            </Appear>
          </Centre>
        </Piece>
        <Piece from={c.prove} to={c.verify}>
          <Page>
            <Stage at={none} dir="left">
              <Crop asset={film.images.receiptProve} w={box.wide.w} h={box.wide.h} />
              <Mono style={{ fontSize: size.mono }}>{dots(...film.hero.invalidations)}</Mono>
            </Stage>
          </Page>
        </Piece>
        <Piece from={c.verify} to={c.verify + shape.cutDiagram}>
          <StampsAndVerify stampsAt={none} verifyAt={frames(shape.cutStamps)} />
        </Piece>
        <Piece from={c.verify + shape.cutDiagram} to={c.settle}>
          <Centre>
            <ForgeryOverlay at={none} lead={frames(shape.cutStrike)} fontSize={size.title} />
          </Centre>
        </Piece>
        <Piece from={c.settle} to={c.today}>
          <Centre>
            <SettleFrame at={frames(shape.cutFell)} />
          </Centre>
        </Piece>
        <Piece from={c.today} to={c.skills}>
          <Page>
            <Stage at={none} dir="left">
              <Row gap={spacing.l} style={{ alignItems: "flex-start" }}>
                <Crop asset={film.images.todayChanges} w={box.wide.w - box.phone.w - spacing.l} h={box.wide.h} />
                <Crop asset={film.images.todayPhoneLight} w={box.phone.w} h={box.wide.h} fit="contain" />
              </Row>
            </Stage>
          </Page>
        </Piece>
        <Piece from={c.skills} to={c.close}>
          <Centre>
            <Terminal rows={verdicts} seconds={c.close - c.skills} width={box.wide.w} fontSize={size.mono} />
          </Centre>
        </Piece>
        <Piece from={c.close} to={c.end}>
          <Closing tagline={COPY.s10.repo} />
        </Piece>
        <CaptionLayers which="cut30" />
        <SoundTrack events={cut30Events()} length={CUT30_FRAMES} fadeOut={false} />
        <DevStamp />
      </Fill>
    </FilmContext.Provider>
  );
}

/** The 10-second cut, 16:9 or 1:1. The square scales the 16:9 stage and gives the captions the room below. */
export function Cut10({ film }: { film: Film }): ReactNode {
  const c = C10;
  const { width } = useVideoConfig();
  const captionScale = width === layout.width ? shape.cutCaption : shape.cutCaptionSquare;
  return (
    <FilmContext.Provider value={film}>
      <Fill style={{ background: colour.paper }}>
        <ScaledStage>
          <Piece from={c.seal} to={c.stamps}>
            <Fill>
              <SealFrame />
            </Fill>
          </Piece>
          <Piece from={c.stamps} to={c.settle}>
            <Fill>
              <StampsAndVerify stampsAt={none} verifyAt={frames(shape.cutStampsShort)} />
            </Fill>
          </Piece>
          <Piece from={c.settle} to={c.close}>
            <Fill>
              <Centre>
                <SettleFrame at={frames(shape.cutFellShort)} />
              </Centre>
            </Fill>
          </Piece>
          <Piece from={c.close} to={c.end}>
            <Fill>
              <Closing tagline={COPY.cut.tagline} />
            </Fill>
          </Piece>
        </ScaledStage>
        <CaptionLayers which="cut10" scale={captionScale} />
        <SoundTrack events={cut10Events()} length={CUT10_FRAMES} fadeOut={false} />
        <DevStamp />
      </Fill>
    </FilmContext.Provider>
  );
}

