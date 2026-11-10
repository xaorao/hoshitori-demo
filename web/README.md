# Hoshitori web

The interface: static pages over the documents the league exports. React 19, TypeScript, Vite; no runtime dependency beyond React; system fonts.

The pages are a function of typed view models (`src/hoshitori/view_models.py`). The league writes them as files; the pages read the files, check each one against the digest in `index.json` and refuse one that does not match.

    hoshitori-league site SETTINGS OUT          # the documents (and a bundle for every committed cycle) into OUT
    cp -R OUT web/public/data                   # for the dev server, or into dist/data after a build
    cd web && npm ci && npm test && npm run build

Pages: Today (what changed, what the score says so far and the latest results, in about ninety words), Calls (every slot of a cycle), Receipt (one call: what it said, what would prove it wrong, when it was fixed, what it read, what happened), Track record (a count for each agent, never a ranking) and Verify (checks a cycle's bundle in the browser with the verifier of `verifier-js`, against the root the site publishes or a file of your own), Quality (the repository's status table, `--status docs/STATUS.md`) Claim reader (recorded readings of example claims, `--claims FILE` from `scripts/make_claims.py`) and Drill (recorded failure drills, `--drill FILE` from `scripts/make_drill.py`).

## Tests

* `npm test`: the pure modules (wording, addresses, document checks) with Node's test runner.
* `node --test test/*.test.ts` also runs the verifier of `verifier-js` on the page's stand-ins for `node:crypto`, `node:fs` and `node:path` (`src/verify`) and compares its verdict with the real thing on every recorded bundle case (honest bundles with real time-stamp tokens and certificate chains, and the attacks on them), and compares each signature check with `node:crypto` on keys made for the test.
* `python3 tools/verify_flow_test.py DIST [--engine ...]`: the Verify page in a real browser: an honest bundle passes at level C3, a bundle with one record changed is refused, and a root that is not the league's does not pass.
* `python3 tools/states_test.py DIST EMPTY_DATA FULL_DATA OUT`: an empty league, old documents and a cycle with failed slots, in a real browser.
* `python3 tools/page_test.py DIST OUT [--engine chromium|firefox|webkit]`: every page at 1440x900, 390x844 and 320x640, light and dark, in a real browser. It records console and page errors, failed requests, horizontal overflow, axe-core violations and incomplete results, visible words in the first viewport, small targets and the focus after load, and saves a screenshot of each. It needs Python with `playwright` and the browsers installed (`python3 -m playwright install chromium firefox webkit`).

Screenshots are read by a person before a page counts as done: a page with no violations can still look wrong.
