#!/usr/bin/env python3
"""One Desk call, from the run to an independently stamped commitment to a settlement (plan J-01).

    python scripts/ondemand_journey.py submit SETTINGS DIR SYMBOL:DIRECTION:HOURS[:MINUTES_AGO] [--proposer prior|model] [--ryo-calls N]
    python scripts/ondemand_journey.py direct SETTINGS DIR SYMBOL PROBABILITY HOURS
    python scripts/ondemand_journey.py settle SETTINGS DIR

``submit`` runs the Desk on live reads with its commit port bound to the on-demand ledger in ``DIR``: a long call is sealed, stamped by
the league's real time-stamp authorities and left to settle; a flat call is a reference and nothing is stamped. ``settle`` runs the league's
own settlement pass over that ledger once the horizon has passed. Use a copy of a league folder, never a league that is running. The RYO
key and, for ``--proposer model``, the model key are read from the environment variables the settings name. The default proposer is the
Desk's own written prior, which needs no model.
"""

from __future__ import annotations

import hashlib
import sys
import time
from decimal import Decimal
from pathlib import Path

from hoshitori import deployment, desk_gates, desk_live, desk_model, desk_plan, ondemand
from hoshitori.collect import now_ms
from hoshitori.ledger import Ledger
from hoshitori.note import Signer
from hoshitori.ryo_pin import PIN
from hoshitori.settle_job import SettleServices


def _open(running: deployment.Running, directory: Path, name: str) -> Ledger:
    signer = Signer.from_seed(running.root.origin, deployment.read_key(running.settings.log_key))
    return Ledger.open(directory / name, signer=signer, origin=running.root.origin, clock=running.services.env.clock)


def _ondemand(running: deployment.Running, directory: Path) -> Ledger:
    """The on-demand ledger. When the settings name one, it is the deployment's own, under its own key and origin (and is closed with the
    deployment); otherwise a ledger in ``directory`` is opened under the league's key, as before, and the settlement is not scheduled."""
    if running.ondemand is not None:
        return running.ondemand
    sys.stderr.write("these settings name no on-demand identity: the league's key signs this ledger and nothing settles it by itself\n")
    return _open(running, directory, "ondemand")


def _close(running: deployment.Running, ledger: Ledger) -> None:
    if ledger is not running.ondemand:
        ledger.close()


