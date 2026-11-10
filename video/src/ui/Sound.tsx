import type { ReactNode } from "react";
import { Audio, Sequence, interpolate, staticFile } from "remotion";
import { FILES, GAIN, ROOM_FADE_SECONDS, type SoundEvent } from "../sound.ts";
import { FPS } from "../timing.ts";

const BED_LEVEL = 1;

/** Room tone, with a fade at the end when `fadeOut`, and the placed events. */
export function SoundTrack({ events, length, fadeOut }: { events: SoundEvent[]; length: number; fadeOut: boolean }): ReactNode {
  const fadeFrames = ROOM_FADE_SECONDS * FPS;
  return (
    <>
      <Audio
        src={staticFile(FILES.room)}
        loop
        volume={(f) => (fadeOut ? interpolate(f, [length - fadeFrames, length], [BED_LEVEL, 0], { extrapolateLeft: "clamp", extrapolateRight: "clamp" }) : BED_LEVEL) * GAIN.room}
      />
      {events.map((e, i) => (
        <Sequence key={`${e.kind}-${e.frame}-${i}`} from={e.frame} layout="none">
          <Audio src={staticFile(e.kind === "soft" ? FILES.tick : FILES[e.kind])} volume={GAIN[e.kind]} />
        </Sequence>
      ))}
    </>
  );
}
