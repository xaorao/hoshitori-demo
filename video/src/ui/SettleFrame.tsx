import type { ReactNode } from "react";
import { clock, dots, priceMove } from "../format.ts";
import { STEP } from "../timing.ts";
import { colour, layout, size } from "../theme.ts";
import { Appear, Head, Mono, Typed, useFilm } from "./kit.tsx";
import { BigCall } from "./BigCall.tsx";

/** Scene 0's frame again, settled: the call's chance with its rule already drawn, the word it settled on landing to the right, and the prices under it. */
export function SettleFrame({ at }: { at: number }): ReactNode {
  const film = useFilm();
  const { row, settlement } = film.hero;
  const done = -(STEP.rule + STEP.numberLead + STEP.fade);
  const detail = dots(priceMove(settlement.reference_price, settlement.outcome_price), film.hero.oracles, `${clock(row.settled_at ?? "")} UTC`);
  return (
    <BigCall
      at={done}
      ruleAt={done}
      rightOfNumber={
        <Appear at={at} dir="down" distance={layout.nudge}>
          <Head fontSize={size.huge} style={{ whiteSpace: "nowrap" }}>
            {row.result}
          </Head>
        </Appear>
      }
      underRule={
        <Mono small style={{ color: colour.graphite }}>
          <Typed text={detail} at={at + STEP.fellDrop} style={{ fontFamily: "inherit" }} />
        </Mono>
      }
    />
  );
}
