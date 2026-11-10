"""An on-demand forecast never enters a league table: it lives in its own ledger, and if its records were fed to the league's views anyway
the views refuse them instead of mixing them (plan J-01, decision 1 and 6)."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest

from hoshitori import canonical, ondemand, view_models
from hoshitori.timeline import to_ms
from tests.commit_checks import RULES, attestor, ledger_in
from tests.ondemand_checks import _world_context, long
from tests.settle_job_checks import Rig


def _records(ledger: Any) -> list[dict[str, Any]]:
    return [canonical.parse_record(ledger.read_record(seq)) for seq in range(ledger.size)]


def test_league_views_hold_no_on_demand_forecast_and_refuse_one_fed_to_them() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        rig = Rig(Path(tmp) / "world")
        now = to_ms(rig.w.clock.now())
        od = ledger_in(Path(tmp) / "od", rig.w.clock)
        sub = ondemand.submission_from_desk(long(), "run-x", _world_context(ondemand, rig, "BTC", now), committed_at_ms=now)
        ondemand.commit(sub, ledger=od, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES, at_ms=now)
        league = _records(rig.w.ledger)
        own = _records(od)
        assert own and all(r["source"].startswith(ondemand.SOURCE_PREFIX) for r in own)
        views = view_models.build(league, rig.w.protocol, now_ms=now)
        assert not any(row.cycle_id.startswith(ondemand.CYCLE_PREFIX) for row in views.rows)
        assert not any(c.cycle_id.startswith(ondemand.CYCLE_PREFIX) for c in views.cycles)
        with pytest.raises(view_models.ViewError):
            view_models.build([*league, *own], rig.w.protocol, now_ms=now)
        od.close()
