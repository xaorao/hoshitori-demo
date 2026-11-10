import type { CSSProperties, ReactNode } from "react";
import { createContext, useContext } from "react";
import { AbsoluteFill, Easing, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import type { Asset, Film } from "../data.ts";
import { COPY } from "../copy.ts";
import { FPS, SLIDE, STEP, TYPE_CPS } from "../timing.ts";
import { box, colour, crop, font, layout, size, spacing, tracking, weight } from "../theme.ts";

// --- the film, and the clock --------------------------------------------------------------------------------------------------------------

export const FilmContext = createContext<Film | null>(null);

export function useFilm(): Film {
  const film = useContext(FilmContext);
  if (film === null) throw new Error("no film data in context");
  return film;
}

const SLIDE_EASE = Easing.bezier(0.2, 0, 0, 1);

/** Eased progress 0..1 of a 240 ms move that starts at `at`. */
export function ease(frame: number, at: number, length: number = SLIDE): number {
  return interpolate(frame, [at, at + length], [0, 1], { easing: SLIDE_EASE, extrapolateLeft: "clamp", extrapolateRight: "clamp" });
}

export function linear(frame: number, at: number, length: number): number {
  return interpolate(frame, [at, at + length], [0, 1], { extrapolateLeft: "clamp", extrapolateRight: "clamp" });
}

// --- type ---------------------------------------------------------------------------------------------------------------------------------

export function Mono({ children, style, small = false }: { children: ReactNode; style?: CSSProperties; small?: boolean }): ReactNode {
  return (
    <span style={{ fontFamily: font.mono, fontSize: small ? size.monoSmall : size.mono, fontWeight: weight.text, color: colour.ink, ...style }}>{children}</span>
  );
}

export function Head({ children, style, fontSize = size.head }: { children: ReactNode; style?: CSSProperties; fontSize?: number }): ReactNode {
  return (
    <div style={{ fontFamily: font.sans, fontWeight: weight.head, fontSize, letterSpacing: tracking.head, lineHeight: 1.04, color: colour.ink, ...style }}>{children}</div>
  );
}

export function Body({ children, style, fontSize = size.body }: { children: ReactNode; style?: CSSProperties; fontSize?: number }): ReactNode {
  return <div style={{ fontFamily: font.sans, fontWeight: weight.text, fontSize, lineHeight: 1.22, color: colour.ink, letterSpacing: tracking.tight, ...style }}>{children}</div>;
}

export function Kicker({ children, style }: { children: ReactNode; style?: CSSProperties }): ReactNode {
  return (
    <div style={{ fontFamily: font.mono, fontSize: size.kicker, letterSpacing: tracking.kicker, textTransform: "uppercase", color: colour.graphite, ...style }}>{children}</div>
  );
}

/** The scene's white field and left margin. */
export function Page({ children, style }: { children?: ReactNode; style?: CSSProperties }): ReactNode {
  return (
    <AbsoluteFill style={{ background: colour.paper }}>
      <div style={{ position: "absolute", left: layout.marginX, right: layout.marginX, top: layout.top, bottom: layout.height - layout.contentBottom, ...style }}>{children}</div>
    </AbsoluteFill>
  );
}

/** A block centred in the content area. */
export function Centre({ children, gap = spacing.l, style }: { children: ReactNode; gap?: number; style?: CSSProperties }): ReactNode {
  return <Page style={{ display: "flex", flexDirection: "column", justifyContent: "center", gap, ...style }}>{children}</Page>;
}

// --- motion -------------------------------------------------------------------------------------------------------------------------------

export type Dir = "fade" | "up" | "down" | "left" | "right";

/**
 * Something that appears at `at` (frame within its scene) by fading or by a 240 ms eased slide, and optionally slides away at `out`.
 * Its place in the layout is held from the first frame, so nothing around it jumps.
 */
export function Appear({
  at,
  out,
  dir = "fade",
  children,
  style,
  outDir = "up",
  distance = layout.slideDistance,
  fill = false,
}: {
  at: number;
  out?: number;
  dir?: Dir;
  outDir?: Dir;
  distance?: number;
  fill?: boolean;
  children: ReactNode;
  style?: CSSProperties;
}): ReactNode {
  const frame = useCurrentFrame();
  const exiting = out !== undefined && frame >= out;
  const p = exiting ? ease(frame, out) : ease(frame, at);
  const unit = (d: Dir): [number, number] => (d === "up" ? [0, 1] : d === "down" ? [0, -1] : d === "left" ? [1, 0] : d === "right" ? [-1, 0] : [0, 0]);
  const [ux, uy] = exiting ? unit(outDir) : unit(dir);
  const k = exiting ? -p * distance : (1 - p) * distance;
  const opacity = frame < at ? 0 : exiting ? 1 - p : p;
  return (
    <div style={{ opacity, transform: `translate(${ux * k}px, ${uy * k}px)`, ...(fill ? { position: "absolute", inset: 0 } : {}), ...style }}>
      {children}
    </div>
  );
}

/** Text typed at 40 characters a second from frame `at`. The full text holds its place so nothing re-wraps. */
export function Typed({ text, at, cps = TYPE_CPS, style, caret = false }: { text: string; at: number; cps?: number; style?: CSSProperties; caret?: boolean }): ReactNode {
  const frame = useCurrentFrame();
  const n = Math.max(0, Math.min(text.length, Math.floor(((frame - at) * cps) / FPS)));
  return (
    <span style={style}>
      <span>{text.slice(0, n)}</span>
      {caret && n < text.length ? <span style={{ color: colour.fog }}>▌</span> : null}
      <span style={{ opacity: 0 }}>{text.slice(n)}</span>
    </span>
  );
}

/** How many frames Typed needs for a text. */
export function typeFrames(text: string, cps: number = TYPE_CPS): number {
  return Math.ceil((text.length / cps) * FPS);
}

/** The accent. It draws left to right, and it is used for exactly one meaning: this was fixed in time. */
export function SealRule({ at, length = STEP.rule, width, height = layout.rule, style }: { at: number; length?: number; width: number | string; height?: number; style?: CSSProperties }): ReactNode {
  const frame = useCurrentFrame();
  const p = linear(frame, at, length);
  return (
    <div style={{ width, height, ...style }}>
      <div style={{ width: `${p * 100}%`, height: "100%", background: colour.amber }} />
    </div>
  );
}

/** The two-ring mark (web/src/brand.tsx `Mark`): the open ring draws, then the filled ring lands. */
export function Mark({ height, at }: { height: number; at: number }): ReactNode {
  const frame = useCurrentFrame();
  const ring = linear(frame, at, STEP.mark / 2);
  const fill = ease(frame, at + STEP.mark / 2);
  const circumference = 2 * Math.PI * 12;
  return (
    <svg viewBox="0 0 64 32" width={height * 2} height={height} role="img" aria-label="Hoshitori">
      <circle cx="16" cy="16" r="12" fill="none" stroke={colour.ink} strokeWidth="4" strokeDasharray={circumference} strokeDashoffset={circumference * (1 - ring)} transform="rotate(-90 16 16)" />
      <circle cx="48" cy="16" r="14" fill={colour.ink} opacity={fill} />
    </svg>
  );
}

export function MarkLockup({ at, scale = 1 }: { at: number; scale?: number }): ReactNode {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: spacing.l * scale }}>
      <Mark height={size.huge * scale} at={at} />
      <Head fontSize={size.huge * scale} style={{ letterSpacing: tracking.head }}>
        {COPY.name}
      </Head>
    </div>
  );
}

