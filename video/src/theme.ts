// The product's own tokens (web/src/styles.css, web/src/brand.tsx): white field, ink, one amber accent, system sans and system mono.
// All layout numbers of the film live here so that scene components stay free of numeric literals.

export const colour = {
  paper: "#ffffff",
  halo: "#fafaf7",
  line: "#e5e7eb",
  fog: "#9ca3af",
  graphite: "#4b5563",
  ink: "#0a0a0a",
  /** The one accent. It means "fixed in time" and nothing else. */
  amber: "#b45309",
} as const;

export const font = {
  sans: 'system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", "Hiragino Sans", "Noto Sans JP", "Yu Gothic", Arial, sans-serif',
  mono: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
} as const;

export const weight = { text: 400, head: 800 } as const;

/** Type scale, px at 1080p. */
export const size = {
  giant: 320,
  huge: 168,
  head: 104,
  title: 72,
  body: 56,
  lead: 46,
  mono: 38,
  monoSmall: 30,
  tiny: 24,
  kicker: 28,
  caption: 42,
  subtitle: 32,
} as const;

export const layout = {
  width: 1920,
  height: 1080,
  marginX: 160,
  top: 96,
  /** Everything above this y stays clear of the caption layer. */
  contentBottom: 800,
  captionEn: 112,
  captionJa: 50,
  rule: 8,
  hair: 2,
  slideDistance: 96,
  nudge: 12,
  origin: 0,
  stageTop: 72,
  ruleWide: 1000,
  page: 1600,
  squareMargin: 0,
  squareLift: 90,
} as const;

export const spacing = { s: 12, m: 24, l: 48, xl: 96 } as const;

export const tracking = { kicker: "0.14em", head: "-0.03em", tight: "-0.02em" } as const;

/** Boxes for crops of real screens, px. */
export const box = {
  wide: { w: 1560, h: 580 },
  tall: { w: 1560, h: 640 },
  half: { w: 880, h: 540 },
  phone: { w: 300, h: 620 },
  diagram: { w: 1600, h: 360 },
  video: { w: 1560, h: 580 },
} as const;

const PAGE = layout.width - layout.marginX * 2;
const COLUMN = 320;
/** Scene 2's geometry, in px inside the content area (its left edge is the margin, its top edge is layout.top). */
export const loop = {
  page: PAGE,
  column: COLUMN,
  centres: [0, 1, 2, 3, 4].map((i) => COLUMN * i + COLUMN / 2),
  thirds: [1, 3, 5].map((k) => (PAGE / 6) * k),
  first: COLUMN / 2,
  last: COLUMN * 4 + COLUMN / 2,
  left: 0,
  dot: 26,
  halfDot: 13,
  halfColumn: COLUMN / 2,
  lineHeight: 4,
  dotTop: 240 - 13 + 2,
  labelTop: 120,
  lineY: 240,
  timesTop: 290,
  practiceTop: 470,
  oneTop: 560,
  tracksTop: 700,
} as const;

/** Crops of real screens: the largest scale of a captured pixel (the captures are at twice the page's size). */
export const crop = { maxScale: 1.15, videoScale: 0.82, videoLeft: 40 } as const;

/** Counts and ratios that shape drawings (they carry no measurement). */
export const shape = {
  squares: 12,
  squareSize: 40,
  squareGap: 16,
  bracketHeight: 28,
  stamp: { w: 300, h: 120, border: 6 },
  dotColumn: 320,
  loopLabel: 56,
  barHeight: 22,
  barWidth: 1200,
  tick: 36,
  half: 0.5,
  third: 1 / 3,
  whole: 1,
  none: 0,
  strokeWide: 3,
  phoneGap: 48,
  listRow: 36,
  pairGap: 64,
  leadIn: 0.12,
  mutedAlpha: 0.38,
  pairCount: 2,
  cutDiagram: 2.4,
  cutStamps: 1.2,
  cutVerify: 1.2,
  cutStrike: 0.4,
  cutFell: 0.1,
  cutStampsShort: 0.9,
  cutFellShort: 0.2,
  cutCaption: 1.2,
  cutCaptionSquare: 1.35,
} as const;

const SEAL_COUNT = 12;
const SEAL_SIZE = 40;
const SEAL_GAP = 16;
const SEAL_PACKED_W = SEAL_COUNT * SEAL_SIZE + (SEAL_COUNT - 1) * SEAL_GAP;
const SEAL_INDEX = Array.from({ length: SEAL_COUNT }, (_, i) => i);
/** Scene 4's diagram: a batch of records gathers into a bracket, becomes a checkpoint, and two operators stamp it. */
export const seal = {
  size: SEAL_SIZE,
  top: 40,
  bracketTop: 120,
  bracketHeight: 28,
  bracketWidth: SEAL_PACKED_W,
  bracketLeft: (PAGE - SEAL_PACKED_W) / 2,
  stemHeight: 56,
  labelTop: 236,
  stampTop: 340,
  cutTop: 120,
  cutVerifyTop: 330,
  stampW: 320,
  stampH: 120,
  stampGap: 64,
  stampBorder: 6,
  stampFrom: 1.18,
  spread: SEAL_INDEX.map((i) => (i * (PAGE - SEAL_SIZE)) / (SEAL_COUNT - 1)),
  packed: SEAL_INDEX.map((i) => (PAGE - SEAL_PACKED_W) / 2 + i * (SEAL_SIZE + SEAL_GAP)),
  index: SEAL_INDEX,
} as const;

/** Scene 5's interval bar, px. */
export const bar = { width: 1200, height: 22, tick: 48, top: 40, label: 120 } as const;

/** Scene 7's drill list, px. */
export const drills = { row: 38, listW: 560, cropW: 940, gap: 48 } as const;

/** Left edge of stamp `i` of `count`, centred as a row inside the content area. */
export function stampLeft(count: number, i: number): number {
  const total = seal.stampW * count + seal.stampGap * (count - 1);
  return (layout.page - total) / 2 + i * (seal.stampW + seal.stampGap);
}
