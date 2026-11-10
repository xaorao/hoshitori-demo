import type { ReactNode } from "react";
import { useCurrentFrame } from "remotion";
import { COPY } from "../copy.ts";
import { clock } from "../format.ts";
import { B, STEP } from "../timing.ts";
import { colour, layout, loop, size } from "../theme.ts";
import { Abs, Appear, Body, Head, LoopDot, Mono, Page, Rule, Typed, linear, pct, useFilm } from "../ui/kit.tsx";

/** The receipt's own step names that the four timed columns read; the fifth column is the verifier. */
const FROM_TIMELINE = ["Evidence frozen", "Decided", "Committed", "Settled"] as const;
const SEAL = COPY.s2.steps.indexOf("Seal");

/** Scene 2: the loop, five dots on one line, with the real times of the hero call written under them. */
export function Scene2(): ReactNode {
  const film = useFilm();
  const frame = useCurrentFrame();
  const b = B.s2;
  const stateTime = (state: string): string => {
    const hit = film.hero.timeline.find((t) => t.state === state);
    if (hit === undefined) throw new Error(`the hero receipt has no "${state}" step`);
    return clock(hit.at);
  };
  const below = (i: number): [string, string] => {
    const name = FROM_TIMELINE[i];
    return name === undefined ? ["verify:", `exit ${film.logs.verifyPython.exit}`] : [name, stateTime(name)];
  };
  const drawn = linear(frame, b.line, STEP.loopLine);
  return (
    <Page>
      <Abs left={loop.first} top={loop.lineY} width={loop.last - loop.first}>
        <Rule width={pct(drawn)} colourOf={colour.ink} height={loop.lineHeight} />
      </Abs>
      {COPY.s2.steps.map((step, i) => {
        const x = loop.centres[i] ?? loop.left;
        const at = b.line + i * STEP.dotGap;
        const [label, time] = below(i);
        const timeAt = b.times + i * STEP.timesGap;
        return (
          <div key={step}>
            <Abs left={x - loop.halfColumn} top={loop.labelTop} width={loop.column} style={{ textAlign: "center" }}>
              <Appear at={at} dir="up" distance={layout.nudge}>
                <Head fontSize={size.title}>{step}</Head>
              </Appear>
            </Abs>
            <Abs left={x - loop.halfDot} top={loop.dotTop} width={loop.dot} height={loop.dot}>
              <Appear at={at}>
                <LoopDot size={loop.dot} sealed={i === SEAL} />
              </Appear>
            </Abs>
            <Abs left={x - loop.halfColumn} top={loop.timesTop} width={loop.column} style={{ textAlign: "center" }}>
              <Appear at={timeAt}>
                <Mono small style={{ color: colour.graphite }}>
                  {label}
                </Mono>
              </Appear>
              <div>
                <Mono>
                  <Typed text={time} at={timeAt} />
                </Mono>
              </div>
            </Abs>
          </div>
        );
      })}
      <Abs left={loop.left} top={loop.practiceTop} width={loop.page}>
        <Appear at={b.practice} dir="up" distance={layout.nudge}>
          <Body fontSize={size.lead} style={{ color: colour.graphite }}>
            {COPY.s2.practice}
          </Body>
        </Appear>
      </Abs>
      <Abs left={loop.left} top={loop.oneTop} width={loop.page}>
        <Appear at={b.tracks} dir="up" distance={layout.nudge}>
          <Head fontSize={size.title}>
            {COPY.s2.one}
          </Head>
        </Appear>
      </Abs>
      {COPY.s2.tracks.map((label, i) => (
        <Abs key={label} left={(loop.thirds[i] ?? loop.left) - loop.halfColumn} top={loop.tracksTop} width={loop.column} style={{ textAlign: "center" }}>
          <Appear at={b.tracks + STEP.trackLead + i * STEP.trackGap}>
            <Mono small style={{ color: colour.graphite }}>
              {label}
            </Mono>
          </Appear>
        </Abs>
      ))}
    </Page>
  );
}