// --- real screens -------------------------------------------------------------------------------------------------------------------------

/**
 * A crop or a pan of a Real screenshot. The PNG is never retouched: the window frames the region that holds the text (`fit="ink"`),
 * scaled to the box width but never past `crop.maxScale`, and when that region is taller than the box the window pans down it.
 * `fit="contain"` shows the whole picture inside the box instead (phone screens).
 */
export function Crop({
  asset,
  w = box.wide.w,
  h = box.wide.h,
  panFrom = 0,
  panLength = 0,
  fit = "ink",
  style,
}: {
  asset: Asset;
  w?: number;
  h?: number;
  panFrom?: number;
  panLength?: number;
  fit?: "ink" | "contain";
  style?: CSSProperties;
}): ReactNode {
  const frame = useCurrentFrame();
  const region = fit === "contain" ? { x: 0, y: 0, w: asset.w, h: asset.h } : asset.ink;
  const scale = fit === "contain" ? Math.min(w / region.w, h / region.h) : Math.min(w / region.w, crop.maxScale);
  const shownW = region.w * scale;
  const shownH = region.h * scale;
  const windowW = Math.min(w, shownW);
  const windowH = Math.min(h, shownH);
  const overflow = Math.max(0, shownH - windowH);
  const travel = panLength > 0 ? linear(frame, panFrom, panLength) : 0;
  return (
    <div style={{ width: windowW, height: windowH, overflow: "hidden", border: `${layout.hair}px solid ${colour.line}`, background: colour.paper, boxSizing: "content-box", position: "relative", ...style }}>
      <Img src={staticFile(asset.path)} style={{ position: "absolute", display: "block", maxWidth: "none", width: asset.w * scale, height: asset.h * scale, left: -region.x * scale, top: -region.y * scale - overflow * travel }} />
    </div>
  );
}