def submit(settings_path: Path, directory: Path, spec: str, proposer: str, ryo_calls: int) -> int:
    symbol, direction, hours, *ago = spec.split(":")
    settings = deployment.load_settings(settings_path)
    directory.mkdir(parents=True, exist_ok=True)
    with deployment.assemble(settings) as running:
        sv = running.services
        pick = None
        if proposer == "model":
            models = sv.models
            if models is None or models.l1 is None:
                sys.stderr.write("these settings configure no model\n")
                return 2
            pick = desk_model.make_proposer(
                models.client,
                models.l1.model,
                max_tokens=models.l1.max_tokens,
                thinking=models.l1.thinking,
                response_format=models.l1.response_format,
            )
        elif proposer == "prior":
            pick = desk_live.prior_proposal
        else:
            sys.stderr.write("the proposer is prior or model\n")
            return 2
        od = _ondemand(running, directory)
        desk_ledger = _open(running, directory, "desk")
        p = sv.protocol
        wiring = ondemand.Wiring(
            od,
            sv.attestor,
            sv.timing_rules,
            p.quote_class,
            p.instrument_kind,
            p.oracle_pair,
            p.primary_venue,
            p.second_venue,
            directory / "commitments",
        )
        stamp = format(int(time.time()) // 60, "x")
        event_ms = int(time.time() * 1000) - int(ago[0]) * 60_000 if ago else None
        trigger = desk_plan.Trigger(f"od-trg-{stamp}", "EVENT", symbol, direction, int(hours), event_ms)
        out = desk_live.run_live(
            sv.env,
            desk_ledger,
            trigger,
            f"od-run-{stamp}",
            frozenset(PIN),
            ryo_calls=ryo_calls,
            proposer=pick,
            tokens=8000 if proposer == "model" else 1,
            on_demand=wiring,
        )
        sys.stdout.write(f"state {out.state} reason {out.reason} forecast {out.forecast_id} proposer {proposer}\n")
        sys.stdout.write(f"on-demand ledger records: {od.size}\n")
        _close(running, od)
        desk_ledger.close()
    return 0


def direct(settings_path: Path, directory: Path, symbol: str, probability: str, hours: int) -> int:
    """A person's own forecast, submitted without a Desk run: the commitment and the settlement are the real ones, the evidence list is
    empty and the content of the forecast is the submitter's assertion (contract section 24, origin class SUBMITTER_ASSERTED)."""
    settings = deployment.load_settings(settings_path)
    directory.mkdir(parents=True, exist_ok=True)
    with deployment.assemble(settings) as running:
        sv = running.services
        registry, _ = desk_live.build_registry(sv.env)
        asset = desk_live.asset_for(registry, symbol)
        if asset is None:
            sys.stderr.write("the symbol is not one instrument on a venue of the league\n")
            return 2
        od = _ondemand(running, directory)
        p = sv.protocol
        now = now_ms(sv.env.clock)
        ctx = ondemand.Context(
            asset.asset_id,
            symbol,
            p.quote_class,
            p.instrument_kind,
            p.oracle_pair,
            p.primary_venue,
            p.second_venue,
            now,
            hours * 3_600_000,
            hashlib.sha256(b"direct-submission").hexdigest(),
            hashlib.sha256(b"no-protocol").hexdigest(),
            hashlib.sha256(b"no-evidence").hexdigest(),
            (),
        )
        sub = ondemand.submission_from_desk(
            desk_gates.FinalAction("LONG", Decimal(probability), Decimal(0)),
            f"direct-{format(now // 60000, 'x')}",
            ctx,
            committed_at_ms=now,
        )
        if not isinstance(sub, ondemand.Submission):
            return 2
        got = ondemand.commit(
            sub, ledger=od, attestor=sv.attestor, rules=sv.timing_rules, at_ms=now, store=directory / "commitments", registry=registry
        )
        sys.stdout.write(
            f"forecast {got.forecast_id} level {got.level} committed_at_ms {got.committed_at_ms} eligible_after_ms {got.eligible_after_ms} "
            f"origin_ms {got.origin_ms}\n"
        )
        _close(running, od)
    return 0


def settle(settings_path: Path, directory: Path) -> int:
    settings = deployment.load_settings(settings_path)
    with deployment.assemble(settings) as running:
        od = _ondemand(running, directory)
        services = SettleServices(running.services.protocol, running.services.env, ondemand.SOURCE_PREFIX + "settlement")
        passed = ondemand.settle(services, od)
        for record in passed.records:
            if record["type"] == "settlement":
                sys.stdout.write(f"settlement {record['body']}\n")
        sys.stdout.write(
            f"written {len(passed.records)} records; still waiting: {list(passed.pending)}; deferred: {list(passed.deferred)}\n"
        )
        _close(running, od)
    return 0


def main(argv: list[str]) -> int:
    if len(argv) >= 4 and argv[0] == "submit":
        proposer = argv[argv.index("--proposer") + 1] if "--proposer" in argv else "prior"
        ryo_calls = int(argv[argv.index("--ryo-calls") + 1]) if "--ryo-calls" in argv else 0
        return submit(Path(argv[1]), Path(argv[2]), argv[3], proposer, ryo_calls)
    if len(argv) == 6 and argv[0] == "direct":
        return direct(Path(argv[1]), Path(argv[2]), argv[3], argv[4], int(argv[5]))
    if len(argv) == 3 and argv[0] == "settle":
        return settle(Path(argv[1]), Path(argv[2]))
    sys.stderr.write(__doc__ or "")
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
