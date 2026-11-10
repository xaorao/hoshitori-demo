import type { ComponentType, ReactNode } from "react";
import { Sequence } from "remotion";
import { Scene0 } from "./scenes/Scene0.tsx";
import { Scene1 } from "./scenes/Scene1.tsx";
import { Scene2 } from "./scenes/Scene2.tsx";
import { Scene3 } from "./scenes/Scene3.tsx";
import { Scene4 } from "./scenes/Scene4.tsx";
import { Scene5 } from "./scenes/Scene5.tsx";
import { Scene6 } from "./scenes/Scene6.tsx";
import { Scene7 } from "./scenes/Scene7.tsx";
import { Scene8 } from "./scenes/Scene8.tsx";
import { Scene9 } from "./scenes/Scene9.tsx";
import { Scene10 } from "./scenes/Scene10.tsx";
import type { Film } from "./data.ts";
import { MAIN_FRAMES, sceneFrom, sceneLength } from "./timing.ts";
import { mainEvents } from "./sound.ts";
import { colour } from "./theme.ts";
import { CaptionLayers } from "./ui/Layers.tsx";
import { SoundTrack } from "./ui/Sound.tsx";
import { DevStamp, Fill, FilmContext } from "./ui/kit.tsx";

export const SCENES: ComponentType[] = [Scene0, Scene1, Scene2, Scene3, Scene4, Scene5, Scene6, Scene7, Scene8, Scene9, Scene10];

/** The main cut: eleven scenes in a row, the caption and subtitle layers over them, the sound under them. */
export function Main({ film }: { film: Film }): ReactNode {
  return (
    <FilmContext.Provider value={film}>
      <Fill style={{ background: colour.paper }}>
        {SCENES.map((Scene, i) => (
          <Sequence key={i} from={sceneFrom(i)} durationInFrames={sceneLength(i)} name={`scene-${i}`}>
            <Scene />
          </Sequence>
        ))}
        <CaptionLayers which="main" />
        <SoundTrack events={mainEvents(film)} length={MAIN_FRAMES} fadeOut />
        <DevStamp />
      </Fill>
    </FilmContext.Provider>
  );
}
