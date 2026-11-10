# Mutation sweeps

Each module has checks, and a sweep makes every small change of the module's code (a comparison turned round, an `and` made an `or`, a
constant moved, a statement dropped) and asks the checks to notice it. This page says how each change was noticed, because "noticed" is not
one thing:

* **Noticed by a check**: a check reported a failure on the changed module. This is the evidence a sweep exists to give.
* **Could not be built**: the changed module could not be loaded at all.
* **Check raised an error**: the checks raised an error on the changed module.
* **Clock or machine only**: the checks ran out of time on two tries, or met an operating-system error that did not come back the same.
  Nothing about the checks is shown by these; they are counted as noticed so that a busy machine cannot invent a survivor, and they are
  named below the table.
* **Survived**: no check noticed the change. Each is listed in the module's test with the reason it is harmless.

A sweep passes when its survivors are exactly the listed harmless ones. A module without a sweep is not in the table.

## What is changed

Each place in a module's syntax tree is changed once, in one of these ways:

* a comparison turned round (`<` to `<=`, `==` to `!=`, `in` to `not in`, `is` to `is not`);
* `and` made `or` and `or` made `and`; a `not` removed; the test of an `if`, a conditional expression or a `while` negated;
* `+` and `-` swapped, `*` made `+`;
* `any` and `all`, `max` and `min`, `.add` and `.discard` swapped; `sorted(x)` replaced by `x`;
* `True` made `False` and the reverse; an integer constant raised by one; a non-empty string constant given a `~` at the end;
* a returned value replaced by `None`;
* a statement dropped: an assignment, an augmented assignment, a `raise`, a `break`, a `continue` or a call used as a statement.

Places that carry words and not rules are left out: docstrings, annotations, the parts of f-strings and the message of a raised error.

## Running one

Each module with a sweep has `tests/<module>_checks.py` (the checks) and `tests/test_<module>.py`, which runs the checks on the real module and
then the sweep. Deselect the slow sweep while working with `-k "not every_change"`. To see the survivors themselves, call
`tests.mutation_sweep.survivors("hoshitori.<module>", "tests.<module>_checks")`. Set `HOSHITORI_SWEEP_LOG` to a file and each call appends one
line; `python tests/sweep_report.py LOG docs/SWEEP.md` makes this page from it. Run sweeps one at a time, and do not edit a module or its
checks while its sweep runs: the sweep imports both.

## Survivors and the list of harmless changes

Every survivor is read. If it is a rule nobody tests, write the check first, see it fail against the changed module, and add it to the
module's checks. If the change does not alter what the code does, list it in the `EQUIVALENT` dictionary of the module's test with the reason.
The key is `function|operator|code|index`, as the sweep prints it. The test requires the list to equal the survivors, so a new survivor fails it
and so does an entry that no longer survives. A change that only a loaded machine can make look noticed (a codec name, a file name) may be
named as load-sensitive, and is then allowed to be missing from the survivors; an unlisted survivor still fails.

## What a clean sweep shows

For the changes it makes, some check notices each one. It does not show that the checks are right, that the module meets its specification,
or that the module is free of faults the changes do not imitate (a missing feature, a wrong design, a race). It is one layer beside the checks
derived from the specification, the property tests, the differential tests against independent implementations and the journey tests.
The table below is made from the sweeps of the code as it was when the page was made; the test beside it fails if a swept file has changed since.

| Module | Places changed | Noticed by a check | Could not be built | Check raised an error | Clock or machine only | Survived |
|---|---|---|---|---|---|---|
| hoshitori.address_gateway | 98 | 95 | 0 | 2 | 0 | 1 |
| hoshitori.address_screen | 281 | 280 | 0 | 0 | 0 | 1 |
| hoshitori.adjudication | 305 | 230 | 0 | 63 | 3 | 9 |
| hoshitori.adjudication_store | 139 | 118 | 0 | 20 | 0 | 1 |
| hoshitori.anytime | 221 | 200 | 0 | 0 | 0 | 21 |
| hoshitori.arena | 226 | 161 | 0 | 64 | 0 | 1 |
| hoshitori.arm_input | 67 | 67 | 0 | 0 | 0 | 0 |
| hoshitori.arm_l1 | 171 | 151 | 20 | 0 | 0 | 0 |
| hoshitori.arm_l1x | 117 | 113 | 2 | 0 | 0 | 2 |
| hoshitori.arm_l2 | 133 | 122 | 11 | 0 | 0 | 0 |
| hoshitori.arm_run_record | 272 | 272 | 0 | 0 | 0 | 0 |
| hoshitori.assertions | 243 | 195 | 48 | 0 | 0 | 0 |
| hoshitori.attest | 81 | 76 | 0 | 0 | 5 | 0 |
| hoshitori.backup | 950 | 935 | 2 | 0 | 0 | 13 |
| hoshitori.bar_coverage | 33 | 17 | 1 | 15 | 0 | 0 |
| hoshitori.barrier_arm | 52 | 52 | 0 | 0 | 0 | 0 |
| hoshitori.barrier_rates | 249 | 247 | 2 | 0 | 0 | 0 |
| hoshitori.belief_revision | 388 | 354 | 18 | 0 | 4 | 12 |
| hoshitori.book | 259 | 257 | 1 | 0 | 0 | 1 |
| hoshitori.call_revisions | 108 | 104 | 2 | 0 | 0 | 2 |
| hoshitori.card_lint | 77 | 60 | 0 | 14 | 0 | 3 |
| hoshitori.challenge_store | 295 | 195 | 0 | 50 | 24 | 26 |
| hoshitori.challenge_view | 113 | 35 | 0 | 78 | 0 | 0 |
| hoshitori.changes | 209 | 199 | 8 | 0 | 0 | 2 |
| hoshitori.claim_assumptions | 62 | 28 | 0 | 34 | 0 | 0 |
| hoshitori.claim_benchmark | 253 | 233 | 0 | 14 | 0 | 6 |
| hoshitori.claim_checks | 23 | 2 | 0 | 21 | 0 | 0 |
| hoshitori.claim_extract | 108 | 37 | 15 | 56 | 0 | 0 |
| hoshitori.claim_graph | 346 | 193 | 12 | 138 | 3 | 0 |
| hoshitori.claim_move | 105 | 45 | 0 | 60 | 0 | 0 |
| hoshitori.claim_next_check | 133 | 80 | 0 | 52 | 0 | 1 |
| hoshitori.claim_preliminary | 66 | 49 | 0 | 17 | 0 | 0 |
| hoshitori.claim_segmenter | 170 | 131 | 0 | 39 | 0 | 0 |
| hoshitori.claim_sketch | 193 | 126 | 0 | 67 | 0 | 0 |
| hoshitori.claim_stability | 51 | 28 | 0 | 22 | 0 | 1 |
| hoshitori.claims_file | 591 | 110 | 0 | 480 | 0 | 1 |
| hoshitori.clusters | 79 | 65 | 0 | 0 | 3 | 11 |
| hoshitori.collect | 252 | 252 | 0 | 0 | 0 | 0 |
| hoshitori.command_log | 79 | 62 | 1 | 9 | 0 | 7 |
| hoshitori.commit | 63 | 61 | 0 | 0 | 0 | 2 |
| hoshitori.commitment_store | 55 | 55 | 0 | 0 | 0 | 0 |
| hoshitori.comparison | 69 | 64 | 1 | 0 | 0 | 4 |
| hoshitori.coverage_view | 87 | 86 | 0 | 0 | 0 | 1 |
| hoshitori.crosscheck | 101 | 28 | 1 | 70 | 0 | 2 |
| hoshitori.cycle | 416 | 404 | 9 | 0 | 0 | 3 |
| hoshitori.decision_alerts | 159 | 159 | 0 | 0 | 0 | 0 |
| hoshitori.deployment | 1097 | 922 | 0 | 0 | 175 | 0 |
| hoshitori.desk | 613 | 608 | 0 | 0 | 0 | 5 |
| hoshitori.desk_acquire | 240 | 239 | 0 | 0 | 0 | 1 |
| hoshitori.desk_baselines | 77 | 62 | 0 | 9 | 1 | 5 |
| hoshitori.desk_gates | 238 | 233 | 5 | 0 | 0 | 0 |
| hoshitori.desk_harness | 120 | 115 | 0 | 0 | 2 | 3 |
| hoshitori.desk_history | 53 | 52 | 0 | 0 | 0 | 1 |
| hoshitori.desk_hook | 280 | 277 | 0 | 0 | 0 | 3 |
| hoshitori.desk_interpret | 125 | 124 | 1 | 0 | 0 | 0 |
| hoshitori.desk_live | 189 | 177 | 0 | 8 | 1 | 3 |
| hoshitori.desk_menu | 238 | 234 | 1 | 0 | 0 | 3 |
| hoshitori.desk_model | 71 | 64 | 6 | 0 | 0 | 1 |
| hoshitori.desk_plan | 151 | 149 | 2 | 0 | 0 | 0 |
| hoshitori.desk_record | 728 | 722 | 5 | 0 | 0 | 1 |
| hoshitori.desk_recovery | 121 | 120 | 0 | 0 | 0 | 1 |
| hoshitori.desk_view | 228 | 225 | 0 | 0 | 0 | 3 |
| hoshitori.drill | 108 | 25 | 0 | 82 | 0 | 1 |
| hoshitori.evidence | 568 | 558 | 8 | 0 | 0 | 2 |
| hoshitori.evidence_sources | 182 | 182 | 0 | 0 | 0 | 0 |
| hoshitori.exploratory_root | 105 | 104 | 0 | 0 | 0 | 1 |
| hoshitori.falsify | 422 | 415 | 0 | 0 | 0 | 7 |
| hoshitori.falsify_calibration | 126 | 125 | 0 | 0 | 0 | 1 |
| hoshitori.features | 123 | 121 | 1 | 0 | 0 | 1 |
| hoshitori.fills | 227 | 224 | 1 | 0 | 1 | 1 |
| hoshitori.forecast | 396 | 393 | 0 | 0 | 0 | 3 |
| hoshitori.forgery | 242 | 76 | 1 | 160 | 1 | 4 |
| hoshitori.gap_record | 109 | 108 | 0 | 0 | 0 | 1 |
| hoshitori.grounding | 468 | 459 | 8 | 0 | 0 | 1 |
| hoshitori.history | 107 | 107 | 0 | 0 | 0 | 0 |
| hoshitori.host_probe | 268 | 263 | 2 | 0 | 0 | 3 |
| hoshitori.impact | 111 | 110 | 0 | 0 | 0 | 1 |
| hoshitori.impact_validation | 245 | 243 | 0 | 0 | 0 | 2 |
| hoshitori.injection | 479 | 471 | 6 | 0 | 0 | 2 |
| hoshitori.intervention | 78 | 28 | 0 | 49 | 0 | 1 |
| hoshitori.lab | 315 | 292 | 1 | 12 | 0 | 10 |
| hoshitori.lab_forward | 259 | 240 | 1 | 7 | 4 | 7 |
| hoshitori.lab_propose | 546 | 494 | 46 | 3 | 0 | 3 |
| hoshitori.lab_view | 374 | 324 | 0 | 38 | 0 | 12 |
| hoshitori.labels | 200 | 192 | 1 | 7 | 0 | 0 |
| hoshitori.league | 462 | 459 | 0 | 0 | 0 | 3 |
| hoshitori.league_cli | 467 | 461 | 2 | 0 | 2 | 2 |
| hoshitori.ledger_reader | 136 | 134 | 0 | 0 | 0 | 2 |
| hoshitori.lessons | 567 | 467 | 1 | 71 | 0 | 28 |
| hoshitori.lessons_store | 130 | 108 | 0 | 22 | 0 | 0 |
| hoshitori.lifecycle | 597 | 571 | 21 | 0 | 0 | 5 |
| hoshitori.lineage | 166 | 165 | 0 | 0 | 1 | 0 |
| hoshitori.manifest | 345 | 343 | 0 | 0 | 0 | 2 |
| hoshitori.model_arms | 203 | 202 | 0 | 0 | 0 | 1 |
| hoshitori.model_call_record | 305 | 305 | 0 | 0 | 0 | 0 |
| hoshitori.model_challenge | 186 | 95 | 1 | 84 | 0 | 6 |
| hoshitori.model_challenge_file | 744 | 141 | 0 | 599 | 0 | 4 |
| hoshitori.model_client | 454 | 454 | 0 | 0 | 0 | 0 |
| hoshitori.model_gateway | 277 | 277 | 0 | 0 | 0 | 0 |
| hoshitori.model_settings | 204 | 202 | 0 | 0 | 0 | 2 |
| hoshitori.move_check | 105 | 45 | 2 | 58 | 0 | 0 |
| hoshitori.ondemand | 192 | 189 | 0 | 0 | 0 | 3 |
| hoshitori.oracle | 112 | 112 | 0 | 0 | 0 | 0 |
| hoshitori.outcome_review | 149 | 138 | 2 | 5 | 0 | 4 |
| hoshitori.paired_stats | 230 | 132 | 0 | 87 | 0 | 11 |
| hoshitori.post_snapshot | 292 | 284 | 0 | 0 | 0 | 8 |
| hoshitori.post_snapshot_job | 37 | 34 | 0 | 0 | 0 | 3 |
| hoshitori.practice | 295 | 288 | 3 | 0 | 0 | 4 |
| hoshitori.practice_job | 39 | 38 | 0 | 0 | 0 | 1 |
| hoshitori.practice_record | 208 | 208 | 0 | 0 | 0 | 0 |
| hoshitori.practice_view | 153 | 148 | 2 | 0 | 0 | 3 |
| hoshitori.protocol | 653 | 650 | 0 | 0 | 1 | 2 |
| hoshitori.quality | 58 | 13 | 0 | 45 | 0 | 0 |
| hoshitori.receipt_links | 26 | 25 | 1 | 0 | 0 | 0 |
| hoshitori.recovery | 175 | 174 | 0 | 0 | 0 | 1 |
| hoshitori.registry | 929 | 923 | 0 | 0 | 0 | 6 |
| hoshitori.registry_job | 73 | 73 | 0 | 0 | 0 | 0 |
| hoshitori.rejudge | 133 | 126 | 0 | 0 | 0 | 7 |
| hoshitori.replay | 274 | 274 | 0 | 0 | 0 | 0 |
| hoshitori.risk_kernel | 151 | 135 | 8 | 0 | 6 | 2 |
| hoshitori.rules_arm | 356 | 348 | 8 | 0 | 0 | 0 |
| hoshitori.rules_challenge | 65 | 43 | 0 | 22 | 0 | 0 |
| hoshitori.rules_sensitivity | 81 | 23 | 1 | 40 | 17 | 0 |
| hoshitori.ryo_collect | 194 | 192 | 0 | 0 | 0 | 2 |
| hoshitori.ryo_envelope | 297 | 99 | 0 | 198 | 0 | 0 |
| hoshitori.ryo_evidence | 150 | 147 | 0 | 0 | 0 | 3 |
| hoshitori.ryo_fields | 378 | 217 | 8 | 151 | 0 | 2 |
| hoshitori.ryo_gateway | 32 | 30 | 2 | 0 | 0 | 0 |
| hoshitori.ryo_plan | 231 | 231 | 0 | 0 | 0 | 0 |
| hoshitori.ryo_plan_job | 138 | 100 | 0 | 0 | 38 | 0 |
| hoshitori.ryo_read | 295 | 295 | 0 | 0 | 0 | 0 |
| hoshitori.safe_fetch | 313 | 305 | 7 | 0 | 0 | 1 |
| hoshitori.safe_fetch_net | 111 | 111 | 0 | 0 | 0 | 0 |
| hoshitori.scheduler | 142 | 141 | 0 | 0 | 1 | 0 |
| hoshitori.scoring | 330 | 322 | 3 | 0 | 4 | 1 |
| hoshitori.settle_job | 251 | 249 | 0 | 0 | 0 | 2 |
| hoshitori.settlement | 268 | 265 | 2 | 0 | 0 | 1 |
| hoshitori.settlement_record | 261 | 261 | 0 | 0 | 0 | 0 |
| hoshitori.site_export | 208 | 75 | 0 | 122 | 10 | 1 |
| hoshitori.snapshot | 466 | 462 | 2 | 0 | 0 | 2 |
| hoshitori.social_gateway | 179 | 152 | 0 | 22 | 0 | 5 |
| hoshitori.source_value | 262 | 257 | 0 | 0 | 0 | 5 |
| hoshitori.sweep_view | 149 | 147 | 0 | 0 | 0 | 2 |
| hoshitori.timeline | 160 | 160 | 0 | 0 | 0 | 0 |
| hoshitori.today_items | 121 | 120 | 0 | 0 | 0 | 1 |
| hoshitori.triggers | 105 | 104 | 0 | 0 | 0 | 1 |
| hoshitori.tsa_gateway | 120 | 120 | 0 | 0 | 0 | 0 |
| hoshitori.typed | 343 | 342 | 0 | 0 | 0 | 1 |
| hoshitori.unicode15 | 1415 | 1415 | 0 | 0 | 0 | 0 |
| hoshitori.venue_funding | 115 | 38 | 0 | 77 | 0 | 0 |
| hoshitori.venue_probe | 71 | 71 | 0 | 0 | 0 | 0 |
| hoshitori.venue_survey | 248 | 230 | 3 | 0 | 5 | 10 |
| hoshitori.view_models | 724 | 270 | 2 | 445 | 0 | 7 |
| hoshitori.voices | 491 | 452 | 0 | 29 | 0 | 10 |
| hoshitori.whales | 79 | 61 | 0 | 13 | 0 | 5 |
| **All** | 37851 | 32958 | 331 | 3850 | 312 | 400 |

