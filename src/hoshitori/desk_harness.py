"""The Desk on recordings: the same run, read from recorded answers, written to a throwaway ledger and never attested or scored.

The recordings are RYO's answers as they were first received. They are loaded byte for byte (a body whose digest no longer matches its file
is refused), served through the recorded transport, which marks every capture it makes as a replay, and read by the Desk under the recorded
binding, so every item says ``recorded`` and every record says so too. The clock is a manual clock set to the moment of the recordings, so
nothing here depends on today. The proposer is a rule that proposes nothing (a flat position): this harness shows the run and its records,
and claims no skill. Nothing it writes is time-stamped by an authority or settled, and the ledger it is given is meant to be thrown away.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from typing import Any, Final

from hoshitori import canonical, desk, desk_acquire, desk_gates, desk_menu, desk_plan, desk_record
from hoshitori.capture import CaptureStore
from hoshitori.clock import Clock, ManualClock
from hoshitori.connector import ConnectorError
from hoshitori.gateway import Call, CallResult, Outcome, TransportFailure
from hoshitori.ledger import Ledger
from hoshitori.lineage import EvidenceStore
from hoshitori.registry import Registry
from hoshitori.ryo_pin import PIN
from hoshitori.ryo_transport import Recorded, RecordedTransport, Recordings
from hoshitori.timeline import to_ms
from hoshitori.timeutil import parse_rfc3339

LABEL: Final = "recorded"
ASSET_PREFIX: Final = "asset:recorded-"
SOURCE_PREFIX: Final = "urn:hoshitori:desk-recorded:"
SYNTHETIC_LABEL: Final = "synthetic"
SYNTHETIC_SOURCE_PREFIX: Final = "urn:hoshitori:desk-synthetic:"
ASSET_TOOLS: Final = {"analyze_token": {}, "deep_analysis": {"include_perp": True}}  # extra arguments the Desk sends beside the symbol
CONTEXT_TOOLS: Final = ("market_overview", "monitor_market_sentiment_shift")
_TOOL = re.compile(r"/tools/([a-z_]+)/call")
NO_MODEL_DIGEST: Final = hashlib.sha256(b"").hexdigest()


class HarnessError(ValueError):
    """Recordings that cannot be used, or a run the harness will not make."""


def _dump(value: Any) -> bytes:
    """How RYO writes JSON, as read from the recordings: compact separators, non-ASCII kept."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


@dataclass(frozen=True)
class RyoRecordings:
    recordings: Recordings
    captured_at: datetime  # the latest moment any of them was captured

    def tools_for(self, symbol: str) -> frozenset[str]:
        """The tools the recordings can answer for a symbol, the way the Desk would ask them."""
        found = {t for t, extra in ASSET_TOOLS.items() if self.recordings.lookup(t, {"symbol": symbol, **extra}) is not None}
        return frozenset(found | {t for t in CONTEXT_TOOLS if self.recordings.lookup(t, {}) is not None})


def load_ryo_recordings(directory: Path) -> RyoRecordings:
    """The good tool calls among the recordings of a folder, each as the bytes it first arrived as."""
    recordings = Recordings()
    latest: datetime | None = None
    files = sorted(directory.glob("call_*.json")) if directory.is_dir() else []
    for path in files:
        data = json.loads(path.read_text())
        response, request = data["response"], data["request"]
        if response["status"] != 200:
            continue
        tool = _TOOL.fullmatch(request["path"])
        if tool is None or not isinstance(request.get("body"), dict):
            continue
        raw = _dump(response["body"])
        if hashlib.sha256(raw).hexdigest() != response["body_sha256"]:
            raise HarnessError(f"{path.name}: the recording no longer reproduces its bytes")
        recordings.add(
            tool.group(1),
            request["body"],
            Recorded(200, {str(k).lower(): str(v) for k, v in response.get("headers", {}).items()}, raw, None, path.stem),
        )
        moment = parse_rfc3339(data["captured_at"])
        latest = moment if latest is None or moment > latest else latest
    if latest is None:
        raise HarnessError(f"there are no recordings of tool calls in {directory}")
    return RyoRecordings(recordings, latest)


