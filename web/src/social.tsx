import { use, useEffect, useRef, useState, type ReactNode } from "react";
import {
  count,
  defaultCycle,
  horizon,
  invalidationWords,
  sourceName,
  when,
  whenShort,
} from "./format.ts";
import { loadDocument } from "./load.ts";
import { href } from "./route.ts";
import { DATA } from "./site.tsx";
import type {
  CycleSummary,
  Receipt,
  Row,
  RunLine,
  SiteIndex,
  Signals,
  Reviews,
  TodayItem,
} from "./types.ts";

/** What the debate's Bear put against the Bull's case, on a receipt of the debate. An agent's challenge: never a person's. */
export function ChallengeBlock({ run }: { run: RunLine | null }): ReactNode {
  const c = run?.challenge ?? null;
  if (c === null) return null;
  const taken = new Set(c.judge_cited);
  return (
    <>
      <h2>The Bear's challenge</h2>
      <p className="muted">
        An agent's challenge, not a person's. It counts toward no one's record.
      </p>
      <p>
        The Bear cited {count(c.bear_only.length, "item")} the Bull did not, and
        the judge cited {c.judge_cited.length} of them. Both cited{" "}
        {count(c.shared, "item")}; the Bull alone, {c.bull_only}.
      </p>
      {c.bear_only.length > 0 ? (
        <ul>
          {c.bear_only.map((e) => (
            <li key={e.evidence_id}>
              {sourceName(e.source)}: {e.path.replace(/^ryo\./, "")}
              {taken.has(e.evidence_id) ? " (the judge cited it)" : ""}
            </li>
          ))}
        </ul>
      ) : null}
    </>
  );
}

