# Limitations

What this project does not show, in one place. The per-unit limits are in `STATUS.md`; this page is for the claims a reader might otherwise assume.

## Evidence

- The record holds no observation from RYO's tools yet. The RYO arm and everything that reads RYO's verdict stay empty until a working key exists; the code path is built and tested against a local stand-in. The first RYO reading is bounded by `RYO_ENABLE.md`.
- The record is short and forward-only. It cannot show that any arm has an edge, and no page claims one. Rates are shown with the number of calls behind them, and a rank is withheld until the paired evidence allows one.
- Model arms depend on the model used. Their results are not a statement about other models, and a challenge result states how sensitive a call was to a piece of evidence, not what moved the market.

## Proof

- Two independent time-stamp authorities prove that a checkpoint existed before a stated time. They do not prove that its content is correct, complete or honestly produced. Their tokens state no accuracy.
- Settlement uses candles from two exchanges. A venue that cannot be read is not the same as a venue that disagrees, and a call whose oracles disagree is voided, not scored.
- Ledger durability is tested against killed processes only. Power loss was not tested.

## Product

- The importance ranking has not been validated against expert labels, and no comprehension study with unfamiliar users has been run. The information budgets are measured, not shown to help.
- No screen reader was run against the site. Automated accessibility checks cover the rules they can check.
- There is no social layer in use: no accounts, no reputation, no public posting.

## Skills

- The organisers' skill contract is unpublished. The skills follow the catalogue style of the live tool list and the public API schemas; registration into the platform is not verified.
- Registry files carry a placeholder owner and nothing is published.

## Hosting

- The demo site at https://hoshitori-demo.vercel.app is a static export of the record, hosting the newest cycle's check bundle only; the league itself is not hosted and runs on the development machine. The site's headers were read from the live response (a Content-Security-Policy that allows only its own scripts, styles and data, and the usual companion headers).