32958 of 37851 places (87%) were noticed by a check; 331 could not be built, 3850 made the checks raise an error, 312 were noticed by the clock or the machine only, and 400 survived (each is listed as harmless, with its reason, in its module's test).

Changes noticed by the clock or the machine only:

* hoshitori.adjudication: Adjudicator._groups.find|test negated|while parent[x] != x:     parent[x] = parent[parent[x]]     x = parent[x]|0
* hoshitori.adjudication: Adjudicator._groups.find|NotEq->Eq|parent[x] != x|0
* hoshitori.adjudication: Adjudicator._groups.find|statement dropped|x = parent[x]|0
* hoshitori.attest: attest|test negated|if remaining <= 0:     break|0
* hoshitori.attest: attest|Mult->Add|rules.close_after_commit_s * 1000|0
* hoshitori.attest: attest|LtE->Lt|remaining <= 0|0
* hoshitori.attest: attest|statement dropped|break|0
* hoshitori.attest: attest|int+1|1000|0
* hoshitori.belief_revision: _ranks|test negated|while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:     end += 1|0
* hoshitori.belief_revision: _ranks|statement dropped|start = end + 1|0
* hoshitori.belief_revision: _ranks|statement dropped|end += 1|0
* hoshitori.belief_revision: _ranks|Add->Sub|end + 1|0
* hoshitori.challenge_store: _strip_tags|statement dropped|i = text.find('<', i)|0
* hoshitori.challenge_store: _strip_tags|test negated|if i < 0:     break|0
* hoshitori.challenge_store: _strip_tags|test negated|if end < 0:     i += 1     continue|0
* hoshitori.challenge_store: _strip_tags|statement dropped|last = i = end|0
* hoshitori.challenge_store: _strip_tags|statement dropped|break|0
* hoshitori.challenge_store: _strip_tags|test negated|if best != -1 and best - opener.end() <= MAX_BLOCK:     closer_len = 9 if lowered.startswith('</script>', best) else 8     end = best + closer_len|0
* hoshitori.challenge_store: _strip_tags|test negated|if gt != -1 and gt - i - 1 <= MAX_TAG:     end = gt + 1|0
* hoshitori.challenge_store: _strip_tags|statement dropped|i += 1|0
* hoshitori.challenge_store: _strip_tags|statement dropped|continue|0
* hoshitori.challenge_store: _strip_tags|test negated|if at != -1 and at < i or at == -2:     at = lowered.find(closer, i)     close_at[closer] = at|0
* hoshitori.challenge_store: _strip_tags|And->Or|best != -1 and best - opener.end() <= MAX_BLOCK|0
* hoshitori.challenge_store: _strip_tags|And->Or|gt != -1 and gt - i - 1 <= MAX_TAG|0
* hoshitori.challenge_store: _strip_tags|int+1|1|2
* hoshitori.challenge_store: _strip_tags|Or->And|at != -1 and at < i or at == -2|0
* hoshitori.challenge_store: _strip_tags|statement dropped|at = lowered.find(closer, i)|0
* hoshitori.challenge_store: _strip_tags|NotEq->Eq|best != -1|0
* hoshitori.challenge_store: _strip_tags|Add->Sub|best + closer_len|0
* hoshitori.challenge_store: _strip_tags|NotEq->Eq|gt != -1|1
* hoshitori.challenge_store: _strip_tags|Add->Sub|gt + 1|0
* hoshitori.challenge_store: _strip_tags|Add->Sub|i + 1|0
* hoshitori.challenge_store: _strip_tags|Add->Sub|i + 1|1
* hoshitori.challenge_store: _strip_tags|NotEq->Eq|at != -1|1
* hoshitori.challenge_store: _strip_tags|int+1|1|4
* hoshitori.challenge_store: _strip_tags|int+1|1|8
* hoshitori.claim_graph: _acyclic|statement dropped|left -= free|0
* hoshitori.claim_graph: order|statement dropped|waiting.remove(ready)|0
* hoshitori.claim_graph: _acyclic|statement dropped|raise GraphError('the dependencies form a loop')|0
* hoshitori.clusters: clusters.root|test negated|while parent[s] != s:     parent[s] = parent[parent[s]]     s = parent[s]|0
* hoshitori.clusters: clusters.root|NotEq->Eq|parent[s] != s|0
* hoshitori.clusters: clusters.root|statement dropped|s = parent[s]|0
* hoshitori.deployment: |int+1|8|0
* hoshitori.deployment: |int+1|5000|0
* hoshitori.deployment: |str+~|'bundle'|0
* hoshitori.deployment: |str+~|'trusted_root.json'|0
* hoshitori.deployment: |int+1|5|0
* hoshitori.deployment: |int+1|1000|0
* hoshitori.deployment: parse_settings|test negated|if models is not None:     _text(models, 'the model settings file')|0
* hoshitori.deployment: parse_settings|statement dropped|ondemand = None|0
* hoshitori.deployment: parse_settings|statement dropped|desk = False|0
* hoshitori.deployment: parse_settings|test negated|if 'desk' in top:     if not isinstance(top['desk'], bool):         raise DeploymentError('desk is true or false')     desk = top['desk']     if desk and (ondem|0
* hoshitori.deployment: new_key|test negated|if file.exists():     raise DeploymentError('a key file is never overwritten')|0
* hoshitori.deployment: new_key|statement dropped|seed = os.urandom(32)|0
* hoshitori.deployment: new_key|statement dropped|descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)|0
* hoshitori.deployment: new_key|return None|return signer.verifier.vkey()|0
* hoshitori.deployment: _arms_of|return None|return [*(arm for arm, _ in rules.arms), *(arm for arm, _, _ in rules.model_arms)]|0
* hoshitori.deployment: _named_files|statement dropped|_check(settings, rules, root, mappings, barrier_mappings, models)|0
* hoshitori.deployment: _practice_configs|statement dropped|terms = rules.practice_values()|0
* hoshitori.deployment: _practice_configs|statement dropped|own = [practice.config_for(arm, terms) for arm in _arms_of(rules)]|0
* hoshitori.deployment: _practice_configs|statement dropped|reference = rules.arms[0][0] if rules.arms else None|0
* hoshitori.deployment: _practice_configs|statement dropped|shadows = [dataclasses.replace(practice.config_for(f'{arm}.shadow', terms), follows=reference, passed_by=arm) for arm, _, _ in rules.model_arms if reference is |0
* hoshitori.deployment: _practice_configs|return None|return (*own, *shadows)|0
* hoshitori.deployment: assemble_reading|statement dropped|root, rules, mappings, barrier_mappings, models = _named_files(settings)|0
* hoshitori.deployment: assemble_reading|statement dropped|captures = CaptureStore(settings.directory / 'captures', read_only=True)|0
* hoshitori.deployment: _open_ondemand|statement dropped|od = settings.ondemand|0
* hoshitori.deployment: _open_ondemand|test negated|if od_root.origin == league_root.origin:     raise DeploymentError("the on-demand ledger has its own origin, not the league's")|0
* hoshitori.deployment: _open_ondemand|statement dropped|od_signer = Signer.from_seed(od_root.origin, read_key(od.log_key))|0
* hoshitori.deployment: _open_ondemand|test negated|if od_signer.verifier.public_key == league_signer.verifier.public_key:     raise DeploymentError("the on-demand ledger has its own signing key, not the league's|0
* hoshitori.deployment: _open_ondemand|statement dropped|ledger = Ledger.open(settings.directory / 'ondemand', signer=od_signer, origin=od_root.origin, clock=clock, segment_records=SEGMENT_RECORDS)|0
* hoshitori.deployment: _open_ondemand|test negated|if od_root.era_check(od_signer.verifier, ledger.size) != trusted_root.ERA_ACCEPT:     ledger.close()     raise DeploymentError("the on-demand log's signing key |0
* hoshitori.deployment: _open_ondemand|return None|return ledger|0
* hoshitori.deployment: _ondemand_pass|return None|return run|0
* hoshitori.deployment: _desk_pass|statement dropped|models = services.models|0
* hoshitori.deployment: _desk_pass|test negated|if models is None or models.l1 is None:     raise DeploymentError("the Desk asks the single-model arm's model, and these settings configure none")|0
* hoshitori.deployment: _desk_pass|statement dropped|l1 = models.l1|0
* hoshitori.deployment: _desk_pass|statement dropped|proposer = desk_model.make_proposer(models.client, l1.model, max_tokens=l1.max_tokens, thinking=l1.thinking, response_format=l1.response_format)|0
* hoshitori.deployment: _desk_pass|statement dropped|p = services.protocol|0
* hoshitori.deployment: _desk_pass|statement dropped|wiring = ondemand.Wiring(ondemand_ledger, services.attestor, services.timing_rules, p.quote_class, p.instrument_kind, p.oracle_pair, p.primary_venue, p.second_v|0
* hoshitori.deployment: _desk_pass|return None|return step|0
* hoshitori.deployment: assemble|statement dropped|clock = clock or SystemClock()|0
* hoshitori.deployment: tally|return None|return dict(sorted(found.items()))|0
* hoshitori.deployment: ledger_state|return None|return {'state': running.ledger.state, 'detail': running.ledger.state_detail}|0
* hoshitori.deployment: _intact|return None|return running.ledger.state == STATE_OK|0
* hoshitori.deployment: _require_intact|test negated|if not _intact(running):     raise DeploymentError(f'the log is in {running.ledger.state}: {running.ledger.state_detail}')|0
* hoshitori.deployment: status|test negated|if not _intact(running):     unknown = ('records', 'cycles', 'slots', 'forecasts', 'failures', 'settlements', 'heartbeat')     return {'ledger': ledger_state(ru|0
* hoshitori.deployment: status|statement dropped|records = scheduler.read_ledger(running.ledger)|0
* hoshitori.deployment: status|statement dropped|cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == cycle.TYPE]))|0
* hoshitori.deployment: status|statement dropped|slots = lifecycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == lifecycle.TYPE]))|0
* hoshitori.deployment: status|statement dropped|now = collect.now_ms(running.clock)|0
* hoshitori.deployment: status|statement dropped|schedule = running.rules.slots_through(now)|0
* hoshitori.deployment: status|statement dropped|beats = cycle.heartbeat(schedule, cycles.values(), now_ms=now, start_grace_ms=running.config.start_grace_ms, finish_within_ms=running.config.finish_within_ms)|0
* hoshitori.deployment: status|statement dropped|count = lifecycle.denominator(slots.values())|0
* hoshitori.deployment: status|statement dropped|report = {'ledger': ledger_state(running), 'records': len(records), 'cycles': tally((cycle.current_state(h).value for h in cycles.values())), 'slots': tally((li|0
* hoshitori.deployment: status|statement dropped|clock_events = read_clock_events(running.settings.directory / 'ledger')|0
* hoshitori.deployment: status|test negated|if clock_events:     report['clock'] = {'events': len(clock_events), 'standing': clock_events[-1].get('kind') != 'rebase', 'last': clock_events[-1]}|0
* hoshitori.deployment: status|test negated|if running.settings.ryo is not None:     report['ryo'] = {'calls_last_hour': running.captures.count_since(RYO_PROVIDER, running.clock.now() - timedelta(hours=1)|0
* hoshitori.deployment: status|return None|return report|0
* hoshitori.deployment: replay_cycle|statement dropped|_require_intact(running)|0
* hoshitori.deployment: replay_cycle|statement dropped|configs = running.model_configs|0
* hoshitori.deployment: replay_cycle|statement dropped|answers = None|0
* hoshitori.deployment: replay_cycle|test negated|if running.model_provider is not None:     answers = model_client.ReplayFetcher.from_store(running.captures, running.model_provider)|0
* hoshitori.deployment: replay_cycle|statement dropped|inputs = replay.Inputs(running.rules, running.mappings, running.barrier_mappings, answers, None if configs is None else configs[0], None if configs is None else|0
* hoshitori.deployment: replay_cycle|return None|return {'cycle': report.cycle_id, 'slots': len(report.slots), **{status.value.lower(): report.count(status) for status in replay.Status}, 'clean': report.clean,|0
* hoshitori.deployment: _committed|statement dropped|_require_intact(running)|0
* hoshitori.deployment: _committed|statement dropped|records = scheduler.read_ledger(running.ledger)|0
* hoshitori.deployment: _committed|statement dropped|cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == cycle.TYPE]))|0
* hoshitori.deployment: _committed|statement dropped|history = cycles.get(cycle_id)|0
* hoshitori.deployment: _committed|test negated|if history is None or cycle.current_state(history) is not cycle.State.COMMITTED:     raise DeploymentError('only a committed cycle can be exported')|0
* hoshitori.deployment: _committed|statement dropped|commitment_id = str(history[-1].step.commitment_id)|0
* hoshitori.deployment: _committed|test negated|if running.config.store is None:     raise DeploymentError('this league keeps no commitments')|0
* hoshitori.deployment: _committed|statement dropped|mine = [r for r in records if r.get('type') == forecast.TYPE and r['body']['cycle_id'] == cycle_id]|0
* hoshitori.deployment: _committed|statement dropped|wanted = {r['id'] for r in mine} | {ref['id'] for r in mine for ref in r['refs']}|0
* hoshitori.deployment: _committed|statement dropped|source = running.source_urn|0
* hoshitori.deployment: _committed|statement dropped|found = {seq for ident in wanted if (seq := running.ledger.find(source, ident)) is not None}|0
* hoshitori.deployment: _committed|test negated|if len(found) != len(wanted):     raise DeploymentError('a record the cycle cites is not in the ledger')|0
* hoshitori.deployment: _committed|return None|return (commitment_id, saved, sorted(found | _whole_batch(running, records, cycle_id, parse_text(split_note(saved.note)[0]).size)))|0
* hoshitori.deployment: _whole_batch|statement dropped|steps = [r for r in records if r.get('type') == cycle.TYPE and cast(dict[str, canonical.JsonValue], r['body']).get('cycle_id') == cycle_id and (cast(dict[str, c|0
* hoshitori.deployment: _whole_batch|test negated|if len(steps) != 1:     return set()|0
* hoshitori.deployment: _whole_batch|statement dropped|size = cast(dict[str, canonical.JsonValue], steps[0]['body']).get('batch_size')|0
* hoshitori.deployment: _whole_batch|statement dropped|at = running.ledger.find(running.source_urn, str(steps[0]['id']))|0
* hoshitori.deployment: _whole_batch|test negated|if isinstance(size, bool) or not isinstance(size, int) or at is None or (not 0 < size <= at < stamped_size):     return set()|0
* hoshitori.deployment: _whole_batch|return None|return set(range(at - size, at + 1))|0
* hoshitori.deployment: _stored_checkpoints|statement dropped|path = directory / 'checkpoints.jsonl'|0
* hoshitori.deployment: _stored_checkpoints|return None|return sorted(found.items())|0
* hoshitori.deployment: _later_part|statement dropped|larger = [(size, note) for size, note in _stored_checkpoints(running.settings.directory / 'ledger') if size > stamped_size]|0
* hoshitori.deployment: _later_part|test negated|if not larger:     return None|0
* hoshitori.deployment: _later_part|statement dropped|size, note = larger[-1]|0
* hoshitori.deployment: _later_part|statement dropped|records = scheduler.read_ledger(running.ledger)|0
* hoshitori.deployment: _later_part|statement dropped|mine = {r['id'] for r in records if r.get('type') == forecast.TYPE and r['body']['cycle_id'] == cycle_id}|0
* hoshitori.deployment: _later_part|statement dropped|steps = [r for r in records if r.get('type') in (lifecycle.TYPE, settlement_record.TYPE) and r['body']['forecast_id'] in mine]|0
* hoshitori.deployment: _later_part|statement dropped|positions = (running.ledger.find(running.source_urn, str(r['id'])) for r in steps)|0
* hoshitori.deployment: _later_part|statement dropped|seqs = sorted((seq for seq in positions if seq is not None and stamped_size <= seq < size))|0
* hoshitori.deployment: _later_part|return None|return bundle.Later(note, size, tuple(seqs)) if seqs else None|0
* hoshitori.deployment: _later_part_size|statement dropped|size = document.get('to_size')|0
* hoshitori.deployment: _later_part_size|return None|return size if isinstance(size, int) and (not isinstance(size, bool)) else 0|0
* hoshitori.deployment: _later_part_grew|return None|return later is not None and later.size > _later_part_size(directory)|0
* hoshitori.deployment: export_cycle|statement dropped|commitment_id, saved, seqs = _committed(running, cycle_id)|0
* hoshitori.deployment: export_cycle|statement dropped|note_text, _ = split_note(saved.note)|0
* hoshitori.deployment: export_cycle|statement dropped|stamped_size = parse_text(note_text).size|0
* hoshitori.deployment: export_cycle|statement dropped|later = _later_part(running, cycle_id, stamped_size)|0
* hoshitori.deployment: export_cycle|statement dropped|bundle.export_bundle(out, running.ledger, seqs, checkpoint_note=saved.note, size=stamped_size, tokens=saved.tokens, later=later)|0
* hoshitori.deployment: export_cycle|return None|return {'cycle': cycle_id, 'commitment': commitment_id, 'records': len(seqs), 'later_records': 0 if later is None else len(later.seqs), 'directory': str(out)}|0
* hoshitori.deployment: forge_cycle|statement dropped|_, saved, seqs = _committed(running, cycle_id)|0
* hoshitori.deployment: forge_cycle|statement dropped|origin = running.root.origin|0
* hoshitori.deployment: forge_cycle|statement dropped|signer = forgery.standin_signer(origin, cycle_id)|0
* hoshitori.deployment: forge_cycle|test negated|if out.exists() and any(out.iterdir()):     raise DeploymentError('the output directory must be new or empty')|0
* hoshitori.deployment: forge_cycle|statement dropped|(out / forgery.DOCUMENT).write_text(forgery.document(made), encoding='utf-8')|0
* hoshitori.deployment: forge_cycle|statement dropped|(out / FORGED_ROOT).write_bytes(demonstration)|0
* hoshitori.deployment: forge_cycle|return None|return {'cycle': cycle_id, 'edit': {'record': made.edit.record_id, 'field': made.edit.field, 'was': made.edit.was, 'now': made.edit.now}, 'standin_vkey': made.s|0
* hoshitori.deployment: _laboratory|test negated|if not log.is_file():     raise DeploymentError(f'the Laboratory log {log} does not exist')|0
* hoshitori.deployment: _desk_ledger_runs|test negated|if not settings.desk or settings.ondemand is None or (not (settings.directory / 'desk' / 'segments').is_dir()):     return None|0
* hoshitori.deployment: export_site|statement dropped|table = None|0
* hoshitori.deployment: export_site|test negated|if status is not None:     try:         table = quality.read_status(status.read_text(encoding='utf-8'))     except (OSError, UnicodeDecodeError, quality.Quality|0
* hoshitori.deployment: export_site|statement dropped|recorded = None|0
* hoshitori.deployment: export_site|test negated|if drills is not None:     try:         recorded = drill.read_drills(drills.read_bytes())     except (OSError, drill.DrillError) as exc:         raise Deploymen|0
* hoshitori.deployment: export_site|statement dropped|read = None|0
* hoshitori.deployment: export_site|test negated|if claims is not None:     try:         read = claims_file.read_claims(claims.read_bytes())     except (OSError, claims_file.ClaimsError) as exc:         raise |0
* hoshitori.deployment: export_site|test negated|if desk_runs is not None:     try:         desk = (desk or []) + desk_view.read_file(desk_runs.read_bytes())     except (OSError, desk_view.DeskViewError) as ex|0
* hoshitori.deployment: export_site|test negated|if sweeps is not None:     try:         swept = sweep_view.read(sweeps.read_text(encoding='utf-8'), _source_root(sweeps))     except (OSError, UnicodeDecodeErro|0
* hoshitori.deployment: export_site|test negated|if coverage is not None:     try:         covered = coverage_view.read((coverage / 'RUBRIC_MATRIX.md').read_text(encoding='utf-8'), (coverage / 'CONTRACT_MATRIX|0
* hoshitori.deployment: export_site|test negated|if signals is not None:     try:         collected_signals = json.loads(signals.read_bytes().decode('utf-8'))     except (OSError, UnicodeDecodeError, ValueErro|0
* hoshitori.deployment: export_site|test negated|if lab_log is not None:     laboratory = _laboratory(lab_log, lab_candles, lab_runs) elif lab_candles or lab_runs is not None:     raise DeploymentError('stored|0
* hoshitori.deployment: export_site|statement dropped|records = scheduler.read_ledger(running.ledger) if _intact(running) else []|0
* hoshitori.deployment: export_site|test negated|if model_challenges and _intact(running):     try:         challenged_model = model_challenge_file.for_site(tried, records)     except model_challenge_file.Chal|0
* hoshitori.deployment: export_site|statement dropped|now = collect.now_ms(running.clock)|0
* hoshitori.deployment: export_site|statement dropped|shown = view_models.build(records, running.rules, now_ms=now, scored=scored)|0
* hoshitori.deployment: export_site|statement dropped|cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == cycle.TYPE]))|0
* hoshitori.deployment: export_site|statement dropped|(out / 'bundles').mkdir(parents=True, exist_ok=True)|0
* hoshitori.deployment: export_site|test negated|if read is not None and _intact(running):     try:         checked_claims = claim_checks.checks_for(read, move_check.held_from(records))     except move_check.M|0
* hoshitori.deployment: export_site|test negated|if forged is not None:     forged_copy = _forged_copy(forged, out, bundled)|0
* hoshitori.deployment: export_site|statement dropped|state = ledger_state(running)|0
* hoshitori.deployment: export_site|statement dropped|written = site_export.write(out, site_export.documents(shown, listed, running.settings.trusted_root.read_bytes(), table, recorded, read, tuple(challenged), tupl|0
* hoshitori.deployment: export_site|return None|return {'directory': str(out), 'files': written.files, 'removed': written.removed, 'bundles': bundled, 'without_bundle': without, 'ledger': state}|0
* hoshitori.deployment: _source_root|statement dropped|root = page.resolve().parent.parent|0
* hoshitori.deployment: _source_root|return None|return root if (root / 'src' / 'hoshitori').is_dir() else None|0
* hoshitori.deployment: _forged_copy|test negated|if summary.cycle_id not in bundled:     raise DeploymentError('the forgery was made from a cycle whose bundle this site does not hold')|0
* hoshitori.deployment: _forged_copy|statement dropped|held = source / FORGED_BUNDLE|0
* hoshitori.deployment: _forged_copy|test negated|if not held.is_dir():     raise DeploymentError('the forgery directory holds no bundle')|0
* hoshitori.deployment: _forged_copy|test negated|if [era.key.vkey() for era in parsed.eras] != [summary.standin_vkey]:     raise DeploymentError("the forgery's root does not name its stand-in key")|0
* hoshitori.deployment: _forged_copy|statement dropped|target = out / site_export.FORGED / summary.cycle_id|0
* hoshitori.deployment: _forged_copy|test negated|if not target.exists():     partial = out / site_export.FORGED / f'.{summary.cycle_id}.part'     shutil.rmtree(partial, ignore_errors=True)     partial.parent.m|0
* hoshitori.deployment: _forged_copy|statement dropped|listed = _listing(summary.cycle_id, target)|0
* hoshitori.deployment: _forged_copy|return None|return site_export.ForgedCopy(summary, listed.files, listed.directories, root_bytes)|0
* hoshitori.deployment: _listing|statement dropped|files = sorted((p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()))|0
* hoshitori.deployment: _listing|statement dropped|folders = sorted((p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_dir()))|0
* hoshitori.deployment: _listing|return None|return site_export.Bundle(cycle_id, tuple(files), tuple(folders))|0
* hoshitori.deployment: make_protocol|test negated|if args.out.exists():     raise DeploymentError('a protocol file is never overwritten')|0
* hoshitori.deployment: make_protocol|statement dropped|args.out.write_bytes(protocol.dump(rules))|0
* hoshitori.deployment: make_protocol|return None|return {'file': str(args.out), 'sha256': rules.sha256, 'slots_from_ms': rules.schedule_start_ms, 'root_sha256': root.sha256}|0
* hoshitori.deployment: Settings|True<->False|False|0
* hoshitori.deployment: _object|statement dropped|raise DeploymentError(f'{what} is an object')|0
* hoshitori.deployment: _text|Or->And|not isinstance(value, str) or not value or len(value) > MAX_TEXT|0
* hoshitori.deployment: _text|statement dropped|raise DeploymentError(f'{what} is a non-empty string')|0
* hoshitori.deployment: _number|Or->And|isinstance(value, bool) or not isinstance(value, int | float) or value <= 0|0
* hoshitori.deployment: _number|statement dropped|raise DeploymentError(f'{what} is a positive number')|0
* hoshitori.deployment: _whole|Or->And|isinstance(value, bool) or not isinstance(value, int) or value < 0|0
* hoshitori.deployment: _whole|statement dropped|raise DeploymentError(f'{what} is a whole number')|0
* hoshitori.deployment: parse_settings|statement dropped|raise DeploymentError(f'the settings have the members {sorted(MEMBERS)}, not {sorted(top)}')|0
* hoshitori.deployment: parse_settings|statement dropped|raise DeploymentError('there is at least one mapping')|0
* hoshitori.deployment: parse_settings|statement dropped|raise DeploymentError('there is at least one venue')|0
* hoshitori.deployment: parse_settings|Or->And|not isinstance(listed, list) or not listed|0
* hoshitori.deployment: parse_settings|statement dropped|raise DeploymentError('there is at least one time-stamp authority')|0
* hoshitori.deployment: parse_settings|Or->And|not isinstance(wanted, list) or not wanted|0
* hoshitori.deployment: parse_settings|statement dropped|raise DeploymentError('there is at least one instrument list')|0
* hoshitori.deployment: parse_settings|IsNot->Is|models is not None|0
* hoshitori.deployment: parse_settings|statement dropped|_text(models, 'the model settings file')|0
* hoshitori.deployment: parse_settings|statement dropped|od = _object(top['ondemand'], 'ondemand')|0
* hoshitori.deployment: parse_settings|test negated|if set(od) != ONDEMAND_MEMBERS:     raise DeploymentError(f'ondemand has the members {sorted(ONDEMAND_MEMBERS)}, not {sorted(od)}')|0
* hoshitori.deployment: parse_settings|statement dropped|ondemand = OnDemandSettings(base / _text(od['log_key'], 'the on-demand log key'), base / _text(od['trusted_root'], 'the on-demand root'))|0
* hoshitori.deployment: parse_settings|True<->False|False|0
* hoshitori.deployment: parse_settings|In->NotIn|'desk' in top|0
* hoshitori.deployment: _parse_ryo|statement dropped|raise DeploymentError(f'the RYO settings have the members {sorted(RYO_MEMBERS)}, not {sorted(entry)}')|0
* hoshitori.deployment: _parse_ryo|Lt->LtE|calls < 1|0
* hoshitori.deployment: _parse_ryo|statement dropped|raise DeploymentError('the daily RYO hour is a UTC hour, 0 to 23')|0
* hoshitori.deployment: read_key|statement dropped|raise DeploymentError('the log key file can be read by others than its owner')|0
* hoshitori.deployment: Running.barrier_mappings|return None|return self.services.barrier_mappings|0
* hoshitori.desk_baselines: run|statement dropped|opened.append(step.check.check_id)|0
* hoshitori.desk_harness: _RecordedRunner.submit|statement dropped|future.set_result(CallResult(self.n, Outcome.OK, capture_id, 1, None, '', 0.0, now))|0
* hoshitori.desk_harness: _RecordedRunner.submit|statement dropped|future.set_result(CallResult(self.n, Outcome.FAILED, None, 1, error, 'NOT_RETRYABLE', 0.0, now))|0
* hoshitori.desk_live: history_port.history.read|statement dropped|cursor = stop|0
* hoshitori.fills: exit_order|statement dropped|open_ms += MINUTE_MS|0
* hoshitori.forgery: _empty|And->Or|directory.exists() and any(directory.iterdir())|0
* hoshitori.lab_forward: run|statement dropped|i += 1|0
* hoshitori.lab_forward: run|statement dropped|i += 1|1
* hoshitori.lab_forward: run|statement dropped|holding, free_from = (None, sale_open)|0
* hoshitori.lab_forward: run|statement dropped|i += 1|2
* hoshitori.league_cli: _run.report|test negated|if once:     stop.set()|0
* hoshitori.league_cli: _run.report|statement dropped|stop.set()|0
* hoshitori.lineage: EvidenceStore.latest|statement dropped|current = following|0
* hoshitori.protocol: Protocol.slots_between|statement dropped|k += 1|0
* hoshitori.risk_kernel: shrink|test negated|if again == weights:     break|0
* hoshitori.risk_kernel: shrink|statement dropped|weights = again|0
* hoshitori.risk_kernel: shrink|Eq->NotEq|again == weights|0
* hoshitori.risk_kernel: shrink|statement dropped|break|0
* hoshitori.risk_kernel: _pass|test negated|-granted if short else granted|0
* hoshitori.risk_kernel: _pass|min->max|min(want, *rooms.values())|0
* hoshitori.rules_sensitivity: _neighbour|test negated|while rules_arm.state_of(rules_arm.normalised(r, atr, hours)) != target:     r += step|0
* hoshitori.rules_sensitivity: sensitivity|Gt->GtE|at > 0|0
* hoshitori.rules_sensitivity: _neighbour|Mult->Add|atr * Decimal(hours).sqrt()|0
* hoshitori.rules_sensitivity: _neighbour|test negated|GRID if direction == UP else -GRID|0
* hoshitori.rules_sensitivity: _neighbour|NotEq->Eq|rules_arm.state_of(rules_arm.normalised(r, atr, hours)) != target|0
* hoshitori.rules_sensitivity: _neighbour|statement dropped|r += step|0
* hoshitori.rules_sensitivity: sensitivity|Add->Sub|at + 1|0
* hoshitori.rules_sensitivity: _neighbour|Eq->NotEq|direction == UP|0
* hoshitori.rules_sensitivity: sensitivity|int+1|1|4
* hoshitori.rules_sensitivity: _neighbour|Mult->Add|edge * scale|0
* hoshitori.rules_sensitivity: sensitivity|Add->Sub|at + 1|1
* hoshitori.rules_sensitivity: sensitivity|Sub->Add|at - 1|1
* hoshitori.rules_sensitivity: sensitivity|Sub->Add|at - 1|2
* hoshitori.rules_sensitivity: sensitivity|int+1|1|6
* hoshitori.rules_sensitivity: sensitivity|int+1|1|7
* hoshitori.rules_sensitivity: sensitivity|int+1|1|8
* hoshitori.rules_sensitivity: sensitivity|int+1|1|9
* hoshitori.ryo_plan_job: |str+~|'ryo-plan'|0
* hoshitori.ryo_plan_job: |str+~|"no oracle pair is known for the token at the end of its window, though the window's own call was settled"|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|out.sort(key=lambda rec: order[str(cast(dict[str, Any], rec['body'])['read_id'])])|0
* hoshitori.ryo_plan_job: _as_window|return None|return settlement.Settlement(ryo_plan.record_id(p.read_id), p.read_id, 'B', p.window.pair_id, settlement.RULE_VERSION, p.ended.state, None, p.ended.reason or p.|0
* hoshitori.ryo_plan_job: _ready|statement dropped|made = call|0
* hoshitori.ryo_plan_job: Passed|True<->False|True|0
* hoshitori.ryo_plan_job: _Plan|True<->False|True|0
* hoshitori.ryo_plan_job: _ready|test negated|if common.contract == 'D' and common.horizon_ms == ryo_plan.HORIZON_MS:     calls.setdefault((common.cycle_id, common.asset_id), made)|0
* hoshitori.ryo_plan_job: _ready|statement dropped|continue|1
* hoshitori.ryo_plan_job: _ready|statement dropped|continue|3
* hoshitori.ryo_plan_job: _as_window|str+~|'B'|0
* hoshitori.ryo_plan_job: _as_window|Or->And|p.ended.reason or p.ended.state.value|0
* hoshitori.ryo_plan_job: _ready|And->Or|common.contract == 'D' and common.horizon_ms == ryo_plan.HORIZON_MS|0
* hoshitori.ryo_plan_job: plan_pass|Or->And|p.ended.state not in (State.SETTLED, State.VOID) or (p.ended.state is State.VOID and p.ended.reason != 'VOID_TIE')|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|out.append(_record(sv, p, _as_window(p), now_ms))|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|continue|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|out.append(_record(sv, p, None, now_ms))|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|continue|1
* hoshitori.ryo_plan_job: plan_pass|statement dropped|failed.append((p.read_id, NO_PAIR))|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|failed.append((p.read_id, str(exc)[:200]))|0
* hoshitori.ryo_plan_job: plan_pass|GtE->Gt|now_ms - (p.window.origin_ms + ryo_plan.HORIZON_MS) >= RETRY_WINDOW_MS|0
* hoshitori.ryo_plan_job: _clean|str+~|'committed_at'|0
* hoshitori.ryo_plan_job: _ready|str+~|'read_id'|0
* hoshitori.ryo_plan_job: _ready|Eq->NotEq|common.horizon_ms == ryo_plan.HORIZON_MS|0
* hoshitori.ryo_plan_job: plan_pass|Sub->Add|now_ms - (p.window.origin_ms + ryo_plan.HORIZON_MS)|0
* hoshitori.ryo_plan_job: plan_pass|test negated|if pair == DEFERRED:     pending.append(p.read_id) else:     out.append(_record(sv, p, settlement.void(p.read_id, 'B', p.window.pair_id, pair), now_ms))|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|continue|3
* hoshitori.ryo_plan_job: plan_pass|statement dropped|pending.append(p.read_id)|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|failed.append((p.read_id, str(exc)[:200]))|1
* hoshitori.ryo_plan_job: _ready|str+~|'body'|0
* hoshitori.ryo_plan_job: plan_pass|NotEq->Eq|p.ended.reason != 'VOID_TIE'|0
* hoshitori.ryo_plan_job: plan_pass|Add->Sub|p.window.origin_ms + k * MINUTE_MS|0
* hoshitori.ryo_plan_job: plan_pass|Add->Sub|p.window.origin_ms + ryo_plan.HORIZON_MS|1
* hoshitori.ryo_plan_job: plan_pass|Eq->NotEq|pair == DEFERRED|0
* hoshitori.ryo_plan_job: plan_pass|statement dropped|pending.append(p.read_id)|1
* hoshitori.ryo_plan_job: plan_pass|str+~|'VOID_TIE'|0
* hoshitori.ryo_plan_job: plan_pass|int+1|200|0
* hoshitori.ryo_plan_job: plan_pass|int+1|200|1
* hoshitori.scheduler: run_forever|statement dropped|report(tick(sv, config))|0
* hoshitori.scoring: _ratio_extreme|test negated|if better >= trial if smallest else better <= trial:     return trial|0
* hoshitori.scoring: _ratio_extreme|statement dropped|trial = better|0
* hoshitori.scoring: _ratio_extreme|GtE->Gt|better >= trial|0
* hoshitori.scoring: _ratio_extreme|LtE->Lt|better <= trial|0
* hoshitori.site_export: write|statement dropped|out.mkdir(parents=True, exist_ok=True)|0
* hoshitori.site_export: write|statement dropped|(out / RECEIPTS).mkdir(exist_ok=True)|0
* hoshitori.site_export: write|statement dropped|temporary.write_bytes(data)|0
* hoshitori.site_export: write|statement dropped|os.replace(temporary, target)|0
* hoshitori.site_export: documents|str+~|'today.json'|0
* hoshitori.site_export: write|True<->False|True|0
* hoshitori.site_export: write|True<->False|True|1
* hoshitori.site_export: write|True<->False|True|2
* hoshitori.site_export: _owned|And->Or|p.is_file() and p.suffix == '.json'|0
* hoshitori.site_export: _owned|Eq->NotEq|p.suffix == '.json'|0
* hoshitori.venue_survey: run|statement dropped|_check(venues, watchlist, minutes, wait_s, base_urls)|0
* hoshitori.venue_survey: _check|Or->And|not watchlist or len(set(watchlist)) != len(watchlist) or any((not s or s != s.upper() for s in watchlist))|0
* hoshitori.venue_survey: _check|statement dropped|raise SurveyError('the watchlist names at least one symbol, each once, in capitals')|0
* hoshitori.venue_survey: run|statement dropped|runner.stop()|0
* hoshitori.venue_survey: main|statement dropped|urls[venue] = url|0

The data the table is made from:

```json
[
 {
  "checks": "tests.address_gateway_checks",
  "checks_sha256": "fbad808399f0d54586f7adf253366d3b926e576421e9e697fcecc44efb8d0020",
  "counts": {
   "CHECK_RAISED": 2,
   "FAILED": 95,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.address_gateway",
  "module_sha256": "f9f2b522d481c346bac858dce9ecad4a1316affbfdd0a9055797b36ff2d249d0",
  "total": 98,
  "unresolved": []
 },
 {
  "checks": "tests.address_screen_checks",
  "checks_sha256": "61e54400536a1b6cb411ee0754e0f81343b1d5a27bb4a342529ca38643eb4938",
  "counts": {
   "FAILED": 280,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.address_screen",
  "module_sha256": "59232161429f5e99c4f1cc7d644a166676768fbb79d0182201788050694e40a3",
  "total": 281,
  "unresolved": []
 },
 {
  "checks": "tests.adjudication_checks",
  "checks_sha256": "2202508160fae682e3461ce03ab5f5d7ef102a76e1892889cb666cba25ab8b54",
  "counts": {
   "CHECK_RAISED": 63,
   "FAILED": 230,
   "SURVIVED": 9,
   "TIMED_OUT": 3
  },
  "function": "failures",
  "module": "hoshitori.adjudication",
  "module_sha256": "a9e74b91916da8c9df3a38e10f823eb8217015b617759780b4880f35bdb38715",
  "total": 305,
  "unresolved": [
   "Adjudicator._groups.find|test negated|while parent[x] != x:     parent[x] = parent[parent[x]]     x = parent[x]|0",
   "Adjudicator._groups.find|NotEq->Eq|parent[x] != x|0",
   "Adjudicator._groups.find|statement dropped|x = parent[x]|0"
  ]
 },
 {
  "checks": "tests.adjudication_store_checks",
  "checks_sha256": "e5932b1dcce85211db4b4b2f7b7f81c345d56299be9a6a9f368aae7d7da8ff3a",
  "counts": {
   "CHECK_RAISED": 20,
   "FAILED": 118,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.adjudication_store",
  "module_sha256": "f538db30746b54361811e69f4be44635a1d859aee591f33a30695744e7339537",
  "total": 139,
  "unresolved": []
 },
 {
  "checks": "tests.anytime_checks",
  "checks_sha256": "295a2ade8c6b6e24e88b502b6bb10e1a99e3f0899173dc585eb11544b4eeb155",
  "counts": {
   "FAILED": 200,
   "SURVIVED": 21
  },
  "function": "failures",
  "module": "hoshitori.anytime",
  "module_sha256": "0fe7e4028591189e2a8a13989d1cb250c0c3338fd85a30cd6c0cc5e54cfe6d57",
  "total": 221,
  "unresolved": []
 },
 {
  "checks": "tests.arena_checks",
  "checks_sha256": "695586d853127e7c6dc826780da58838190191e08ce4a165ad33d38213aac464",
  "counts": {
   "CHECK_RAISED": 64,
   "FAILED": 161,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.arena",
  "module_sha256": "c5d806a99e4a4731eedbf0f7d3021f736fe5be74dbb5650b557538370b924d3e",
  "total": 226,
  "unresolved": []
 },
 {
  "checks": "tests.arm_input_checks",
  "checks_sha256": "efad7ea8ff840ffcdc11639529d488ff4e587f851773b22b9267e74ff186c0f6",
  "counts": {
   "FAILED": 67
  },
  "function": "failures",
  "module": "hoshitori.arm_input",
  "module_sha256": "bb63002001d5f3388b885570ceb7bed59d306a90e919c8aadb92a0eec55e6b49",
  "total": 67,
  "unresolved": []
 },
 {
  "checks": "tests.arm_l1_checks",
  "checks_sha256": "b299060822ab0ea092687a493b8591d70a79b93ceffa7dd36951735e8db95821",
  "counts": {
   "BUILD_FAILED": 20,
   "FAILED": 151
  },
  "function": "failures",
  "module": "hoshitori.arm_l1",
  "module_sha256": "df52eef1730833e8064f97844d2f0aa46cb701fbf751f4c4da882b2da5982c9d",
  "total": 171,
  "unresolved": []
 },
 {
  "checks": "tests.arm_l1x_checks",
  "checks_sha256": "329adca821ef0c1d9603f002f58ed38b064b9efd5d2e6cd47fd42cad9d6a4d92",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 113,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.arm_l1x",
  "module_sha256": "b97e633632e268df9354e9548fccd49d402f62b599e39197086e7c8114166c3b",
  "total": 117,
  "unresolved": []
 },
 {
  "checks": "tests.arm_l2_checks",
  "checks_sha256": "b00baef31bca93b55dd5fe9235a300e0846161fa9830236771eb791515907bcc",
  "counts": {
   "BUILD_FAILED": 11,
   "FAILED": 122
  },
  "function": "failures",
  "module": "hoshitori.arm_l2",
  "module_sha256": "e5ccdf5c20c04207374be0c798226cbb338f0bc5445cb02c0ea8262207b3d932",
  "total": 133,
  "unresolved": []
 },
 {
  "checks": "tests.arm_run_record_checks",
  "checks_sha256": "c0b6f5c3b97dc51a16c4fca5698a4fd0471600e0b2e5c6590d3c01e87af6d4be",
  "counts": {
   "FAILED": 272
  },
  "function": "failures",
  "module": "hoshitori.arm_run_record",
  "module_sha256": "7ed1fcfc3fbffab30ea592d49c58edd03ca9a085f2f2efbee1077f5b72846761",
  "total": 272,
  "unresolved": []
 },
 {
  "checks": "tests.assertions_checks",
  "checks_sha256": "a93eee5fab41044842db925cdbad918ef76b29973d463a830f5ddaafa24a3c29",
  "counts": {
   "BUILD_FAILED": 48,
   "FAILED": 195
  },
  "function": "failures",
  "module": "hoshitori.assertions",
  "module_sha256": "a6e3378a44cc320d279bf60a0757e33ddd52bc9e2dee6f8b1f326156e0f2a44b",
  "total": 243,
  "unresolved": []
 },
 {
  "checks": "tests.attest_checks",
  "checks_sha256": "0167b0150481c3a4719b77ce21d0cde403587f673d4cd1bbdbf38931c84624b2",
  "counts": {
   "FAILED": 76,
   "TIMED_OUT": 5
  },
  "function": "failures",
  "module": "hoshitori.attest",
  "module_sha256": "36b1b9094a2ceaec4d0f27741ca82861961d08c77ff86e9f841577d57d7e22fb",
  "total": 81,
  "unresolved": [
   "attest|test negated|if remaining <= 0:     break|0",
   "attest|Mult->Add|rules.close_after_commit_s * 1000|0",
   "attest|LtE->Lt|remaining <= 0|0",
   "attest|statement dropped|break|0",
   "attest|int+1|1000|0"
  ]
 },
 {
  "checks": "tests.backup_checks",
  "checks_sha256": "a42fd691703fef49293dd59ddfcd3c67ea7eb5c8f5911434bf86ac73078d19ab",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 935,
   "SURVIVED": 13
  },
  "function": "failures",
  "module": "hoshitori.backup",
  "module_sha256": "218eaea4bd947d8f732d2410e168f9dfd534179734c88c08c752af0628113f30",
  "total": 950,
  "unresolved": []
 },
 {
  "checks": "tests.bar_coverage_checks",
  "checks_sha256": "2054d7ee48996efdff917b90c9a0ed15593a11b00a3fcab529bf62ca30712b84",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 15,
   "FAILED": 17
  },
  "function": "failures",
  "module": "hoshitori.bar_coverage",
  "module_sha256": "8d2e14cc2a0b5b1d69ac22ec410061d4a16e2d685bf03cd9575ec212c724e79b",
  "total": 33,
  "unresolved": []
 },
 {
  "checks": "tests.barrier_arm_checks",
  "checks_sha256": "3b0af41c203416452416530ba0c851e44ab380a804fa15d3085d1fff934c5b15",
  "counts": {
   "FAILED": 52
  },
  "function": "failures",
  "module": "hoshitori.barrier_arm",
  "module_sha256": "33dd672f95533443036f3610e9fa7d6f1b4a48db94f9d064c646ce69163161d6",
  "total": 52,
  "unresolved": []
 },
 {
  "checks": "tests.barrier_rates_checks",
  "checks_sha256": "5f4ffe80625397f244a9f0f5eb9d85bd2b23d49fa7f28c2c692c305fe145f0d0",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 247
  },
  "function": "failures",
  "module": "hoshitori.barrier_rates",
  "module_sha256": "b122a71ab34e363de6ca76e0acc4ad1e6fe597a36d8a3369e9826bb988dc06b0",
  "total": 249,
  "unresolved": []
 },
 {
  "checks": "tests.belief_revision_checks",
  "checks_sha256": "6a15f01f1bbf92ae3fb7d5b8b2aaffe723361e99b2cb2d6536e67fbac6768b0b",
  "counts": {
   "BUILD_FAILED": 18,
   "FAILED": 354,
   "SURVIVED": 12,
   "TIMED_OUT": 4
  },
  "function": "failures",
  "module": "hoshitori.belief_revision",
  "module_sha256": "48a3cef90fb85a18be466be213495ba81d241793841b57042bf59fe818f0edcd",
  "total": 388,
  "unresolved": [
   "_ranks|test negated|while end + 1 < len(order) and values[order[end + 1]] == values[order[start]]:     end += 1|0",
   "_ranks|statement dropped|start = end + 1|0",
   "_ranks|statement dropped|end += 1|0",
   "_ranks|Add->Sub|end + 1|0"
  ]
 },
 {
  "checks": "tests.book_checks",
  "checks_sha256": "ac49e360c6ed8649f9240b61477a843908b12d5ebcf8a58b9083959cb2c1d477",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 257,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.book",
  "module_sha256": "5242bdd7dd58554d128799d0883cc8d097dc1fa13164109c2aca5bda79a67981",
  "total": 259,
  "unresolved": []
 },
 {
  "checks": "tests.call_revisions_checks",
  "checks_sha256": "5dc7855fd24b3457dc01363a055ed65dfe73b924ba1311eb7de7fc1ca2a4f922",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 104,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.call_revisions",
  "module_sha256": "58d0a3303dcb49fec58e019e41f49ea9af0afee597168cca18975853a6a437c9",
  "total": 108,
  "unresolved": []
 },
 {
  "checks": "tests.card_lint_checks",
  "checks_sha256": "67c4c60e23d67f80adc611294a43ab807625057a1e5794f0aa4ab15ddba2f63c",
  "counts": {
   "CHECK_RAISED": 14,
   "FAILED": 60,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.card_lint",
  "module_sha256": "f62de48d743ab96a1ec8c5f069cdc5974e5708a82baf77a1b0345bf7276a3fc4",
  "total": 77,
  "unresolved": []
 },
 {
  "checks": "tests.challenge_store_checks",
  "checks_sha256": "975d773699637e104f2cf27ec662fbf1672110b895a271049df0067acb5f48ac",
  "counts": {
   "CHECK_RAISED": 50,
   "FAILED": 195,
   "SURVIVED": 26,
   "TIMED_OUT": 24
  },
  "function": "failures",
  "module": "hoshitori.challenge_store",
  "module_sha256": "26fc780a213ac8efd2f6bb42cbea5c2329507d7f927b1d9b3b2c2667436b7781",
  "total": 295,
  "unresolved": [
   "_strip_tags|statement dropped|i = text.find('<', i)|0",
   "_strip_tags|test negated|if i < 0:     break|0",
   "_strip_tags|test negated|if end < 0:     i += 1     continue|0",
   "_strip_tags|statement dropped|last = i = end|0",
   "_strip_tags|statement dropped|break|0",
   "_strip_tags|test negated|if best != -1 and best - opener.end() <= MAX_BLOCK:     closer_len = 9 if lowered.startswith('</script>', best) else 8     end = best + closer_len|0",
   "_strip_tags|test negated|if gt != -1 and gt - i - 1 <= MAX_TAG:     end = gt + 1|0",
   "_strip_tags|statement dropped|i += 1|0",
   "_strip_tags|statement dropped|continue|0",
   "_strip_tags|test negated|if at != -1 and at < i or at == -2:     at = lowered.find(closer, i)     close_at[closer] = at|0",
   "_strip_tags|And->Or|best != -1 and best - opener.end() <= MAX_BLOCK|0",
   "_strip_tags|And->Or|gt != -1 and gt - i - 1 <= MAX_TAG|0",
   "_strip_tags|int+1|1|2",
   "_strip_tags|Or->And|at != -1 and at < i or at == -2|0",
   "_strip_tags|statement dropped|at = lowered.find(closer, i)|0",
   "_strip_tags|NotEq->Eq|best != -1|0",
   "_strip_tags|Add->Sub|best + closer_len|0",
   "_strip_tags|NotEq->Eq|gt != -1|1",
   "_strip_tags|Add->Sub|gt + 1|0",
   "_strip_tags|Add->Sub|i + 1|0",
   "_strip_tags|Add->Sub|i + 1|1",
   "_strip_tags|NotEq->Eq|at != -1|1",
   "_strip_tags|int+1|1|4",
   "_strip_tags|int+1|1|8"
  ]
 },
 {
  "checks": "tests.challenge_view_checks",
  "checks_sha256": "66b28e2c4ffbcea1bb453ad8619e6a791396e5ae38c2b5fb5e9b45523156f035",
  "counts": {
   "CHECK_RAISED": 78,
   "FAILED": 35
  },
  "function": "failures",
  "module": "hoshitori.challenge_view",
  "module_sha256": "229b16f626336e3263158bc6944163cc60835ee91716080f7c47d75ba6c0923b",
  "total": 113,
  "unresolved": []
 },
 {
  "checks": "tests.changes_checks",
  "checks_sha256": "80c1708a1e269f1091741a172a51672a88c3d31379bdfc103687ed6ab8b3f002",
  "counts": {
   "BUILD_FAILED": 8,
   "FAILED": 199,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.changes",
  "module_sha256": "9811af2072e1a45309c0cbb44432d90bf794352afc04e8a2048f04d28a9961e9",
  "total": 209,
  "unresolved": []
 },
 {
  "checks": "tests.claim_assumptions_checks",
  "checks_sha256": "d383b2228876833ce9c4407d167325ac8d0dce467d7a45ece91316cb5bbaf8bd",
  "counts": {
   "CHECK_RAISED": 34,
   "FAILED": 28
  },
  "function": "failures",
  "module": "hoshitori.claim_assumptions",
  "module_sha256": "a589fe8b5bf5860c140fa2b9ca0e423d9a370d06906ca33a884abeff7a5899ff",
  "total": 62,
  "unresolved": []
 },
 {
  "checks": "tests.claim_benchmark_checks",
  "checks_sha256": "db81ce84f66cfec7246d0c5408569178df721366f9ce21d8bb69d9398724f79f",
  "counts": {
   "CHECK_RAISED": 14,
   "FAILED": 233,
   "SURVIVED": 6
  },
  "function": "failures",
  "module": "hoshitori.claim_benchmark",
  "module_sha256": "2b07b8a4cb5f8ed9583029f6b322959bd7881d9b4e3e1c62afd57f79398916ec",
  "total": 253,
  "unresolved": []
 },
 {
  "checks": "tests.claim_checks_checks",
  "checks_sha256": "a6e00aedb61bb8726805576d12c20993e45c31b9d758d8075e548a9512048b40",
  "counts": {
   "CHECK_RAISED": 21,
   "FAILED": 2
  },
  "function": "failures",
  "module": "hoshitori.claim_checks",
  "module_sha256": "53cf6b04e9c5c23e62e7f19df2ec5f9763fab1517d77a2af4c11eebbd7304d2f",
  "total": 23,
  "unresolved": []
 },
 {
  "checks": "tests.claim_extract_checks",
  "checks_sha256": "c708ff41bb8060a6a2d07022363dd6e13cdb0652d242fa237fc3e9f646134c03",
  "counts": {
   "BUILD_FAILED": 15,
   "CHECK_RAISED": 56,
   "FAILED": 37
  },
  "function": "failures",
  "module": "hoshitori.claim_extract",
  "module_sha256": "a8f1ec53c9a00179d39e1a7161c76e12718e8ea3ff413bd736aa25bc77cc93c0",
  "total": 108,
  "unresolved": []
 },
 {
  "checks": "tests.claim_graph_checks",
  "checks_sha256": "dd363ad40628264d4401a52c430447bd43a7e017838f8b6dfc83f8cfcdaf69a3",
  "counts": {
   "BUILD_FAILED": 12,
   "CHECK_RAISED": 138,
   "FAILED": 193,
   "TIMED_OUT": 3
  },
  "function": "failures",
  "module": "hoshitori.claim_graph",
  "module_sha256": "7f924e215e384088ade869cd4cb15ff8202fa68e810aaa5df393d8ec38df1d30",
  "total": 346,
  "unresolved": [
   "_acyclic|statement dropped|left -= free|0",
   "order|statement dropped|waiting.remove(ready)|0",
   "_acyclic|statement dropped|raise GraphError('the dependencies form a loop')|0"
  ]
 },
 {
  "checks": "tests.claim_move_checks",
  "checks_sha256": "6f33969da237903e097a2a2d38713fb022718391b5a284a5bc7888b9258a369b",
  "counts": {
   "CHECK_RAISED": 60,
   "FAILED": 45
  },
  "function": "failures",
  "module": "hoshitori.claim_move",
  "module_sha256": "a25f788cc78ee1e008ba84605d7cd9db7e313092003ade0348759211891de5a1",
  "total": 105,
  "unresolved": []
 },
 {
  "checks": "tests.claim_next_check_checks",
  "checks_sha256": "02e804c12fedf8c17fe62567751e4ae25dfe731bdade6f20642f1987c0f041dc",
  "counts": {
   "CHECK_RAISED": 52,
   "FAILED": 80,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.claim_next_check",
  "module_sha256": "ed0b3d07dcd3f1b8faeb217e35489c91046edbc32a9a520a1373879e91268ab1",
  "total": 133,
  "unresolved": []
 },
 {
  "checks": "tests.claim_preliminary_checks",
  "checks_sha256": "858ae3495634614c67cd05aff911c118fd50b9c565466a04802e814e39e6bf32",
  "counts": {
   "CHECK_RAISED": 17,
   "FAILED": 49
  },
  "function": "failures",
  "module": "hoshitori.claim_preliminary",
  "module_sha256": "49b2a9c2ec08880ac0915a891860c2c0f47cbd60d23011d169e159c109e1d601",
  "total": 66,
  "unresolved": []
 },
 {
  "checks": "tests.claim_segmenter_checks",
  "checks_sha256": "5b63ec35d1a70877c7f9f8366556e19ec162f210b0f3e2a77d2f6b30578b6a26",
  "counts": {
   "CHECK_RAISED": 39,
   "FAILED": 131
  },
  "function": "failures",
  "module": "hoshitori.claim_segmenter",
  "module_sha256": "a11fa418293ce462faa9072f6aebb6bdd68c54ea477df0caed2c0de4587c750f",
  "total": 170,
  "unresolved": []
 },
 {
  "checks": "tests.claim_sketch_checks",
  "checks_sha256": "36555b2ee2139166c78a77543351f314fa9a1cac1b1537f5609140f1001293f3",
  "counts": {
   "CHECK_RAISED": 67,
   "FAILED": 126
  },
  "function": "failures",
  "module": "hoshitori.claim_sketch",
  "module_sha256": "81268977ffe5915af496f7411806cc59e8dcb74e8e92e46e692709a44f298e2c",
  "total": 193,
  "unresolved": []
 },
 {
  "checks": "tests.claim_stability_checks",
  "checks_sha256": "a585866313b2f7988a39382038c7bf0fb1beeaf94b3221e60f332ff0a1a0f087",
  "counts": {
   "CHECK_RAISED": 22,
   "FAILED": 28,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.claim_stability",
  "module_sha256": "d273efd92dfa815958c90f90c0c2a3a18e84de4501b4ec4aaa5bbca79a9d7d6b",
  "total": 51,
  "unresolved": []
 },
 {
  "checks": "tests.claims_file_checks",
  "checks_sha256": "29ef0ca158478ba9abf71357d2a97a9929e0eb1c56ada9a4fef5e4803f714d7f",
  "counts": {
   "CHECK_RAISED": 480,
   "FAILED": 110,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.claims_file",
  "module_sha256": "a8131973e40ddb7292b0d7cdcc662ae38d8972dc6aeba98ce61ec7e9fb088d35",
  "total": 591,
  "unresolved": []
 },
 {
  "checks": "tests.clusters_checks",
  "checks_sha256": "5cc8e09f38848e6d31405e2e88ed123ccd3755bcbd7aaf071c4035bf6070a52d",
  "counts": {
   "FAILED": 65,
   "SURVIVED": 11,
   "TIMED_OUT": 3
  },
  "function": "failures",
  "module": "hoshitori.clusters",
  "module_sha256": "382efb68a43b21c7b585204e7d977fcc981f901582c2c6612922cc5d77d3995c",
  "total": 79,
  "unresolved": [
   "clusters.root|test negated|while parent[s] != s:     parent[s] = parent[parent[s]]     s = parent[s]|0",
   "clusters.root|NotEq->Eq|parent[s] != s|0",
   "clusters.root|statement dropped|s = parent[s]|0"
  ]
 },
 {
  "checks": "tests.collect_checks",
  "checks_sha256": "1913f77f75c707855f0a98d6f6ed57593414bfd042a3c1790f9cda77787f7fbf",
  "counts": {
   "FAILED": 252
  },
  "function": "failures",
  "module": "hoshitori.collect",
  "module_sha256": "e087ba0f3dac5d9485130dc437f8e53992c619f86cbb1ece97baf73450ba9e71",
  "total": 252,
  "unresolved": []
 },
 {
  "checks": "tests.command_log_checks",
  "checks_sha256": "74854e9716f7cdfffbd2660c7a5ac72c1393ffee8a338807dda4736984db2b10",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 9,
   "FAILED": 62,
   "SURVIVED": 7
  },
  "function": "failures",
  "module": "hoshitori.command_log",
  "module_sha256": "b9a7256febb119df58b5d75e58bd71d841c370a47c96a2dd8994cc2cfc814c20",
  "total": 79,
  "unresolved": []
 },
 {
  "checks": "tests.commit_checks",
  "checks_sha256": "ceba2c29f92c62231a095868429894ab1127fab0020e7c5cafee9e1fc8a231a5",
  "counts": {
   "FAILED": 61,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.commit",
  "module_sha256": "9bf3132305165f1eb81d0ea175fa8de3254215e37c668511545cdb5610e6bd13",
  "total": 63,
  "unresolved": []
 },
 {
  "checks": "tests.commitment_store_checks",
  "checks_sha256": "ab1731c9000d4464d5f8607c06b2493d3c091e25faf982d483288b7c8b6c54ec",
  "counts": {
   "FAILED": 55
  },
  "function": "failures",
  "module": "hoshitori.commitment_store",
  "module_sha256": "dcb2e9f668b893d1ce890af421aba004825b9251af125d786d09d220fc25ce1d",
  "total": 55,
  "unresolved": []
 },
 {
  "checks": "tests.comparison_checks",
  "checks_sha256": "5be092afeb19f36d59695110a6ed564879ca7552ac2a07721648df792b92ca26",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 64,
   "SURVIVED": 4
  },
  "function": "failures",
  "module": "hoshitori.comparison",
  "module_sha256": "f8c755e50ca12f6774827eb5cdc7d972b8975de3c52c301dd79f241efd1cc7c3",
  "total": 69,
  "unresolved": []
 },
 {
  "checks": "tests.coverage_view_checks",
  "checks_sha256": "136cadd8b5fe7ad68ac221373b77b506c1b9a95dc7881645bbdc2024517f38f8",
  "counts": {
   "FAILED": 86,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.coverage_view",
  "module_sha256": "0f41d58db836539bc254179c66f48dfea2643d1b87a5e0d980199067bd9ae25f",
  "total": 87,
  "unresolved": []
 },
 {
  "checks": "tests.crosscheck_checks",
  "checks_sha256": "75861167d6da5a7dc5d9df0622b9d8105d80395351cfcb5e518f22e64a478181",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 70,
   "FAILED": 28,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.crosscheck",
  "module_sha256": "b4662b5a806be3069cf43dc5c2ba70937cd97ce82c31ff1e88789fcc8751b27d",
  "total": 101,
  "unresolved": []
 },
 {
  "checks": "tests.cycle_checks",
  "checks_sha256": "eac4bc4f5245985a9d2a62ecb9ecc668d8b89df2df8241b46ca30b198d12135f",
  "counts": {
   "BUILD_FAILED": 9,
   "FAILED": 404,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.cycle",
  "module_sha256": "0494ee394f57d5807fc8cc4445df235c43d5798a4bcdc1659ece86b32e147198",
  "total": 416,
  "unresolved": []
 },
 {
  "checks": "tests.decision_alerts_checks",
  "checks_sha256": "0406ea9154d2fea7487f4210b7a4e220903ab92d875638abd4d026c7654da4b0",
  "counts": {
   "FAILED": 159
  },
  "function": "failures",
  "module": "hoshitori.decision_alerts",
  "module_sha256": "e2a09b5fa5354fe67226d6cf5d1177444c83cc544a45ecbf5d753e7866eeea99",
  "total": 159,
  "unresolved": []
 },
 {
  "checks": "tests.deployment_checks",
  "checks_sha256": "3f36701c760489149686fab4cc06a23655135dd553c0c517a2eea1b069124ab4",
  "counts": {
   "FAILED": 922,
   "TIMED_OUT": 175
  },
  "function": "failures",
  "module": "hoshitori.deployment",
  "module_sha256": "8e279bb9e14b28afeff9837f37d1b23dc5249f3be44d86f88240e7ed51c5b03d",
  "total": 1097,
  "unresolved": [
   "|int+1|8|0",
   "|int+1|5000|0",
   "|str+~|'bundle'|0",
   "|str+~|'trusted_root.json'|0",
   "|int+1|5|0",
   "|int+1|1000|0",
   "parse_settings|test negated|if models is not None:     _text(models, 'the model settings file')|0",
   "parse_settings|statement dropped|ondemand = None|0",
   "parse_settings|statement dropped|desk = False|0",
   "parse_settings|test negated|if 'desk' in top:     if not isinstance(top['desk'], bool):         raise DeploymentError('desk is true or false')     desk = top['desk']     if desk and (ondem|0",
   "new_key|test negated|if file.exists():     raise DeploymentError('a key file is never overwritten')|0",
   "new_key|statement dropped|seed = os.urandom(32)|0",
   "new_key|statement dropped|descriptor = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 384)|0",
   "new_key|return None|return signer.verifier.vkey()|0",
   "_arms_of|return None|return [*(arm for arm, _ in rules.arms), *(arm for arm, _, _ in rules.model_arms)]|0",
   "_named_files|statement dropped|_check(settings, rules, root, mappings, barrier_mappings, models)|0",
   "_practice_configs|statement dropped|terms = rules.practice_values()|0",
   "_practice_configs|statement dropped|own = [practice.config_for(arm, terms) for arm in _arms_of(rules)]|0",
   "_practice_configs|statement dropped|reference = rules.arms[0][0] if rules.arms else None|0",
   "_practice_configs|statement dropped|shadows = [dataclasses.replace(practice.config_for(f'{arm}.shadow', terms), follows=reference, passed_by=arm) for arm, _, _ in rules.model_arms if reference is |0",
   "_practice_configs|return None|return (*own, *shadows)|0",
   "assemble_reading|statement dropped|root, rules, mappings, barrier_mappings, models = _named_files(settings)|0",
   "assemble_reading|statement dropped|captures = CaptureStore(settings.directory / 'captures', read_only=True)|0",
   "_open_ondemand|statement dropped|od = settings.ondemand|0",
   "_open_ondemand|test negated|if od_root.origin == league_root.origin:     raise DeploymentError(\"the on-demand ledger has its own origin, not the league's\")|0",
   "_open_ondemand|statement dropped|od_signer = Signer.from_seed(od_root.origin, read_key(od.log_key))|0",
   "_open_ondemand|test negated|if od_signer.verifier.public_key == league_signer.verifier.public_key:     raise DeploymentError(\"the on-demand ledger has its own signing key, not the league's|0",
   "_open_ondemand|statement dropped|ledger = Ledger.open(settings.directory / 'ondemand', signer=od_signer, origin=od_root.origin, clock=clock, segment_records=SEGMENT_RECORDS)|0",
   "_open_ondemand|test negated|if od_root.era_check(od_signer.verifier, ledger.size) != trusted_root.ERA_ACCEPT:     ledger.close()     raise DeploymentError(\"the on-demand log's signing key |0",
   "_open_ondemand|return None|return ledger|0",
   "_ondemand_pass|return None|return run|0",
   "_desk_pass|statement dropped|models = services.models|0",
   "_desk_pass|test negated|if models is None or models.l1 is None:     raise DeploymentError(\"the Desk asks the single-model arm's model, and these settings configure none\")|0",
   "_desk_pass|statement dropped|l1 = models.l1|0",
   "_desk_pass|statement dropped|proposer = desk_model.make_proposer(models.client, l1.model, max_tokens=l1.max_tokens, thinking=l1.thinking, response_format=l1.response_format)|0",
   "_desk_pass|statement dropped|p = services.protocol|0",
   "_desk_pass|statement dropped|wiring = ondemand.Wiring(ondemand_ledger, services.attestor, services.timing_rules, p.quote_class, p.instrument_kind, p.oracle_pair, p.primary_venue, p.second_v|0",
   "_desk_pass|return None|return step|0",
   "assemble|statement dropped|clock = clock or SystemClock()|0",
   "tally|return None|return dict(sorted(found.items()))|0",
   "ledger_state|return None|return {'state': running.ledger.state, 'detail': running.ledger.state_detail}|0",
   "_intact|return None|return running.ledger.state == STATE_OK|0",
   "_require_intact|test negated|if not _intact(running):     raise DeploymentError(f'the log is in {running.ledger.state}: {running.ledger.state_detail}')|0",
   "status|test negated|if not _intact(running):     unknown = ('records', 'cycles', 'slots', 'forecasts', 'failures', 'settlements', 'heartbeat')     return {'ledger': ledger_state(ru|0",
   "status|statement dropped|records = scheduler.read_ledger(running.ledger)|0",
   "status|statement dropped|cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == cycle.TYPE]))|0",
   "status|statement dropped|slots = lifecycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == lifecycle.TYPE]))|0",
   "status|statement dropped|now = collect.now_ms(running.clock)|0",
   "status|statement dropped|schedule = running.rules.slots_through(now)|0",
   "status|statement dropped|beats = cycle.heartbeat(schedule, cycles.values(), now_ms=now, start_grace_ms=running.config.start_grace_ms, finish_within_ms=running.config.finish_within_ms)|0",
   "status|statement dropped|count = lifecycle.denominator(slots.values())|0",
   "status|statement dropped|report = {'ledger': ledger_state(running), 'records': len(records), 'cycles': tally((cycle.current_state(h).value for h in cycles.values())), 'slots': tally((li|0",
   "status|statement dropped|clock_events = read_clock_events(running.settings.directory / 'ledger')|0",
   "status|test negated|if clock_events:     report['clock'] = {'events': len(clock_events), 'standing': clock_events[-1].get('kind') != 'rebase', 'last': clock_events[-1]}|0",
   "status|test negated|if running.settings.ryo is not None:     report['ryo'] = {'calls_last_hour': running.captures.count_since(RYO_PROVIDER, running.clock.now() - timedelta(hours=1)|0",
   "status|return None|return report|0",
   "replay_cycle|statement dropped|_require_intact(running)|0",
   "replay_cycle|statement dropped|configs = running.model_configs|0",
   "replay_cycle|statement dropped|answers = None|0",
   "replay_cycle|test negated|if running.model_provider is not None:     answers = model_client.ReplayFetcher.from_store(running.captures, running.model_provider)|0",
   "replay_cycle|statement dropped|inputs = replay.Inputs(running.rules, running.mappings, running.barrier_mappings, answers, None if configs is None else configs[0], None if configs is None else|0",
   "replay_cycle|return None|return {'cycle': report.cycle_id, 'slots': len(report.slots), **{status.value.lower(): report.count(status) for status in replay.Status}, 'clean': report.clean,|0",
   "_committed|statement dropped|_require_intact(running)|0",
   "_committed|statement dropped|records = scheduler.read_ledger(running.ledger)|0",
   "_committed|statement dropped|cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == cycle.TYPE]))|0",
   "_committed|statement dropped|history = cycles.get(cycle_id)|0",
   "_committed|test negated|if history is None or cycle.current_state(history) is not cycle.State.COMMITTED:     raise DeploymentError('only a committed cycle can be exported')|0",
   "_committed|statement dropped|commitment_id = str(history[-1].step.commitment_id)|0",
   "_committed|test negated|if running.config.store is None:     raise DeploymentError('this league keeps no commitments')|0",
   "_committed|statement dropped|mine = [r for r in records if r.get('type') == forecast.TYPE and r['body']['cycle_id'] == cycle_id]|0",
   "_committed|statement dropped|wanted = {r['id'] for r in mine} | {ref['id'] for r in mine for ref in r['refs']}|0",
   "_committed|statement dropped|source = running.source_urn|0",
   "_committed|statement dropped|found = {seq for ident in wanted if (seq := running.ledger.find(source, ident)) is not None}|0",
   "_committed|test negated|if len(found) != len(wanted):     raise DeploymentError('a record the cycle cites is not in the ledger')|0",
   "_committed|return None|return (commitment_id, saved, sorted(found | _whole_batch(running, records, cycle_id, parse_text(split_note(saved.note)[0]).size)))|0",
   "_whole_batch|statement dropped|steps = [r for r in records if r.get('type') == cycle.TYPE and cast(dict[str, canonical.JsonValue], r['body']).get('cycle_id') == cycle_id and (cast(dict[str, c|0",
   "_whole_batch|test negated|if len(steps) != 1:     return set()|0",
   "_whole_batch|statement dropped|size = cast(dict[str, canonical.JsonValue], steps[0]['body']).get('batch_size')|0",
   "_whole_batch|statement dropped|at = running.ledger.find(running.source_urn, str(steps[0]['id']))|0",
   "_whole_batch|test negated|if isinstance(size, bool) or not isinstance(size, int) or at is None or (not 0 < size <= at < stamped_size):     return set()|0",
   "_whole_batch|return None|return set(range(at - size, at + 1))|0",
   "_stored_checkpoints|statement dropped|path = directory / 'checkpoints.jsonl'|0",
   "_stored_checkpoints|return None|return sorted(found.items())|0",
   "_later_part|statement dropped|larger = [(size, note) for size, note in _stored_checkpoints(running.settings.directory / 'ledger') if size > stamped_size]|0",
   "_later_part|test negated|if not larger:     return None|0",
   "_later_part|statement dropped|size, note = larger[-1]|0",
   "_later_part|statement dropped|records = scheduler.read_ledger(running.ledger)|0",
   "_later_part|statement dropped|mine = {r['id'] for r in records if r.get('type') == forecast.TYPE and r['body']['cycle_id'] == cycle_id}|0",
   "_later_part|statement dropped|steps = [r for r in records if r.get('type') in (lifecycle.TYPE, settlement_record.TYPE) and r['body']['forecast_id'] in mine]|0",
   "_later_part|statement dropped|positions = (running.ledger.find(running.source_urn, str(r['id'])) for r in steps)|0",
   "_later_part|statement dropped|seqs = sorted((seq for seq in positions if seq is not None and stamped_size <= seq < size))|0",
   "_later_part|return None|return bundle.Later(note, size, tuple(seqs)) if seqs else None|0",
   "_later_part_size|statement dropped|size = document.get('to_size')|0",
   "_later_part_size|return None|return size if isinstance(size, int) and (not isinstance(size, bool)) else 0|0",
   "_later_part_grew|return None|return later is not None and later.size > _later_part_size(directory)|0",
   "export_cycle|statement dropped|commitment_id, saved, seqs = _committed(running, cycle_id)|0",
   "export_cycle|statement dropped|note_text, _ = split_note(saved.note)|0",
   "export_cycle|statement dropped|stamped_size = parse_text(note_text).size|0",
   "export_cycle|statement dropped|later = _later_part(running, cycle_id, stamped_size)|0",
   "export_cycle|statement dropped|bundle.export_bundle(out, running.ledger, seqs, checkpoint_note=saved.note, size=stamped_size, tokens=saved.tokens, later=later)|0",
   "export_cycle|return None|return {'cycle': cycle_id, 'commitment': commitment_id, 'records': len(seqs), 'later_records': 0 if later is None else len(later.seqs), 'directory': str(out)}|0",
   "forge_cycle|statement dropped|_, saved, seqs = _committed(running, cycle_id)|0",
   "forge_cycle|statement dropped|origin = running.root.origin|0",
   "forge_cycle|statement dropped|signer = forgery.standin_signer(origin, cycle_id)|0",
   "forge_cycle|test negated|if out.exists() and any(out.iterdir()):     raise DeploymentError('the output directory must be new or empty')|0",
   "forge_cycle|statement dropped|(out / forgery.DOCUMENT).write_text(forgery.document(made), encoding='utf-8')|0",
   "forge_cycle|statement dropped|(out / FORGED_ROOT).write_bytes(demonstration)|0",
   "forge_cycle|return None|return {'cycle': cycle_id, 'edit': {'record': made.edit.record_id, 'field': made.edit.field, 'was': made.edit.was, 'now': made.edit.now}, 'standin_vkey': made.s|0",
   "_laboratory|test negated|if not log.is_file():     raise DeploymentError(f'the Laboratory log {log} does not exist')|0",
   "_desk_ledger_runs|test negated|if not settings.desk or settings.ondemand is None or (not (settings.directory / 'desk' / 'segments').is_dir()):     return None|0",
   "export_site|statement dropped|table = None|0",
   "export_site|test negated|if status is not None:     try:         table = quality.read_status(status.read_text(encoding='utf-8'))     except (OSError, UnicodeDecodeError, quality.Quality|0",
   "export_site|statement dropped|recorded = None|0",
   "export_site|test negated|if drills is not None:     try:         recorded = drill.read_drills(drills.read_bytes())     except (OSError, drill.DrillError) as exc:         raise Deploymen|0",
   "export_site|statement dropped|read = None|0",
   "export_site|test negated|if claims is not None:     try:         read = claims_file.read_claims(claims.read_bytes())     except (OSError, claims_file.ClaimsError) as exc:         raise |0",
   "export_site|test negated|if desk_runs is not None:     try:         desk = (desk or []) + desk_view.read_file(desk_runs.read_bytes())     except (OSError, desk_view.DeskViewError) as ex|0",
   "export_site|test negated|if sweeps is not None:     try:         swept = sweep_view.read(sweeps.read_text(encoding='utf-8'), _source_root(sweeps))     except (OSError, UnicodeDecodeErro|0",
   "export_site|test negated|if coverage is not None:     try:         covered = coverage_view.read((coverage / 'RUBRIC_MATRIX.md').read_text(encoding='utf-8'), (coverage / 'CONTRACT_MATRIX|0",
   "export_site|test negated|if signals is not None:     try:         collected_signals = json.loads(signals.read_bytes().decode('utf-8'))     except (OSError, UnicodeDecodeError, ValueErro|0",
   "export_site|test negated|if lab_log is not None:     laboratory = _laboratory(lab_log, lab_candles, lab_runs) elif lab_candles or lab_runs is not None:     raise DeploymentError('stored|0",
   "export_site|statement dropped|records = scheduler.read_ledger(running.ledger) if _intact(running) else []|0",
   "export_site|test negated|if model_challenges and _intact(running):     try:         challenged_model = model_challenge_file.for_site(tried, records)     except model_challenge_file.Chal|0",
   "export_site|statement dropped|now = collect.now_ms(running.clock)|0",
   "export_site|statement dropped|shown = view_models.build(records, running.rules, now_ms=now, scored=scored)|0",
   "export_site|statement dropped|cycles = cycle.rebuild(cast(list[dict[str, canonical.JsonValue]], [r for r in records if r.get('type') == cycle.TYPE]))|0",
   "export_site|statement dropped|(out / 'bundles').mkdir(parents=True, exist_ok=True)|0",
   "export_site|test negated|if read is not None and _intact(running):     try:         checked_claims = claim_checks.checks_for(read, move_check.held_from(records))     except move_check.M|0",
   "export_site|test negated|if forged is not None:     forged_copy = _forged_copy(forged, out, bundled)|0",
   "export_site|statement dropped|state = ledger_state(running)|0",
   "export_site|statement dropped|written = site_export.write(out, site_export.documents(shown, listed, running.settings.trusted_root.read_bytes(), table, recorded, read, tuple(challenged), tupl|0",
   "export_site|return None|return {'directory': str(out), 'files': written.files, 'removed': written.removed, 'bundles': bundled, 'without_bundle': without, 'ledger': state}|0",
   "_source_root|statement dropped|root = page.resolve().parent.parent|0",
   "_source_root|return None|return root if (root / 'src' / 'hoshitori').is_dir() else None|0",
   "_forged_copy|test negated|if summary.cycle_id not in bundled:     raise DeploymentError('the forgery was made from a cycle whose bundle this site does not hold')|0",
   "_forged_copy|statement dropped|held = source / FORGED_BUNDLE|0",
   "_forged_copy|test negated|if not held.is_dir():     raise DeploymentError('the forgery directory holds no bundle')|0",
   "_forged_copy|test negated|if [era.key.vkey() for era in parsed.eras] != [summary.standin_vkey]:     raise DeploymentError(\"the forgery's root does not name its stand-in key\")|0",
   "_forged_copy|statement dropped|target = out / site_export.FORGED / summary.cycle_id|0",
   "_forged_copy|test negated|if not target.exists():     partial = out / site_export.FORGED / f'.{summary.cycle_id}.part'     shutil.rmtree(partial, ignore_errors=True)     partial.parent.m|0",
   "_forged_copy|statement dropped|listed = _listing(summary.cycle_id, target)|0",
   "_forged_copy|return None|return site_export.ForgedCopy(summary, listed.files, listed.directories, root_bytes)|0",
   "_listing|statement dropped|files = sorted((p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()))|0",
   "_listing|statement dropped|folders = sorted((p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_dir()))|0",
   "_listing|return None|return site_export.Bundle(cycle_id, tuple(files), tuple(folders))|0",
   "make_protocol|test negated|if args.out.exists():     raise DeploymentError('a protocol file is never overwritten')|0",
   "make_protocol|statement dropped|args.out.write_bytes(protocol.dump(rules))|0",
   "make_protocol|return None|return {'file': str(args.out), 'sha256': rules.sha256, 'slots_from_ms': rules.schedule_start_ms, 'root_sha256': root.sha256}|0",
   "Settings|True<->False|False|0",
   "_object|statement dropped|raise DeploymentError(f'{what} is an object')|0",
   "_text|Or->And|not isinstance(value, str) or not value or len(value) > MAX_TEXT|0",
   "_text|statement dropped|raise DeploymentError(f'{what} is a non-empty string')|0",
   "_number|Or->And|isinstance(value, bool) or not isinstance(value, int | float) or value <= 0|0",
   "_number|statement dropped|raise DeploymentError(f'{what} is a positive number')|0",
   "_whole|Or->And|isinstance(value, bool) or not isinstance(value, int) or value < 0|0",
   "_whole|statement dropped|raise DeploymentError(f'{what} is a whole number')|0",
   "parse_settings|statement dropped|raise DeploymentError(f'the settings have the members {sorted(MEMBERS)}, not {sorted(top)}')|0",
   "parse_settings|statement dropped|raise DeploymentError('there is at least one mapping')|0",
   "parse_settings|statement dropped|raise DeploymentError('there is at least one venue')|0",
   "parse_settings|Or->And|not isinstance(listed, list) or not listed|0",
   "parse_settings|statement dropped|raise DeploymentError('there is at least one time-stamp authority')|0",
   "parse_settings|Or->And|not isinstance(wanted, list) or not wanted|0",
   "parse_settings|statement dropped|raise DeploymentError('there is at least one instrument list')|0",
   "parse_settings|IsNot->Is|models is not None|0",
   "parse_settings|statement dropped|_text(models, 'the model settings file')|0",
   "parse_settings|statement dropped|od = _object(top['ondemand'], 'ondemand')|0",
   "parse_settings|test negated|if set(od) != ONDEMAND_MEMBERS:     raise DeploymentError(f'ondemand has the members {sorted(ONDEMAND_MEMBERS)}, not {sorted(od)}')|0",
   "parse_settings|statement dropped|ondemand = OnDemandSettings(base / _text(od['log_key'], 'the on-demand log key'), base / _text(od['trusted_root'], 'the on-demand root'))|0",
   "parse_settings|True<->False|False|0",
   "parse_settings|In->NotIn|'desk' in top|0",
   "_parse_ryo|statement dropped|raise DeploymentError(f'the RYO settings have the members {sorted(RYO_MEMBERS)}, not {sorted(entry)}')|0",
   "_parse_ryo|Lt->LtE|calls < 1|0",
   "_parse_ryo|statement dropped|raise DeploymentError('the daily RYO hour is a UTC hour, 0 to 23')|0",
   "read_key|statement dropped|raise DeploymentError('the log key file can be read by others than its owner')|0",
   "Running.barrier_mappings|return None|return self.services.barrier_mappings|0"
  ]
 },
 {
  "checks": "tests.desk_checks",
  "checks_sha256": "a22451461a0d44a404698a5ebeac004c875a55d1ea656bfb362e4db06f0e1848",
  "counts": {
   "FAILED": 608,
   "SURVIVED": 5
  },
  "function": "failures",
  "module": "hoshitori.desk",
  "module_sha256": "d465b39a32932715914936d1710ce181d319be5702b0bd75f78a4f13ea6a6afc",
  "total": 613,
  "unresolved": []
 },
 {
  "checks": "tests.desk_acquire_checks",
  "checks_sha256": "690077f0d15dd582f2c2b45aee01d35b80caf483ee7dbbf4ce5d30fe094ddd0c",
  "counts": {
   "FAILED": 239,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_acquire",
  "module_sha256": "85565f4b755a9508f1ebcd227abdd0d260a78e6c87e4c4110aac137b1679b428",
  "total": 240,
  "unresolved": []
 },
 {
  "checks": "tests.desk_baselines_checks",
  "checks_sha256": "011af641eecd375a8a263c5d150930993b3821c71da573ebbacd757f0b5a5efd",
  "counts": {
   "CHECK_RAISED": 9,
   "FAILED": 62,
   "SURVIVED": 5,
   "TIMED_OUT": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_baselines",
  "module_sha256": "437e37b58619a287540190545c1c25e75addfa3614d9d2a442356403731ba86f",
  "total": 77,
  "unresolved": [
   "run|statement dropped|opened.append(step.check.check_id)|0"
  ]
 },
 {
  "checks": "tests.desk_gates_checks",
  "checks_sha256": "190c570ca4308ffaca9f003290bc614e5b34a0935990f8ba027ce79327cb622b",
  "counts": {
   "BUILD_FAILED": 5,
   "FAILED": 233
  },
  "function": "failures",
  "module": "hoshitori.desk_gates",
  "module_sha256": "a1cb46317f35a02e2797e06201cd393712fb77029cebf0f49078006c0ec650f5",
  "total": 238,
  "unresolved": []
 },
 {
  "checks": "tests.desk_harness_checks",
  "checks_sha256": "5369fc265f4697c5a55094e555735269ef4a6fa7001d28c3a687fb8c5fc32966",
  "counts": {
   "FAILED": 115,
   "SURVIVED": 3,
   "TIMED_OUT": 2
  },
  "function": "failures",
  "module": "hoshitori.desk_harness",
  "module_sha256": "a5919114320225637196199f3a8b57b2c960bd154dd1950891cb797decb5923f",
  "total": 120,
  "unresolved": [
   "_RecordedRunner.submit|statement dropped|future.set_result(CallResult(self.n, Outcome.OK, capture_id, 1, None, '', 0.0, now))|0",
   "_RecordedRunner.submit|statement dropped|future.set_result(CallResult(self.n, Outcome.FAILED, None, 1, error, 'NOT_RETRYABLE', 0.0, now))|0"
  ]
 },
 {
  "checks": "tests.desk_history_checks",
  "checks_sha256": "bce2094687a38dc94b4b9b1c5016423acb7bd462fb25213e127fc977f01cb77e",
  "counts": {
   "FAILED": 52,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_history",
  "module_sha256": "6e49fea7989d85ef540739c16159872aae2b9341d3354891f9ef3d989e011d8e",
  "total": 53,
  "unresolved": []
 },
 {
  "checks": "tests.desk_hook_checks",
  "checks_sha256": "ce48a406c6337c0b93d272d7bdf8e51aaa263de3e957561122b5d5cedc024cf8",
  "counts": {
   "FAILED": 277,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.desk_hook",
  "module_sha256": "9ce31d51c13ae4bfbd5f35d18e8c7ec2a08f273197cd0028c5886e333059fdca",
  "total": 280,
  "unresolved": []
 },
 {
  "checks": "tests.desk_interpret_checks",
  "checks_sha256": "b9e2e105f54c9323db0496b6afbe3a4203929c007fa373e6da1a7d94be526894",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 124
  },
  "function": "failures",
  "module": "hoshitori.desk_interpret",
  "module_sha256": "2c470f7eac3e2f69a64d7c994f0eda49a8d01506788b4a07188a770c3a6bbc34",
  "total": 125,
  "unresolved": []
 },
 {
  "checks": "tests.desk_live_checks",
  "checks_sha256": "f4c2d6446eecf326b27ddf571acc31eea24b6efd90b68c959212dd297db692c7",
  "counts": {
   "CHECK_RAISED": 8,
   "FAILED": 177,
   "SURVIVED": 3,
   "TIMED_OUT": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_live",
  "module_sha256": "5974e4474ae6b8eabc0f1ccbecacbb65a4ea70db4ee07a2c6a9365abd66657aa",
  "total": 189,
  "unresolved": [
   "history_port.history.read|statement dropped|cursor = stop|0"
  ]
 },
 {
  "checks": "tests.desk_menu_checks",
  "checks_sha256": "8cc120743c833f6f8bcaf3c40368fb2bb5968df67d38a953a034b5a8d430f658",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 234,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.desk_menu",
  "module_sha256": "3a1c0cfc3cab86c64458c40e4fedd4e5d334402d0976bd77a7477fda09bd6e19",
  "total": 238,
  "unresolved": []
 },
 {
  "checks": "tests.desk_model_checks",
  "checks_sha256": "9ae8c8274601d45643d0ec6a74801f864a231436caf8db0093e407b1dbb2ffd5",
  "counts": {
   "BUILD_FAILED": 6,
   "FAILED": 64,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_model",
  "module_sha256": "e01a18f1c47943b361c1f233b7dcd2ca691d1c33ee375f2d3656ff7cb4129b17",
  "total": 71,
  "unresolved": []
 },
 {
  "checks": "tests.desk_plan_checks",
  "checks_sha256": "ef56a1cfe894d1823783350f580463d2334d4580f097c22218c17dd7e339b2e2",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 149
  },
  "function": "failures",
  "module": "hoshitori.desk_plan",
  "module_sha256": "92b1667d28dd59ba97b99afe43a5d9cd8914297a7e1ac65d039a6636108ee2c1",
  "total": 151,
  "unresolved": []
 },
 {
  "checks": "tests.desk_record_checks",
  "checks_sha256": "4409b75f5e9b5cdc71569d25bbb34dca521028db57509648338dc20f16665ee9",
  "counts": {
   "BUILD_FAILED": 5,
   "FAILED": 722,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_record",
  "module_sha256": "453e673a6bea1b9892b3e56747ad02b50afdd9c55dc657acd85f59e946691801",
  "total": 728,
  "unresolved": []
 },
 {
  "checks": "tests.desk_recovery_checks",
  "checks_sha256": "038f7a0d7d088e80314dfcf2034dd9676ea0fe77df7f623160e7b73b9a419247",
  "counts": {
   "FAILED": 120,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.desk_recovery",
  "module_sha256": "80b20ae58b48897c7fb12603da2247c4d27edf1a7ad2d940dc84cc22337e5dd6",
  "total": 121,
  "unresolved": []
 },
 {
  "checks": "tests.desk_view_checks",
  "checks_sha256": "dead4b7c1cd3ce0e3b3536193019c78851fc68606ba370a90d8fa8902307566e",
  "counts": {
   "FAILED": 225,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.desk_view",
  "module_sha256": "b8ff4ccbf999adc2468ee84eee5058bf1177d22a12e1d6b0621cb1961b51bb35",
  "total": 228,
  "unresolved": []
 },
 {
  "checks": "tests.drill_checks",
  "checks_sha256": "ef7130aa1a9ab37e527dbc0654a1218b0ca939be3a78d1d91da5a29ab608f532",
  "counts": {
   "CHECK_RAISED": 82,
   "FAILED": 25,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.drill",
  "module_sha256": "c2b3779d3d6f5dec61c138526a4d42cbe5026b2afa024a7c654979846f0bf919",
  "total": 108,
  "unresolved": []
 },
 {
  "checks": "tests.evidence_checks",
  "checks_sha256": "b5425dcbf4314af2aa65c83c79e055dae10604c1ce689451e6a1566a08cc41a9",
  "counts": {
   "BUILD_FAILED": 8,
   "FAILED": 558,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.evidence",
  "module_sha256": "afa73ab363875918b89e92296848689d6c31a4d500627e701b322bab7c3c9dd7",
  "total": 568,
  "unresolved": []
 },
 {
  "checks": "tests.evidence_sources_checks",
  "checks_sha256": "9166f6b143d548f6ec6d44879754c886e2e91909ed8cfa8a9b42c7e1d7806f2c",
  "counts": {
   "FAILED": 182
  },
  "function": "failures",
  "module": "hoshitori.evidence_sources",
  "module_sha256": "b33074a340b3e56b4f096ad9988fa15b316434eb30ec4f68ea0f53b8198ae9c8",
  "total": 182,
  "unresolved": []
 },
 {
  "checks": "tests.exploratory_root_checks",
  "checks_sha256": "f5113be3231dae92d60002ebbc9421ebbef053f1290e0922ff95cfe157ff5c10",
  "counts": {
   "FAILED": 104,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.exploratory_root",
  "module_sha256": "041c45720416034df09d8b630d44926d0200d22018def81462332244f39c284d",
  "total": 105,
  "unresolved": []
 },
 {
  "checks": "tests.falsify_checks",
  "checks_sha256": "119f26bdfabe080aaf139358d9873c3475a1e864a8d05f285432eebc52fa31b6",
  "counts": {
   "FAILED": 415,
   "SURVIVED": 7
  },
  "function": "failures",
  "module": "hoshitori.falsify",
  "module_sha256": "f0adc9edd4d2631c296571b45098ce1aba525c007a9b76803e6177d15aed8a89",
  "total": 422,
  "unresolved": []
 },
 {
  "checks": "tests.falsify_calibration_checks",
  "checks_sha256": "40604b992d7539287d9da0920346436cd71fec10d46332f66cacc92bbc1e60a5",
  "counts": {
   "FAILED": 125,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.falsify_calibration",
  "module_sha256": "64149df980f6ce0f8550b51e5826b5df66ceb6f9473ac75158adf797e93f5a62",
  "total": 126,
  "unresolved": []
 },
 {
  "checks": "tests.features_checks",
  "checks_sha256": "3276eae68ada89174a35fd2a39d0e8df5634868a6c2dec42ad13c534a1ef32f6",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 121,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.features",
  "module_sha256": "878dbf4de8dcc17e4585f75af675f410f553742c662a524b4fab93572d86e35a",
  "total": 123,
  "unresolved": []
 },
 {
  "checks": "tests.fills_checks",
  "checks_sha256": "e6565e6abcfde50ca08aadc424ec14a2da224a9fc4749a912974ba37a3a67640",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 224,
   "SURVIVED": 1,
   "TIMED_OUT": 1
  },
  "function": "failures",
  "module": "hoshitori.fills",
  "module_sha256": "d03d6e8ab26fb0c5b4696803cb7597a21eeacd22d263159a1709b1cf8cd95b94",
  "total": 227,
  "unresolved": [
   "exit_order|statement dropped|open_ms += MINUTE_MS|0"
  ]
 },
 {
  "checks": "tests.forecast_checks",
  "checks_sha256": "8d0efb47fa7a88c3fc9552b5db57d8ae156cfff320ce6f644999d0d9ecd70144",
  "counts": {
   "FAILED": 393,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.forecast",
  "module_sha256": "fa7530469e730cb2c2681b53a348a4a24672e2a6d4f2b834ecac6476a500c686",
  "total": 396,
  "unresolved": []
 },
 {
  "checks": "tests.forgery_checks",
  "checks_sha256": "271128d63786e309a8fe4782d858a90f7794504f0208c7bdde171c7658c8fd65",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 160,
   "ENVIRONMENT": 1,
   "FAILED": 76,
   "SURVIVED": 4
  },
  "function": "failures",
  "module": "hoshitori.forgery",
  "module_sha256": "37c48c97132b22ff072fb77e99816712530d971fc66cef5d00469f2ff8e561aa",
  "total": 242,
  "unresolved": [
   "_empty|And->Or|directory.exists() and any(directory.iterdir())|0"
  ]
 },
 {
  "checks": "tests.gap_record_checks",
  "checks_sha256": "216c637aab9fcac422855b8a27c797c065aae8294817037e575d265a2e0a7282",
  "counts": {
   "FAILED": 108,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.gap_record",
  "module_sha256": "b3830a60a0df0f97bc44335a8a16f738c3adfd993198e124a2bace930cf0cbf9",
  "total": 109,
  "unresolved": []
 },
 {
  "checks": "tests.grounding_checks",
  "checks_sha256": "ce71fe6d54581b90a1bbe11c436f7962447cba5a664385389a9b30f68fb6e2a5",
  "counts": {
   "BUILD_FAILED": 8,
   "FAILED": 459,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.grounding",
  "module_sha256": "3c39f1d768562c17f80762b91de89645fbcc7fc3839d0bf5f8280dfdcba13d08",
  "total": 468,
  "unresolved": []
 },
 {
  "checks": "tests.history_checks",
  "checks_sha256": "1e84a58d6e9e4e607249659bc6abc32cbc6cc553d963faf796e56d6af0e90129",
  "counts": {
   "FAILED": 107
  },
  "function": "failures",
  "module": "hoshitori.history",
  "module_sha256": "68da95ca327f44dbc4f3be8876736c92345d12279acd7ca4715a12541a0261e0",
  "total": 107,
  "unresolved": []
 },
 {
  "checks": "tests.host_probe_checks",
  "checks_sha256": "693ee6aec377d59533a5338e012feda27c0011049681a94bf210219eae6720ed",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 263,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.host_probe",
  "module_sha256": "cfa89c8916227237e1d7404cbe5d0f370a21530ef6b85a769529d9a9de28d431",
  "total": 268,
  "unresolved": []
 },
 {
  "checks": "tests.impact_checks",
  "checks_sha256": "f5110b77fbc97bcb7d088c431f2f424fe17a1bd921864f4a6e3385f3f2169665",
  "counts": {
   "FAILED": 110,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.impact",
  "module_sha256": "e7b85926f158ed2702db5d0161edb957ac94cf46450f35830440f2092c074c01",
  "total": 111,
  "unresolved": []
 },
 {
  "checks": "tests.impact_validation_checks",
  "checks_sha256": "442dcbf17736ee08ee8934b253f9f10d0670c131b23dacf620aacc222ee4e843",
  "counts": {
   "FAILED": 243,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.impact_validation",
  "module_sha256": "64e51adcf7d03a7c502ebab9b86dfdef48506a4b09765cbee3958b1f4daca0c7",
  "total": 245,
  "unresolved": []
 },
 {
  "checks": "tests.injection_checks",
  "checks_sha256": "8dafc960a05776242eb7e9e49b24ef5ecaa58b7fcd0c4f80ca0c7be7e7f9d2d4",
  "counts": {
   "BUILD_FAILED": 6,
   "FAILED": 471,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.injection",
  "module_sha256": "50ce3bfd27dcd1c269628262d74613d44b1dd8b6308da57edfd2e242aea07c82",
  "total": 479,
  "unresolved": []
 },
 {
  "checks": "tests.intervention_checks",
  "checks_sha256": "c49c7fb67dd2f4c522772685d3621df74cfe52b36e12aea8e668333a041baa63",
  "counts": {
   "CHECK_RAISED": 49,
   "FAILED": 28,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.intervention",
  "module_sha256": "ca83302b097ea1a86facd017a56a59b92396b4c3509289dd146a938194a967e4",
  "total": 78,
  "unresolved": []
 },
 {
  "checks": "tests.lab_checks",
  "checks_sha256": "2210d2b40bf957edf88583bd4dec9ab986d21fb045c2ce86eb1e59399ae82215",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 12,
   "FAILED": 292,
   "SURVIVED": 10
  },
  "function": "failures",
  "module": "hoshitori.lab",
  "module_sha256": "d9b2d90707a3110d1c3ec64e2976e00472d31bae2bba333be0a86838ca2de5c0",
  "total": 315,
  "unresolved": []
 },
 {
  "checks": "tests.lab_forward_checks",
  "checks_sha256": "fea40b5723f2ff436f3b2018d8273c6982e57ef6e6d56903650750018aafdedc",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 7,
   "FAILED": 240,
   "SURVIVED": 7,
   "TIMED_OUT": 4
  },
  "function": "failures",
  "module": "hoshitori.lab_forward",
  "module_sha256": "8c6691f69e6453dcb1112456cd55bb01b409116534d73345246464232198ec16",
  "total": 259,
  "unresolved": [
   "run|statement dropped|i += 1|0",
   "run|statement dropped|i += 1|1",
   "run|statement dropped|holding, free_from = (None, sale_open)|0",
   "run|statement dropped|i += 1|2"
  ]
 },
 {
  "checks": "tests.lab_propose_checks",
  "checks_sha256": "b449372007fa8cb299a183f9080e05da8bd64f65c579d631eb99f50d06e29301",
  "counts": {
   "BUILD_FAILED": 46,
   "CHECK_RAISED": 3,
   "FAILED": 494,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.lab_propose",
  "module_sha256": "ad701d25c9fd5cffc590891337d78104aea9496f6cfb37af52d9c27223ff6d91",
  "total": 546,
  "unresolved": []
 },
 {
  "checks": "tests.lab_view_checks",
  "checks_sha256": "d8c4e6953909d57ab084b6eb638449f6c934597bf28ff78848f6ca8c3fbdcb04",
  "counts": {
   "CHECK_RAISED": 38,
   "FAILED": 324,
   "SURVIVED": 12
  },
  "function": "failures",
  "module": "hoshitori.lab_view",
  "module_sha256": "82c30db63ed44634af60705db8d6daa3dc96747b52c0f61f2b87b9db0da6ca8b",
  "total": 374,
  "unresolved": []
 },
 {
  "checks": "tests.labels_checks",
  "checks_sha256": "f00cc02a00156583f0ddf3dbaaaacdf1aa183f22b3bfc1f6ad19d8e65b58d4e3",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 7,
   "FAILED": 192
  },
  "function": "failures",
  "module": "hoshitori.labels",
  "module_sha256": "bd9fb04bb89b32019872efb18d362a2c95347212405b0bb33a026d36838d84ea",
  "total": 200,
  "unresolved": []
 },
 {
  "checks": "tests.league_all_checks",
  "checks_sha256": "73f0c2d0dc5cfd35c0266f77698d295403d3a875c980fdeb4e17ed6b8654babb",
  "counts": {
   "FAILED": 459,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.league",
  "module_sha256": "78e192caea77c67947a5089b7b0ac2e715d360a8f62d149095a7c34f867b319e",
  "total": 462,
  "unresolved": []
 },
 {
  "checks": "tests.league_cli_checks",
  "checks_sha256": "146e380d938d1cef005113ca982c0779a735c9935ac9ab90f61089091289b6aa",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 461,
   "SURVIVED": 2,
   "TIMED_OUT": 2
  },
  "function": "failures",
  "module": "hoshitori.league_cli",
  "module_sha256": "21c32145c48e669832ada3fc63f2eaa1b4f59c263a60d98c7327430600784429",
  "total": 467,
  "unresolved": [
   "_run.report|test negated|if once:     stop.set()|0",
   "_run.report|statement dropped|stop.set()|0"
  ]
 },
 {
  "checks": "tests.ledger_reader_checks",
  "checks_sha256": "3a5cca32bc6149f4d62de7b7a85e89ac235232116b0d9709e626d7bd481b323e",
  "counts": {
   "FAILED": 134,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.ledger_reader",
  "module_sha256": "2c1d53f038e39172a601adf63c4d644d500730736cf6dd945e5d790d9540e956",
  "total": 136,
  "unresolved": []
 },
 {
  "checks": "tests.lessons_checks",
  "checks_sha256": "2c143633563f31eb37ae510cb6a8dfde03973c469ad38f7c6dc07ae00608e737",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 71,
   "FAILED": 467,
   "SURVIVED": 28
  },
  "function": "failures",
  "module": "hoshitori.lessons",
  "module_sha256": "d37b820d8828af1d6848b30cc5ba91983d881767b10ef9b64f2d43e6d55c250d",
  "total": 567,
  "unresolved": []
 },
 {
  "checks": "tests.lessons_store_checks",
  "checks_sha256": "233d309ef05233213951092a42c6c51663e1346de96f38053ed5e8a6392aa81c",
  "counts": {
   "CHECK_RAISED": 22,
   "FAILED": 108
  },
  "function": "failures",
  "module": "hoshitori.lessons_store",
  "module_sha256": "c263d5e94f36519625397449c47503af796d3b5a8a6baa1f4b194be1c10cc22e",
  "total": 130,
  "unresolved": []
 },
 {
  "checks": "tests.lifecycle_checks",
  "checks_sha256": "aa5a7a1553a7455de5dcfae58555a338bcf141322ccedeb9956dad655f50621a",
  "counts": {
   "BUILD_FAILED": 21,
   "FAILED": 571,
   "SURVIVED": 5
  },
  "function": "failures",
  "module": "hoshitori.lifecycle",
  "module_sha256": "19d255ba2c34c03ddfb0c43fe6a614259921b76a4268c6c2f5ec65b4848c7e27",
  "total": 597,
  "unresolved": []
 },
 {
  "checks": "tests.lineage_checks",
  "checks_sha256": "fe0cd8e91da04eca52b04b06367d320963ce01b7747bec66970361d0079b09fe",
  "counts": {
   "FAILED": 165,
   "TIMED_OUT": 1
  },
  "function": "failures",
  "module": "hoshitori.lineage",
  "module_sha256": "39ed39365782e871efcc3ad7b140c5f589f04e7c480327cc00d25dcfef4fb67c",
  "total": 166,
  "unresolved": [
   "EvidenceStore.latest|statement dropped|current = following|0"
  ]
 },
 {
  "checks": "tests.manifest_checks",
  "checks_sha256": "3ae5179e4eeffc69739b5c227659b36b38223c50b44d1c765684e380a0a5c814",
  "counts": {
   "FAILED": 343,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.manifest",
  "module_sha256": "388324d07f7da1a490ec00d6b5d0b2d6e9e0fd3da18be3e5d43f1e03c2de5f9b",
  "total": 345,
  "unresolved": []
 },
 {
  "checks": "tests.model_arms_checks",
  "checks_sha256": "71df15dad7db968a3eeaccff1df1f2fb39b58490a6e6cc4926211d8524d2c27d",
  "counts": {
   "FAILED": 202,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.model_arms",
  "module_sha256": "fbb43f4f6a631fde8e7c3b0cea7a190b63beda7f3ed1cd135bc5adf107ff7b13",
  "total": 203,
  "unresolved": []
 },
 {
  "checks": "tests.model_call_record_checks",
  "checks_sha256": "e76743b7c803bac08435d15416de03b127617991b679c85cb423c8bb381410f0",
  "counts": {
   "FAILED": 305
  },
  "function": "failures",
  "module": "hoshitori.model_call_record",
  "module_sha256": "fc77e3d051792f9d81905c965755c77b6ff5b3f94c302b22007d175127968dae",
  "total": 305,
  "unresolved": []
 },
 {
  "checks": "tests.model_challenge_checks",
  "checks_sha256": "3f6cd781880db441c7aba829f18395b3cd4c2d286c0fe3d65567fc2af12a609a",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 84,
   "FAILED": 95,
   "SURVIVED": 6
  },
  "function": "failures",
  "module": "hoshitori.model_challenge",
  "module_sha256": "d49e4d1ca998e0c98bce3ffda53d1be91c527959f297ca70da30e4b77c674a77",
  "total": 186,
  "unresolved": []
 },
 {
  "checks": "tests.model_challenge_file_checks",
  "checks_sha256": "75c1598618bb20ba71eff2fd148495b091fedd35cacfb702610e274f5fccffa1",
  "counts": {
   "CHECK_RAISED": 599,
   "FAILED": 141,
   "SURVIVED": 4
  },
  "function": "failures",
  "module": "hoshitori.model_challenge_file",
  "module_sha256": "60b0265a5d2660ede838648f105a56ed660a6d875bbe5ea4f8ea22f6ace80d86",
  "total": 744,
  "unresolved": []
 },
 {
  "checks": "tests.model_client_checks",
  "checks_sha256": "ab0a4952afabf692267826292b762eef3da9d9a068956e37e1fc405857bb09bf",
  "counts": {
   "FAILED": 454
  },
  "function": "failures",
  "module": "hoshitori.model_client",
  "module_sha256": "0a5582cf05a3fb0ef05b089d368645a44ba9a4edd4ecfe2743aa47f0446f42f6",
  "total": 454,
  "unresolved": []
 },
 {
  "checks": "tests.model_gateway_checks",
  "checks_sha256": "2ea547d8357105bc9634caccb6f7d98c7456ddd2bc6e581aab25b2c3e0765610",
  "counts": {
   "FAILED": 277
  },
  "function": "failures",
  "module": "hoshitori.model_gateway",
  "module_sha256": "82b972711c74fb283e1fc21010d10a9593a7fed0af8bf4bc8d2df5f0faca0676",
  "total": 277,
  "unresolved": []
 },
 {
  "checks": "tests.model_settings_checks",
  "checks_sha256": "8a1aa0b3f455bee5cbdd036a00b35a3f350896a29a697681714079537ef3c56d",
  "counts": {
   "FAILED": 202,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.model_settings",
  "module_sha256": "24152ad27613e390432cef333bd654d639792c95604980f786d25c7126ce5aae",
  "total": 204,
  "unresolved": []
 },
 {
  "checks": "tests.move_check_checks",
  "checks_sha256": "6d1565c7cdfbe2d17b114d23a2d3213306e667220c8f1f3202171829d342f7bb",
  "counts": {
   "BUILD_FAILED": 2,
   "CHECK_RAISED": 58,
   "FAILED": 45
  },
  "function": "failures",
  "module": "hoshitori.move_check",
  "module_sha256": "4c91892fc1f4e8acbd989b411b4a00180a9713285b37617de95e161679ca908c",
  "total": 105,
  "unresolved": []
 },
 {
  "checks": "tests.ondemand_checks",
  "checks_sha256": "9f169867039a1a6ac17a64a2d61e8df782fd05e71854a0ba06d239eb702d9dc1",
  "counts": {
   "FAILED": 189,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.ondemand",
  "module_sha256": "6a06273f7e332b20f26b478b7331796ff81528601c5f172b5054355b2d1b1b85",
  "total": 192,
  "unresolved": []
 },
 {
  "checks": "tests.oracle_checks",
  "checks_sha256": "09b75265b380688cd2de5ee92e821ebd245ba970cf763fa0bb80640873853ece",
  "counts": {
   "FAILED": 112
  },
  "function": "failures",
  "module": "hoshitori.oracle",
  "module_sha256": "aa4d4d98ec03d6ccebb61172cedaa1a8c249c4bc67934db982b0968addde2811",
  "total": 112,
  "unresolved": []
 },
 {
  "checks": "tests.outcome_review_checks",
  "checks_sha256": "b6df61ff03f0bc7f78f4932406b2cb7b8e174ae68c7d77cac1ccaccbb1f9b97e",
  "counts": {
   "BUILD_FAILED": 2,
   "CHECK_RAISED": 5,
   "FAILED": 138,
   "SURVIVED": 4
  },
  "function": "failures",
  "module": "hoshitori.outcome_review",
  "module_sha256": "02561ff3f67428092aac7b392c36d6b9c995bd994fa6f2d7ed8fa51e568a79be",
  "total": 149,
  "unresolved": []
 },
 {
  "checks": "tests.paired_stats_checks",
  "checks_sha256": "969f1fc8212516d5ca1951785bf051d14a305dd805bccb978be1aed461f2c856",
  "counts": {
   "CHECK_RAISED": 87,
   "FAILED": 132,
   "SURVIVED": 11
  },
  "function": "failures",
  "module": "hoshitori.paired_stats",
  "module_sha256": "c046c5b8491a228ad61b879329df8672cfb3a86657541717ba80953ed6bba81b",
  "total": 230,
  "unresolved": []
 },
 {
  "checks": "tests.post_snapshot_checks",
  "checks_sha256": "1d63247fef1d09ac89459eac753a71e0300a53646a9f8fe4c6bdabccedd91d58",
  "counts": {
   "FAILED": 284,
   "SURVIVED": 8
  },
  "function": "failures",
  "module": "hoshitori.post_snapshot",
  "module_sha256": "49cdd0c9c97f8ba5791537aae16ddbc9e7cbd30f299f4467d94b8ee90ebc4100",
  "total": 292,
  "unresolved": []
 },
 {
  "checks": "tests.post_snapshot_job_checks",
  "checks_sha256": "f379e4025aab1dfa4f073c0460fb98d54bcaf02090f997d0766af31dd8753d23",
  "counts": {
   "FAILED": 34,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.post_snapshot_job",
  "module_sha256": "1a158601516903737aec034978b27cd592453136d1c49e2357885e255bc916f4",
  "total": 37,
  "unresolved": []
 },
 {
  "checks": "tests.practice_checks",
  "checks_sha256": "8c183b6ab7cd94214df1defe47d30f9e1f6dc476db99f4150ceb2ed4785556ef",
  "counts": {
   "BUILD_FAILED": 3,
   "FAILED": 288,
   "SURVIVED": 4
  },
  "function": "failures",
  "module": "hoshitori.practice",
  "module_sha256": "8fdce314771918abc0f5934f4a618f501b2cea95e9e3cca36109e370a2d390c0",
  "total": 295,
  "unresolved": []
 },
 {
  "checks": "tests.practice_job_checks",
  "checks_sha256": "ae528a06df7934f25f08b463316537017984f219ef32d679eb0672bf8c7728dc",
  "counts": {
   "FAILED": 38,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.practice_job",
  "module_sha256": "9321bdcaa03244b3b28e1117b96f640b844e32558b3bfad239262d74ce513098",
  "total": 39,
  "unresolved": []
 },
 {
  "checks": "tests.practice_record_checks",
  "checks_sha256": "484f7e40508553fadc46864ef6ecf64d6b54509828d0f11e658033d6ffbe6067",
  "counts": {
   "FAILED": 208
  },
  "function": "failures",
  "module": "hoshitori.practice_record",
  "module_sha256": "29bc6d4cee7750c376db12da98a64adecccf6e8bb80730d4e1edbe059529ff35",
  "total": 208,
  "unresolved": []
 },
 {
  "checks": "tests.practice_view_checks",
  "checks_sha256": "ca54d1758c39aa609de5b7e0456fd772f0d82e00a32c8ca5dc80d3e249f9f44e",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 148,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.practice_view",
  "module_sha256": "983b42a0e4ae35bbe5ee4bc20ec1ee2b4dd44923667aac9f0c679cbc855fd285",
  "total": 153,
  "unresolved": []
 },
 {
  "checks": "tests.protocol_checks",
  "checks_sha256": "cb663bc4de29a8810be73ef6fcc8cd9aa9352dab274198ab53ce8128c0c44bff",
  "counts": {
   "FAILED": 650,
   "SURVIVED": 2,
   "TIMED_OUT": 1
  },
  "function": "failures",
  "module": "hoshitori.protocol",
  "module_sha256": "b4cdb47e18f5809e83e3c3cc299fc355b8f98b23118564b730794a761155a101",
  "total": 653,
  "unresolved": [
   "Protocol.slots_between|statement dropped|k += 1|0"
  ]
 },
 {
  "checks": "tests.quality_checks",
  "checks_sha256": "4cb40d2c4b25292cdd24beaef6b2d23cbc688fd3403d61257e3516f10ed3e122",
  "counts": {
   "CHECK_RAISED": 45,
   "FAILED": 13
  },
  "function": "failures",
  "module": "hoshitori.quality",
  "module_sha256": "3c5ce23dba3485f14a8fe05e1b5fe5892ea95f7c6c2292be97214fee3e1a1053",
  "total": 58,
  "unresolved": []
 },
 {
  "checks": "tests.receipt_links_checks",
  "checks_sha256": "ae5885bea04f9c1160ad1119095247a00c62b31556d67e0fc56fde8204509e1a",
  "counts": {
   "BUILD_FAILED": 1,
   "FAILED": 25
  },
  "function": "failures",
  "module": "hoshitori.receipt_links",
  "module_sha256": "d55b492c2a6dea513279e3470338d5044b57eb8a23c520d28f4b95dc1ef26602",
  "total": 26,
  "unresolved": []
 },
 {
  "checks": "tests.recovery_checks",
  "checks_sha256": "cab0cf3ebd790fc70c30a9007680e8491b0639ee6517f09ad0f14937d0dcb439",
  "counts": {
   "FAILED": 174,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.recovery",
  "module_sha256": "dce7b3d42184c93b8ee01869039083c2c02cee20454a560cb2650deb7a3df6a5",
  "total": 175,
  "unresolved": []
 },
 {
  "checks": "tests.registry_checks",
  "checks_sha256": "34f41fa18f3fe771cd66b24dc801c6e4fbbbfbf96201da23edc37fc268c9f72f",
  "counts": {
   "FAILED": 923,
   "SURVIVED": 6
  },
  "function": "failures",
  "module": "hoshitori.registry",
  "module_sha256": "bdf084e82d34e92799fa453be5d93c10a225fb9e5917cb8380ba0bfa8f890f1c",
  "total": 929,
  "unresolved": []
 },
 {
  "checks": "tests.registry_job_checks",
  "checks_sha256": "3dd43a5bdb21fa1a975c5f030e71b5320abeca4ccbc34fd6292db1754e2a45b9",
  "counts": {
   "FAILED": 73
  },
  "function": "failures",
  "module": "hoshitori.registry_job",
  "module_sha256": "60c749f4f0a4fb6abcd3c2d58d0d071739e1ebf23676c4877f5f77d46fe2f7dd",
  "total": 73,
  "unresolved": []
 },
 {
  "checks": "tests.rejudge_checks",
  "checks_sha256": "fa43f46f7ec17769348537a4b91df8d5c1d58a3286d94911178ecf9959035b85",
  "counts": {
   "FAILED": 126,
   "SURVIVED": 7
  },
  "function": "failures",
  "module": "hoshitori.rejudge",
  "module_sha256": "14efb44fcccc4ae96da07a0ab7e62520e8c0f0c07308877bb1a5b9d58afb797f",
  "total": 133,
  "unresolved": []
 },
 {
  "checks": "tests.replay_checks",
  "checks_sha256": "b5a6a9cad26581c0ea370c0e9f914609a1b77a1a6a6a1ecfeb91613b54d12f13",
  "counts": {
   "FAILED": 274
  },
  "function": "failures",
  "module": "hoshitori.replay",
  "module_sha256": "fb1867d47600e1bae4ae5068ce9bb6ec19b33baaba2b635f692280fea947d315",
  "total": 274,
  "unresolved": []
 },
 {
  "checks": "tests.risk_kernel_checks",
  "checks_sha256": "450e607ff54e2b51783966594d25c3fd8fb1bb6c859b8394f31d3144d3c18c26",
  "counts": {
   "BUILD_FAILED": 8,
   "FAILED": 135,
   "SURVIVED": 2,
   "TIMED_OUT": 6
  },
  "function": "failures",
  "module": "hoshitori.risk_kernel",
  "module_sha256": "d5b323e8c687fdd1bc4a926f9e3ea9aae93855f489a68d239250acd823fc9664",
  "total": 151,
  "unresolved": [
   "shrink|test negated|if again == weights:     break|0",
   "shrink|statement dropped|weights = again|0",
   "shrink|Eq->NotEq|again == weights|0",
   "shrink|statement dropped|break|0",
   "_pass|test negated|-granted if short else granted|0",
   "_pass|min->max|min(want, *rooms.values())|0"
  ]
 },
 {
  "checks": "tests.rules_arm_checks",
  "checks_sha256": "89f83b573c8813c96d77f48a0646b33d5eb1c6c3f96ffdcf28925b2610e54bd3",
  "counts": {
   "BUILD_FAILED": 8,
   "FAILED": 348
  },
  "function": "failures",
  "module": "hoshitori.rules_arm",
  "module_sha256": "6f7e3fadb78e2add8ea0bd88bd723dd0a42c30290fc2e44b6b5e8e236e843111",
  "total": 356,
  "unresolved": []
 },
 {
  "checks": "tests.rules_challenge_checks",
  "checks_sha256": "6e93287ea7432cdc75002750c086bb3505c0c75f109ba034ea646010f3950c73",
  "counts": {
   "CHECK_RAISED": 22,
   "FAILED": 43
  },
  "function": "failures",
  "module": "hoshitori.rules_challenge",
  "module_sha256": "9c160509e9de8b806fe3a70190ecc8571a7b0f8d604a8de8ca304698f14f5f56",
  "total": 65,
  "unresolved": []
 },
 {
  "checks": "tests.rules_sensitivity_checks",
  "checks_sha256": "7cef929fa340a52d51195f8f80c4252109f8c9d6c88574f1920e7ee42660b40f",
  "counts": {
   "BUILD_FAILED": 1,
   "CHECK_RAISED": 40,
   "FAILED": 23,
   "TIMED_OUT": 17
  },
  "function": "failures",
  "module": "hoshitori.rules_sensitivity",
  "module_sha256": "6ddb0c38acd15184c8222eb0c8121e73781c78668e72bdd46f39736ca93cbedf",
  "total": 81,
  "unresolved": [
   "_neighbour|test negated|while rules_arm.state_of(rules_arm.normalised(r, atr, hours)) != target:     r += step|0",
   "sensitivity|Gt->GtE|at > 0|0",
   "_neighbour|Mult->Add|atr * Decimal(hours).sqrt()|0",
   "_neighbour|test negated|GRID if direction == UP else -GRID|0",
   "_neighbour|NotEq->Eq|rules_arm.state_of(rules_arm.normalised(r, atr, hours)) != target|0",
   "_neighbour|statement dropped|r += step|0",
   "sensitivity|Add->Sub|at + 1|0",
   "_neighbour|Eq->NotEq|direction == UP|0",
   "sensitivity|int+1|1|4",
   "_neighbour|Mult->Add|edge * scale|0",
   "sensitivity|Add->Sub|at + 1|1",
   "sensitivity|Sub->Add|at - 1|1",
   "sensitivity|Sub->Add|at - 1|2",
   "sensitivity|int+1|1|6",
   "sensitivity|int+1|1|7",
   "sensitivity|int+1|1|8",
   "sensitivity|int+1|1|9"
  ]
 },
 {
  "checks": "tests.ryo_collect_checks",
  "checks_sha256": "f0dc949609769aad6b3a449912de8708c915e791bc74a9da096e9f3ddb369f27",
  "counts": {
   "FAILED": 192,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.ryo_collect",
  "module_sha256": "ae7b604cbdd04ee8a851e58ab48f932b8da014e5a97af176d6bc7b6e061a9a90",
  "total": 194,
  "unresolved": []
 },
 {
  "checks": "tests.ryo_envelope_checks",
  "checks_sha256": "d83a1f71732bdc92a4abc045f4f9d33591ff8e19a2be808207ed4e12f1ae4191",
  "counts": {
   "CHECK_RAISED": 198,
   "FAILED": 99
  },
  "function": "failures",
  "module": "hoshitori.ryo_envelope",
  "module_sha256": "fdbf33eeb89aca57c97df5a087ad045f61255878dd717066f11176a5c80d688c",
  "total": 297,
  "unresolved": []
 },
 {
  "checks": "tests.ryo_evidence_checks",
  "checks_sha256": "cab9c6fe44e9e18f0e93af2cdb7c78b5903348cbe0c4f3c10bb9fa5885fd1183",
  "counts": {
   "FAILED": 147,
   "SURVIVED": 3
  },
  "function": "failures",
  "module": "hoshitori.ryo_evidence",
  "module_sha256": "8cf1f404c13d5a8c2d50d0752a4578e2c531d822e82eca39d0d190e243e90b68",
  "total": 150,
  "unresolved": []
 },
 {
  "checks": "tests.ryo_fields_checks",
  "checks_sha256": "4c00fe08bd93f9dde68b4cb4a8fa3141fd5a2e13ccc2f1c6031a6a3c3b908995",
  "counts": {
   "BUILD_FAILED": 8,
   "CHECK_RAISED": 151,
   "FAILED": 217,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.ryo_fields",
  "module_sha256": "85936444b6cecb0b47817333369e5062d2a077ec4144e3d93c6b83c00920af01",
  "total": 378,
  "unresolved": []
 },
 {
  "checks": "tests.ryo_gateway_checks",
  "checks_sha256": "cf4f2ac15f3d019650355a16de37841781b76cd772cc8e98bc5eec0fb6015390",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 30
  },
  "function": "failures",
  "module": "hoshitori.ryo_gateway",
  "module_sha256": "a8259e727fc8e9fa797b02062715acbc8d33d652699977eb4e10c2e782e004e4",
  "total": 32,
  "unresolved": []
 },
 {
  "checks": "tests.ryo_plan_checks",
  "checks_sha256": "7beb5706d70dc8e1ee40494dcc6feac48710d846eddf767e69949cd448ec240e",
  "counts": {
   "FAILED": 231
  },
  "function": "failures",
  "module": "hoshitori.ryo_plan",
  "module_sha256": "86a25d83af092e37561353b35dcf4eaf902d5c4bf0cf628b219dc270d0b77cfb",
  "total": 231,
  "unresolved": []
 },
 {
  "checks": "tests.ryo_plan_job_checks",
  "checks_sha256": "9d173cd0aae3f8e589629a73b5ff459c9349fa47f24ddd07735e0d50dba320d7",
  "counts": {
   "FAILED": 100,
   "TIMED_OUT": 38
  },
  "function": "failures",
  "module": "hoshitori.ryo_plan_job",
  "module_sha256": "76d259766e386d34c680486c5acdb70c0d92ab9990d63381b61407cd4d2559b6",
  "total": 138,
  "unresolved": [
   "|str+~|'ryo-plan'|0",
   "|str+~|\"no oracle pair is known for the token at the end of its window, though the window's own call was settled\"|0",
   "plan_pass|statement dropped|out.sort(key=lambda rec: order[str(cast(dict[str, Any], rec['body'])['read_id'])])|0",
   "_as_window|return None|return settlement.Settlement(ryo_plan.record_id(p.read_id), p.read_id, 'B', p.window.pair_id, settlement.RULE_VERSION, p.ended.state, None, p.ended.reason or p.|0",
   "_ready|statement dropped|made = call|0",
   "Passed|True<->False|True|0",
   "_Plan|True<->False|True|0",
   "_ready|test negated|if common.contract == 'D' and common.horizon_ms == ryo_plan.HORIZON_MS:     calls.setdefault((common.cycle_id, common.asset_id), made)|0",
   "_ready|statement dropped|continue|1",
   "_ready|statement dropped|continue|3",
   "_as_window|str+~|'B'|0",
   "_as_window|Or->And|p.ended.reason or p.ended.state.value|0",
   "_ready|And->Or|common.contract == 'D' and common.horizon_ms == ryo_plan.HORIZON_MS|0",
   "plan_pass|Or->And|p.ended.state not in (State.SETTLED, State.VOID) or (p.ended.state is State.VOID and p.ended.reason != 'VOID_TIE')|0",
   "plan_pass|statement dropped|out.append(_record(sv, p, _as_window(p), now_ms))|0",
   "plan_pass|statement dropped|continue|0",
   "plan_pass|statement dropped|out.append(_record(sv, p, None, now_ms))|0",
   "plan_pass|statement dropped|continue|1",
   "plan_pass|statement dropped|failed.append((p.read_id, NO_PAIR))|0",
   "plan_pass|statement dropped|failed.append((p.read_id, str(exc)[:200]))|0",
   "plan_pass|GtE->Gt|now_ms - (p.window.origin_ms + ryo_plan.HORIZON_MS) >= RETRY_WINDOW_MS|0",
   "_clean|str+~|'committed_at'|0",
   "_ready|str+~|'read_id'|0",
   "_ready|Eq->NotEq|common.horizon_ms == ryo_plan.HORIZON_MS|0",
   "plan_pass|Sub->Add|now_ms - (p.window.origin_ms + ryo_plan.HORIZON_MS)|0",
   "plan_pass|test negated|if pair == DEFERRED:     pending.append(p.read_id) else:     out.append(_record(sv, p, settlement.void(p.read_id, 'B', p.window.pair_id, pair), now_ms))|0",
   "plan_pass|statement dropped|continue|3",
   "plan_pass|statement dropped|pending.append(p.read_id)|0",
   "plan_pass|statement dropped|failed.append((p.read_id, str(exc)[:200]))|1",
   "_ready|str+~|'body'|0",
   "plan_pass|NotEq->Eq|p.ended.reason != 'VOID_TIE'|0",
   "plan_pass|Add->Sub|p.window.origin_ms + k * MINUTE_MS|0",
   "plan_pass|Add->Sub|p.window.origin_ms + ryo_plan.HORIZON_MS|1",
   "plan_pass|Eq->NotEq|pair == DEFERRED|0",
   "plan_pass|statement dropped|pending.append(p.read_id)|1",
   "plan_pass|str+~|'VOID_TIE'|0",
   "plan_pass|int+1|200|0",
   "plan_pass|int+1|200|1"
  ]
 },
 {
  "checks": "tests.ryo_read_checks",
  "checks_sha256": "ca4f6ff41d5ec81462162ff7f69a775746f0fface8dafe39a86d226339a92686",
  "counts": {
   "FAILED": 295
  },
  "function": "failures",
  "module": "hoshitori.ryo_read",
  "module_sha256": "676ce4bfb85ad72c444319f9e1137bfce3675d3e45654fbeed728f727aac2f69",
  "total": 295,
  "unresolved": []
 },
 {
  "checks": "tests.safe_fetch_checks",
  "checks_sha256": "028e4ef887b8004314e3d676dcc9e9b9d035e5f93b3882f07cd0ffea115d8aec",
  "counts": {
   "BUILD_FAILED": 7,
   "FAILED": 305,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.safe_fetch",
  "module_sha256": "776f4bc9e05218f18dcadcde23ae598accd4c67776b1843d1f1c1775980fc800",
  "total": 313,
  "unresolved": []
 },
 {
  "checks": "tests.safe_fetch_net_checks",
  "checks_sha256": "94a928851aeba431b81e37bb754d040db417956ee28e94d530382c5e660c9fa0",
  "counts": {
   "FAILED": 111
  },
  "function": "failures",
  "module": "hoshitori.safe_fetch_net",
  "module_sha256": "eb30b4d70e2cddc06d1862ce75f32aad397661dd56d964df54606e4d7ba0bdfe",
  "total": 111,
  "unresolved": []
 },
 {
  "checks": "tests.scheduler_checks",
  "checks_sha256": "f9822a3285a21160d91cded68c481c5faedddc0b4b76c2820a3b382ea53df039",
  "counts": {
   "FAILED": 141,
   "TIMED_OUT": 1
  },
  "function": "failures",
  "module": "hoshitori.scheduler",
  "module_sha256": "3a4444970e301922fa9cff9a1e87486a76bdffe3d9d337d4bfca1faca8dee456",
  "total": 142,
  "unresolved": [
   "run_forever|statement dropped|report(tick(sv, config))|0"
  ]
 },
 {
  "checks": "tests.scoring_checks",
  "checks_sha256": "78a74921eb15a0925a5744aaa8b9f38772c9ada567687bc2c0c26fadd77107d0",
  "counts": {
   "BUILD_FAILED": 3,
   "FAILED": 322,
   "SURVIVED": 1,
   "TIMED_OUT": 4
  },
  "function": "failures",
  "module": "hoshitori.scoring",
  "module_sha256": "082dcc18c311902dca5a691b9faa388f1139e10f9d15b7528e88d61c2c85cec6",
  "total": 330,
  "unresolved": [
   "_ratio_extreme|test negated|if better >= trial if smallest else better <= trial:     return trial|0",
   "_ratio_extreme|statement dropped|trial = better|0",
   "_ratio_extreme|GtE->Gt|better >= trial|0",
   "_ratio_extreme|LtE->Lt|better <= trial|0"
  ]
 },
 {
  "checks": "tests.settle_job_checks",
  "checks_sha256": "2c599d26fafdcef7e002c63e590af6473441b917bbc7bb1a663f7bf57b121602",
  "counts": {
   "FAILED": 249,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.settle_job",
  "module_sha256": "cc5ee7bf4c071b0eccf23a76e86599e0d169cd71738eb8ff70bd4c73f9d64984",
  "total": 251,
  "unresolved": []
 },
 {
  "checks": "tests.settlement_checks",
  "checks_sha256": "b203f84febba2c150007bb40eb1e86050d51010ec851dc96724a0170553aee5b",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 265,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.settlement",
  "module_sha256": "616035136e1a2b7842e5b880673a609b2f2dbae8449865061a53b021e7d5d274",
  "total": 268,
  "unresolved": []
 },
 {
  "checks": "tests.settlement_record_checks",
  "checks_sha256": "fe128997a322c0fa8c9a6ab8edbca878d9cfe076fa91a8ba8bc390e4e137e54e",
  "counts": {
   "FAILED": 261
  },
  "function": "failures",
  "module": "hoshitori.settlement_record",
  "module_sha256": "73f7e5ff88f13ef7a55111a2a9cbc8571851ff76a769f9e6d4f922a49412a54b",
  "total": 261,
  "unresolved": []
 },
 {
  "checks": "tests.site_export_checks",
  "checks_sha256": "830a97c23628c2af10a987b15a70564c38a44d9fc5cb60d3c43eb64c3d7f9146",
  "counts": {
   "CHECK_RAISED": 122,
   "ENVIRONMENT": 10,
   "FAILED": 75,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.site_export",
  "module_sha256": "8011d3bbc6cc2fc1acce6c9ce0b46552494d4d74930d9ac733b5e822c71a0b5b",
  "total": 208,
  "unresolved": [
   "write|statement dropped|out.mkdir(parents=True, exist_ok=True)|0",
   "write|statement dropped|(out / RECEIPTS).mkdir(exist_ok=True)|0",
   "write|statement dropped|temporary.write_bytes(data)|0",
   "write|statement dropped|os.replace(temporary, target)|0",
   "documents|str+~|'today.json'|0",
   "write|True<->False|True|0",
   "write|True<->False|True|1",
   "write|True<->False|True|2",
   "_owned|And->Or|p.is_file() and p.suffix == '.json'|0",
   "_owned|Eq->NotEq|p.suffix == '.json'|0"
  ]
 },
 {
  "checks": "tests.snapshot_checks",
  "checks_sha256": "a47fd246620dc41de9edd7698e7a733798a96d3776194298ed5890e71afd578e",
  "counts": {
   "BUILD_FAILED": 2,
   "FAILED": 462,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.snapshot",
  "module_sha256": "3c91ad409c5860251c9ba473abff11c5db82e6d83ede971a6d703acb723f2267",
  "total": 466,
  "unresolved": []
 },
 {
  "checks": "tests.social_gateway_checks",
  "checks_sha256": "dd706ee36b0706f6eabf389afaa42aa1c72abcb2dcf54cb2ff6ff16b9f1de336",
  "counts": {
   "CHECK_RAISED": 22,
   "FAILED": 152,
   "SURVIVED": 5
  },
  "function": "failures",
  "module": "hoshitori.social_gateway",
  "module_sha256": "aa50643d3316c513516f91d4d7d91cacb979605d995bc274128a82c36fbc1fa6",
  "total": 179,
  "unresolved": []
 },
 {
  "checks": "tests.source_value_checks",
  "checks_sha256": "f568f03cf40e1f3fb776fb7efbc173821c47e3d399e1fe9146549e98cf332783",
  "counts": {
   "FAILED": 257,
   "SURVIVED": 5
  },
  "function": "failures",
  "module": "hoshitori.source_value",
  "module_sha256": "0925d09de22f4d82234653959c3f6fd24bcaca8ce5fccc1e7c40070958b0879b",
  "total": 262,
  "unresolved": []
 },
 {
  "checks": "tests.sweep_view_checks",
  "checks_sha256": "41f7b939985fbdcd24ce21c09aa7a1ddf2993d436a357a983840460358c2ff3b",
  "counts": {
   "FAILED": 147,
   "SURVIVED": 2
  },
  "function": "failures",
  "module": "hoshitori.sweep_view",
  "module_sha256": "9110eb90d48c03ccedb25d1fb2815409cb9d30f6461a0d9a0e16658d0ff5e53f",
  "total": 149,
  "unresolved": []
 },
 {
  "checks": "tests.timeline_checks",
  "checks_sha256": "1bb314892eeaf7dce37bd14ca5235af3d2ca75dec4c0d579c640ad5071ea4309",
  "counts": {
   "FAILED": 160
  },
  "function": "failures",
  "module": "hoshitori.timeline",
  "module_sha256": "01af171a5a2ef3a444caa848f16c755671893ac19159bbc38503d5ec8ea692b1",
  "total": 160,
  "unresolved": []
 },
 {
  "checks": "tests.today_items_checks",
  "checks_sha256": "067c2f8f5913ff212be7bdf52b7e235aa0b4a4a1362575ea32106cd3ea85c528",
  "counts": {
   "FAILED": 120,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.today_items",
  "module_sha256": "559c70f321929af0aef7be0378f8019c7131eb662eccd792e3ac47fdeba6a2dc",
  "total": 121,
  "unresolved": []
 },
 {
  "checks": "tests.triggers_checks",
  "checks_sha256": "76df2da40f18d2e2213b7b11fa22dbdc0d7e32d47ad2fdbbecd921fc6fd0c83c",
  "counts": {
   "FAILED": 104,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.triggers",
  "module_sha256": "ad466d770073fe4b369f1b15581d88e8c3a5d668254749420bbbad010d0547c5",
  "total": 105,
  "unresolved": []
 },
 {
  "checks": "tests.tsa_gateway_checks",
  "checks_sha256": "7f06d36902539aaeeed55ef94074a4b97e87b379c1de13746194cbfb3fb3921c",
  "counts": {
   "FAILED": 120
  },
  "function": "failures",
  "module": "hoshitori.tsa_gateway",
  "module_sha256": "7fad0bcc227f2dfee73a791ced768d905c3ab77fc299a2d2a5e2d57248f3aa9e",
  "total": 120,
  "unresolved": []
 },
 {
  "checks": "tests.typed_checks",
  "checks_sha256": "92074e05e6f7d2d9064e8a18ad05693548500edd66bc47e428c52e1df133a5f2",
  "counts": {
   "FAILED": 342,
   "SURVIVED": 1
  },
  "function": "failures",
  "module": "hoshitori.typed",
  "module_sha256": "4b6885894466dfa1ca9395c56fd3fadb0fd4091fa6f1938f6bd250c6b4a927c6",
  "total": 343,
  "unresolved": []
 },
 {
  "checks": "tests.unicode15_checks",
  "checks_sha256": "d5cb0eaa31bfca95973c65b4454edc8c1701dc1e0e0d972809786acd96af7e02",
  "counts": {
   "FAILED": 1415
  },
  "function": "failures",
  "module": "hoshitori.unicode15",
  "module_sha256": "8fe9e0e4fd506abe1de76a51438dd7df1cd0e071103d70ef554ed27eb4a97ebc",
  "total": 1415,
  "unresolved": []
 },
 {
  "checks": "tests.venue_funding_checks",
  "checks_sha256": "3cd3055a9e339828871ce25726f3c508394d76ccf5f7142ec4f57f85a37461b5",
  "counts": {
   "CHECK_RAISED": 77,
   "FAILED": 38
  },
  "function": "failures",
  "module": "hoshitori.venue_funding",
  "module_sha256": "f8e639fc69ba4fb5d0075dee8e4be8c82376abb273bbb1da09a73a1983d021d3",
  "total": 115,
  "unresolved": []
 },
 {
  "checks": "tests.venue_probe_checks",
  "checks_sha256": "25450b46fd8432d48e62a4af538f4036fa10fa089e478cac198a458426254613",
  "counts": {
   "FAILED": 71
  },
  "function": "failures",
  "module": "hoshitori.venue_probe",
  "module_sha256": "d9c2a8cb613278bbe7663af2276e986687f80b06013da52f595e78b8483af272",
  "total": 71,
  "unresolved": []
 },
 {
  "checks": "tests.venue_survey_checks",
  "checks_sha256": "35d4dc85bebca73854bd8d8ff4ccd7abfe89a189250fa83f76b8435025c93524",
  "counts": {
   "BUILD_FAILED": 3,
   "FAILED": 230,
   "SURVIVED": 10,
   "TIMED_OUT": 5
  },
  "function": "failures",
  "module": "hoshitori.venue_survey",
  "module_sha256": "dd3567d0a03ce62d09d170a17b07a4d1c149ab969f41b333c815dbbc1cb2b830",
  "total": 248,
  "unresolved": [
   "run|statement dropped|_check(venues, watchlist, minutes, wait_s, base_urls)|0",
   "_check|Or->And|not watchlist or len(set(watchlist)) != len(watchlist) or any((not s or s != s.upper() for s in watchlist))|0",
   "_check|statement dropped|raise SurveyError('the watchlist names at least one symbol, each once, in capitals')|0",
   "run|statement dropped|runner.stop()|0",
   "main|statement dropped|urls[venue] = url|0"
  ]
 },
 {
  "checks": "tests.view_models_checks",
  "checks_sha256": "60d352ce09efac1bc1e3bd190578eee966d9eaee01d51e53d5f91fee2db33831",
  "counts": {
   "BUILD_FAILED": 2,
   "CHECK_RAISED": 445,
   "FAILED": 270,
   "SURVIVED": 7
  },
  "function": "failures",
  "module": "hoshitori.view_models",
  "module_sha256": "13aea01759d14d671737c5ca6e467a32fa6c884cf358196a0dc083ad643f1f63",
  "total": 724,
  "unresolved": []
 },
 {
  "checks": "tests.voices_checks",
  "checks_sha256": "efccedf6ca6abf7220d926fdb10376aa6b39410d077a7c176d6e8179421428de",
  "counts": {
   "CHECK_RAISED": 29,
   "FAILED": 452,
   "SURVIVED": 10
  },
  "function": "failures",
  "module": "hoshitori.voices",
  "module_sha256": "0a1ba114ac10bdd39e2424586e30bdee1166d79596bb7f80c88ecf92fa70bc33",
  "total": 491,
  "unresolved": []
 },
 {
  "checks": "tests.whales_checks",
  "checks_sha256": "b408d6d170eaa5edf7fef5908dedad3bedf6a18bab99eea93bfc5d5e8fa605f6",
  "counts": {
   "CHECK_RAISED": 13,
   "FAILED": 61,
   "SURVIVED": 5
  },
  "function": "failures",
  "module": "hoshitori.whales",
  "module_sha256": "0c169f5a45a122558aa040133749a9c9831d1cd9893bd296c14c735a06936105",
  "total": 79,
  "unresolved": []
 }
]
```
