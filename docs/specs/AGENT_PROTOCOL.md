# Agent protocol

Scope: agent runtime, adaptive next check, debate and league arms. Status: DESIGNED. Product rules: contract sections 6, 13, 25, 43 to 48, 53. Sources studied (depth in the study ledger): LangGraph (code: checkpoint durability modes; MIT), Inspect AI (docs: errors, retries, limits, logs; MIT), DBOS Transact (code: recovery; MIT), ProFinAgent (DAG executor and rule files; no licence file, ideas only), DeepControl (information utility used only during training and needing a gold answer; at test time the model decides when to stop, so it is not the basis of our selector, which follows the value-of-information form of arXiv 2601.06407; no licence file, ideas only), AI-Trader (tool-only information paradigm; no licence file), Haystack (agent exit conditions and step cap; Apache-2.0), TradingAgents (roles, memory; Apache-2.0).

## 1. Decision: a thin typed state machine of our own, durable through the ledger

| Option | Verdict and reason |
|---|---|
| LangGraph | NOT adopted for the loop. Its default checkpoint mode is `durability="async"` (state of step n is persisted while step n+1 runs; modes are `sync`, `async`, `exit`, read in `langgraph/types.py`), a crash can replay a step whose side effect already happened (the open issue read in the landscape study), and a second persistence layer would have to be kept consistent with our ledger and outbox. With `sync` and a custom checkpointer writing to our outbox it would work; it would still add a graph DSL, a state-reducer model and a dependency whose behaviour we then have to test against our invariants |
| DBOS Transact | NOT adopted. It runs on SQLite (default system database) or Postgres and recovers pending workflows by re-enqueueing them. **E4 measured it with the same kill rounds as our outbox** (30 SIGKILL rounds, SQLite): 10,814 workflows, 0 duplicate ledger lines, 0 torn lines, all workflows `SUCCESS` after recovery, 0 pending; the one external effect that is not a database transaction (a done-mark file) was executed twice for one workflow, as expected for at-least-once steps, so idempotency of effects stays our job exactly as with the outbox. Throughput 383 workflows per second against 1,830 for a plain two-transaction outbox on the same machine (both far above the need). The deciding reason is semantic: the contract forbids resuming an interrupted cycle after its snapshot (`ABANDONED`, never reused), while automatic resumption of pending work on restart is what DBOS is for; every cycle step would have to be cancelled explicitly on restart. For user-initiated investigations resumption would be welcome and may justify DBOS later |
| Temporal | NOT adopted: needs a server, determinism rules for workflow code and a sandbox; the benefit appears at scale and with multi-host workers we do not have |
| Own state machine | CHOSEN: about a dozen typed steps, each a pure function of recorded inputs, the ledger and outbox as the only durability, replay from stored model and tool outputs. Smaller than any alternative and testable against our invariants directly |

The runtime has no framework-level magic: every step reads and writes `RunState` (a Pydantic model), and every effect (gateway call, model call, ledger append) is a recorded event with a natural id.

## 2. The run

```
START -> PLAN -> LOOP{ SELECT_CHECK -> ACQUIRE -> INTERPRET -> UPDATE_GRAPH -> STOP_TEST }
      -> FALSIFY -> DECIDE -> GATES -> COMMIT | ABSTAIN | FAIL
```

| Step | Who computes it | Input | Output | Notes |
|---|---|---|---|---|
| `PLAN` | model (typed) | candidate, claim graph, menu | ordered hypotheses, required premises | the plan can propose but does not choose tools |
| `SELECT_CHECK` | **deterministic** `select_next_check` | `RunState` | ranked checks with components, or `NO_CHECK_WORTH_RUNNING`, `NO_AVAILABLE_CHECK`, `CANNOT_FLIP_DECISION` | the same function as the interface (contract section 44.1); an injected instruction cannot choose a tool |
| `ACQUIRE` | gateway | a registered check | `RawCapture` + `EvidenceItem`s | operational regime: live call; league: read from the frozen snapshot |
| `INTERPRET` | deterministic parse; model only for text claims | evidence | claim-graph node updates with spans | numbers by code |
| `UPDATE_GRAPH` | deterministic | node updates | new statuses by the propagation rules (contract section 34.4) | support never propagates |
| `STOP_TEST` | deterministic | budgets, margins | continue or stop with a reason from the closed lists | stop reasons are not abstentions |
| `FALSIFY` | deterministic tests + model-assisted | the target assumption | `FalsificationRecord` | contract section 48; can only reduce exposure |
| `DECIDE` | model (typed) or rules arm | graph, evidence, falsification | proposal: action, `p`, invalidation, cited evidence ids | the model proposes |
| `GATES` | **deterministic** | proposal | final action | grounding gate, required evidence, minimum edge, falsification outcome, risk kernel (monotone shrink); both proposal and final action are stored |
| `COMMIT` | outbox | final record | ledger append | idempotent by natural id |

