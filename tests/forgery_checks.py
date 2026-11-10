"""Checks of the forged copy of a bundle (``hoshitori.forgery``), runnable against a mutant.

The forger is someone who holds the log's real signing key. These checks make one from a test world: they change a forecast, rebuild every
hash, sign the new checkpoint with the real key and keep the original time-stamps, then ask the real verifier what it makes of the copy, and
ask what the process left untouched.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import types
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import bundle as layout
from hoshitori import canonical, verify
from hoshitori.checkpoint import parse_text
from hoshitori.clock import ManualClock
from hoshitori.ledger import Ledger
from hoshitori.note import Signer, split_note
from hoshitori.trusted_root import parse_root
from tests import bundle_support as B
from tests import root_support as R
from tests.verify_checks import Case as VerifyCase
from tests.verify_checks import verdict_of

WAS = "0.488462"


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
        except self.mod.ForgeryError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def tokens_of(world: B.World) -> dict[str, bytes]:
    return {path.stem: path.read_bytes() for path in (world.bundle_dir / layout.TSA).glob(f"*{layout.TOKEN_SUFFIX}")}


def standin(mod: types.ModuleType, cycle_id: str = "cy-test") -> Any:
    return mod.standin_signer(R.LOG_ORIGIN, cycle_id)


def forge(mod: types.ModuleType, world: B.World, root: Path, **changes: Any) -> Any:
    arguments: dict[str, Any] = {
        "signer": standin(mod),
        "origin": R.LOG_ORIGIN,
        "note": world.note,
        "tokens": tokens_of(world),
        "seqs": [world.seq_evidence, world.seq_forecast, world.seq_committing],
        "cycle_id": "cy-test",
        "work": root / "work",
        "out": root / "forged",
        **changes,
    }
    return mod.forge(world.ledger, **arguments)


def tree_of(directory: Path) -> dict[str, bytes]:
    return {p.relative_to(directory).as_posix(): p.read_bytes() for p in sorted(directory.rglob("*")) if p.is_file()}


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("the field and the values", (mod.FIELD, mod.BOLD, mod.TIMID), ("p", "0.990000", "0.010000"))
    c.eq(
        "the frozen values",
        [mod.Edit.__dataclass_params__.frozen, mod.Summary.__dataclass_params__.frozen, mod.Forged.__dataclass_params__.frozen],
        [True, True, True],
    )
    c.eq(
        "the document's name, format and refusal",
        (mod.DOCUMENT, mod.FORMAT, mod.DOCUMENT_REFUSAL),
        ("forgery.json", 1, "the forgery document is not one this version wrote"),
    )
    c.eq("a segment holds as many records as the league's", mod.SEGMENT_RECORDS, 5000)
    # the stand-in key is derived from the cycle alone, so it is the same every time and anyone can see it is not a secret
    seed = hashlib.sha256(b"hoshitori forgery demonstration key: cy-test").digest()
    c.eq("the stand-in key", standin(mod).verifier.vkey(), Signer.from_seed(R.LOG_ORIGIN, seed).verifier.vkey())
    c.eq("another cycle has another", standin(mod, "cy-other").verifier.vkey() != standin(mod).verifier.vkey(), True)
    c.eq(
        "another origin has another name",
        mod.standin_signer("other.example/log", "cy-test").verifier.vkey() != standin(mod).verifier.vkey(),
        True,
    )
    return c.bad


def check_the_forgery(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = B.make_world(root / "world", gap_s=5, body_extra={"p": WAS})  # the records were committed at different times
        before_bundle = tree_of(world.bundle_dir)
        size, root_before = world.ledger.size, world.ledger.root()
        got = forge(mod, world, root)
        note_text, _ = split_note(world.note)
        stamped = parse_text(note_text)
        c.eq("the cycle is named", got.cycle_id, "cy-test")
        c.eq(
            "the edit says what was changed",
            (got.edit.record_id, got.edit.field, got.edit.was, got.edit.now),
            ("fc1", "p", WAS, "0.990000"),
        )
        c.eq("the size is the stamped checkpoint's", got.size, stamped.size)
        c.eq("the honest root is the stamped checkpoint's", got.honest_root, stamped.root.hex())
        c.eq("the forged root differs", got.forged_root != got.honest_root and len(got.forged_root) == 64, True)
        c.eq("the records are the bundle's", got.seqs, (world.seq_evidence, world.seq_forecast, world.seq_committing))
        # what was left alone
        c.eq("the original ledger is as it was", (world.ledger.size, world.ledger.root()), (size, root_before))
        c.eq("the honest bundle is as it was", tree_of(world.bundle_dir), before_bundle)
        # what the copy holds
        forged = tree_of(root / "forged")
        honest_record = canonical.parse_record(
            before_bundle[f"{layout.ITEMS}/{layout.item_stem(world.seq_forecast)}{layout.CANONICAL_SUFFIX}"]
        )
        forged_record = canonical.parse_record(forged[f"{layout.ITEMS}/{layout.item_stem(world.seq_forecast)}{layout.CANONICAL_SUFFIX}"])
        c.eq("the forecast's chance was changed", (honest_record["body"]["p"], forged_record["body"]["p"]), (WAS, "0.990000"))  # type: ignore[index]
        honest_record["body"]["p"] = "0.990000"  # type: ignore[index]
        c.eq("and nothing else of it", forged_record, honest_record)
        evidence = f"{layout.ITEMS}/{layout.item_stem(world.seq_evidence)}{layout.CANONICAL_SUFFIX}"
        c.eq("the evidence record is byte for byte the same", forged[evidence], before_bundle[evidence])
        c.eq("the original time-stamps are kept as they were", tokens_of_dir(root / "forged"), tokens_of(world))
        proof = f"{layout.ITEMS}/{layout.item_stem(world.seq_forecast)}{layout.PROOF_SUFFIX}"
        c.eq("the proof carries a new checkpoint", forged[proof] != before_bundle[proof], True)
        # what the verifier makes of it
        demo = parse_root(mod.demo_root(world.extra["root_document"], R.LOG_ORIGIN, got.standin_vkey))  # type: ignore[arg-type]
        got_verdict = verdict_of(verify, VerifyCase("forged", root / "forged", demo))
        c.eq(
            "record integrity passes, because the log was rebuilt and signed with the key the root names",
            got_verdict["integrity"]["ok"],
            True,
        )
        against_real = verdict_of(verify, VerifyCase("forged against the real root", root / "forged", world.root))
        c.eq("the real root does not accept the stand-in key", against_real["integrity"]["ok"], False)
        c.eq("the stand-in key is what the document names", got.standin_vkey, standin(mod).verifier.vkey())
        timing = got_verdict["timing"]
        c.eq("no time-stamp matches the new checkpoint", [(t["valid"], t["reason"]) for t in timing["tokens"]], [(False, "IMPRINT")] * 2)
        c.eq("so no time is believed", timing["level"], "C1")
        # the forger who holds the log's key also rewrites the committing step, so the forged batch is complete as written: only the
        # time-stamps, which it cannot make again, tell the copy from the real cycle
        c.eq("the forger rewrote the committing step to name the changed batch", got_verdict["cycle"]["complete"], True)
        c.eq(
            "and it cannot count toward a score",
            (got_verdict["eligibility"]["scorable"], got_verdict["eligibility"]["reasons"]),
            (False, ["LEVEL_BELOW_C3"]),
        )
        honest_step = canonical.parse_record(
            before_bundle[f"{layout.ITEMS}/{layout.item_stem(world.seq_committing)}{layout.CANONICAL_SUFFIX}"]
        )
        forged_step = canonical.parse_record(forged[f"{layout.ITEMS}/{layout.item_stem(world.seq_committing)}{layout.CANONICAL_SUFFIX}"])
        c.eq(
            "only the root of the committing step differs from the real one",
            (
                {k: v for k, v in honest_step["body"].items() if k != "batch_root"},
                honest_step["body"]["batch_root"] != forged_step["body"]["batch_root"],
            ),  # type: ignore[union-attr, index]
            ({k: v for k, v in forged_step["body"].items() if k != "batch_root"}, True),  # type: ignore[union-attr, index]
        )
        honest = verdict_of(verify, VerifyCase("honest", world.bundle_dir, world.root))
        c.eq("the honest bundle still reaches C3", (honest["timing"]["level"], honest["eligibility"]["scorable"]), ("C3", True))
        # the same forgery twice gives the same root: nothing in it is chance
        again = forge(mod, world, root / "again")
        c.eq("a forgery is repeatable", (again.forged_root, again.standin_vkey), (got.forged_root, got.standin_vkey))
    return c.bad


def tokens_of_dir(directory: Path) -> dict[str, bytes]:
    return {path.stem: path.read_bytes() for path in (directory / layout.TSA).glob(f"*{layout.TOKEN_SUFFIX}")}


def check_the_value(mod: types.ModuleType) -> list[str]:
    """The new chance is the bold one, unless the call was already confident, and then it is the timid one."""
    c = Case(mod)
    for was, now in (
        ("0.488462", "0.990000"),
        ("0.950000", "0.010000"),
        ("0.990000", "0.010000"),
        ("0.949999", "0.990000"),
        ("0.01", "0.990000"),
    ):
        with tempfile.TemporaryDirectory() as tmp:
            world = B.make_world(Path(tmp) / "world", body_extra={"p": was})
            c.eq(f"a call of {was}", forge(mod, world, Path(tmp)).edit.now, now)
    c.eq(
        "the rule is a function of the chance alone",
        [mod.new_value(Decimal(x)) for x in ("0.5", "0.95", "0.96", "0.0")],
        ["0.990000", "0.010000", "0.010000", "0.990000"],
    )
    return c.bad


def lookalike(world: B.World, *, root_at: int | None = None, records: dict[int, bytes] | None = None) -> Any:
    """A ledger that is the world's except where told: ``records`` gives other bytes for a record, and a root asked for at any size but
    ``root_at`` is not the real one. It stands for a source the forger cannot replay exactly."""

    class Look:
        size = world.ledger.size

        def root(self, size: int | None = None) -> bytes:
            return world.ledger.root(size) if size == root_at or root_at is None else b"\x00" * 32

        def read_record(self, seq: int) -> bytes:
            return (records or {}).get(seq) or world.ledger.read_record(seq)

        def find(self, source: str, record_id: str) -> int | None:
            return world.ledger.find(source, record_id)

    return Look()


def check_what_is_changed(mod: types.ModuleType) -> list[str]:
    """Which record, and from what, when there is a choice."""
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        two = B.make_world(root / "two", second_forecast_gap_s=3, body_extra={"p": WAS}, second_body_extra={"p": "0.3"})
        later = two.ledger.find(B.SOURCE, "fc2")
        assert later is not None
        got = forge(mod, two, root / "a", seqs=[later, two.seq_forecast, two.seq_evidence])
        c.eq(
            "the first forecast by position is the one changed, whatever the order given", (got.edit.record_id, got.edit.was), ("fc1", WAS)
        )
        c.eq("and the records are listed in order", got.seqs, (two.seq_evidence, two.seq_forecast, later))
        elsewhere = B.make_world(root / "elsewhere", evidence_body_extra={"p": "0.4"}, body_extra={"p": WAS})
        got = forge(mod, elsewhere, root / "b")
        c.eq("only a forecast is changed, whatever else carries a chance", got.edit.record_id, "fc1")
        for odd in ("abc", "NaN", "Infinity", "0", "1", "1.5", "-0.2"):
            skipped = B.make_world(root / f"skip-{odd}", second_forecast_gap_s=3, body_extra={"p": odd}, second_body_extra={"p": "0.3"})
            second = skipped.ledger.find(B.SOURCE, "fc2")
            assert second is not None
            got = forge(mod, skipped, root / f"c-{odd}", seqs=[skipped.seq_evidence, skipped.seq_forecast, second])
            c.eq(f"a chance of {odd!r} is not one, so the next forecast is changed", (got.edit.record_id, got.edit.was), ("fc2", "0.3"))
        none = B.make_world(root / "none", body_extra={"p": "abc"})
        c.refuses("no forecast with a chance", lambda: forge(mod, none, root / "d"), "the cycle has no up-or-down forecast to change")
    return c.bad


def check_the_work(mod: types.ModuleType) -> list[str]:
    """What the forger leaves behind and what it accepts to write into."""
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = B.make_world(root / "world", body_extra={"p": WAS})
        (root / "w").mkdir()
        (root / "o").mkdir()
        got = forge(mod, world, root, work=root / "w", out=root / "o")
        c.eq("a directory that exists and is empty is used", got.edit.record_id, "fc1")
        c.eq("and is filled", (root / "o" / layout.ITEMS).is_dir(), True)
        clock = ManualClock(datetime(2026, 11, 1, tzinfo=UTC))
        reopened = Ledger.open(root / "w" / "ledger", signer=R.log_signer(7), origin=R.LOG_ORIGIN, clock=clock, segment_records=5000)
        c.eq("the replayed ledger was closed, so it can be opened again", reopened.size, world.ledger.size)
        reopened.close()
        spaced = B.make_world(root / "spaced", body_extra={"p": WAS})
        c.refuses(
            "a work directory whose files are not the forger's",
            lambda: forge(mod, spaced, root / "x", work=root),
            "the work directory must be new or empty",
        )
    return c.bad


def check_an_inexact_source(mod: types.ModuleType) -> list[str]:
    """A ledger that cannot be replayed as it was is refused, and the three ways it can fail are told apart."""
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = B.make_world(root / "world", second_forecast_gap_s=3, body_extra={"p": WAS}, second_body_extra={"p": "0.3"})
        last = world.ledger.size - 1
        size_at_stamp = parse_text(split_note(world.note)[0]).size
        c.eq("the world's checkpoint covers every record", size_at_stamp, world.ledger.size)
        c.refuses(
            "a prefix whose root is not the original's",
            lambda: forge_from(mod, world, lookalike(world, root_at=size_at_stamp), root / "a"),
            "the ledger could not be replayed as it was before the change",
        )
        repeated = lookalike(world, records={last: world.ledger.read_record(0)})
        c.refuses(
            "a record that repeats an earlier one, so the replay has fewer records",
            lambda: forge_from(mod, world, repeated, root / "b"),
            "the ledger could not be replayed as it was before the change",
        )
        changed = canonical.dumps({**canonical.parse_record(world.ledger.read_record(world.seq_forecast)), "body": {"horizon": "1h"}})
        clash = lookalike(world, records={last: changed})
        try:
            forge_from(mod, world, clash, root / "c")
            c.bad.append("a record that clashes with an earlier one: was accepted")
        except mod.ForgeryError as exc:
            c.eq("a record that clashes is named", str(exc).startswith(f"record {last} cannot be replayed: "), True)
        except Exception as exc:
            c.bad.append(f"a record that clashes: raised {type(exc).__name__}: {exc}")
        c.refuses(
            "a time-stamp file that cannot be a name",
            lambda: forge(mod, world, root / "d", tokens={"bad/name": b"x"}),
            "the forged bundle cannot be written: bad token name 'bad/name'",
        )
    return c.bad


def forge_from(mod: types.ModuleType, world: B.World, source: Any, root: Path) -> Any:
    """``forge`` over ``source`` instead of the world's ledger, with the world's stamped checkpoint and time-stamps."""
    return mod.forge(
        source,
        signer=R.log_signer(7),
        origin=R.LOG_ORIGIN,
        note=world.note,
        tokens=tokens_of(world),
        seqs=[world.seq_evidence, world.seq_forecast],
        cycle_id="cy-test",
        work=root / "work",
        out=root / "forged",
    )


def check_refusals(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = B.make_world(root / "world", body_extra={"p": WAS})
        c.refuses("no record", lambda: forge(mod, world, root / "a", seqs=[]), "a forgery needs the records of a bundle")
        c.refuses(
            "no tokens", lambda: forge(mod, world, root / "b", tokens={}), "a forgery keeps the original time-stamps, and there are none"
        )
        c.refuses(
            "only evidence",
            lambda: forge(mod, world, root / "c", seqs=[world.seq_evidence]),
            "the cycle has no up-or-down forecast to change",
        )
        c.refuses(
            "a record the ledger does not have",
            lambda: forge(mod, world, root / "d", seqs=[world.seq_forecast, 9999]),
            "record 9999 is not in the ledger up to the stamped checkpoint",
        )
        other = B.make_world(
            root / "other", body_extra={"p": "0.7"}
        )  # the same key, other content: the checkpoint's root is not this ledger's
        c.refuses(
            "a checkpoint that is another ledger's",
            lambda: forge(mod, world, root / "e", note=other.note),
            "the stamped checkpoint is not of this ledger",
        )
        (root / "work2").mkdir()
        (root / "work2" / "x").write_text("x")
        c.refuses(
            "a work directory that is not empty",
            lambda: forge(mod, world, root / "f", work=root / "work2"),
            "the work directory must be new or empty",
        )
        (root / "out2").mkdir()
        (root / "out2" / "x").write_text("x")
        c.refuses(
            "an output directory that is not empty",
            lambda: forge(mod, world, root / "g", out=root / "out2"),
            "the output directory must be new or empty",
        )
        c.refuses(
            "a checkpoint of another log",
            lambda: forge(mod, world, root / "h", origin="other.example/log"),
            "the stamped checkpoint is not of this ledger",
        )
        c.refuses(
            "a checkpoint that is not one",
            lambda: forge(mod, world, root / "h2", note=b"not a note"),
            "the stamped checkpoint cannot be read",
        )
    return c.bad


def check_demo_root(mod: types.ModuleType) -> list[str]:
    """The root a forged copy is checked against: the real one with the log's key replaced, and nothing else."""
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        world = B.make_world(Path(tmp) / "world", body_extra={"p": WAS})
        real = world.extra["root_document"]
        vkey = standin(mod).verifier.vkey()
        made = mod.demo_root(real, R.LOG_ORIGIN, vkey)  # type: ignore[arg-type]
        document = json.loads(made)
        c.eq(
            "the log's key is the stand-in's, for every size",
            document["log"],
            {"origin": R.LOG_ORIGIN, "eras": [{"vkey": vkey, "from_size": 0, "through_size": None}]},
        )
        was = json.loads(real)  # type: ignore[arg-type]
        c.eq(
            "everything else is the real root's",
            {k: v for k, v in document.items() if k != "log"},
            {k: v for k, v in was.items() if k != "log"},
        )
        parsed = parse_root(made)
        c.eq("it is a root the parser accepts, of this log, with the one era", (parsed.origin, len(parsed.eras)), (R.LOG_ORIGIN, 1))
        c.eq("it is in canonical form", made, canonical.dumps(document))
        c.refuses("a root of another log", lambda: mod.demo_root(real, "other.example/log", vkey), "the root is not for this log")  # type: ignore[arg-type]
        c.refuses("a root that is not JSON", lambda: mod.demo_root(b"{", R.LOG_ORIGIN, vkey), "the real root cannot be read")
        c.refuses("a root that is not an object", lambda: mod.demo_root(b"[]", R.LOG_ORIGIN, vkey), "the real root cannot be read")
        c.refuses(
            "a root with no log", lambda: mod.demo_root(json.dumps({"a": 1}).encode(), R.LOG_ORIGIN, vkey), "the real root cannot be read"
        )
        for odd in ("text", ["a"], 5, None):
            c.refuses(
                f"a root whose log is {odd!r}",
                lambda odd=odd: mod.demo_root(json.dumps({"log": odd}).encode(), R.LOG_ORIGIN, vkey),
                "the real root cannot be read",
            )
        c.refuses("a key that is not one", lambda: mod.demo_root(real, R.LOG_ORIGIN, "not a key"), "the stand-in key is not a key")  # type: ignore[arg-type]
        c.refuses(
            "a root the parser refuses",
            lambda: mod.demo_root(json.dumps({**was, "version": "one"}).encode(), R.LOG_ORIGIN, vkey),
            "the demonstration root is not a root: MALFORMED: version must be an integer from 1 to 9007199254740991",
        )
    return c.bad


