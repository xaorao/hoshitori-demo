import { useEffect, useState, type ReactNode } from "react";
import { InShort } from "./inshort";
import { href } from "./route.ts";
import {
  checkWords,
  kindWord,
  nextCheckLine,
  readingLine,
  sketchLine,
  when,
  wordsAt,
} from "./format.ts";
import type { Claim, ClaimCheck, ClaimSketch } from "./types.ts";

function Sketch({
  sketch,
  words,
}: {
  sketch: ClaimSketch;
  words: string;
}): ReactNode {
  return (
    <div className="sketch">
      <p>
        <strong>If “{words}” were scored.</strong>{" "}
        {sketchLine(sketch.status, sketch.reason)}
      </p>
      {sketch.choices.length > 0 ? (
        <ul>
          {sketch.choices.map((c) => (
            <li key={c.field}>
              {c.question}
              {c.options.length > 0 ? (
                <span className="muted"> ({c.options.join(", ")})</span>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}

function ClaimView({
  claim,
  checks,
  invented = false,
}: {
  claim: Claim;
  checks: ClaimCheck[];
  invented?: boolean;
}): ReactNode {
  const page = claim.page;
  return (
    <li className="claim">
      <blockquote>{claim.text}</blockquote>
      {claim.assets.filter((t) => /^[A-Za-z0-9]{1,20}$/.test(t)).length > 0 ? (
        <p className="muted">
          The agents&apos; own calls on{" "}
          {claim.assets
            .filter((t) => /^[A-Za-z0-9]{1,20}$/.test(t))
            .map((t, i) => (
              <span key={t}>
                {i > 0 ? ", " : ""}
                <a href={href({ page: "callsby", by: "token", value: t })}>
                  {t}
                </a>
              </span>
            ))}
          : made from their own evidence, not from this claim.
        </p>
      ) : null}
      {page === null ? (
        <p className="notice">{claim.message}</p>
      ) : (
        <>
          <p className="notice">
            {invented
              ? "An invented example: no model read it and nothing was checked."
              : page.banner}
          </p>
          <p>
            <strong>{page.headline}</strong>
          </p>
          <ul className="parts">
            {page.parts.map((p) => (
              <li key={p.node_id}>
                <span className="pill">{kindWord(p.kind)}</span>{" "}
                <span className="pill">Not checked</span>{" "}
                <span>“{p.quote}”</span>
              </li>
            ))}
          </ul>
          {invented ? null : claim.reading !== null ? (
            <p>
              {readingLine(
                claim.reading.state,
                claim.runs,
                claim.reading.score,
                claim.reading.validated,
              )}
            </p>
          ) : (
            <p className="muted">
              Only one reading was possible, so nothing was compared.
            </p>
          )}
          {claim.disputes.length > 0 ? (
            <ul className="findings">
              {claim.disputes.map((d) => (
                <li key={`${d.kind}-${d.start}`}>
                  “{wordsAt(claim.text, d.start, d.end)}”: {d.detail}.
                </li>
              ))}
            </ul>
          ) : null}
          {checks.length > 0 ? (
            <div className="sketch">
              <p>
                <strong>
                  Checked afterwards, against the league's own evidence.
                </strong>{" "}
                The reading above was made before any check. The window judged
                here ends when the league's latest snapshot was taken, not when
                the claim was made.
              </p>
              <ul>
                {checks.map((k) => (
                  <li key={k.node_id}>
                    “{k.words}”:{" "}
                    <span
                      className={`pill ${k.status === "SUPPORTED" ? "ok" : k.status === "CONTRADICTED" ? "warn" : ""}`}
                    >
                      {checkWords(k.status)}
                    </span>{" "}
                    {k.reason}
                    {k.snapshot_at !== null ? (
                      <span className="muted">
                        {" "}
                        (snapshot of {when(k.snapshot_at)})
                      </span>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {claim.sketches.map((s) => (
            <Sketch
              key={s.node_id}
              sketch={s}
              words={
                page.parts.find((p) => p.node_id === s.node_id)?.quote ?? ""
              }
            />
          ))}
          <p>{page.next_step}</p>
          {claim.assumptions.some((a) => a.changed.length > 0) ? (
            <details>
              <summary>What the claim depends on</summary>
              <p className="muted">
                Suppositions about the claim's structure, not findings. Each
                says which other parts would change.
              </p>
              <ul>
                {claim.assumptions
                  .filter((a) => a.changed.length > 0 && a.kind !== "TRUE")
                  .map((a) => (
                    <li key={`${a.node_id}-${a.kind}`}>{a.sentence}</li>
                  ))}
              </ul>
            </details>
          ) : null}
          {claim.next_check !== null ? (
            <p>
              {nextCheckLine(
                claim.next_check.status,
                claim.next_check.words,
                claim.next_check.instruction,
                claim.next_check.source,
                claim.next_check.reason,
              )}
            </p>
          ) : null}
        </>
      )}
      <p className="muted">
        {isSynthetic(claim.model) ? (
          <>Invented example: no model read it.</>
        ) : (
          <>
            Read {claim.runs} times by{" "}
            <span className="mono">{claim.model}</span> at {when(claim.read_at)}
            .
          </>
        )}
      </p>
    </li>
  );
}

type Live =
  | { state: "idle" }
  | { state: "reading" }
  | { state: "done"; claim: Claim }
  | { state: "refused"; text: string };

/** Paste a claim and have it read, when the page is served by `hoshitori-league serve` (a static host has no such endpoint, and then the form is not shown). */
export function LiveClaim(): ReactNode {
  const [available, setAvailable] = useState<{ max: number } | null>(null);
  const [text, setText] = useState("");
  const [live, setLive] = useState<Live>({ state: "idle" });
  useEffect(() => {
    let current = true;
    if (document.querySelector('meta[name="hoshitori-live"]') === null) return; // a static copy has no endpoint to ask
    fetch("api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((h: { claims?: boolean; max_chars?: number } | null) => {
        if (current && h !== null && h.claims === true)
          setAvailable({ max: h.max_chars ?? 600 });
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, []);
  if (available === null)
    return (
      <section aria-labelledby="live-claim">
        <h2 id="live-claim">Read your own claim</h2>
        <p className="muted" id="live-claim-off">
          This copy of the site has no model connected, so it cannot read a new
          claim. The examples below show what a reading looks like.
        </p>
        <textarea
          aria-labelledby="live-claim"
          aria-describedby="live-claim-off"
          className="claim-box"
          rows={3}
          disabled
          placeholder="Not available on this copy of the site"
        />
      </section>
    );
  const send = (): void => {
    setLive({ state: "reading" });
    fetch("api/claim", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    })
      .then(async (r) => {
        const body = (await r.json()) as Claim & {
          error?: string;
          retry_after_s?: number;
        };
        if (r.ok) setLive({ state: "done", claim: body });
        else
          setLive({
            state: "refused",
            text: `${body.error ?? "The claim was not read."}${body.retry_after_s !== undefined ? ` Ask again in ${Math.ceil(body.retry_after_s / 60)} minutes.` : ""}`,
          });
      })
      .catch(() =>
        setLive({ state: "refused", text: "The server could not be reached." }),
      );
  };
  return (
    <section aria-labelledby="live-claim">
      <h2 id="live-claim">Read your own claim</h2>
      <p>
        Paste one claim and it is cut into parts, as the examples below are.
        Nothing is checked against a market here. The text goes to the model
        provider and is not stored by this server.
      </p>
      <p>
        <label htmlFor="claim-text">
          The claim, at most {available.max} characters
        </label>
        <br />
        <textarea
          id="claim-text"
          rows={3}
          maxLength={available.max}
          value={text}
          onChange={(e) => setText(e.target.value)}
          style={{ width: "100%", maxWidth: 640 }}
        />
      </p>
      <p>
        <button
          type="button"
          disabled={text.trim() === "" || live.state === "reading"}
          onClick={send}
        >
          {live.state === "reading" ? "Reading..." : "Read this claim"}
        </button>
      </p>
      <div aria-live="polite">
        {live.state === "refused" ? (
          <p className="notice">{live.text}</p>
        ) : null}
        {live.state === "done" ? (
          <ol className="claims">
            <ClaimView claim={live.claim} checks={[]} />
          </ol>
        ) : null}
      </div>
    </section>
  );
}

/** An example written for the page together with its reading, so that no model read it. */
function isSynthetic(model: string): boolean {
  return model.startsWith("synthetic");
}

export function ClaimsView({
  claims,
  checks,
}: {
  claims: Claim[];
  checks: ClaimCheck[];
}): ReactNode {
  const invented =
    claims.length > 0 && claims.every((c) => isSynthetic(c.model));
  return (
    <>
      <p className="kicker">Check a claim</p>
      <h1>How a claim is cut into parts, before anything is checked.</h1>
      <InShort
        does="Cuts a claim into parts (what was observed, what it is said to mean, what is forecast) and ties each part to the exact words."
        means="A label says what kind of part it is. It does not say the part is true."
        sure="Shown: the exact words behind each part. Not shown: whether any part is true; nothing is checked until a check is run."
        next="Read the parts, then do the next check each claim names. Where a model is connected, paste your own claim."
      />
      {invented ? (
        <>
          <p className="lead">
            The examples below were invented to show the page, and so were their
            readings.
          </p>
          <p>
            No model read them and no market was looked at. They show how a
            claim is cut into parts; nothing on this page is a check of whether
            a claim is true, and nothing here is a live reading. Pasting a claim
            below, when this site has a model, reads it for real.
          </p>
        </>
      ) : (
        <>
          <p className="lead">
            Each example below was read by a model three times, and a plain
            reader of the text looked for what the parts left out.
          </p>
          <p>
            These are example claims we wrote, recorded on the date shown.
            Nothing on this page is a check of whether a claim is true, and
            nothing here is a live reading.
          </p>
        </>
      )}
      <LiveClaim />
      <h2>Examples</h2>
      <ol className="claims">
        {claims.map((c) => (
          <ClaimView
            key={c.id}
            claim={c}
            checks={checks.filter((k) => k.claim_id === c.id)}
            invented={invented}
          />
        ))}
      </ol>
    </>
  );
}
