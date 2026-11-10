"""A small world for the bundle verifier: a real ledger, real time-stamp tokens from two stand-in authorities, a trusted root
that pins them, and an exported bundle. Cases copy the bundle and damage one thing."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from hoshitori import bundle, canonical, cycle, envelope, lifecycle, settlement, settlement_record  # noqa: F401
from hoshitori.clock import ManualClock
from hoshitori.league import batch_root
from hoshitori.ledger import Ledger, strip_committed_at
from hoshitori.note import split_note
from hoshitori.timeutil import format_rfc3339_ms, parse_rfc3339
from hoshitori.trusted_root import TrustedRoot, parse_root
from tests import root_support as R
from tests import tsa_pki as P
from tests.ledger_checks import START

SOURCE = "urn:hoshitori:test"
RAW_PAYLOAD = b'{"price":"12.5","note":"as received, never canonicalised"}'
RAW_HASH = hashlib.sha256(RAW_PAYLOAD).hexdigest()


def evidence_record(
    *, data_mode: str = "live", raw_hash: str = RAW_HASH, body_extra: dict[str, canonical.JsonValue] | None = None
) -> dict[str, canonical.JsonValue]:
    return {
        "spec": "hoshitori.record/1",
        "type": "evidence",
        "type_version": 1,
        "id": "ev1",
        "source": SOURCE,
        "created_at": "2026-10-29T11:59:00.000Z",
        "body": {"data_mode": data_mode, "raw_sha256": raw_hash, "tool": "analyze_token", **(body_extra or {})},
    }


SETTLEMENTS: dict[str, tuple[settlement.State, str | None, str | None, str | None, str | None]] = {
    # key: state, outcome, reason, p0, outcome price. The directional rule: an outcome of 1 is a price above the first, 0 below it, a tie is void.
    "up": (settlement.State.SETTLED, "1", None, "100", "101"),
    "down": (settlement.State.SETTLED, "0", None, "100", "99"),
    "wrong_up": (settlement.State.SETTLED, "0", None, "100", "101"),
    "wrong_down": (settlement.State.SETTLED, "1", None, "100", "99"),
    "tie_settled": (settlement.State.SETTLED, "0", None, "100", "100"),
    "tie": (settlement.State.VOID, None, "VOID_TIE", "100", "100"),
    "tie_wrong": (settlement.State.VOID, None, "VOID_TIE", "100", "101"),
    "void_gap": (settlement.State.VOID, None, "VOID_GAP", None, None),
}
DIRECTIONAL: dict[str, canonical.JsonValue] = {"contract": "D", "horizon_ms": 3_600_000, "oracle_pair": "A"}
BARRIER_FORECAST: dict[str, canonical.JsonValue] = {"contract": "B", "horizon_ms": 3_600_000, "oracle_pair": "A"}
# the settlements of a barrier call: state, outcome, reason, first price, outcome price, and the touch minute as an offset from the origin
# faults: the valid record they start from, and the edit made to its body once it is written (a writer refuses to write them)
BARRIER_FAULTS: dict[str, tuple[str, str, int | str | None]] = {
    "b_timeout_touch": ("b_timeout", "touch_open_ms", 120_000),
    "b_target_no_touch": ("b_target", "touch_open_ms", None),
    "b_touch_at_end": ("b_target", "touch_open_ms", 3_600_000),
    "b_touch_before": ("b_stop", "touch_open_ms", -60_000),
    "b_touch_unaligned": ("b_target", "touch_open_ms", 120_001),
    "b_no_p0": ("b_target", "p0", None),
    "b_other_outcome": ("b_target", "outcome", "WIN"),
    "b_with_price": ("b_target", "outcome_price", "101"),
}
BARRIER: dict[str, tuple[settlement.State, str | None, str | None, str | None, str | None, int | None]] = {
    "b_target": (settlement.State.SETTLED, "TARGET", None, "100", None, 120_000),
    "b_stop": (settlement.State.SETTLED, "STOP", None, "100", None, 600_000),
    "b_timeout": (settlement.State.SETTLED, "TIMEOUT", None, "100", None, None),
}


def settlement_for(kind: str, forecast_id: str = "fc1", origin_ms: int = 0) -> settlement.Settlement:
    contract, touch = "D", None
    if kind in BARRIER:
        state, outcome, reason, p0, price, offset = BARRIER[kind]
        contract, touch = "B", None if offset is None else origin_ms + offset
    else:
        state, outcome, reason, p0, price = SETTLEMENTS[kind]
    return settlement.Settlement(
        settlement._result(
            forecast_id,
            contract,
            "A",
            state,
            outcome,
            reason,
            None if p0 is None else Decimal(p0),
            None if price is None else Decimal(price),
            touch,
            (),
        ).settlement_id,
        forecast_id,
        contract,
        "A",
        settlement.RULE_VERSION,
        state,
        outcome,
        reason,
        p0,
        price,
        touch,
        (),
    )


def forecast_record(
    opens_at: str, refs: list[dict[str, canonical.JsonValue]], body_extra: dict[str, canonical.JsonValue] | None = None
) -> dict[str, canonical.JsonValue]:
    return {
        "spec": "hoshitori.record/1",
        "type": "forecast",
        "type_version": 1,
        "id": "fc1",
        "source": SOURCE,
        "created_at": "2026-10-29T11:59:30.000Z",
        "body": {"horizon": "24h", "outcome_window_opens_at": opens_at, **(body_extra or {})},
        "refs": refs,  # type: ignore[dict-item]
    }


@dataclass
class World:
    directory: Path
    ledger: Ledger
    root: TrustedRoot
    chain_a: P.Chain
    chain_b: P.Chain
    note: bytes
    note_text: bytes
    seq_evidence: int
    seq_forecast: int
    forecast_committed_at: str
    bundle_dir: Path
    seq_committing: int | None = None  # the cycle's committing step, the last record of the batch, when the world has one
    stamped_size: int = 0  # the size of the checkpoint the tokens stamp
    later_note: bytes | None = None  # the signed note of the later checkpoint, when the world has a later part
    later_seqs: tuple[int, ...] = ()  # the records the later part proves
    extra: dict[str, object] = field(default_factory=dict)

    def token(self, chain: P.Chain, offset_s: float, *, digest: bytes | None = None, **options: object) -> bytes:
        moment = parse_rfc3339(self.forecast_committed_at) + timedelta(seconds=offset_s)
        wanted = digest if digest is not None else hashlib.sha256(self.note_text).digest()
        return P.build_token(wanted, chain, P.TokenOptions(gen_time=moment, nonce=None, **options))  # type: ignore[arg-type]

    def copy_bundle(self, name: str) -> Path:
        target = self.directory / f"case-{name}"
        shutil.copytree(self.bundle_dir, target)
        return target


def make_world(
    directory: Path,
    *,
    eras: list[dict[str, object]] | None = None,
    data_mode: str = "live",
    gap_s: int = 0,
    window_s: int = 300,
    tokens: bool = True,
    ref_hash_override: str | None = None,
    log_seed: int = 7,
    tokens_offsets: tuple[float, float] = (1, 3),
    opens_at_override: str | None = None,
    second_forecast_gap_s: int | None = None,
    evidence_without_mode: bool = False,
    second_live_evidence: bool = False,
    body_extra: dict[str, canonical.JsonValue] | None = None,
    second_body_extra: dict[str, canonical.JsonValue] | None = None,
    evidence_body_extra: dict[str, canonical.JsonValue] | None = None,
    cycle_batch: bool = True,
    batch_root_override: str | None = None,
    batch_size_override: int | None = None,
    committing_body: dict[str, canonical.JsonValue] | None = None,
    second_committing: bool = False,
    forecast: bool = True,
    later: bool = False,
    later_eligible_delta_ms: int = 0,
    later_open: bool = False,
    later_origin_delta_ms: int = 0,
    later_ending: bool = False,
    later_skip_awaiting: bool = False,
    later_duplicate: str | None = None,  # a state whose step is written twice: COMMITTED, AWAITING_ORIGIN, OPEN or SETTLED
    later_bad_eligible: bool = False,  # the waiting step names an eligible time that is not a time
    later_settlement: str
    | None = None,  # the settlement record written with the ending step: a key of SETTLEMENTS, or one of the faults below
    later_due_after_s: float | None = None,  # seconds after the call's window ends that the settlement is written; none: a minute after
) -> World:
    directory.mkdir(parents=True, exist_ok=True)
    chain_a, chain_b = P.Chain.build("Authority A"), P.Chain.build("Authority B")
    log_signer = R.log_signer(log_seed)
    clock = ManualClock(START)
    ledger = Ledger.open(directory / "ledger", signer=log_signer, origin=R.LOG_ORIGIN, clock=clock, segment_records=100)
    ledger.submit(evidence_record(data_mode=data_mode, body_extra=evidence_body_extra))
    ledger.append_pending()
    seq_evidence = ledger.find(SOURCE, "ev1")
    assert seq_evidence is not None
    clock.advance(timedelta(seconds=gap_s))
    opens_at = opens_at_override or format_rfc3339_ms(START + timedelta(seconds=gap_s + window_s))
    evidence_bytes = ledger.read_record(seq_evidence)
    ref_hash = ref_hash_override or hashlib.sha256(strip_committed_at(evidence_bytes)).hexdigest()
    seq_extra: list[int] = []
    if evidence_without_mode:
        ledger.submit({**evidence_record(), "id": "ev2", "body": {"tool": "analyze_token"}})
        ledger.append_pending()
        extra_seq = ledger.find(SOURCE, "ev2")
        assert extra_seq is not None
        seq_extra.append(extra_seq)
    refs: list[dict[str, canonical.JsonValue]] = [{"id": "ev1", "sha256": ref_hash}]
    if second_live_evidence:  # a second labelled observation that the forecast cites too
        ledger.submit({**evidence_record(), "id": "ev2"})
        ledger.append_pending()
        second = ledger.find(SOURCE, "ev2")
        assert second is not None
        seq_extra.append(second)
        refs.append({"id": "ev2", "sha256": hashlib.sha256(strip_committed_at(ledger.read_record(second))).hexdigest()})
    if forecast:
        ledger.submit(forecast_record(opens_at, refs, body_extra))
        ledger.append_pending()
    seq_forecast = ledger.find(SOURCE, "fc1") if forecast else seq_evidence  # a cycle that made no call has none
    assert seq_forecast is not None
    last_forecast = seq_forecast
    if second_forecast_gap_s is not None:
        clock.advance(timedelta(seconds=second_forecast_gap_s))
        later_opens = format_rfc3339_ms(START + timedelta(seconds=gap_s + second_forecast_gap_s + window_s))
        ledger.submit({**forecast_record(later_opens, [{"id": "ev1", "sha256": ref_hash}], second_body_extra), "id": "fc2"})
        ledger.append_pending()
        found = ledger.find(SOURCE, "fc2")
        assert found is not None
        last_forecast = found
        seq_extra.append(found)
    committed = str(canonical.parse_record(ledger.read_record(last_forecast))["committed_at"])
    seq_committing: int | None = None
    if cycle_batch:  # the cycle's committing step is the last record of the batch, and names the records before it
        batch = [canonical.parse_record(strip_committed_at(ledger.read_record(s))) for s in range(0, ledger.size)]
        step = cycle.Step(
            cycle.State.COMMITTING,
            1,
            batch_root=batch_root_override or batch_root(batch),
            batch_size=batch_size_override or len(batch),
        )
        record = cycle.to_record(cycle.Transition(cycle.cycle_id("a" * 64, 60_000), 6, step), SOURCE, 1)
        if committing_body is not None:  # a hostile writer: members the cycle module itself would refuse
            record["body"] = {**record["body"], **committing_body}  # type: ignore[dict-item]
        ledger.submit(record)
        ledger.append_pending()
        seq_committing = ledger.size - 1
        if second_committing:  # a second cycle's step in the same log: a bundle shows one cycle
            second = cycle.Step(
                cycle.State.COMMITTING,
                2,
                batch_root=batch_root([canonical.parse_record(strip_committed_at(ledger.read_record(seq_committing)))]),
                batch_size=1,
            )
            ledger.submit(cycle.to_record(cycle.Transition(cycle.cycle_id("b" * 64, 120_000), 6, second), SOURCE, 2))
            ledger.append_pending()
            seq_extra.append(ledger.size - 1)
    note = ledger.checkpoint()
    note_text, _ = split_note(note)
    stamped_size = ledger.size
    later_note: bytes | None = None
    later_seqs: tuple[int, ...] = ()
    if later:  # what the log appended after the batch: the forecast's later steps, under a later checkpoint
        committed_ms = int(parse_rfc3339(committed).timestamp() * 1000)
        attested_ms = committed_ms + int(tokens_offsets[1] * 1000)
        spread_ms = int((tokens_offsets[1] - tokens_offsets[0]) * 1000)
        eligible_ms = attested_ms + 5000 + spread_ms + later_eligible_delta_ms
        steps = [
            lifecycle.Step(lifecycle.State.COMMITTED, committed_ms + 100, level=lifecycle.Level.C3, committed_at_ms=committed_ms),
            lifecycle.Step(
                lifecycle.State.AWAITING_ORIGIN,
                committed_ms + 100,
                eligible_after_ms=eligible_ms,
                attested_at_ms=attested_ms,
                uncertainty_ms=5000,
                spread_ms=spread_ms,
            ),
        ]
        origin_ms = (eligible_ms // 60_000 + 1) * 60_000 + later_origin_delta_ms
        if later_open:
            steps.append(lifecycle.Step(lifecycle.State.OPEN, origin_ms, origin_ms=origin_ms))
        kind = later_settlement if later_settlement is not None else ("up" if later_ending else None)
        base_kind = BARRIER_FAULTS[kind][0] if kind in BARRIER_FAULTS else kind
        made = (
            settlement_for(base_kind if base_kind in SETTLEMENTS or base_kind in BARRIER else "up", origin_ms=origin_ms)
            if kind is not None
            else None
        )
        if later_ending:
            ending_state = made.state if made is not None else lifecycle.State.SETTLED
            cited = "st-other" if kind == "id_mismatch" else "st1" if made is None else made.settlement_id
            steps.append(lifecycle.Step(ending_state, origin_ms + 3_600_000, settlement_id=cited))
        if later_skip_awaiting:
            steps = [s for s in steps if s.state is not lifecycle.State.AWAITING_ORIGIN]
        if later_duplicate is not None:
            steps += [s for s in steps if s.state.value == later_duplicate]
        first_later = ledger.size
        for number, step in enumerate(steps, start=5):
            step_record = lifecycle.to_record(lifecycle.Transition("fc1", number, step), SOURCE, number)
            if later_bad_eligible and step.state is lifecycle.State.AWAITING_ORIGIN:
                step_record["body"] = {**step_record["body"], "eligible_after": "soon"}  # type: ignore[dict-item]
            ledger.submit(step_record)
            ledger.append_pending()
        if (
            made is not None and kind != "absent"
        ):  # the settlement is written when the call's window has ended, as the pass that ends the call does
            due_ms = origin_ms + 3_600_000 + int(1000 * (60 if later_due_after_s is None else later_due_after_s))
            if later_due_after_s != -1:  # -1: written at once, before the window is over
                clock.advance(timedelta(milliseconds=max(0, due_ms - int(clock.now().timestamp() * 1000))))
            digest = hashlib.sha256(strip_committed_at(ledger.read_record(seq_forecast))).hexdigest()
            record = settlement_record.to_record(made, digest, SOURCE, 1)
            if kind == "malformed":
                record["body"] = {k: v for k, v in record["body"].items() if k != "outcome"}  # type: ignore[union-attr]
            if kind == "bad_ref":
                record["refs"] = [{"id": "fc1", "sha256": "0" * 64}]
            if kind == "ref_other":  # names a record in the bundle with its right digest, not the forecast
                record["refs"] = [{"id": "ev1", "sha256": ref_hash}]
            edits = {
                "bad_state": {"state": "PENDING"},
                "wrong_contract": {"contract": "B"},
                "no_prices": {"p0": None},
                "zero_price": {"p0": "0"},
                "number_outcome": {"outcome": 1},
                "number_contract": {"contract": 5},
                "extra_member": {"note": "x"},
            }
            if kind in BARRIER_FAULTS:
                _, member, value = BARRIER_FAULTS[kind]
                record["body"] = {
                    **record["body"],
                    member: origin_ms + value if member == "touch_open_ms" and isinstance(value, int) else value,
                }  # type: ignore[dict-item]
            if kind in edits:
                record["body"] = {**record["body"], **edits[kind]}  # type: ignore[dict-item]
            ledger.submit(record)
            ledger.append_pending()
            if kind == "conflicting":
                ledger.submit({**record, "id": "st-second"})
                ledger.append_pending()
        later_seqs = tuple(range(first_later, ledger.size))
        later_note = ledger.checkpoint()
    document = R.make_document(
        1,
        root_keys=[R.new_signer(1)],
        eras=eras or [{"vkey": log_signer.verifier.vkey(), "from_size": 0, "through_size": None}],
        pinned=[(chain_a.root, "operator-a"), (chain_b.root, "operator-b")],
    )
    root = parse_root(canonical.dumps(document))  # type: ignore[arg-type]
    world = World(
        directory,
        ledger,
        root,
        chain_a,
        chain_b,
        note,
        note_text,
        seq_evidence,
        seq_forecast,
        committed,
        directory / "bundle",
        seq_committing,
        stamped_size,
        later_note,
        later_seqs,
    )
    world.extra["root_document"] = canonical.dumps(document)  # type: ignore[arg-type]
    produced = {"a": world.token(chain_a, tokens_offsets[0]), "b": world.token(chain_b, tokens_offsets[1])} if tokens else {}
    bundle.export_bundle(
        world.bundle_dir,
        ledger,
        sorted([seq_evidence, seq_forecast, *seq_extra, *([] if seq_committing is None else [seq_committing])]),
        checkpoint_note=note,
        size=stamped_size,
        later=None if later_note is None else bundle.Later(later_note, ledger.size, later_seqs),
        tokens=produced,
        raw={RAW_HASH: RAW_PAYLOAD},
        publication={"claims": [{"url": "https://example.org/post", "sha256": "0" * 64}]},
    )
    return world