def menu_for(symbol: str, loaded: RyoRecordings) -> desk_menu.Menu:
    """The menu a run on these recordings has: the RYO checks they can answer, and no exchange."""
    return desk_menu.build_menu(
        ASSET_PREFIX + symbol.lower(), symbol, {}, loaded.tools_for(symbol) & frozenset(PIN), desk_menu.CostTable.from_latencies({})
    )


class _RecordedRunner:
    """Takes a call and answers it from the recordings through the recorded transport; never touches a network."""

    def __init__(self, transport: RecordedTransport, captures: CaptureStore, clock: Clock) -> None:
        self.transport, self.captures, self.clock = transport, captures, clock
        self.n = 0

    def submit(self, call: Call) -> Future[CallResult]:
        self.n += 1
        future: Future[CallResult] = Future()
        capture_id = f"cap-{self.n:032x}"
        now = self.clock.now()
        try:
            capture = self.transport.fetch(call.operation, call.args, 1.0, capture_id, now)
        except TransportFailure as exc:
            error = ConnectorError(exc.error_class, exc.detail)
            future.set_result(CallResult(self.n, Outcome.FAILED, None, 1, error, "NOT_RETRYABLE", 0.0, now))
            return future
        self.captures.append(capture)
        future.set_result(CallResult(self.n, Outcome.OK, capture_id, 1, None, "", 0.0, now))
        return future


def _digest(text: bytes) -> str:
    return hashlib.sha256(text).hexdigest()


def run_recorded(loaded: RyoRecordings, ledger: Ledger, trigger: desk_plan.Trigger, run_id: str, label: str = LABEL) -> desk.RunOutcome:
    """One Desk run on the recordings, its records submitted to ``ledger`` (which the caller means to throw away).

    ``label`` is what the records say they are: ``recorded`` for answers once read from a source, ``synthetic`` for answers invented for an
    example. Either way the answers are read through the recorded path, and nothing is committed."""
    if label not in (LABEL, SYNTHETIC_LABEL):
        raise HarnessError(f"a run on recordings is labelled {LABEL} or {SYNTHETIC_LABEL}")
    clock = ManualClock(loaded.captured_at)
    menu = menu_for(trigger.symbol, loaded)
    source = (SOURCE_PREFIX if label == LABEL else SYNTHETIC_SOURCE_PREFIX) + run_id
    with tempfile.TemporaryDirectory() as scratch:
        captures = CaptureStore(Path(scratch) / "captures")
        env = desk_acquire.DeskEnv(
            _RecordedRunner(RecordedTransport(loaded.recordings, clock.now), captures, clock),
            captures,
            {},
            Registry(),
            EvidenceStore(),
            clock,
            "recorded",
            1.0,
        )

        def sink(kind: str, body: dict[str, canonical.JsonValue]) -> None:
            ledger.submit(desk_record.to_record(kind, body, source, to_ms(clock.now())))

        def propose(request: desk.ProposeRequest) -> desk.Proposed:
            """No skill is claimed: the harness holds a flat position whatever the evidence says."""
            return desk.Proposed(desk_gates.Proposal("FLAT", Decimal("0.5"), Decimal(0)), (), NO_MODEL_DIGEST, 0)

        ports = desk.Ports(
            acquire=partial(desk_acquire.acquire, env),
            store=env.store,
            history=lambda ctx: None,
            propose=propose,
            grounded=lambda proposed, ids: set(proposed.cites) <= set(ids),
            stale=lambda ctx: False,  # the clock stands at the recordings' own moment
            room=lambda ctx: Decimal("0.05"),
            commit=lambda request: f"{label}-{request.run_id}",  # names no forecast: a run on recordings commits nothing
            sink=sink,
            calibration=None,
            clock=clock,
        )
        versions = desk.Versions(_digest(b"desk-recorded-harness"), _digest(Path(desk.__file__).read_bytes()), _digest(b"no-protocol"))
        budget = desk.Budget({"ryo": 10}, 3600, 1)
        outcome = desk.run(ports, trigger, menu, budget, run_id, desk.Binding(label), versions)
    ledger.append_pending()
    return outcome
