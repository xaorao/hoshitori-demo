"""Checks of the batch commitment (``hoshitori.commit``), runnable against a mutant. The ledger is real; the authorities are replaced by an
attestor that builds the verdict of ``timing.assess`` from made-up verified tokens placed relative to the commit time it is given."""

from __future__ import annotations

import hashlib
import itertools
import json
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from hoshitori import canonical
from hoshitori.attest import Attestation, Stamp
from hoshitori.clock import ManualClock
from hoshitori.ids import natural_id
from hoshitori.ledger import Ledger
from hoshitori.timeline import from_ms
from hoshitori.timing import TokenEvidence, assess
from hoshitori.tsa import VerifiedToken
from tests import root_support as R
from tests.timing_checks import fp, make_rules, operator

START = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)
SOURCE = "urn:hoshitori:cycle:test"
A, B, C = operator("a"), operator("b"), operator("c")
RULES = make_rules([A, B, C])


def record(n: int, kind: str = "forecast") -> dict[str, canonical.JsonValue]:
    return {
        "spec": "hoshitori.record/1",
        "type": kind,
        "type_version": 1,
        "id": f"{kind}-{n}",
        "source": SOURCE,
        "created_at": "2026-10-29T11:59:00.000Z",
        "body": {"n": n},
    }


class TickClock(ManualClock):
    """A clock that moves ``step`` seconds every time it is read, so each record of a batch has its own commit time."""

    def __init__(self, start: datetime, step: float) -> None:
        super().__init__(start)
        self.step = step

    def now(self) -> datetime:
        moment = super().now()
        self.set(moment + timedelta(seconds=self.step))
        return moment


def verified(operator_name: str, name: str, at_ms: int) -> TokenEvidence:
    return TokenEvidence(
        name,
        VerifiedToken(from_ms(at_ms), "1.2.3", 1, None, None, fp(f"root-{operator_name}"), fp(f"signer-{name}"), operator_name, ()),
    )


def attestor(offsets: list[tuple[str, str, float]], seen: list[tuple[bytes, int]] | None = None) -> Callable[[bytes, int], Attestation]:
    """An attestor whose tokens are ``(operator, name, seconds after the latest commit)``."""

    def attest(digest: bytes, committed_at_ms: int) -> Attestation:
        if seen is not None:
            seen.append((digest, committed_at_ms))
        evidence = tuple(verified(op, name, committed_at_ms + round(after * 1000)) for op, name, after in offsets)
        stamps = tuple(Stamp(name, 1, b"\x30\x00") for _, name, _ in offsets)
        timing = assess(from_ms(committed_at_ms), None, evidence, RULES)
        return Attestation(digest, committed_at_ms, committed_at_ms + 90_000, stamps, (), (), timing, 5_000, evidence)

    return attest


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str) -> None:
        try:
            call()
        except self.mod.CommitError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def ledger_in(root: Path, clock: ManualClock) -> Ledger:
    return Ledger.open(root / "ledger", signer=R.log_signer(7), origin=R.LOG_ORIGIN, clock=clock, segment_records=100)