def check_document(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        world = B.make_world(root / "world", body_extra={"p": WAS})
        got = forge(mod, world, root)
        text = mod.document(got)
        parsed = json.loads(text)
        c.eq(
            "the document says what was done and nothing more",
            parsed,
            {
                "format": 1,
                "cycle_id": "cy-test",
                "edit": {"record": "fc1", "field": "p", "was": WAS, "now": "0.990000"},
                "standin_vkey": got.standin_vkey,
                "size": got.size,
                "honest_root": got.honest_root,
                "forged_root": got.forged_root,
            },
        )
        c.eq("it is written in a fixed order and ends with a newline", (text == json.dumps(parsed, indent=2, sort_keys=True) + "\n"), True)
        c.eq("it reads back", mod.read_document(text.encode()), got.summary())
        c.eq("a summary is written as the forgery it came from is", mod.document(got.summary()), text)
        c.eq("a size of one is a size", mod.read_document(json.dumps({**parsed, "size": 1}).encode()).size, 1)
        for label, broken in (
            ("not JSON", b"{"),
            ("not an object", b"[]"),
            ("another format", json.dumps({**parsed, "format": 2}).encode()),
            ("a field missing", json.dumps({k: v for k, v in parsed.items() if k != "edit"}).encode()),
            ("an extra field", json.dumps({**parsed, "more": 1}).encode()),
            ("a root that is not a digest", json.dumps({**parsed, "forged_root": "xyz"}).encode()),
            ("the same root for both", json.dumps({**parsed, "forged_root": parsed["honest_root"]}).encode()),
            ("an edit that changes nothing", json.dumps({**parsed, "edit": {**parsed["edit"], "now": WAS}}).encode()),
            ("a size that is not a number", json.dumps({**parsed, "size": "3"}).encode()),
            ("no stand-in key", json.dumps({k: v for k, v in parsed.items() if k != "standin_vkey"}).encode()),
            ("a stand-in key that is not a key", json.dumps({**parsed, "standin_vkey": "not a key"}).encode()),
            ("a size of nothing", json.dumps({**parsed, "size": 0}).encode()),
            ("bytes that are not text", b"\xff\xfe{}"),
            ("an edit that is nothing", json.dumps({**parsed, "edit": None}).encode()),
            ("an edit that is a list", json.dumps({**parsed, "edit": ["p"]}).encode()),
            ("an edit with a field too many", json.dumps({**parsed, "edit": {**parsed["edit"], "x": 1}}).encode()),
            ("a field that is empty", json.dumps({**parsed, "edit": {**parsed["edit"], "field": ""}}).encode()),
            ("a value with a space round it", json.dumps({**parsed, "edit": {**parsed["edit"], "was": " " + WAS}}).encode()),
            ("a value that is a number", json.dumps({**parsed, "edit": {**parsed["edit"], "was": 0.49}}).encode()),
            ("a record that is a number", json.dumps({**parsed, "edit": {**parsed["edit"], "record": 5}}).encode()),
            ("a cycle with a space round it", json.dumps({**parsed, "cycle_id": " cy"}).encode()),
            ("a format that is not a whole number", json.dumps({**parsed, "format": True}).encode()),
        ):
            c.refuses(label, lambda b=broken: mod.read_document(b), mod.DOCUMENT_REFUSAL)
    return c.bad


def check_renamed_batch(mod: types.ModuleType) -> list[str]:
    """The forger rewrites a cycle's committing step to name the root of the batch as it changed it, and only a step that names a size the
    records before it can hold; every other record, and a step of an older cycle that named none, is left exactly as it was."""
    from hoshitori.league import batch_root

    c = Case(mod)
    before: list[dict[str, Any]] = [{"type": "evidence", "id": f"e{i}", "body": {"n": str(i)}} for i in range(5)]

    def step(**body: Any) -> dict[str, Any]:
        return {"type": "cycle_state", "id": "s", "body": {"state": "COMMITTING", "batch_root": "0" * 64, **body}}

    def renamed(record: dict[str, Any], prior: list[dict[str, Any]] = before) -> str:
        mod._renamed_batch(record, prior)
        return str(record["body"]["batch_root"])

    c.eq("a batch of three names the root of the last three", renamed(step(batch_size=3)), batch_root(before[-3:]))
    c.eq("a batch of one", renamed(step(batch_size=1)), batch_root(before[-1:]))
    c.eq("a batch of all the records before it", renamed(step(batch_size=5)), batch_root(before))
    for label, record in (
        ("a size past the records before it", step(batch_size=6)),
        ("a size of zero", step(batch_size=0)),
        ("a size below zero", step(batch_size=-1)),
        ("a size that is a boolean", step(batch_size=True)),
        ("a size that is text", step(batch_size="3")),
        ("a step that names no size", step()),
        ("another state of the cycle", {**step(batch_size=3), "body": {"state": "COMMITTED", "batch_size": 3, "batch_root": "0" * 64}}),
        ("another kind of record", {**step(batch_size=3), "type": "forecast"}),
        ("a record with no body", {"type": "cycle_state", "id": "s", "body": "x"}),
    ):
        body_before = repr(record)
        mod._renamed_batch(record, before)
        c.eq(f"{label} is left as it was", repr(record), body_before)
    c.eq("a size of zero with no records before it", renamed(step(batch_size=0), []), "0" * 64)
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    checks: tuple[Callable[[types.ModuleType], list[str]], ...] = (
        check_constants,
        check_the_forgery,
        check_the_value,
        check_what_is_changed,
        check_the_work,
        check_an_inexact_source,
        check_refusals,
        check_demo_root,
        check_document,
        check_renamed_batch,
    )
    for check in checks:
        bad += [f"{check.__name__}: {message}" for message in check(mod)]
    return bad
