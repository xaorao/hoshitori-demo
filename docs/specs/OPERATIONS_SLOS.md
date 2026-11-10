# Service-level indicators and objectives

Scope: what "working" means, measured. Status: DESIGNED; every number below is a **prior**, to be replaced by values measured over a pre-registered observation period; no SLO is claimed to be met. Product rules: the common 40 points (combining sources, coping with failure), contract sections 12, 16, 25, 33; standard 5d, 5.

## 1. How objectives are set

An indicator is a ratio of good events to valid events over a window (as in the SRE practice of the SLO and error-budget method; the book itself was not downloaded, so the method is stated here as understood and is NOT VERIFIED against it). The objective is a target for that ratio. The budget is `1 − objective`. A budget burn above a pre-registered rate pages the operator and freezes risky changes. Objectives are set after one measured period, not before; until then the targets are provisional and displayed as such.

## 2. User-facing indicators

| Indicator | Good event | Valid event | Prior objective |
|---|---|---|---|
| Today availability | Today page returned within 1 s (cached) with content not older than twice the cycle interval, or an explicit stale label | page request | 99.5% |
| Today freshness | the list's `generated_at` within twice the precompute interval | minute | 99% |
| Preliminary page latency | preliminary page within 5 s (a prior from contract 35.6, model latency NOT MEASURED) | on-demand investigation started | 90% |
| First independent check | first check result within 10 s | on-demand investigation | 90% |
| Check completion | all planned checks reach a terminal state within the stated deadline | investigation | 95% (the rest are `PARTIAL_AUDIT`, stated) |
| Verification | the offline verifier returns a verdict on a downloaded bundle on a clean machine | bundle verify | 100% for the delivered bundle set (a defect otherwise) |

## 3. Pipeline indicators

| Indicator | Good | Valid | Prior |
|---|---|---|---|
| Cycle start | cycle starts within 60 s of schedule | scheduled cycle | 99% |
| Cycle completion | all arms accepted or `FAILED_LATE`/`FAILED_EVIDENCE` with a reason; manifest sealed and snapshot hashed | cycle | 98% (the rest `CYCLE_ABANDONED`, shown) |
| Commitment level | cycle reaches C3 | committed cycle | 95%; the share missing C3 is always shown (contract 8) |
| Evidence freshness at collection | each item within its collection limit (price 60 s, RYO tools 300 s, news 900 s) | evidence item | 99% |
| Source health | per source: successful calls over attempted, `status` ok or partial | call | measured, no single target; a source below its floor is flagged `COVERAGE_LOSS` |
| Settlement | forecast settled or given an explicit terminal state within 1 hour after the window closes | forecast | 99% |
| Outbox age | oldest unappended outbox row under 10 s | minute | 99.9% |

## 4. Error-budget policy

When the budget of a user-facing objective is spent: feature work that touches that path stops, the next change must reduce risk (a fix, a test, a runbook), and the post-incident review is published in the repository's operations notes. Scored periods are never extended or altered by an incident; an affected cycle is marked, not hidden.

## 5. What these do not cover

Prediction quality, ranking quality and user comprehension are evaluated by the benchmark programs, not by SLOs. A green SLO dashboard is not evidence of a correct decision.

## 6. Tests

Synthetic probes of the page, the on-demand path and the verifier run from a second host every minute; every SLI has a fixture that makes it fail and a test that the alert fires; the fault-injection matrix drives the cycle indicators; the budget arithmetic is unit-tested.
