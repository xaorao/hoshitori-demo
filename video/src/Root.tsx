import type { ReactNode } from "react";
import { getVideoMetadata } from "@remotion/media-utils";
import { Composition, staticFile } from "remotion";
import { Cut10, Cut30 } from "./Cuts.tsx";
import { Main } from "./Main.tsx";
import build from "./build.json";
import { inkRegion } from "./ink.ts";
import { loadFilm, type Build, type Film, type Props, type Reader } from "./data.ts";
import { CUT10_FRAMES, CUT30_FRAMES, FPS, MAIN_FRAMES } from "./timing.ts";
import { layout } from "./theme.ts";

const reader: Reader = {
  async json(path) {
    const res = await fetch(staticFile(path));
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.json();
  },
  async text(path) {
    const res = await fetch(staticFile(path));
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return res.text();
  },
  async head(path, bytes) {
    const res = await fetch(staticFile(path), { headers: { Range: `bytes=0-${bytes - 1}` } });
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    return new Uint8Array((await res.arrayBuffer()).slice(0, bytes));
  },
  async ink(path) {
    const res = await fetch(staticFile(path));
    if (!res.ok) throw new Error(`${path}: ${res.status}`);
    const bitmap = await createImageBitmap(await res.blob());
    return inkRegion(bitmap);
  },
  async seconds(path) {
    return (await getVideoMetadata(staticFile(path))).durationInSeconds;
  },
  async exists(path) {
    try {
      const res = await fetch(staticFile(path), { method: "HEAD" });
      return res.ok && !(res.headers.get("content-type") ?? "").includes("text/html");
    } catch {
      return false;
    }
  },
};

type FilmProps = Props & { film?: Film };

const calculate = async ({ props }: { props: FilmProps }): Promise<{ props: FilmProps }> => {
  const film = await loadFilm(reader, build as Build, props);
  return { props: { ...props, film } };
};

function Wrapped({ which, ...props }: FilmProps & { which: "main" | "cut30" | "cut10" }): ReactNode {
  if (props.film === undefined) return null;
  if (which === "main") return <Main film={props.film} />;
  if (which === "cut30") return <Cut30 film={props.film} />;
  return <Cut10 film={props.film} />;
}

const MainView = (p: FilmProps): ReactNode => <Wrapped which="main" {...p} />;
const Cut30View = (p: FilmProps): ReactNode => <Wrapped which="cut30" {...p} />;
const Cut10View = (p: FilmProps): ReactNode => <Wrapped which="cut10" {...p} />;

/** Four compositions: the main cut, the 30-second cut and the 10-second cut in 16:9 and 1:1. */
export function Root(): ReactNode {
  return (
    <>
      <Composition id="Main" component={MainView} width={layout.width} height={layout.height} fps={FPS} durationInFrames={MAIN_FRAMES} defaultProps={{}} calculateMetadata={calculate} />
      <Composition id="Cut30" component={Cut30View} width={layout.width} height={layout.height} fps={FPS} durationInFrames={CUT30_FRAMES} defaultProps={{}} calculateMetadata={calculate} />
      <Composition id="Cut10" component={Cut10View} width={layout.width} height={layout.height} fps={FPS} durationInFrames={CUT10_FRAMES} defaultProps={{}} calculateMetadata={calculate} />
      <Composition id="Cut10Square" component={Cut10View} width={layout.height} height={layout.height} fps={FPS} durationInFrames={CUT10_FRAMES} defaultProps={{}} calculateMetadata={calculate} />
    </>
  );
}
