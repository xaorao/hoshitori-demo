# Accessibility specification

Scope: accessibility by construction and proof. Status: DESIGNED; the automated layer was run on the E11 mock (axe-core 4.10.2, SHA-256 b511cd9d...5e3, MPL-2.0, run only); the manual layer has not been run and needs people. Sources read: WCAG 2.2 (W3C Recommendation, full text extracted; the success criteria quoted below were read in the text), WAI-ARIA Authoring Practices (grid pattern text; table, feed, alert, disclosure and dialog patterns listed, not read in full), axe-core (README and 155 rule files listed, rules run, source not read), React Aria and TradingView Lightweight Charts (earlier: Lightweight Charts source search found no ARIA or keyboard handling).

## 1. Binding criteria (WCAG 2.2, read in the text)

| Criterion | Level | What it requires of this design | Where checked |
|---|---|---|---|
| 1.4.3, 1.4.11 contrast | AA | text 4.5:1, interface components and chart marks 3:1, in light, dark and forced colours | axe plus a manual check; E11 screenshots in both schemes |
| 1.4.10 Reflow | AA | no horizontal scrolling at 320 CSS px (equivalent to 400 percent zoom) | E11: no overflow at 320 x 640 |
| 1.4.13 Content on hover or focus | AA | any tooltip is dismissible, hoverable and persistent; **no information exists only in a hover tooltip** (the failure of the open-alpha-arena reasoning column) | component rule and test |
| 2.1.1, 2.1.2, 2.1.4 | A | every function by keyboard; no trap; character shortcuts only if remappable or off by default | manual protocol |
| 2.2.2 Pause, stop, hide | A | auto-updating content (live check results) can be paused or controlled; an update mode toggle on level 2 | component rule |
| 2.4.11 Focus not obscured (min) | AA | sticky banners and queue notices never fully cover the focused item | page test with scrolled tab walk |
| 2.5.7 Dragging movements | AA | no drag-only action; reordering and range selection have click or keyboard alternatives | component rule |
| 2.5.8 Target size (min) | AA | pointer targets at least 24 by 24 CSS px or sufficient spacing; the design uses 44 px | own check in E11 (a skip link measured 23.2 px high at rest while off-screen, which axe did not report: the test measures focused state too) |
| 3.2.6 Consistent help | A | the help or limits link has the same relative order on every page | layout test |
| 3.3.7 Redundant entry | A | a claim or a source link is never requested twice in one process | flow test |
| 3.3.8 Accessible authentication (min) | AA | a browser key or a link; no memory or puzzle test | design rule |
| 4.1.3 Status messages | AA | check results, queue changes and errors are programmatically determinable without focus | live-region tests with screen readers |

## 2. Structure decisions (ADR-011)

- **Today:** a plain list of `article` cards under headings, one tab stop per card (its single control). **Not an ARIA grid and not a feed**: the APG grid is a composite widget with one tab stop and arrow-key navigation that "requires the author to provide code that manages focus movement"; it earns its cost for dense interactive tabular data. A list of cards with one control each needs none of it. (E11: 10 tab stops in the expected order: the skip link, the claim input and button, and seven card controls, a visible 3 px outline on each.)
- **Screener:** a native `table` with sortable header buttons and `aria-sort`; all focusable elements are in the tab sequence, which is the APG table behaviour. A grid is adopted only if cells become interactive, decided by the keyboard walkthrough, not before.
- **Thesis page:** headings for the four content blocks; the argument map is a nested list with statuses as text, with a visual diagram as an optional enhancement generated from the same data; disclosure widgets for expandable evidence (button with `aria-expanded`).
- **Dialogs:** native `dialog` or React Aria's modal, focus returns to the invoking control; the publish preview and the challenge composer are dialogs.
- **Charts:** a text summary, an accessible data table and a keyboard-operable list of markers, all generated from the chart's data object; the canvas is `aria-hidden` and decorative (contract 54).
- **Language:** `lang` set; uncertainty is conveyed in words, never colour alone; state chips carry text; icons are decorative or labelled.
- **Motion:** `prefers-reduced-motion` removes transitions; nothing flashes.

## 3. Test layers

| Layer | Tool | What it can and cannot find |
|---|---|---|
| Static | ESLint a11y plugin, the view-model lints | structural mistakes; no rendering |
| Automated rendered | axe-core 4.10.2 inside Playwright (Chromium, Firefox, WebKit) on every route, every state fixture, light and dark at three widths, and in forced colours and with reduced motion at the desktop and the phone width (the page test fails when anything still moves under reduced motion) | the axe README states about 57 percent of WCAG issues are found automatically; **E11: zero violations and zero incomplete results on the mock** and that is "necessary and nowhere near sufficient" |
| Page checks | Playwright | visible-word counts; tab order and visible focus; target sizes; no horizontal overflow; console and page errors; a focus-not-obscured walk (the element under the middle of the focused control must be the control itself); the navigation, where the help and limits links sit, the same list on every page of one layout (WCAG 3.2.6) |
| Manual protocol | NVDA with Firefox and Chrome on Windows; VoiceOver with Safari on macOS; keyboard-only runs of the five journeys; 200 percent and 400 percent zoom; forced colours; reduced motion; at least one daily screen-reader user | what the other layers cannot: reading order sense, announcement quality, cognitive load |

The five journeys: read Today and open the leading item; check a pasted claim and follow the checks arriving; challenge one piece of evidence; read a receipt and run offline verification; follow a thesis and read an update.

## 4. What is not claimed

No claim of conformance is made. The mock is a single page; screen readers were not run; contrast was checked by axe, not by measurement of every pairing; Lightweight Charts accessibility is judged from a source search only. The accessibility claim stays "designed" until the manual protocol is done by people who are not the author.

## 5. Failure modes

| Failure | Behaviour |
|---|---|
| A component update regresses focus handling | the keyboard walk in CI fails the build |
| Information only in a tooltip, colour or chart | fails the structure lint or the page test |
| A live update steals focus | fails the update test |