/** A card for one call, drawn from its receipt: the call, its agent, its time-stamp level and its digest. It can be downloaded as an SVG. */
export function ShareCard({ r }: { r: Receipt }): ReactNode {
  const holder = useRef<SVGSVGElement | null>(null);
  const [copied, setCopied] = useState(false);
  const row = r.row;
  const title = `${row.symbol} · ${horizon(row.hours)} · ${row.arm_name}`;
  const said = row.chance ?? "No call";
  const fixed =
    r.committed_at === null
      ? "Not committed"
      : `Committed ${when(r.committed_at)}`;
  const level =
    row.level === null || row.level === undefined
      ? ""
      : row.level === "C3" || row.level === "C4"
        ? " · independently time-stamped"
        : " · recorded, not independently time-stamped";
  // the headline is one line inside 1,056 of the card's 1,200 units: a bold sans letter is about 0.6 of its size wide
  const saidSize = Math.max(44, Math.min(110, Math.floor(1056 / (said.length * 0.6))));
  const download = (): void => {
    const node = holder.current;
    if (node === null) return;
    const text = new XMLSerializer().serializeToString(node);
    const url = URL.createObjectURL(
      new Blob([text], { type: "image/svg+xml" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = `hoshitori-${row.symbol}-${row.hours}h-${row.arm_name}.svg`
      .replace(/\s+/g, "-")
      .toLowerCase();
    a.click();
    URL.revokeObjectURL(url);
  };
  const copy = (): void => {
    void navigator.clipboard
      ?.writeText(window.location.href)
      .then(() => setCopied(true));
  };
  return (
    <>
      <h2>Share this call</h2>
      <svg
        ref={holder}
        xmlns="http://www.w3.org/2000/svg"
        viewBox="0 0 1200 630"
        role="img"
        aria-label={`Share card: ${title}, ${said}`}
        style={{
          width: "100%",
          maxWidth: 600,
          height: "auto",
          border: "1px solid #d4d4d8",
          borderRadius: 8,
        }}
      >
        <rect width="1200" height="630" fill="#ffffff" />
        <circle
          cx="76"
          cy="76"
          r="22"
          fill="none"
          stroke="#111111"
          strokeWidth="6"
        />
        <circle cx="128" cy="76" r="22" fill="#111111" />
        <text
          x="172"
          y="88"
          fontFamily="system-ui, sans-serif"
          fontSize="40"
          fontWeight="700"
          fill="#111111"
        >
          Hoshitori
        </text>
        <text
          x="72"
          y="270"
          fontFamily="system-ui, sans-serif"
          fontSize="48"
          fill="#52525b"
        >
          {title}
        </text>
        <text
          x="72"
          y="400"
          fontFamily="system-ui, sans-serif"
          fontSize={saidSize}
          fontWeight="700"
          fill="#111111"
          {...(said.length * 0.6 * saidSize > 1056
            ? { textLength: 1056, lengthAdjust: "spacingAndGlyphs" }
            : {})}
        >
          {said}
        </text>
        <text
          x="72"
          y="500"
          fontFamily="system-ui, sans-serif"
          fontSize="30"
          fill="#52525b"
        >
          {`${fixed}${level}`}
        </text>
        <text
          x="72"
          y="550"
          fontFamily="ui-monospace, monospace"
          fontSize="26"
          fill="#71717a"
        >
          {`digest ${r.forecast_digest.slice(0, 16)}`}
        </text>
        <text
          x="72"
          y="596"
          fontFamily="system-ui, sans-serif"
          fontSize="24"
          fill="#71717a"
        >
          A practice call, not advice. Time-stamped before its outcome.
        </text>
      </svg>
      <p>
        <button type="button" onClick={download}>
          Download the card (SVG)
        </button>{" "}
        <button type="button" onClick={copy}>
          {copied ? "Link copied" : "Copy the link to this receipt"}
        </button>
      </p>
    </>
  );
}

const ARM_ORDER = ["L2", "L1x", "L1", "L0"];

/** The text of a public post about the last committed cycle, from numbers the page already holds. */
export function postText(
  cycle: CycleSummary,
  calls: number,
  noCalls: number,
): string {
  return `Hoshitori time-stamped ${count(calls, "forecast")} from ${count(cycle.slots, "slot")} (${count(noCalls, "no-call")}) in the cycle of ${whenShort(cycle.planned_for)}, each before its outcome. Check the record yourself, offline. Practice calls, not advice. @ryodigital`;
}

function PostKit({
  cycle,
  rows,
}: {
  cycle: CycleSummary;
  rows: Row[];
}): ReactNode {
  const [copied, setCopied] = useState(false);
  const mine = rows.filter((r) => r.cycle_id === cycle.cycle_id);
  const text = postText(
    cycle,
    mine.filter((r) => r.chance !== null).length,
    mine.filter((r) => r.chance === null && r.state === "Abstained").length,
  );
  return (
    <section aria-labelledby="post-kit">
      <h2 id="post-kit">Post about this cycle</h2>
      <p>
        <label htmlFor="post-text">
          A post you can edit and publish yourself; the numbers are this
          cycle's.
        </label>
        <br />
        <textarea
          id="post-text"
          rows={4}
          readOnly
          value={text}
          style={{ width: "100%", maxWidth: 640 }}
        />
      </p>
      <p>
        <button
          type="button"
          onClick={() => {
            void navigator.clipboard
              ?.writeText(text)
              .then(() => setCopied(true));
          }}
        >
          {copied ? "Copied" : "Copy the post"}
        </button>
      </p>
    </section>
  );
}

type Loaded = { id: string; receipt: Receipt | null; failed: boolean };

/** The public reasoning feed: the calls of the last committed cycle, each with what it used, what would change its mind and, for the
 * debate, the Bear's challenge. Receipts load as they are asked for. */
export function FeedView({
  rows,
  cycles,
  index,
}: {
  rows: Row[];
  cycles: CycleSummary[];
  index: SiteIndex;
}): ReactNode {
  const chosen = defaultCycle(cycles);
  const mine = rows
    .filter((r) => r.cycle_id === chosen && r.contract === "D")
    .sort(
      (a, b) =>
        ARM_ORDER.indexOf(a.arm) - ARM_ORDER.indexOf(b.arm) ||
        a.symbol.localeCompare(b.symbol) ||
        a.hours - b.hours,
    );
  const arms = [...new Set(mine.map((r) => r.arm_name))];
  const [arm, setArm] = useState<string | null>(null);
  const [shown, setShown] = useState(12);
  const [loaded, setLoaded] = useState<Record<string, Loaded>>({});
  const visible = mine
    .filter((r) => arm === null || r.arm_name === arm)
    .slice(0, shown);
  const cycle = cycles.find((c) => c.cycle_id === chosen);
  useEffect(() => {
    let live = true;
    for (const r of visible) {
      if (loaded[r.forecast_id] !== undefined || r.chance === null) continue; // a slot that made no call has no receipt
      setLoaded((l) => ({
        ...l,
        [r.forecast_id]: { id: r.forecast_id, receipt: null, failed: false },
      }));
      loadDocument<Receipt>(DATA, index, `receipts/${r.forecast_id}.json`).then(
        (receipt) =>
          live &&
          setLoaded((l) => ({
            ...l,
            [r.forecast_id]: { id: r.forecast_id, receipt, failed: false },
          })),
        () =>
          live &&
          setLoaded((l) => ({
            ...l,
            [r.forecast_id]: { id: r.forecast_id, receipt: null, failed: true },
          })),
      );
    }
    return () => {
      live = false;
    };
  }, [visible.map((r) => r.forecast_id).join(","), index]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <>
      <p className="kicker">Feed</p>
      <h1>What each agent called, and why.</h1>
      {cycle === undefined ? (
        <p>No cycle has run yet.</p>
      ) : (
        <p>
          The calls of the cycle of {whenShort(cycle.planned_for)}. Each says
          what it used and what would prove it wrong. Nothing here is a person's
          call; the Bear's challenges are an agent's.
        </p>
      )}
      <nav aria-label="Agents">
        <ul className="chips">
          <li>
            <button
              type="button"
              className="pill"
              aria-pressed={arm === null}
              onClick={() => setArm(null)}
            >
              All agents
            </button>
          </li>
          {arms.map((a) => (
            <li key={a}>
              <button
                type="button"
                className="pill"
                aria-pressed={arm === a}
                onClick={() => setArm(a)}
              >
                {a}
              </button>
            </li>
          ))}
        </ul>
      </nav>
      {visible.map((r) => {
        const got = loaded[r.forecast_id];
        const rc = got?.receipt ?? null;
        const used =
          rc === null
            ? []
            : [...new Set(rc.evidence.map((e) => sourceName(e.source)))];
        return (
          <article key={r.forecast_id} aria-labelledby={`f-${r.forecast_id}`}>
            <h2 id={`f-${r.forecast_id}`}>
              {r.symbol}, {horizon(r.hours)}: {r.arm_name}
            </h2>
            <p className="lead">{r.chance ?? "No call"}</p>
            {r.chance === null && r.reason !== null ? <p>{r.reason}</p> : null}
            {r.chance === null ? null : rc === null ? (
              got?.failed === true ? (
                <p className="muted">Its receipt could not be loaded.</p>
              ) : (
                <p className="muted">Loading what it used.</p>
              )
            ) : (
              <>
                {used.length > 0 ? <p>Used: {used.join(", ")}.</p> : null}
                {rc.invalidations[0] !== undefined ? (
                  <p>
                    Wrong if:{" "}
                    {invalidationWords(rc.invalidations[0]).replace(/\.$/, "")}.
                  </p>
                ) : null}
                {rc.run?.challenge ? (
                  <p>
                    Agent challenge: the Bear cited{" "}
                    {count(rc.run.challenge.bear_only.length, "item")} the Bull
                    did not; the judge cited{" "}
                    {rc.run.challenge.judge_cited.length}.
                  </p>
                ) : null}
              </>
            )}
            {r.chance === null ? null : (
              <p>
                <a
                  href={href({ page: "receipt", id: r.forecast_id })}
                  aria-label={`Receipt and share card of ${r.symbol}, ${horizon(r.hours)}, ${r.arm_name}`}
                >
                  Receipt and share card
                </a>
              </p>
            )}
          </article>
        );
      })}
      {mine.filter((r) => arm === null || r.arm_name === arm).length > shown ? (
        <p>
          <button type="button" onClick={() => setShown(shown + 12)}>
            Show more
          </button>
        </p>
      ) : null}
      {cycle !== undefined ? <PostKit cycle={cycle} rows={rows} /> : null}
      <section aria-labelledby="not-this">
        <h2 id="not-this">What this is not</h2>
        <p>
          No money moves here and no order is placed: every call is a practice
          call. Nothing on this site is advice. It never asks for a wallet, a
          seed phrase, a key or a payment, and no token is sold. If any page
          seems to ask for one, it is not this site.
        </p>
      </section>
    </>
  );
}

/** What public voices say about each token and what a fixed cohort of large accounts holds in it. A view beside the league: it feeds no agent. */
export function VoicesView({ index }: { index: SiteIndex }): ReactNode {
  const [doc, setDoc] = useState<Signals | null | undefined>(undefined);
  const listed = index.files["signals.json"] !== undefined;
  useEffect(() => {
    let live = true;
    if (!listed) {
      setDoc(null);
      return;
    }
    loadDocument<Signals>(DATA, index, "signals.json").then(
      (d) => live && setDoc(d),
      () => live && setDoc(null),
    );
    return () => {
      live = false;
    };
  }, [index, listed]);
  return (
    <>
      <p className="kicker">Voices and whales</p>
      <h1>Who is talking about each token, and what the big accounts hold.</h1>
      <p>
        Not part of any agent's evidence and not shown to help a decision. It
        counts voices after removing echoes, and it shows what a fixed group of
        large Hyperliquid accounts holds.
      </p>
      {doc === undefined ? <p>Reading the voices.</p> : null}
      {doc === null ? (
        <p>No voices have been collected for this site.</p>
      ) : null}
      {doc !== undefined && doc !== null ? <SignalsBody doc={doc} /> : null}
    </>
  );
}

function money(text: string): string {
  const n = Number(text);
  if (!Number.isFinite(n)) return text;
  return `${n < 0 ? "-" : ""}$${Math.abs(n).toLocaleString("en-US", { maximumFractionDigits: 0 })}`;
}

function SignalsBody({ doc }: { doc: Signals }): ReactNode {
  const failed = [...doc.accounts, ...doc.wallets].filter(
    (s) => s.state !== "READ",
  ).length;
  return (
    <>
      <p className="muted">
        Collected {when(doc.collected_at)}, from{" "}
        {count(doc.accounts.length, "account")} and{" "}
        {count(doc.wallets.length, "wallet")}; the last {doc.window_hours} hours
        of posts.
        {failed > 0 ? ` ${count(failed, "source")} could not be read.` : ""}
      </p>
      {doc.tokens.map((t) => (
        <section key={t.symbol} aria-labelledby={`sig-${t.symbol}`}>
          <h2 id={`sig-${t.symbol}`}>{t.symbol}</h2>
          <p>
            <a href={href({ page: "callsby", by: "token", value: t.symbol })}>Every call on {t.symbol}</a>
          </p>
          <h3>Voices</h3>
          {t.voices.mentions === 0 ? (
            <p>
              No post named {t.symbol} in the window. That is not the same as no
              interest.
            </p>
          ) : (
            <p>
              {count(t.voices.mentions, "post")} from{" "}
              {count(t.voices.authors, "account")} named it. After removing{" "}
              {t.voices.echoes.length}{" "}
              {t.voices.echoes.length === 1 ? "echo" : "echoes"},{" "}
              {count(t.voices.independent, "independent voice")} remain:{" "}
              {t.voices.up} lean up, {t.voices.down} lean down,{" "}
              {t.voices.neutral} neither (by a word list).
              {t.voices.local_mentions > 0
                ? ` ${count(t.voices.local_mentions, "post")} from a person's own connector are shown apart and not counted.`
                : ""}
            </p>
          )}
          {t.voices.echoes.length > 0 ? (
            <details>
              <summary>
                Why {count(t.voices.echoes.length, "post")} did not count as a
                voice
              </summary>
              <ul>
                {t.voices.echoes.map((e) => (
                  <li key={e.uri}>
                    {e.author}: {e.reason}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
          <h3>Large accounts</h3>
          {t.whales.wallets === 0 ? (
            <p>No wallet was read.</p>
          ) : t.whales.holding === 0 ? (
            <p>
              None of the {count(t.whales.wallets, "wallet")} read holds a
              position in {t.symbol} now.
            </p>
          ) : (
            <p>
              {t.whales.holding} of {count(t.whales.wallets, "wallet")} hold it:{" "}
              {t.whales.long} long ({money(t.whales.gross_long)}) and{" "}
              {t.whales.short} short ({money(t.whales.gross_short)}), net{" "}
              {money(t.whales.net)}. The largest position is{" "}
              {Math.round(Number(t.whales.top_share) * 100)}% of the total.
            </p>
          )}
        </section>
      ))}
      <details>
        <summary>Every source that was asked</summary>
        <ul>
          {doc.accounts.map((s) => (
            <li key={s.actor}>
              {s.actor}:{" "}
              {s.state === "READ"
                ? count(s.posts, "post")
                : `could not be read (${s.reason ?? "unknown"})`}
            </li>
          ))}
          {doc.wallets.length > 0 ? (
            <li>
              {count(
                doc.wallets.filter((w) => w.state === "READ").length,
                "wallet",
              )}{" "}
              read of {doc.wallets.length}
            </li>
          ) : null}
        </ul>
      </details>
      <h2>What this does not know</h2>
      <ul>
        {doc.not_known.map((s) => (
          <li key={s}>{s}</li>
        ))}
      </ul>
      {failed > 0 ? (
        <>
          <h2>Sources that could not be read</h2>
          <ul>
            {[
              ...doc.accounts.map((a) => ({ name: a.actor, s: a })),
              ...doc.wallets.map((w) => ({ name: w.address, s: w })),
            ]
              .filter((x) => x.s.state !== "READ")
              .map((x) => (
                <li key={x.name}>
                  {x.name}: {x.s.reason ?? "unknown"}
                </li>
              ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

function useReviews(index: SiteIndex): Reviews | null | undefined {
  const [doc, setDoc] = useState<Reviews | null | undefined>(undefined);
  const listed = index.files["reviews.json"] !== undefined;
  useEffect(() => {
    let live = true;
    if (!listed) {
      setDoc(null);
      return;
    }
    loadDocument<Reviews>(DATA, index, "reviews.json").then(
      (d) => live && setDoc(d),
      () => live && setDoc(null),
    );
    return () => {
      live = false;
    };
  }, [index, listed]);
  return doc;
}

/** What the stored record shows about a settled call, in descriptions only. Never a reason for the outcome, never a lesson. */
export function ReviewBlock({
  index,
  id,
}: {
  index: SiteIndex;
  id: string;
}): ReactNode {
  const doc = useReviews(index);
  const mine = doc?.reviews.find((r) => r.forecast_id === id);
  if (mine === undefined) return null;
  return (
    <>
      <h2>What the review found</h2>
      <p>
        The price {mine.outcome} {mine.move.replace(/^[-+]/, "")} over the{" "}
        {horizon(mine.hours)};
        that went {mine.agreed}. A review describes the record. It does not say
        the reasoning was right or wrong.
      </p>
      <ul>
        {mine.observations.map((o) => (
          <li key={o.code}>{o.text}</li>
        ))}
      </ul>
    </>
  );
}

const PATTERN_WORDS: Record<string, string> = {
  LOW_CONVICTION: "a chance close to one half",
  EVIDENCE_GAP_AT_DECISION: "a source that brought nothing",
  MARKET_MOVEMENT_CONSISTENT_WITH_ALTERNATIVE: "a move that BTC shared",
};

/** What has and has not been learned from settled calls: the counts, and what is needed before a lesson can be tested. */
export function LearningLedger({ index }: { index: SiteIndex }): ReactNode {
  const doc = useReviews(index);
  if (doc === undefined || doc === null) return null;
  const l = doc.ledger;
  return (
    <>
      <h2>What has been learned</h2>
      <p>{l.statement}</p>
      <dl className="facts">
        <dt>Settled calls reviewed</dt>
        <dd>{l.reviewed}</dd>
        <dt>Lessons proposed, tested, promoted, rejected</dt>
        <dd>
          {l.proposed}, {l.tested}, {l.promoted}, {l.rejected}
        </dd>
      </dl>
      {l.patterns.length > 0 ? (
        <>
          <h3>Patterns that keep coming back</h3>
          <ul>
            {l.patterns.map((p) => (
              <li key={`${p.arm_name}-${p.code}`}>
                {p.arm_name}: {PATTERN_WORDS[p.code] ?? p.code}: {p.support} of{" "}
                {l.n_support} settled calls needed, {p.against_the_call} of them
                went against the call.{" "}
                {p.status === "PROPOSED"
                  ? "A change is proposed and not tested."
                  : "Being watched."}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}

// Documents the Today page needs before it is shown: read once per export, and the page waits for them (React's `use` suspends the
// boundary around the whole page), so they arrive with the rest of the page and nothing below them moves.
const READS = new Map<string, Promise<unknown>>();
function waitFor<T>(index: SiteIndex, path: string): T | null {
  if (index.files[path] === undefined) return null;
  const key = `${index.generated_at} ${path}`;
  let read = READS.get(key);
  if (read === undefined) {
    read = loadDocument<T>(DATA, index, path).catch(() => null);
    READS.set(key, read);
  }
  return use(read) as T | null;
}

/** The token most talked about by independent voices, if at least three: shown as important and not yet checked, with no stance and no call.
 * Ranking rule v1: the number of independent voices in the window; it is an estimate of importance, not a validated one. */
export function UncheckedRow({ index }: { index: SiteIndex }): ReactNode {
  const doc = waitFor<Signals>(index, "signals.json");
  const top =
    doc === null
      ? undefined
      : [...doc.tokens].sort(
          (a, b) => b.voices.independent - a.voices.independent,
        )[0];
  if (doc === null || top === undefined || top.voices.independent < 3)
    return null;
  return (
    <aside aria-labelledby="unchecked-h" className="notice">
      <h2 id="unchecked-h">Important, not yet checked: {top.symbol}</h2>
      <p>
        {count(top.voices.independent, "independent voice")} named it in the
        last {doc.window_hours} hours. Nothing has checked what they say or
        whether any of it is new.
      </p>
      <p>
        <a href={href({ page: "claims" })}>Check this</a> ·{" "}
        <a href={href({ page: "voices" })}>See the voices</a>
      </p>
    </aside>
  );
}

const ITEM_STATE: Record<string, string> = {
  BREAKING_UNVERIFIED: "Not yet checked",
  BEING_CHECKED: "Being checked",
  NO_RELIABLE_CONCLUSION: "No reliable conclusion",
  INVESTIGATED_THESIS: "Investigated",
  MATERIAL_UPDATE: "Changed after the call",
};

/** States whose only reason for ranking is the state itself, already said by the label and the sentence: no second line repeats it. */
const SELF_EVIDENT = new Set(["BREAKING_UNVERIFIED", "MATERIAL_UPDATE"]);

/** The developments the product has recorded: a state in words on each, no stance on one nobody has checked.
 * Ordered by the provisional research-impact ranking of ``impact``; the page says it is provisional and gives the reasons of each. */
export function Developments({ index }: { index: SiteIndex }): ReactNode {
  const all = waitFor<TodayItem[]>(index, "items.json");
  // An invented example is never a development: it stays on the Desk page, where it is labelled, and is not ranked on the first screen.
  const items = all === null ? null : all.filter((i) => i.label !== "synthetic");
  if (items === null || items.length === 0) return null;
  return (
    <section aria-labelledby="developments-h">
      <h2 id="developments-h">Developments</h2>
      <p className="muted">
        Most consequential first, by a provisional ranking that independent
        experts have not yet checked.
      </p>
      <details>
        <summary>How they are ordered</summary>
        <p className="muted">
          The same order for everyone: nothing about you changes it. The first
          investigated or updated call is listed first, the best not yet checked
          beneath it, then the rest by score. An independent origin is where
          information first came from; two reports of one announcement count
          once.
        </p>
      </details>
      <ul className="developments">
        {items.map((i) => (
          <li key={i.item_id}>
            <p className="change-head">
              <span className="pill">{ITEM_STATE[i.state] ?? i.state}</span>{" "}
              <strong>{i.symbol}</strong>
              {i.label === "synthetic"
                ? ", an invented example"
                : i.label === "recorded"
                  ? ", replayed from stored captures"
                  : ""}
              {i.first_seen !== null ? `, first seen ${when(i.first_seen)}` : ""}
            </p>
            <p>
              {i.headline}
              {i.state === "BREAKING_UNVERIFIED" ? ` ${i.unknown}` : ""}
            </p>
            {i.reasons.length > 0 && !SELF_EVIDENT.has(i.state) ? (
              <p className="muted">
                Ranked here: {i.reasons.join(" ")} (score {i.score}).
              </p>
            ) : null}
            {i.link !== null && i.state === "MATERIAL_UPDATE" ? (
              <p>
                <a href={href({ page: "receipt", id: i.link })}>See the call</a>
              </p>
            ) : i.link !== null ? (
              <p>
                <a href={href({ page: "desk" })}>See the run</a>
              </p>
            ) : null}
          </li>
        ))}
      </ul>
    </section>
  );
}

interface ChallengeRow {
  id: string;
  received_at: string;
  kind: string;
  url: string;
  quote: string;
  note: string;
  state: string;
  origin: string;
  reason: string | null;
}

const KIND_WORDS: Record<string, string> = {
  ADD_SOURCE: "A source this call did not read",
  DISPUTE_CLAIM: "A source that disputes it",
  CORRECT_FACT: "A correction of a fact it relied on",
};

function stateWords(c: ChallengeRow): string {
  if (c.state === "QUOTE_FOUND")
    return "The server fetched the source and the quoted words are in it.";
  if (c.state === "QUOTE_NOT_FOUND")
    return "The server fetched the source and the quoted words are not in it.";
  return `The server could not fetch the source (${c.reason ?? "unknown"}), so this is only what the person said.`;
}

/** File an evidence challenge against a call, and see the ones filed. Shown only when the page is served with a live endpoint that keeps them.
 * A challenge is checked against the source it cites; it earns no credit and changes no call. */
export function ChallengeThis({ id }: { id: string }): ReactNode {
  const [on, setOn] = useState(false);
  const [rows, setRows] = useState<ChallengeRow[]>([]);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<string | null>(null);
  const [form, setForm] = useState({
    kind: "DISPUTE_CLAIM",
    url: "",
    quote: "",
    note: "",
  });
  useEffect(() => {
    let live = true;
    if (document.querySelector('meta[name="hoshitori-live"]') === null) return;
    fetch("api/health")
      .then((r) => (r.ok ? r.json() : null))
      .then((h: { challenges?: boolean } | null) => {
        if (!live || h === null || h.challenges !== true) return;
        setOn(true);
        return fetch(`api/challenges?receipt=${id}`)
          .then((r) => (r.ok ? r.json() : null))
          .then(
            (d: { challenges?: ChallengeRow[] } | null) =>
              live && d !== null && setRows(d.challenges ?? []),
          );
      })
      .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [id]);
  if (!on) return null;
  const submit = (): void => {
    setBusy(true);
    setSaid(null);
    fetch("api/challenge", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ receipt: id, ...form }),
    })
      .then(async (r) => ({
        ok: r.ok,
        body: (await r.json()) as {
          challenge?: ChallengeRow;
          new?: boolean;
          error?: string;
        },
      }))
      .then(({ ok, body }) => {
        if (!ok || body.challenge === undefined) {
          setSaid(body.error ?? "This could not be filed.");
          return;
        }
        const c = body.challenge;
        setRows((old) => (old.some((o) => o.id === c.id) ? old : [...old, c]));
        setSaid(
          `${body.new === true ? "Filed." : "This was filed before."} ${stateWords(c)}`,
        );
      })
      .catch(() => setSaid("The server could not be reached."))
      .finally(() => setBusy(false));
  };
  return (
    <>
      <h2>Challenge this call</h2>
      <p className="muted">
        Cite a source and quote its words. The server fetches the source itself
        and looks for them. A challenge earns no credit, changes no call, and
        says nothing about anyone&apos;s reasoning. How many people agree counts
        for nothing.
      </p>
      <form
        className="challenge-form"
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <p>
          <label>
            What it is{" "}
            <select
              value={form.kind}
              onChange={(e) => setForm({ ...form, kind: e.target.value })}
            >
              {Object.entries(KIND_WORDS).map(([k, w]) => (
                <option key={k} value={k}>
                  {w}
                </option>
              ))}
            </select>
          </label>
        </p>
        <p>
          <label>
            Source address (https){" "}
            <input
              type="url"
              required
              maxLength={500}
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
            />
          </label>
        </p>
        <p>
          <label>
            The words you quote from it (12 to 400 characters)
            <textarea
              required
              minLength={12}
              maxLength={400}
              rows={3}
              value={form.quote}
              onChange={(e) => setForm({ ...form, quote: e.target.value })}
            />
          </label>
        </p>
        <p>
          <label>
            A note (optional)
            <textarea
              maxLength={500}
              rows={2}
              value={form.note}
              onChange={(e) => setForm({ ...form, note: e.target.value })}
            />
          </label>
        </p>
        <button type="submit" className="pill" disabled={busy}>
          {busy ? "Checking the source" : "File the challenge"}
        </button>
      </form>
      <div role="status" aria-live="polite">
        {said !== null ? <p>{said}</p> : null}
      </div>
      {rows.length > 0 ? (
        <>
          <h3>Filed so far</h3>
          <ul>
            {rows.map((c) => (
              <li key={c.id}>
                {KIND_WORDS[c.kind] ?? c.kind}: “{c.quote}”: {stateWords(c)}
              </li>
            ))}
          </ul>
        </>
      ) : null}
    </>
  );
}
