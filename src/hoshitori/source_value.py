"""Whether a source of evidence makes the calls better, kept apart from whether it changes them.

The evidence challenge (``rules_challenge``) shows whether a call changes when one source is taken out of its frozen snapshot: decision
sensitivity. This module asks the next question, on calls that have settled: is the call made with the source closer to what happened than
the call made without it? For each committed cycle, each candidate, each rules arm and each horizon whose up-or-down call settled to a rise
or a fall, the rules arm is run again on the same snapshot without each source family, exactly and at no cost (it is deterministic). The
call with the source and the call without it are then two forecasters of the same slot, compared by ``comparison.compare`` with the same
rules as any pair of agents: the Brier difference, the windows that do not overlap, the anytime-valid interval, four states and never a
rank. "First better" means the source helped; "second better" that the call was better without it.

* **Only what can be scored is scored.** A slot where the rule made no call is left out. A slot where, without the source, the rule makes
  no call (the source is required) cannot be scored and is counted as ``required``. A slot whose re-run cannot be evaluated is counted as
  ``not_evaluated``.
* **The re-run must reproduce the call.** If the rule run again on the full snapshot does not give the probability that was committed
  (the mapping it was run with is not the one in force now), the slot is counted as ``differs`` and left out, so a changed rule is never
  mistaken for the value of a source.
* **Exploratory.** The sources and the comparison were not pre-registered before the record began, so every line is exploratory; the page
  says so.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

from hoshitori import challenge_view, comparison, forecast, labels, rules_arm, rules_challenge
from hoshitori.intervention import source_ids
from hoshitori.protocol import Protocol
from hoshitori.timeline import from_ms, to_ms
from hoshitori.timeutil import parse_rfc3339

WITH: Final = "with"
WITHOUT: Final = "without"
HOUR_MS: Final = 3_600_000


@dataclass(frozen=True)
class SourceLine:
    arm: str  # the agent whose calls were made again: a rules arm, or a model arm from recorded runs
    arm_name: str  # its name as every page writes it (``labels.arm_name``)
    source: str
    hours: int
    shared: int  # settled calls scored with and without the source
    mean: str | None  # mean Brier difference, without less with: above zero the source helped; four places
    lower: str | None
    upper: str | None
    state: str  # comparison's state: first (with) better, second (without) better, not distinguishable yet, too few
    since: int | None
    required: int  # slots where the rule makes no call without the source
    not_evaluated: int
    differs: int  # slots where the re-run did not reproduce the committed call


@dataclass(frozen=True)
class SourceValue:
    alpha: str
    v_opt: str
    min_shown: int
    lines: tuple[SourceLine, ...]


def _four(x: float | None) -> str | None:
    if x is None:
        return None
    shown = f"{x:.4f}"
    return "0.0000" if shown == "-0.0000" else shown


def build(
    records: Sequence[Mapping[str, Any]],
    protocol: Protocol,
    mappings: Mapping[str, rules_arm.BaseRates],
    model_runs: Sequence[ModelRuns] = (),
) -> SourceValue:
    """Every source of every committed cycle, at every horizon, from the rules arms' settled up-or-down calls, then the lines of the
    recorded model runs."""
    calls = {r["id"]: r["body"] for r in records if r.get("type") == "forecast" and r["body"]["contract"] == "D"}
    outcomes = {
        r["body"]["forecast_id"]: int(r["body"]["outcome"] == "1")
        for r in records
        if r.get("type") == "settlement" and r["body"]["state"] == "SETTLED" and r["body"]["forecast_id"] in calls
    }
    origins = {
        r["body"]["forecast_id"]: to_ms(parse_rfc3339(r["body"]["origin"]))
        for r in records
        if r.get("type") == "forecast_state" and r["body"]["state"] == "OPEN" and r["body"].get("origin") is not None
    }
    planned = {
        r["body"]["cycle_id"]: from_ms(to_ms(parse_rfc3339(r["body"]["planned_for"]))).hour
        for r in records
        if r.get("type") == "cycle_state" and r["body"]["state"] == "PLANNED"
    }
    arms = [arm for arm, _ in protocol.arms if arm in mappings]
    cycles = sorted({calls[fid]["cycle_id"] for fid in outcomes if calls[fid]["arm_id"] in arms})
    scored: dict[tuple[str, str, int], dict[str, list[comparison.Call]]] = {}
    counts: dict[tuple[str, str, int], list[int]] = {}  # required, not evaluated, differs
    for cycle_id in cycles:
        _, _, frozen = challenge_view.cycle_of(records, cycle_id)
        store = challenge_view.store_of(records)
        sources = challenge_view.ordered(item.source for item, _ in frozen.items)
        for slot in frozen.slots:
            for arm in arms:
                for hours in protocol.horizons_hours:
                    fid = forecast.forecast_id(cycle_id, arm, slot.asset_id, "D", hours * HOUR_MS)
                    if fid not in outcomes or fid not in origins:
                        continue
                    committed = calls[fid]["p"]
                    for source in sources:
                        key = (arm, source, hours)
                        tally = counts.setdefault(key, [0, 0, 0])
                        result = rules_challenge.challenge(
                            frozen, store, source_ids(frozen, source), mappings[arm], slot.candidate_id, hours
                        )
                        if result.original.p is None:
                            continue
                        if str(result.original.p) != str(committed):
                            tally[2] += 1
                            continue
                        if result.changed is None:
                            tally[1] += 1
                            continue
                        if result.changed.p is None:
                            tally[0] += 1
                            continue
                        made = {"cycle_id": cycle_id, "asset_id": slot.asset_id, "planned_hour": planned[cycle_id]}
                        close = origins[fid] + hours * HOUR_MS
                        pair = scored.setdefault(key, {WITH: [], WITHOUT: []})
                        pair[WITH].append(comparison.Call(**made, close_ms=close, p=result.original.p, outcome=outcomes[fid]))
                        pair[WITHOUT].append(comparison.Call(**made, close_ms=close, p=result.changed.p, outcome=outcomes[fid]))
    lines = [_line(key, scored.get(key, {}), counts[key]) for key in sorted(counts)]
    for runs in model_runs:
        lines += model_lines(runs)
    return SourceValue(str(comparison.ALPHA), str(comparison.V_OPT), comparison.MIN_SHOWN, tuple(lines))


def _line(key: tuple[str, str, int], pairs: Mapping[str, list[comparison.Call]], tally: Sequence[int]) -> SourceLine:
    arm, source, hours = key
    line = comparison.compare(WITH, WITHOUT, hours, pairs)
    required, not_evaluated, differs = tally
    return SourceLine(
        arm,
        labels.arm_name(arm),
        source,
        hours,
        line.shared,
        _four(line.mean),
        _four(line.lower),
        _four(line.upper),
        line.state,
        line.since,
        required,
        not_evaluated,
        differs,
    )


# --- a model arm, from recorded runs --------------------------------------------------------------------------------------------------
# A model gives a different answer each time it is asked, so the committed call is not compared with a fresh one: the arm is asked
# twice per slot, once on the full snapshot and once without the source, with the same seed, and those two answers are the pair. The
# file below is what was asked and answered (``scripts/model_source_value.py`` writes it); the lines are made from it at export, so they
# can be checked against the answers without asking again.
RUNS_FORMAT: Final = 1


class RunsError(ValueError):
    """A recorded model-run file that cannot be read as one."""


@dataclass(frozen=True)
class ModelPair:
    cycle_id: str
    asset_id: str
    symbol: str
    hours: int
    planned_hour: int
    close_ms: int
    outcome: int  # 1 rose, 0 fell
    seed: int
    with_p: str | None  # the arm's chance on the full snapshot; none when it made no call
    without_p: str | None  # the same without the source; none when it made no call
    failed: bool  # a run failed (no usable answer): not scored, counted as not evaluated


@dataclass(frozen=True)
class ModelRuns:
    arm: str
    model: str
    agent_version: str
    source: str
    pairs: tuple[ModelPair, ...]


def read_model_runs(data: bytes) -> ModelRuns:
    """A recorded model-run file, read strictly: a missing or mistyped member is refused, never guessed."""
    try:
        doc = json.loads(data)
        if doc["format"] != RUNS_FORMAT:
            raise RunsError(f"a model-run file of format {doc['format']!r}, not {RUNS_FORMAT}")
        pairs = tuple(
            ModelPair(
                _text(x["cycle_id"]),
                _text(x["asset_id"]),
                _text(x["symbol"]),
                _whole(x["hours"]),
                _hour(x["planned_hour"]),
                _whole(x["close_ms"]),
                _bit(x["outcome"]),
                _whole(x["seed"]),
                _chance(x["with_p"]),
                _chance(x["without_p"]),
                _flag(x["failed"]),
            )
            for x in doc["pairs"]
        )
        return ModelRuns(_text(doc["arm"]), _text(doc["model"]), _text(doc["agent_version"]), _text(doc["source"]), pairs)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        if isinstance(exc, RunsError):
            raise
        raise RunsError(f"not a model-run file: {exc}") from exc


def _text(v: Any) -> str:
    if not isinstance(v, str) or not v:
        raise RunsError("a text member is empty or not text")
    return v


def _whole(v: Any) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise RunsError("a count is not a whole number")
    return v


def _hour(v: Any) -> int:
    if _whole(v) > 23:
        raise RunsError("an hour of the day is 0 to 23")
    return int(v)


def _bit(v: Any) -> int:
    if v not in (0, 1) or isinstance(v, bool):
        raise RunsError("an outcome is 0 or 1")
    return int(v)


def _flag(v: Any) -> bool:
    if not isinstance(v, bool):
        raise RunsError("a flag is true or false")
    return v


def _chance(v: Any) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str) or not 0 <= Decimal(v) <= 1:
        raise RunsError("a chance is a decimal between 0 and 1, written as text")
    return v


def model_lines(runs: ModelRuns) -> list[SourceLine]:
    """One line per horizon: the pairs where the arm called on both sides are scored; a pair where it called only with the source counts
    as the source being required; a failed pair is not evaluated; a pair with no call on the full snapshot is left out."""
    scored: dict[int, dict[str, list[comparison.Call]]] = {}
    tally: dict[int, list[int]] = {}
    for x in runs.pairs:
        counts = tally.setdefault(x.hours, [0, 0, 0])
        if x.failed:
            counts[1] += 1
            continue
        if x.with_p is None:
            continue
        if x.without_p is None:
            counts[0] += 1
            continue
        pair = scored.setdefault(x.hours, {WITH: [], WITHOUT: []})
        for side, p in ((WITH, x.with_p), (WITHOUT, x.without_p)):
            pair[side].append(comparison.Call(x.cycle_id, x.asset_id, x.planned_hour, x.close_ms, Decimal(p), x.outcome))
    return [_line((runs.arm, runs.source, hours), scored.get(hours, {}), tally[hours]) for hours in sorted(tally)]


def check_against(runs: ModelRuns, records: Sequence[Mapping[str, Any]]) -> None:
    """Refuse recorded runs that this ledger does not bear out: every pair must name a settled up-or-down call of the file's arm, in its
    cycle, on its token and horizon, made by the file's agent version, and carry that call's outcome."""
    settled = {
        r["body"]["forecast_id"]: r["body"]["outcome"] for r in records if r.get("type") == "settlement" and r["body"]["state"] == "SETTLED"
    }
    calls = {r["id"]: r["body"] for r in records if r.get("type") == "forecast"}
    for x in runs.pairs:
        fid = forecast.forecast_id(x.cycle_id, runs.arm, x.asset_id, "D", x.hours * HOUR_MS)
        call = calls.get(fid)
        if call is None or fid not in settled:
            raise RunsError(f"no settled call of {runs.arm} in {x.cycle_id} on {x.symbol} over {x.hours} hours")
        if call["agent_version_hash"] != runs.agent_version:
            raise RunsError(f"the call in {x.cycle_id} on {x.symbol} was made by another version of {runs.arm}")
        if str(x.outcome) != settled[fid]:
            raise RunsError(f"the outcome of {x.symbol} in {x.cycle_id} over {x.hours} hours is not the ledger's")