def attested_lines(root: Path) -> list[Any]:
    path = root / "ledger" / "attested.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("limit", mod.MAX_RECORDS, 5000)
    for cls in (mod.Committed, mod.Commitment):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_batch(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    clock = ManualClock(START)
    ledger = ledger_in(root, clock)
    seen: list[tuple[bytes, int]] = []
    records = [record(1, "snapshot"), record(2), record(3), record(4)]
    got = mod.commit_batch(ledger, records, attestor=attestor([("a", "ta", 1), ("b", "tb", 3)], seen), rules=RULES)
    c.eq(
        "four records, in the order given, at consecutive places",
        [(r.source, r.record_id, r.seq) for r in got.records],
        [(SOURCE, "snapshot-1", 0), (SOURCE, "forecast-2", 1), (SOURCE, "forecast-3", 2), (SOURCE, "forecast-4", 3)],
    )
    c.eq("they were committed when the ledger's clock said", {r.committed_at_ms for r in got.records}, {int(START.timestamp() * 1000)})
    c.eq("every record reached C3", [r.level for r in got.records], ["C3"] * 4)
    c.eq("the batch is uniform and C3", (got.uniform, got.level), (True, "C3"))
    c.eq("the checkpoint is the ledger's, over all four", (got.size, got.root, got.note), (4, ledger.root(), ledger.checkpoint()))
    text = got.note.split(b"\n\n")[0] + b"\n"
    c.eq(
        "what was sent to be stamped is the digest of the checkpoint text and the latest commit time",
        seen,
        [(hashlib.sha256(text).digest(), int(START.timestamp() * 1000))],
    )
    c.eq("the commitment has an id from its root and size", got.commitment_id, natural_id("cm", {"root": got.root.hex(), "size": 4}))
    c.eq("the origin is the attestation's", got.origin_ms, got.attestation.origin_ms)
    c.eq("a record's level by its key", (got.level_of(SOURCE, "forecast-3"), got.level_of(SOURCE, "snapshot-1")), ("C3", "C3"))
    try:
        got.level_of(SOURCE, "nothing")
    except KeyError:
        pass
    else:
        c.bad.append("an unknown record: was accepted")
    lines = attested_lines(root)
    c.eq(
        "an attested checkpoint was remembered by the ledger, with who stamped it",
        [(x["size"], x["root"], x["evidence"]) for x in lines],
        [(4, got.root.hex(), "rfc3161:ta,tb")],
    )
    ledger.close()
    return c.bad


def check_names(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    ledger = ledger_in(root, ManualClock(START))
    got = mod.commit_batch(ledger, [record(1)], attestor=attestor([("a", "tz", 1), ("b", "ta", 3)]), rules=RULES)
    c.eq(
        "the authorities that carried the level are listed in name order, not arrival order",
        [x["evidence"] for x in attested_lines(root)],
        ["rfc3161:ta,tz"],
    )
    c.eq("and the level is C3", got.level, "C3")
    ledger.close()
    return c.bad


def check_low_level(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    ledger = ledger_in(root, ManualClock(START))
    got = mod.commit_batch(ledger, [record(1), record(2)], attestor=attestor([("a", "ta", 1)]), rules=RULES)
    c.eq(
        "one operator is not enough: every record is C1",
        ([r.level for r in got.records], got.uniform, got.level),
        (["C1", "C1"], True, "C1"),
    )
    c.eq("and nothing is registered as attested", attested_lines(root), [])
    c.eq("with no token counted the origin follows the commit time", got.origin_ms > int(START.timestamp() * 1000) - 1, True)
    ledger.close()
    return c.bad


def check_spread(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    ledger = ledger_in(root, TickClock(START, 40.0))
    got = mod.commit_batch(ledger, [record(1), record(2), record(3)], attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES)
    times = [r.committed_at_ms for r in got.records]
    c.eq("the records were committed forty seconds apart", [b - a for a, b in itertools.pairwise(times)], [40_000, 40_000])
    c.eq(
        "tokens 1 and 3 seconds after the latest commit are inside every record's 60 second window except the first",
        [r.level for r in got.records],
        ["C1", "C3", "C3"],
    )
    c.eq("so the batch is not uniform and its level is the lowest", (got.uniform, got.level), (False, "C1"))
    c.eq(
        "the attestation itself reached C3 (it is judged at the latest commit time) and the checkpoint is remembered",
        (got.attestation.level, len(attested_lines(root))),
        ("C3", 1),
    )
    ledger.close()
    return c.bad


def check_validation(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    ledger = ledger_in(root, ManualClock(START))
    yes = attestor([("a", "ta", 1), ("b", "tb", 3)])
    says = "a batch has from 1 to 5000 records"
    c.refuses("an empty batch", lambda: mod.commit_batch(ledger, [], attestor=yes, rules=RULES), says)
    c.refuses("5001 records", lambda: mod.commit_batch(ledger, [record(n) for n in range(5001)], attestor=yes, rules=RULES), says)
    c.refuses(
        "a record twice", lambda: mod.commit_batch(ledger, [record(1), record(1)], attestor=yes, rules=RULES), "a record is in a batch once"
    )
    c.eq("nothing of a refused batch is in the ledger", ledger.size, 0)
    limit = mod.MAX_RECORDS
    mod.MAX_RECORDS = 6  # the limit is applied where the batch is checked, so a small one shows its edge without 5000 appends
    try:
        c.refuses(
            "one more than the limit",
            lambda: mod.commit_batch(ledger, [record(n) for n in range(7)], attestor=yes, rules=RULES),
            "a batch has from 1 to 6 records",
        )
        edge = mod.commit_batch(ledger, [record(n, "edge") for n in range(6)], attestor=yes, rules=RULES)
        c.eq("exactly the limit is fine", len(edge.records), 6)
    finally:
        mod.MAX_RECORDS = limit
    one = mod.commit_batch(ledger, [record(9)], attestor=yes, rules=RULES)
    c.eq("a batch of one is fine", (one.size, [r.level for r in one.records]), (7, ["C3"]))
    again = mod.commit_batch(ledger, [record(9)], attestor=yes, rules=RULES)
    c.eq("submitting the same record again returns it where it is", (again.size, again.records[0].seq), (7, 6))
    key = (SOURCE, "forecast-9")
    stamped = mod.attest_appended(ledger, [key], attestor=yes, rules=RULES)
    c.eq(
        "records already in the ledger are stamped without being appended",
        (stamped.size, ledger.size, [r.level for r in stamped.records]),
        (7, 7, ["C3"]),
    )
    c.eq(
        "and the stamp covers the record where it is",
        [(r.source, r.record_id, r.seq) for r in stamped.records],
        [(SOURCE, "forecast-9", 6)],
    )
    c.refuses("stamping nothing", lambda: mod.attest_appended(ledger, [], attestor=yes, rules=RULES), says)
    c.refuses(
        "stamping a record that is not there",
        lambda: mod.attest_appended(ledger, [(SOURCE, "nothing")], attestor=yes, rules=RULES),
        "a record to stamp is not in the ledger",
    )
    c.refuses(
        "stamping one that is there and one that is not",
        lambda: mod.attest_appended(ledger, [key, (SOURCE, "nothing")], attestor=yes, rules=RULES),
        "a record to stamp is not in the ledger",
    )
    mod.MAX_RECORDS = 1
    try:
        c.refuses(
            "stamping more than the limit",
            lambda: mod.attest_appended(ledger, [key, (SOURCE, "nothing")], attestor=yes, rules=RULES),
            "a batch has from 1 to 1 records",
        )
    finally:
        mod.MAX_RECORDS = limit
    ledger.close()
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_batch, check_names, check_low_level, check_spread, check_validation):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
