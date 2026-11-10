"""Checks of the commitment store (``hoshitori.commitment_store``), runnable against a mutant."""

from __future__ import annotations

import dataclasses
import tempfile
import types
from collections.abc import Callable
from pathlib import Path

from hoshitori.attest import Stamp
from hoshitori.clock import ManualClock
from hoshitori.commit import Commitment, commit_batch
from tests.commit_checks import RULES, START, attestor, ledger_in, record


def commitment(root: Path, *, n: int = 1, note: bytes | None = None, tokens: dict[str, bytes] | None = None) -> Commitment:
    ledger = ledger_in(root, ManualClock(START))
    base = commit_batch(ledger, [record(n)], attestor=attestor([("a", "ta", 1), ("b", "tb", 3)]), rules=RULES)
    ledger.close()
    stamps = tuple(Stamp(name, 1, data) for name, data in sorted((tokens or {"ta": b"first answer", "tb": b"\x00second\xff"}).items()))
    return dataclasses.replace(
        base, note=base.note if note is None else note, attestation=dataclasses.replace(base.attestation, stamps=stamps)
    )


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
        except self.mod.StoreError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("names", (mod.NOTE, mod.SUFFIX, mod.MAX_BYTES), ("note", ".tsr", 1_048_576))
    c.eq("Saved is frozen", mod.Saved.__dataclass_params__.frozen, True)
    return c.bad


def check_roundtrip(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    store = root / "store"
    made = commitment(root / "l1")
    where = mod.save(store, made)
    c.eq("it lives under the commitment's id", where, store / made.commitment_id)
    c.eq("the files", sorted(p.name for p in where.iterdir()), ["note", "ta.tsr", "tb.tsr"])
    c.eq("nothing is left in staging", sorted(p.name for p in store.iterdir()), [made.commitment_id])
    saved = mod.load(store, made.commitment_id)
    c.eq("it reads back whole", (saved.note, saved.tokens), (made.note, {"ta": b"first answer", "tb": b"\x00second\xff"}))
    c.eq("saved again, the same is nothing new", mod.save(store, made), where)
    other = dataclasses.replace(made, note=made.note + b"x")
    c.refuses("other contents under the same id", lambda: mod.save(store, other), "this commitment is already saved with other contents")
    changed = commitment(root / "l2", tokens={"ta": b"first answer", "tb": b"another"})
    c.refuses(
        "other tokens under the same id",
        lambda: mod.save(store, dataclasses.replace(made, attestation=changed.attestation)),
        "this commitment is already saved with other contents",
    )
    # a save that died before it finished leaves a staging directory, with a stale file
    fresh = commitment(root / "l3", n=2, tokens={"ta": b"x", "tb": b"y"})
    stale = store / (fresh.commitment_id + ".new")
    stale.mkdir()
    (stale / "old.tsr").write_bytes(b"left over")
    mod.save(store, fresh)
    c.eq(
        "a leftover of a dead save is cleared away",
        sorted(p.name for p in (store / fresh.commitment_id).iterdir()),
        ["note", "ta.tsr", "tb.tsr"],
    )
    c.eq("and its staging directory is gone", sorted(p.name for p in store.iterdir()), sorted([made.commitment_id, fresh.commitment_id]))
    return c.bad


def check_refusals(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    store = root / "store"
    made = commitment(root / "l1")
    c.refuses("an id with a slash", lambda: mod.load(store, "../x"), "a commitment id is lower-case letters and digits")
    c.refuses("an id with capitals", lambda: mod.load(store, "ABC"), "a commitment id is lower-case letters and digits")
    c.refuses("an empty id", lambda: mod.load(store, ""), "a commitment id is lower-case letters and digits")
    c.refuses("an id too long", lambda: mod.load(store, "a" * 65), "a commitment id is lower-case letters and digits")
    c.refuses("nothing saved", lambda: mod.load(store, "abc"), "no such commitment is saved")
    store.mkdir()
    (store / "abc").mkdir()
    c.refuses("a directory with no note is not a commitment", lambda: mod.load(store, "abc"), "no such commitment is saved")
    (store / "abc" / "note").write_bytes(b"n")
    (store / "abc" / "stray.txt").write_bytes(b"?")
    c.refuses("a file that is not a token", lambda: mod.load(store, "abc"), "stray.txt is not a file of a commitment")
    (store / "abc" / "stray.txt").unlink()
    (store / "abc" / "UP.tsr").write_bytes(b"?")
    c.refuses("a token with a bad name", lambda: mod.load(store, "abc"), "UP.tsr is not a file of a commitment")
    c.eq("a real id is made of letters and digits", mod._ID.fullmatch(made.commitment_id) is not None, True)
    badname = commitment(root / "l2", tokens={"Bad": b"x", "fine": b"y"})
    c.refuses(
        "an authority name that is not a file name",
        lambda: mod.save(root / "s2", badname),
        "an authority's name is 1 to 32 characters of a-z, 0-9, - and _",
    )
    longname = commitment(root / "l3", tokens={"a" * 33: b"x", "fine": b"y"})
    c.refuses("a name too long", lambda: mod.save(root / "s3", longname), "an authority's name is 1 to 32 characters of a-z, 0-9, - and _")
    mod.save(root / "s4", commitment(root / "l5", tokens={"a" * 32: b"x"}))
    limit = mod.MAX_BYTES
    size = len(made.note)  # the limit is applied where the sizes are compared, so a small one shows its edge
    mod.MAX_BYTES = size
    try:
        message = f"a note or a token is at most {size} bytes"
        c.refuses(
            "a token over the limit, beside one within it",
            lambda: mod.save(root / "s5", commitment(root / "l6", tokens={"ta": b"x" * (size + 1), "tb": b"y"})),
            message,
        )
        c.refuses(
            "a note over the limit",
            lambda: mod.save(root / "s6", commitment(root / "l7", note=made.note + b"n", tokens={"ta": b"x"})),
            message,
        )
        mod.save(root / "s7", commitment(root / "l8", tokens={"ta": b"x" * size}))
    finally:
        mod.MAX_BYTES = limit
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (check_constants, check_roundtrip, check_refusals):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = check(mod) if check is check_constants else check(mod, Path(tmp))  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1100:]}")
    return bad