/**
 * A Real recording (Playwright webm). Never sped up: it plays at its own rate from `startFrom`. The window shows the top-left part of the
 * page, scaled by `crop.videoScale`, so the focus ring is large enough to see.
 */
export function Recording({ asset, w = box.video.w, h = box.video.h, startFrom = 0 }: { asset: Asset; w?: number; h?: number; startFrom?: number }): ReactNode {
  return (
    <div style={{ width: w, height: h, border: `${layout.hair}px solid ${colour.line}`, overflow: "hidden", background: colour.paper, boxSizing: "content-box", position: "relative" }}>
      <OffthreadVideo src={staticFile(asset.path)} startFrom={startFrom} muted style={{ position: "absolute", left: -crop.videoLeft, top: 0, width: layout.width * crop.videoScale, height: layout.height * crop.videoScale, maxWidth: "none" }} />
    </div>
  );
}

// --- the dev stamp ------------------------------------------------------------------------------------------------------------------------

export function DevStamp(): ReactNode {
  const film = useFilm();
  if (!film.dev) return null;
  return (
    <div style={{ position: "absolute", top: 0, right: 0, background: colour.ink, color: colour.paper, fontFamily: font.mono, fontSize: size.monoSmall, letterSpacing: tracking.kicker, padding: "12px 24px", zIndex: 10 }}>
      {COPY.dev} · {COPY.devNote}
    </div>
  );
}

/** The honest 30-second clock of scene 6: it is a clock, not a claim, so it follows real screen time and stops where the action is done. */
export function ClockCorner({ text }: { text: string }): ReactNode {
  return (
    <div style={{ position: "absolute", right: layout.marginX, top: layout.top, fontFamily: font.mono, fontSize: size.mono, color: colour.graphite }}>{text}</div>
  );
}

export function useScale(): { w: number; h: number; square: boolean } {
  const { width, height } = useVideoConfig();
  return { w: width, h: height, square: width === height };
}

// --- layout helpers (scenes hold no numbers of their own) ----------------------------------------------------------------------------------

export function Abs({ children, style, ...p }: { children?: ReactNode; style?: CSSProperties; left?: number; top?: number; right?: number; bottom?: number; width?: number | string; height?: number | string }): ReactNode {
  return <div style={{ position: "absolute", ...p, ...style }}>{children}</div>;
}

export function Col({ children, gap = spacing.m, style }: { children?: ReactNode; gap?: number; style?: CSSProperties }): ReactNode {
  return <div style={{ display: "flex", flexDirection: "column", gap, ...style }}>{children}</div>;
}

export function Row({ children, gap = spacing.m, style }: { children?: ReactNode; gap?: number; style?: CSSProperties }): ReactNode {
  return <div style={{ display: "flex", flexDirection: "row", alignItems: "center", gap, ...style }}>{children}</div>;
}

export function Rule({ colourOf = colour.line, width, height = layout.hair, style }: { colourOf?: string; width: number | string; height?: number; style?: CSSProperties }): ReactNode {
  return <div style={{ width, height, background: colourOf, ...style }} />;
}

/** A fraction of a number as a CSS percentage. */
export function pct(fraction: number): string {
  return `${fraction * 100}%`;
}

export { AbsoluteFill as Fill, box, colour, layout, size, spacing };

/** A slot below the kicker where one idea at a time slides in and the previous one slides away. */
export function Stage({ at, out, dir = "up", children, centred = false }: { at: number; out?: number; dir?: Dir; children: ReactNode; centred?: boolean }): ReactNode {
  return (
    <Abs left={layout.origin} top={layout.stageTop} width={layout.page}>
      <Appear at={at} {...(out === undefined ? {} : { out })} dir={dir} outDir="up">
        <div style={{ display: "flex", flexDirection: "column", gap: spacing.m, alignItems: centred ? "center" : "flex-start" }}>{children}</div>
      </Appear>
    </Abs>
  );
}

/** Pan timing for a crop that is on screen from `at` to `out`: it starts after the slide-in and ends before the slide-out. */
export function pan(at: number, out: number): { panFrom: number; panLength: number } {
  return { panFrom: at + SLIDE, panLength: Math.max(0, out - at - SLIDE * 2) };
}

/** A dot on the loop line. A sealed one is the accent: it marks the moment something was fixed in time. */
export function LoopDot({ size: diameter, sealed }: { size: number; sealed: boolean }): ReactNode {
  return <div style={{ width: diameter, height: diameter, borderRadius: diameter, background: sealed ? colour.amber : colour.ink }} />;
}