Stop reasons (closed): `UTILITY_BELOW_THRESHOLD`, `CANNOT_FLIP_DECISION`, `BUDGET_EXHAUSTED`, `ALL_MENU_OPENED`. Abstention reasons (closed): `EVIDENCE_CONFLICT_UNRESOLVED`, `ESSENTIAL_PREMISE_UNPROVEN`, `BELOW_MINIMUM_EDGE`, `OUT_OF_SCOPE` (voluntary `NO_CALL`); `FAILED_EVIDENCE` when a required source is down (operational failure, never an abstention).

## 3. The model interface

`ModelClient.generate(request: TypedRequest) -> TypedResponse` is the only way any code reaches a model. Provider-neutral (the decision to start with an OpenAI-compatible endpoint, NVIDIA's, is a deployment choice behind this interface; the model is chosen by benchmark, ADR-009).

- **Typed in, typed out.** The request names a stage and carries a Pydantic response schema; the response is validated; one repair attempt with the validator's message, then `MODEL_OUTPUT_INVALID` (a failure class, not a guess). Free text appears only inside typed fields with length caps.
- **Every call is a record.** `ModelCall`: stage, run id, prompt version hash, request digest, response digest, model id and parameters, token counts, latency, finish reason, retries. Prompt and response bodies are stored privately (they may contain private claim text) and referenced by digest in the public ledger; a bundle can include them when the content is public.
- **Replay.** Temperature zero is not repeatable (a study cited in the landscape report), so repeatability comes from storing outputs, not from re-running: `replay(run)` feeds each step the stored `ModelCall` response and must reproduce the final record byte for byte. **Fresh-run agreement** (re-asking the model) is a different measure and writes new linked records (contract section 11).
- **No tool-calling by the model.** The model never emits a call; the gateway runs registered checks chosen by code. Where a provider API offers tool calling it is not used.
- **Budgets and failure.** Per-stage timeouts; token ceilings; provider failure follows the same breaker and back-off vocabulary as the gateway; a model outage is `FAILED` (operational) with `evidence preserved`; the rules arm and the deterministic stages continue, and the page says the model provider is down (contract section 35.7).
- **Untrusted text** is delimited, flagged as data and never mixed with instructions; no secret is ever placed in a prompt (tested by prompt capture, contract section 53).

## 4. Tools (the registry)

Every tool is a typed, read-only function behind the gateway: `ryo.market_overview`, `ryo.scan_market`, `ryo.analyze_token`, `ryo.deep_analysis`, `ryo.compare_tokens`, `ryo.monitor_sentiment`, `exchange.candles`, `exchange.funding`, `news.search`, `registry.resolve`, `web.fetch` (safe client). Each declares input and output schemas, cost in the provider's unit, timeout, idempotency (all `GET`-like), and the evidence kind it yields. A `Check` is a registered tool call bound to a claim-graph node. The registry is built from the live RYO catalogue and the adapters' contracts; a tool in `CONTRACT_DRIFT` is unavailable and `select_next_check` knows it.

## 5. `select_next_check` as a module

`select_next_check(state: RunState, menu: Menu, calib: CalibrationStore, lam: float) -> Ranking`. Pure. Components of `value(c)` are computed by named functions (`resolvability`, `decision_gain`, `essentiality`, `independence_bonus`, `cost`) so each can be unit-tested; dependency order is enforced after scoring (a downstream premise cannot outrank an unproven upstream one). The `CalibrationStore` is read-only at run time and is built offline from experiment-cycle full menus (contract section 44.1a); a stratum below `n_min` observations or above the error threshold falls back to dependency order and the decision log says so. Property tests: determinism, dependency order, stability under permutation of the menu, equal outputs for the agent and the interface on the same state.

**Policies for the experiment** (contract section 43.4): `P-FIXED`, `P-DEPENDENCY`, `P-ADAPT`, `P-RANDOM`, `P-ALL`, implemented behind one `Policy` interface and replayable against recorded full menus; a policy error falls back to `P-FIXED` and is labelled. **Rule from the benchmark program:** if `P-ADAPT` does not beat `P-DEPENDENCY` and `P-FIXED` at equal budget on missed contradictions and cost, the simpler rule ships.

**Corroboration across origin families (built in the Desk run, `P-DEPENDENCY`).** A part that exactly one origin family supports (contract section 40: every RYO tool is one family; an exchange is one family whatever its products) stays open to the one next-check rule while a check of another family that can read it is untried and affordable, and only such checks are offered for it. Two supporting families settle the part and a third is not sought. Corroboration is asked for and never required: with no other family readable or payable the part stands on the one family and the record names who read it. Families that disagree make the part MIXED; a run left with a disputed required part abstains as `EVIDENCE_CONFLICT_UNRESOLVED`. The check record carries `origin_family` and `own_status`; the page reads "N independent origins read this part". The gates do not yet treat one origin and two differently: a lower ceiling for a single origin would be a pre-registered rule.

**The address screen (built in the Desk run).** When the menu holds a screen of the token's contract (`ADDRESS_FLAG_CHECK`, offered only with a contract from the registry and a provider that is attached), the run screens it first, before any check of the claim. The screen is the address-flag skill read through the gateway (`address_screen`, `address_gateway`). A source that lists the address ends the run as `OUT_OF_SCOPE` with the gate `HAZARD_SCREEN` stopped; a clean read (`NO_FLAG_FOUND`) or an incomplete one passes that gate with a sentence that says so, changes nothing about how the claim is judged and never raises confidence; a screen that could not be made leaves no gate. It is not an origin that read the claim and is not counted as one. "No flag found" is never "safe".

## 6. Run state machine and the decision log

```
RUNNING -> STOPPED(reason) -> {COMMITTED | ABSTAINED(reason)}
RUNNING -> FAILED(class) -> (retry by the scheduler under the cycle's rules) | FAILED_FINAL
```

Every transition is a ledger event. The **decision log** of a run is the ordered list of `Check` records (check, rank components, why it outranked the alternatives, cost, result summary), `ModelCall` records, the falsification record, the gate events and the final record. It is the object the thesis page renders (*what the agent checked and why*) and the object a judge inspects.

## 7. Operational and league regimes share code

`CONTROLLED_LEAGUE` and `OPERATIONAL` differ only in two bindings: where `ACQUIRE` reads from (the frozen snapshot or the live gateway) and how the origin is set (the cycle origin or the run's own `eligible_after`). The information class is stamped on the record from the binding, not set by the code that runs. Copy and tables never call snapshot menu selection "live tool use".

## 8. Fixed-order baseline and the independent scorer

- The fixed-order live agent (`P-FIXED`) runs on a pre-registered share of triggers with the same menu and budget as the adaptive agent.
- The reasoning-quality scorer (contract section 45) is separate from the agents: it reads stored decision logs and graph records and writes a vector; its labels come from humans, a model judge is secondary and only after it matches human agreement. Harness choice for the offline benchmarks (Inspect AI adopted, or our own small runner) is ADR-008: Inspect's failed samples are not scored by default (documentation read), so any harness must compute its own denominators including errors. **Spike E15 (decided):** with the pinned clone (mock model, offline, 12 planted abstention scenarios, one planted solver failure) the run status was `success`, the headline accuracy was 1.0 over the 11 completed samples, the errored sample was absent from every metric (a custom metric never saw it, and values reach metrics already converted to floats), and the true coverage, 11 of 12, was available only from the log (`results.completed_samples` against `results.total_samples`). Inspect is adopted for the model-in-the-loop benchmark runs (retries, logging, parallelism) **only behind a mandatory report step that computes coverage and error counts from the log and refuses to print an accuracy without them**; the settlement scorer of the league is our own deterministic code over the ledger and does not use it.

## 9. Failure modes and tests

| Failure | Behaviour | Test |
|---|---|---|
| Crash between any two steps | the run resumes from the last committed event or is closed `FAILED`; no duplicate check, call or record | kill rounds at every step boundary |
| Model returns invalid JSON twice | `MODEL_OUTPUT_INVALID`; run falls back to the rules arm's decision flagged `DEGRADED_MODEL` or fails per stage rule | fault injection |
| Model proposes a check not on the menu | rejected, logged | policy test |
| Injected instruction in evidence text | recorded as `UNTRUSTED_INSTRUCTION_IGNORED`; no effect | injection suite |
| Budget exceeded mid-step | the step is not started if it cannot finish; stop reason recorded | budget test |
| Replay with changed code version | refused with the version mismatch (the run record names its code and prompt hashes) | version test |
| Two runs for the same candidate and cycle | second is a no-op (natural id) | idempotency test |

Connection to the journey: this is the "one autonomous investigation" step. The page for a run is the decision log; the next-check card and the agent's own next check are one function.
