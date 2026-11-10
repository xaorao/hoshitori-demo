import type { ReactNode } from "react";

export type Tokens = {
  colours: Record<string, { role: string; light: string; dark: string }>;
  contrast: { foreground: string; background: string; use: string; minimum: number; light: number; dark: number }[];
};

/** The mark: one open ring and one filled ring, the tally of a win and a loss in sumo. It takes the colour of the text around it. */
export function Mark({ size = 20, decorative = false }: { size?: number; decorative?: boolean }): ReactNode {
  return (
    <svg
      className="logo-mark"
      viewBox="0 0 64 32"
      width={size * 2}
      height={size}
      {...(decorative ? { "aria-hidden": true } : { role: "img", "aria-label": "Hoshitori" })}
    >
      <circle cx="16" cy="16" r="12" fill="none" stroke="currentColor" strokeWidth="4" />
      <circle cx="48" cy="16" r="14" fill="currentColor" />
    </svg>
  );
}

const DOWNLOADS: [string, string][] = [
  ["mark.svg", "The mark"],
  ["logo.svg", "Mark and name"],
  ["favicon.svg", "Tab icon, follows the colour scheme"],
  ["social-card.png", "Share card, 1200 x 630"],
  ["tokens.css", "Colour variables, light and dark"],
  ["tokens.json", "The same, with measured contrast"],
];

const DO = [
  "Say what happened, then what it means. One short line each.",
  "Give the number and its count: 21 settled calls, not “strong results”.",
  "Say what was not checked, in the same place as what was.",
  "Use the words on the page for a state: Open, Settled, Void, Too late, Failed.",
];
const DONT = [
  "No “revolutionary”, “AI-powered”, “alpha”, “guaranteed”, “beats the market”.",
  "No ranking before the evidence allows one. Say “not enough settled calls”.",
  "No advice. Every call is practice, and the page says so.",
  "No colour as the only signal. Every state is a word as well.",
];

function Swatch({ name, role, light, dark }: { name: string; role: string; light: string; dark: string }): ReactNode {
  return (
    <li className="swatch">
      <span className="swatch-chip" style={{ background: `var(--${name})` }} aria-hidden="true" />
      <span>
        <strong>{name}</strong>
        <span className="muted"> {role}</span>
        <br />
        <span className="mono">
          {light} · dark {dark}
        </span>
      </span>
    </li>
  );
}

export function BrandPage({ tokens }: { tokens: Tokens | null }): ReactNode {
  return (
    <>
      <p className="kicker">Brand</p>
      <h1>Mostly nothing. A little ink. One green for proof.</h1>
      <p className="lead">The rules this site follows, so a page or a post made by anyone looks like it belongs.</p>

      <h2>Mark</h2>
      <p>One open ring and one filled ring: the tally of a win and a loss. It stands for a call that has been settled.</p>
      <div className="brand-row">
        <div className="brand-tile">
          <Mark size={48} />
        </div>
        <div className="brand-tile dark-tile">
          <Mark size={48} />
        </div>
      </div>
      <p className="muted">Use ink on paper or paper on ink, never the accent. Leave the height of one ring clear on every side. Do not stretch it, outline it, add effects, or change the rings apart from each other.</p>
      <p>
        The tally is reserved for the mark and for settled outcomes. It is punctuation, not a pattern: it is never a background or a bullet.
      </p>

      <h2>Colour</h2>
      <p>Three parts paper, one part ink, a small amount of green. Green means one thing: independently verified, or settled. Amber means risk or failure. Nothing else uses either.</p>
      {tokens === null ? (
        <p className="muted">The colour list is not loaded.</p>
      ) : (
        <>
          <ul className="swatches">
            {Object.entries(tokens.colours).map(([name, c]) => (
              <Swatch key={name} name={name} role={c.role} light={c.light} dark={c.dark} />
            ))}
          </ul>
          <div className="table-wrap" tabIndex={0} role="region" aria-label="Measured contrast of every text pair">
            <table>
              <caption>Measured contrast of every text pair (WCAG ratio; 4.5 needed, 3 for the focus ring)</caption>
              <thead>
                <tr>
                  <th scope="col">Pair</th>
                  <th scope="col">Used for</th>
                  <th scope="col" className="num">Light</th>
                  <th scope="col" className="num">Dark</th>
                </tr>
              </thead>
              <tbody>
                {tokens.contrast.map((c) => (
                  <tr key={`${c.foreground}-${c.background}`}>
                    <th scope="row">
                      {c.foreground} on {c.background}
                    </th>
                    <td>{c.use}</td>
                    <td className="num">{c.light.toFixed(2)}</td>
                    <td className="num">{c.dark.toFixed(2)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>Type</h2>
      <p>
        The system sans in two weights: 400 for text and 800 for headlines, tight and large. A system mono for kickers, ids, hashes, commitment levels and times. No web fonts, so no
        text waits for a download.
      </p>
      <p className="kicker">Kicker in mono</p>
      <p className="mono">fc7k2…q4 · C3 · 2026-10-30T12:09Z</p>

      <h2>Voice</h2>
      <div className="brand-cols">
        <div>
          <h3>Do</h3>
          <ul>
            {DO.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Do not</h3>
          <ul>
            {DONT.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ul>
        </div>
      </div>

      <h2>Parts</h2>
      <p>Four parts earn their place: the ink button, the pill (Open, Settled, Void, Failed), the receipt block and the count. Borders are 1 px. Radii are 4, 6 and a full pill. No shadows, no gradients.</p>
      <p>
        <span className="button">Ink button</span> <span className="pill">Open</span> <span className="pill ok">Settled</span> <span className="pill warn">Failed</span>
      </p>

      <h2>Downloads</h2>
      <ul>
        {DOWNLOADS.map(([file, what]) => (
          <li key={file}>
            <a href={file} download>
              {file}
            </a>{" "}
            <span className="muted">{what}</span>
          </li>
        ))}
      </ul>
    </>
  );
}
