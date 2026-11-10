import type { ReactNode } from "react";
import { JUDGE_MAP, TRACKS } from "./judges.ts";
import { href } from "./route.ts";
import { WHERE_WORDS } from "./format.ts";

export function JudgesView(): ReactNode {
  return (
    <>
      <p className="kicker">For judges</p>
      <h1>Where each line of the rubric can be checked.</h1>
      <p className="lead">The organiser scores every track out of 100: 40 points common to all tracks and 60 for the track. Each line below says where to look and where the evidence stops.</p>
      <p>This page does not score anything. It points to places in this site and says what each place does not show.</p>
      {Object.entries(TRACKS).map(([group, track]) => (
        <section key={group} aria-labelledby={`judges-${group}`}>
          <h2 id={`judges-${group}`}>{track.heading}</h2>
          <p className="muted">{track.note}</p>
          <ul className="judge-lines">
            {JUDGE_MAP.filter((r) => r.group === group).map((r) => (
              <li key={r.line}>
                <p>
                  <strong>{r.line}</strong> <span className="pill">{r.points} points</span>
                </p>
                <p>
                  {r.look}{" "}
                  <a href={href(r.where)}>
                    Open {WHERE_WORDS[r.where.page] ?? r.where.page}
                    <span className="visually-hidden"> for {r.line}</span>
                  </a>
                </p>
                <p className="muted">Does not show: {r.limit}</p>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </>
  );
}
