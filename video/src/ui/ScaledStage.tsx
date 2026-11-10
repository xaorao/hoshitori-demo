import type { ReactNode } from "react";
import { useVideoConfig } from "remotion";
import { layout } from "../theme.ts";

/** In the square cut the 16:9 stage is scaled to fit the width and centred; in 16:9 it is the frame itself. */
export function ScaledStage({ children }: { children: ReactNode }): ReactNode {
  const { width, height } = useVideoConfig();
  if (width === layout.width) return <>{children}</>;
  const scale = (width - layout.squareMargin * 2) / layout.width;
  return (
    <div style={{ position: "absolute", left: (width - layout.width) / 2, top: (height - layout.height) / 2 - layout.squareLift, width: layout.width, height: layout.height, transform: `scale(${scale})`, transformOrigin: "center center" }}>{children}</div>
  );
}
