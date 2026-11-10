import type { ReactNode } from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import captionsEn from "../captions.en.json";
import subtitlesJa from "../subtitles.ja.json";
import { applyOverrides, beatAt, overridesFor, resolveBeats, type Beat, type CompositionKey, type Layers, type Overrides } from "../captions.ts";
import type { Film } from "../data.ts";
import { COPY } from "../copy.ts";
import { FPS } from "../timing.ts";
import { colour, font, layout, size, weight } from "../theme.ts";
import { useFilm } from "./kit.tsx";

const EN = captionsEn as Layers;
const JA = subtitlesJa as Layers;

/** The beats the record changes: see COPY.override. */
export function filmOverrides(film: Film): Overrides {
  return overridesFor({ missedCycles: film.today.missed_cycles.length, resettleCount: film.resettle !== null }, COPY.override);
}

/** The caption and subtitle layers for one composition, with the data's tokens filled in and the data-dependent beats swapped. */
export function layersFor(key: CompositionKey, tokens: Record<string, string>, overrides: Overrides): { en: Beat[]; ja: Beat[] } {
  return {
    en: applyOverrides(resolveBeats(EN[key], tokens), "en", overrides),
    ja: applyOverrides(resolveBeats(JA[key], tokens), "ja", overrides),
  };
}

/** Burned-in caption (English) with the Japanese subtitle under it, at the bottom of the frame. */
export function CaptionLayers({ which, scale = 1 }: { which: CompositionKey; scale?: number }): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const { width } = useVideoConfig();
  const { en, ja } = layersFor(which, film.tokens, filmOverrides(film));
  const t = frame / FPS;
  const e = beatAt(en, t);
  const j = beatAt(ja, t);
  const square = width <= layout.height;
  return (
    <div style={{ position: "absolute", left: 0, right: 0, bottom: square ? layout.captionJa : layout.captionJa, textAlign: "center", pointerEvents: "none" }}>
      {e === null ? null : (
        <div style={{ fontFamily: font.sans, fontWeight: weight.text, fontSize: size.caption * scale, lineHeight: 1.22, color: colour.ink }}>
          {e.lines.map((l) => (
            <div key={l}>{l}</div>
          ))}
        </div>
      )}
      {j === null ? null : (
        <div style={{ marginTop: 12, fontFamily: font.sans, fontWeight: weight.text, fontSize: size.subtitle * scale, lineHeight: 1.3, color: colour.graphite }}>
          {j.lines.map((l) => (
            <div key={l}>{l}</div>
          ))}
        </div>
      )}
    </div>
  );
}

export { colour };
