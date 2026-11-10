import type { ReactNode } from "react";
import { InShort } from "./inshort";
import { useOpenOn } from "./open_on.ts";
import { href } from "./route.ts";
import {
  changeWords,
  count,
  deskWord,
  endingLine,
  falsificationKey,
  gateWord,
  originsLine,
  plainDecimal,
  spentLine,
} from "./format.ts";
import type {
  DeskCheck,
  DeskDecision,
  DeskFalsification,
  DeskRun,
} from "./types.ts";

function Checks({ checks }: { checks: DeskCheck[] }): ReactNode {
  if (checks.length === 0) return <p className="muted">No check was made.</p>;
  return (
    <ol className="desk-checks">
      {checks.map((c) => (
        <li key={c.seq}>
          <strong>{deskWord("check", c.check_type)}</strong>{" "}
          <span className="muted">from {c.provider}</span>:{" "}
          <span className={`pill ${c.outcome === "OK" ? "" : "warn"}`.trim()}>
            {deskWord("outcome", c.outcome)}
          </span>
          {c.error_class !== null ? (
            <> ({deskWord("error", c.error_class)})</>
          ) : null}{" "}
          <span className="pill">{deskWord("status", c.status_after)}</span>
          <span className="muted">
            {" "}
            ·{" "}
            {changeWords(
              c.status_before,
              c.own_status,
              c.status_after,
              c.check_type,
            )}{" "}
            · {deskWord("layer", c.layer)} ·{" "}
            {c.alternatives === 0
              ? "no other check was open"
              : `chosen over ${count(c.alternatives, "other check")}`}
          </span>
        </li>
      ))}
    </ol>
  );
}

function Falsification({ f }: { f: DeskFalsification }): ReactNode {
  return (
    <>
      <p>
        <strong>What we tried to disprove.</strong>{" "}
        {deskWord("falsification", falsificationKey(f))}
      </p>
      <ul className="findings">
        {f.tests.map((t) => (
          <li key={t.type}>
            {deskWord("test", t.type)}{" "}
            <span className="pill">{deskWord("result", t.result)}</span>
            {t.reason !== null ? (
              <span className="muted"> ({deskWord("why", t.reason)})</span>
            ) : null}
          </li>
        ))}
      </ul>
      <p className="muted">
        The size before the tests:{" "}
        {deskWord("stance", f.pre_test.stance).toLowerCase()},{" "}
        {plainDecimal(f.pre_test.weight)} of the book. The most the tests
        allowed: {plainDecimal(f.ceiling.weight)}.
      </p>
      <p className="muted">
        These tests were calibrated on planted series. A check on real history
        is not settled for the four-hour market test, and no real case with a
        known cause exists to measure it against.
      </p>
    </>
  );
}

function Decision({ d }: { d: DeskDecision }): ReactNode {
  return (
    <>
      <p>
        <strong>The proposal was</strong>{" "}
        {deskWord("stance", d.proposal.stance).toLowerCase()} at{" "}
        {plainDecimal(d.proposal.p)}, {plainDecimal(d.proposal.weight)} of the
        book, citing {count(d.proposal.cites, "piece")} of evidence. The gates
        then ran in this order:
      </p>
      <ol className="desk-gates">
        {d.gates.map((g) => (
          <li key={g.gate}>
            {deskWord("gate", g.gate)}:{" "}
            <span className={`pill${g.status === "PASS" ? "" : " warn"}`}>
              {gateWord(g.status, d.proposal.weight)}
            </span>
            {g.detail !== "" ? (
              <span className="muted"> ({g.detail})</span>
            ) : null}
          </li>
        ))}
      </ol>
      <p>
        <strong>Result:</strong> {deskWord("final", d.final.kind)}
        {d.final.stance !== null && d.final.weight !== null ? (
          <>
            : {deskWord("stance", d.final.stance).toLowerCase()},{" "}
            {plainDecimal(d.final.weight)} of the book
          </>
        ) : null}
        .
      </p>
    </>
  );
}

function RunView({ run }: { run: DeskRun }): ReactNode {
  return (
    <li className="claim" id={`run-${run.run_id}`} tabIndex={-1}>
      <p>
        <a className="pill" href={href({ page: "callsby", by: "token", value: run.symbol })}>
          <span className="visually-hidden">Every call on </span>
          {run.symbol}
        </a>{" "}
        <span className="pill">{deskWord("label", run.label)}</span>
      </p>
      <p>
        <strong>{endingLine(run.state, run.reason, run.open)}</strong> Found
        through {deskWord("route", run.route)}.
      </p>
      {run.hypothesis !== null ? (
        <p>
          <strong>The question it asked:</strong> “{run.hypothesis.statement}”{" "}
          <span className="muted">
            It would be wrong if: {run.hypothesis.invalidation}
          </span>
        </p>
      ) : (
        <p className="muted">It stopped before it asked a question.</p>
      )}
      <Checks checks={run.checks} />
      {run.checks.length > 0 ? (
        <p className="muted">{originsLine(run.origins)}</p>
      ) : null}
      {run.falsification !== null ? (
        <Falsification f={run.falsification} />
      ) : null}
      {run.decision !== null ? <Decision d={run.decision} /> : null}
      <p className="muted">Spent {spentLine(run.spent, run.budget)}</p>
    </li>
  );
}

export function DeskView({
  runs,
  focus,
}: {
  runs: DeskRun[];
  focus?: string;
}): ReactNode {
  const opened = runs.find((r) => r.run_id === focus) ?? null;
  useOpenOn(opened === null ? null : `run-${opened.run_id}`);
  return (
    <>
      <p className="kicker">The Desk</p>
      <h1>What the Desk checked, why it chose each check, and how it ended.</h1>
      <InShort
        does="Investigates one claim about a market move. It chooses each check from a fixed menu, tries to disprove its own call, then runs the answer through gates that can only shrink it."
        means="A run ends as a practice call, an abstention or no reliable conclusion, with the reason. A practice call is not a statement that the claim is true."
        sure="Shown: every check, what it read and which gate cut the size. Uncertain: the tests that try to disprove a call were calibrated on planted series and are not settled on real history."
        next="Open a run and follow its checks in order. A run marked synthetic or recorded says so."
      />
      <p>
        {runs.some((r) => r.label === "recorded")
          ? "A run marked recorded was replayed from stored captures of the sources, not read live. Nothing on this page says a claim is true: a check that finds support says so for the sources it checked."
          : "Nothing on this page says a claim is true: a check that finds support says so for the sources it checked."}
      </p>
      {runs.some((r) => r.label === "synthetic") ? (
        <p className="notice">
          A run marked synthetic was made from answers invented for this
          example, in the format of the real service. No source was read and no
          model was called. It shows what a run looks like, not what any market
          did.
        </p>
      ) : null}
      {focus !== undefined && opened === null ? (
        <p className="notice">
          The run this link names is not in this export; every run it holds is
          listed below.
        </p>
      ) : null}
      {runs.length === 0 ? (
        <p className="notice">No Desk run is recorded in this export.</p>
      ) : (
        <ol className="claims">
          {runs.map((r) => (
            <RunView key={r.run_id} run={r} />
          ))}
        </ol>
      )}
    </>
  );
}
