# Adversarial set

`tests/test_adversarial_set.py` runs each attack on the record through the check that owns it. Each check is the module's own, worked out by hand and swept against code changes. Run it with:

    python -m pytest tests/test_adversarial_set.py -rA

| Attack | What must happen | Check | What it does not prove |
|---|---|---|---|
| Delayed data | A missing outcome candle is retried, then the call is unsettleable; no price is guessed | `settle_job_checks.check_missing` | A venue that serves a wrong candle rather than none |
| Contradicting venues | Oracles that disagree make the call unsettleable; a tie is void | `settle_job_checks.check_outcomes` | Two venues that agree and are both wrong |
| Missing RYO evidence | A failed RYO call is recorded and the cycle commits on its other routes | `league_checks.check_ryo_route` | RYO answering with plausible but false values |
| RYO refuses the key | Nothing is asked; the reason is recorded | `league_checks.check_ryo_key_status` | A key status that RYO reports wrongly |
| A required source down | The slot fails as missing evidence, never as an abstention | `league_checks.check_failed_evidence` | |
| Clock skew | A stepped clock is refused by the writer and shown to the operator | `deployment_checks.check_clock_status` | A clock that drifts slowly within the tolerance |
| Edited records | A forged bundle is refused by the verifier against the honest root | `forgery_checks.check_the_forgery` | An attacker who holds the signing key |
| Unsupported model assertions | A number the evidence does not hold is refused | `grounding_checks.check_numeric` | A wrong conclusion drawn from true numbers |
| False source claims | A quote that is not in the text is refused | `claim_extract_checks.check_to_graph` | A true quote taken out of its context |
| Embedded instructions | No instruction in untrusted text changes a decision | `injection_checks.check_the_real_defences` | Attacks outside the tested grid |
| Outsider re-settlement | Published calls re-settled from public candles; an edited start, end or outcome is caught | `test_resettle_published` | Target-or-stop calls; the second oracle |

## Re-settling the published record yourself

`scripts/resettle_published.py DATA_DIR` uses only Python's standard library (3.9 or later) and Binance's public candle route. On the packed record (the figures change each time it is packed; the script prints the current count) every settled up-or-down call is re-settled and compared. Target-or-stop calls and unsettled calls are counted as not checked, never as passed.
