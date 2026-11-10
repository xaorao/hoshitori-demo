"""Checks of the settings, the key and the assembly of a league (``hoshitori.deployment``), runnable against a mutant.

The outside world is the loopback fake of ``e2e_league``; the settings, the root, the protocol, the mapping and the key are real files.
"""

from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import types
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import Any

from hoshitori import (
    barrier_rates,
    canonical,
    claims_file,
    cycle,
    forgery,
    lifecycle,
    model_arms,
    model_challenge_file,
    model_settings,
    protocol,
    rules_arm,
    scheduler,
    view_models,
)
from hoshitori.cli import main as verify_main
from hoshitori.clock import SystemClock
from hoshitori.note import Signer
from hoshitori.timeline import to_ms
from tests import model_world
from tests import root_support as R
from tests.claim_extract_checks import reply
from tests.claims_file_checks import made as claims_made
from tests.e2e_league import MINUTE, OffsetClock, Outside, outside
from tests.model_challenge_file_checks import RECORDED
from tests.protocol_checks import make
from tests.rules_arm_checks import graded
from tests.smart_model import SmartModel

MODEL_KEY_ENV = "HOSHITORI_TEST_MODEL_KEY"
CONFIG: dict[str, Any] = {
    "model": "fake/model",
    "max_tokens": 2000,
    "temperature": "0",
    "seed": None,
    "response_format": "json_schema",
    "timeout_s": 30,
    "thinking": None,
}
MODEL_CUTOFFS = {"L1": ((1, 200_000), (4, 600_000), (24, 700_000)), "L2": ((4, 700_000),), "L1x": ((4, 780_000),)}


def models_document(url: str, **over: Any) -> dict[str, Any]:
    doc: dict[str, Any] = {
        "name": "fake",
        "base_url": url,
        "key_env": MODEL_KEY_ENV,
        "workers": 4,
        "concurrency": 8,
        "rate_per_min": 600,
        "attempt_cap_s": 60,
        "min_attempt_s": 1,
        "l1": {**CONFIG},
        "l2": {"bull": {**CONFIG}, "bear": {**CONFIG}, "judge": {**CONFIG}, "cap_tokens": 100000},
        "l1x": {"sample": {**CONFIG, "temperature": "0.7"}, "seed_base": 1000, "max_samples": 15},
    }
    doc.update(over)
    return doc


def good_settings() -> dict[str, Any]:
    return {
        "directory": "run",
        "protocol": "protocol.json",
        "mappings": {"L0": "mapping.json"},
        "barrier_mappings": {},
        "trusted_root": "root.json",
        "log_key": "log.key",
        "source": "urn:hoshitori:league:test",
        "venues": {"binance-spot": "http://127.0.0.1:1", "okx": None},
        "authorities": [{"name": "alpha", "url": "https://a.example/tsr"}, {"name": "beta", "url": "https://b.example/tsr"}],
        "lists": [{"venue": "binance-spot", "scope": "spot"}, {"venue": "okx", "scope": "spot", "kind": "spot"}],
        "tick_every_s": 30,
        "start_grace_ms": 300000,
        "finish_within_ms": 1200000,
        "wait_s": 30,
        "allow_insecure_loopback": False,
        "allow_plain_http_authorities": False,
        "practice": False,
        "models": None,
    }


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
        except self.mod.DeploymentError as exc:
            if str(exc) != says:
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def parse(mod: types.ModuleType, **changes: Any) -> Any:
    raw = good_settings()
    raw.update(changes)
    return mod.parse_settings(json.dumps(raw), Path("/base"))


def drop(mod: types.ModuleType, name: str) -> Any:
    raw = good_settings()
    del raw[name]
    return mod.parse_settings(json.dumps(raw), Path("/base"))


def check_constants(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("members", sorted(mod.MEMBERS), sorted(good_settings()))
    c.eq("workers", mod.WORKERS, 8)
    for cls in (mod.Settings,):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    return c.bad


def check_settings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    s = parse(mod)
    c.eq(
        "paths against the file's directory",
        (s.directory, s.protocol, s.mappings, s.trusted_root, s.log_key),
        (
            Path("/base/run"),
            Path("/base/protocol.json"),
            {"L0": Path("/base/mapping.json")},
            Path("/base/root.json"),
            Path("/base/log.key"),
        ),
    )
    c.eq("source and venues", (s.source, s.venues), ("urn:hoshitori:league:test", {"binance-spot": "http://127.0.0.1:1", "okx": None}))
    c.eq("authorities", [(a.name, a.url) for a in s.authorities], [("alpha", "https://a.example/tsr"), ("beta", "https://b.example/tsr")])
    c.eq("lists", [(x.venue, x.scope, x.kind) for x in s.lists], [("binance-spot", "spot", None), ("okx", "spot", "spot")])
    c.eq(
        "numbers",
        (s.tick_every_s, s.start_grace_ms, s.finish_within_ms, s.wait_s, s.allow_insecure_loopback, s.allow_plain_http_authorities),
        (30.0, 300000, 1200000, 30.0, False, False),
    )
    flags = parse(mod, allow_insecure_loopback=True)
    c.eq("the loopback flag alone", (flags.allow_insecure_loopback, flags.allow_plain_http_authorities), (True, False))
    flags = parse(mod, allow_plain_http_authorities=True)
    c.eq("the plain-http flag alone", (flags.allow_insecure_loopback, flags.allow_plain_http_authorities), (False, True))
    c.refuses(
        "not JSON",
        lambda: mod.parse_settings("{", Path(".")),
        "the settings are not JSON: Expecting property name enclosed in double quotes",
    )
    c.refuses("not an object", lambda: mod.parse_settings("[]", Path(".")), "the settings is an object")
    members = f"the settings have the members {sorted(mod.MEMBERS)}, not "
    for name in sorted(good_settings()):
        raw = {k: v for k, v in good_settings().items() if k != name}
        c.refuses(f"missing {name}", lambda raw=raw: mod.parse_settings(json.dumps(raw), Path(".")), members + str(sorted(raw)))
    extra = {**good_settings(), "x": 1}
    c.refuses("an extra member", lambda: mod.parse_settings(json.dumps(extra), Path(".")), members + str(sorted(extra)))
    c.refuses("an empty mapping set", lambda: parse(mod, mappings={}), "there is at least one mapping")
    c.refuses("mappings not an object", lambda: parse(mod, mappings=[]), "mappings is an object")
    c.eq("no barrier mapping is allowed", parse(mod).barrier_mappings, {})
    c.eq(
        "barrier mappings are read against the file's directory",
        parse(mod, barrier_mappings={"L0": "b.json"}).barrier_mappings,
        {"L0": Path("/base/b.json")},
    )
    c.refuses("barrier mappings not an object", lambda: parse(mod, barrier_mappings=[]), "barrier_mappings is an object")
    c.refuses(
        "a barrier mapping that is not a name",
        lambda: parse(mod, barrier_mappings={"L0": ""}),
        "the barrier mapping of arm L0 is a non-empty string",
    )
    c.refuses("a mapping that is not a name", lambda: parse(mod, mappings={"L0": ""}), "the mapping of arm L0 is a non-empty string")
    c.refuses("no venues", lambda: parse(mod, venues={}), "there is at least one venue")
    c.refuses("venues not an object", lambda: parse(mod, venues=[]), "venues is an object")
    parse(mod, directory="d" * mod.MAX_TEXT)
    c.refuses("a name of 1001 characters", lambda: parse(mod, directory="d" * (mod.MAX_TEXT + 1)), "directory is a non-empty string")
    c.eq("the limit", mod.MAX_TEXT, 1000)
    c.refuses(
        "a list's venue that is not text", lambda: parse(mod, lists=[{"venue": 3, "scope": "s"}]), "a list's venue is a non-empty string"
    )
    c.refuses(
        "a list's scope that is not text", lambda: parse(mod, lists=[{"venue": "v", "scope": 3}]), "a list's scope is a non-empty string"
    )
    c.refuses(
        "a list's kind that is not text",
        lambda: parse(mod, lists=[{"venue": "v", "scope": "s", "kind": 3}]),
        "a list's kind is a non-empty string",
    )
    c.refuses(
        "an authority's name that is not text",
        lambda: parse(mod, authorities=[{"name": 3, "url": "https://a.example/"}]),
        "an authority's name is a non-empty string",
    )
    c.refuses(
        "an authority's url that is not text",
        lambda: parse(mod, authorities=[{"name": "a", "url": 3}]),
        "an authority's url is a non-empty string",
    )
    c.refuses("a base url that is not text", lambda: parse(mod, venues={"okx": 3}), "the base url of okx is a non-empty string")
    c.refuses("no authorities", lambda: parse(mod, authorities=[]), "there is at least one time-stamp authority")
    c.refuses("authorities not a list", lambda: parse(mod, authorities={}), "there is at least one time-stamp authority")
    c.refuses("an authority that is not an object", lambda: parse(mod, authorities=[3]), "an authority is an object")
    c.refuses("an authority with a member short", lambda: parse(mod, authorities=[{"name": "a"}]), "an authority has a name and a url")
    c.refuses(
        "an authority with an extra",
        lambda: parse(mod, authorities=[{"name": "a", "url": "https://a/", "x": 1}]),
        "an authority has a name and a url",
    )
    c.refuses(
        "an authority name that is not allowed",
        lambda: parse(mod, authorities=[{"name": "A B", "url": "https://a.example/"}]),
        "an authority's name is 1 to 32 characters of a-z, 0-9, - and _",
    )
    c.refuses(
        "an authority url with credentials",
        lambda: parse(mod, authorities=[{"name": "a", "url": "https://u:p@a.example/"}]),
        "an authority's URL is an http(s) URL with a host and no credentials, query or fragment",
    )
    c.refuses("no lists", lambda: parse(mod, lists=[]), "there is at least one instrument list")
    c.refuses("lists not a list", lambda: parse(mod, lists="x"), "there is at least one instrument list")
    c.refuses("a list that is not an object", lambda: parse(mod, lists=[1]), "an instrument list is an object")
    c.refuses(
        "a list with only a venue",
        lambda: parse(mod, lists=[{"venue": "okx"}]),
        "an instrument list has a venue and a scope, and may have a kind",
    )
    c.refuses(
        "a list with an extra",
        lambda: parse(mod, lists=[{"venue": "okx", "scope": "spot", "x": 1}]),
        "an instrument list has a venue and a scope, and may have a kind",
    )
    c.refuses(
        "a loopback flag that is not a boolean",
        lambda: parse(mod, allow_insecure_loopback="yes"),
        "allow_insecure_loopback is true or false",
    )
    c.refuses(
        "a plain-http flag that is not a boolean",
        lambda: parse(mod, allow_plain_http_authorities="yes"),
        "allow_plain_http_authorities is true or false",
    )
    c.refuses("a practice flag that is not a boolean", lambda: parse(mod, practice="yes"), "practice is true or false")
    c.eq("the practice flag is read, and is off in the fixture", (parse(mod).practice, parse(mod, practice=True).practice), (False, True))
    c.refuses("a source that is not a urn", lambda: parse(mod, source="x"), "the source is a urn")
    c.refuses("an empty source", lambda: parse(mod, source=""), "the source is a non-empty string")
    for name, label in (("tick_every_s", "the pause between ticks"), ("wait_s", "the wait")):
        for bad in (0, -1, True, "1"):
            c.refuses(f"{name} of {bad!r}", lambda name=name, bad=bad: parse(mod, **{name: bad}), f"{label} is a positive number")
    parse(mod, tick_every_s=0.5)
    for name, label in (("start_grace_ms", "the start grace"), ("finish_within_ms", "the time a cycle has to finish")):
        for bad in (-1, True, 1.5, "1"):
            c.refuses(f"{name} of {bad!r}", lambda name=name, bad=bad: parse(mod, **{name: bad}), f"{label} is a whole number")
        parse(mod, **{name: 0})
    return c.bad


def check_files_and_keys(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    c.refuses(
        "a settings file that is not there",
        lambda: mod.load_settings(root / "none.json"),
        "the settings cannot be read: No such file or directory",
    )
    (root / "s.json").write_text(json.dumps(good_settings()))
    c.eq("a settings file is read against its own directory", mod.load_settings(root / "s.json").directory, root / "run")
    key = root / "k"
    vkey = mod.new_key(key, R.LOG_ORIGIN)
    c.eq(
        "a new key is made for the log and its public key returned",
        vkey,
        __import__("hoshitori.note", fromlist=["Signer"]).Signer.from_seed(R.LOG_ORIGIN, mod.read_key(key)).verifier.vkey(),
    )
    c.eq("readable by its owner only", oct(key.stat().st_mode & 0o777), "0o600")
    c.eq("a thirty-two byte seed", len(mod.read_key(key)), 32)
    c.refuses("never overwritten", lambda: mod.new_key(key, R.LOG_ORIGIN), "a key file is never overwritten")
    c.eq("two keys are different", mod.new_key(root / "k2", R.LOG_ORIGIN) != vkey, True)
    c.refuses("a name that is not a log name", lambda: mod.new_key(root / "k3", "a b"), "the log's name: " + _note_message("a b"))
    c.eq("and no file was made", (root / "k3").exists(), False)
    for mode in (0o640, 0o604, 0o660, 0o666, 0o644, 0o706):
        key.chmod(mode)
        c.refuses(f"a key with mode {oct(mode)}", lambda: mod.read_key(key), "the log key file can be read by others than its owner")
    key.chmod(0o400)
    c.eq("read-only for the owner is fine", len(mod.read_key(key)), 32)
    key.chmod(0o600)
    c.refuses("no key file", lambda: mod.read_key(root / "none"), "the log key cannot be read: No such file or directory")
    bad = root / "bad"
    bad.write_bytes(b"not base64 !!\n")
    bad.chmod(0o600)
    c.refuses("a key that is not base64", lambda: mod.read_key(bad), "the log key file is not base64")
    injected = root / "injected"
    text = base64.b64encode(bytes(range(32))).decode()
    injected.write_bytes((text[:10] + "!" + text[10:]).encode())
    injected.chmod(0o600)
    c.refuses("base64 with a stray character in it", lambda: mod.read_key(injected), "the log key file is not base64")
    foreign = root / "foreign"
    foreign.write_bytes(b"\xff\xfe\x00")
    foreign.chmod(0o600)
    c.refuses("a key file that is not text", lambda: mod.read_key(foreign), "the log key file is not base64")
    short = root / "short"
    short.write_bytes(base64.b64encode(b"x" * 31))
    short.chmod(0o600)
    c.refuses("a key of the wrong size", lambda: mod.read_key(short), "the log key is 32 bytes")
    longer = root / "long"
    longer.write_bytes(base64.b64encode(b"x" * 33))
    longer.chmod(0o600)
    c.refuses("a key too long", lambda: mod.read_key(longer), "the log key is 32 bytes")
    return c.bad


def _note_message(name: str) -> str:
    from hoshitori.note import NoteError, Signer

    try:
        Signer.from_seed(name, b"\x00" * 32)
    except NoteError as exc:
        return str(exc)
    return ""


def world_files(directory: Path, world: Outside, **over: Any) -> Path:
    """Every file of a league that can run against ``world``, with the changes asked for."""
    from hoshitori import deployment

    key = directory / "log.key"
    vkey = deployment.new_key(key, R.LOG_ORIGIN)
    document = R.make_document(
        1, root_keys=[R.new_signer(1)], eras=[{"vkey": over.pop("vkey", vkey), "from_size": 0, "through_size": None}], pinned=world.pinned()
    )
    root_bytes = canonical.dumps(document)  # type: ignore[arg-type]
    (directory / "root.json").write_bytes(root_bytes)
    mapping = graded(rules_arm)
    (directory / "mapping.json").write_bytes(rules_arm.dump(mapping))
    barrier = over.pop("barrier", None)  # None, or the barrier mapping to pin and write
    unpinned = over.pop("unpinned_barrier", None)  # a barrier mapping to write and name in the settings without pinning it
    if barrier is None and unpinned is not None:
        (directory / "barrier.json").write_bytes(barrier_rates.dump(unpinned))
    if barrier is not None:
        (directory / "barrier.json").write_bytes(barrier_rates.dump(barrier))
    models_doc = over.pop("models_doc", None)  # None, or the model settings to write and pin
    model_arms: tuple[Any, ...] = ()
    if models_doc is not None:
        (directory / "models.json").write_text(json.dumps(models_doc))
        pinned_versions = model_settings.parse(json.dumps(models_doc)).versions()
        cutoffs = over.pop("model_cutoffs", MODEL_CUTOFFS)
        model_arms = tuple(
            (arm, over.pop("model_pin", {}).get(arm, version), cutoffs[arm]) for arm, version in sorted(pinned_versions.items())
        )
    optional = over.pop("optional_sources", ())
    start = (to_ms(OffsetClock().now()) // MINUTE - over.pop("ago_minutes", 1)) * MINUTE
    pins = {"root_version": 1, "root_sha256": hashlib.sha256(root_bytes).hexdigest(), **over.pop("pins", {})}
    rules = make(
        protocol,
        name="dep",
        primary_venue="binance-spot",
        second_venue="okx",
        schedule_start_ms=start,
        arms=(("L0", over.pop("arm_pin", mapping.sha256)),),
        barrier_arms=() if barrier is None else (("L0", over.pop("pinned", barrier.sha256)),),
        model_arms=model_arms,
        optional_sources=optional,
        snapshot_freshness_ms=900_000,
        **pins,
    )
    (directory / "protocol.json").write_bytes(protocol.dump(rules))
    settings = {
        **good_settings(),
        "venues": {"binance-spot": world.exchange.url, "okx": world.exchange.url},
        "authorities": world.authorities,
        "allow_insecure_loopback": True,
        "start_grace_ms": 10 * MINUTE,
        **({"barrier_mappings": {"L0": "barrier.json"}} if barrier is not None or unpinned is not None else {}),
        **({"models": "models.json"} if models_doc is not None else {}),
        **over,
    }
    path = directory / "settings.json"
    path.write_text(json.dumps(settings))
    return path


def check_ondemand_settings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    base = Path("/base")
    absent = mod.parse_settings(json.dumps(good_settings()), base)
    c.eq("without the member there is no on-demand ledger", absent.ondemand, None)
    given = {**good_settings(), "ondemand": {"log_key": "od.key", "trusted_root": "od-root.json"}}
    got = mod.parse_settings(json.dumps(given), base)
    c.eq(
        "with it the key and the root are named",
        (got.ondemand.log_key, got.ondemand.trusted_root),
        (base / "od.key", base / "od-root.json"),
    )
    members = ["log_key", "trusted_root"]
    for label, bad in (
        ("a missing key", {"trusted_root": "od-root.json"}),
        ("a missing root", {"log_key": "od.key"}),
        ("an extra member", {"log_key": "od.key", "trusted_root": "r.json", "origin": "x"}),
        ("an empty object", {}),
    ):
        c.refuses(
            label,
            lambda bad=bad: mod.parse_settings(json.dumps({**good_settings(), "ondemand": bad}), base),
            f"ondemand has the members {members}, not {sorted(bad)}",
        )
    c.refuses(
        "a member that is not an object",
        lambda: mod.parse_settings(json.dumps({**good_settings(), "ondemand": "x"}), base),
        "ondemand is an object",
    )
    c.refuses(
        "a key that is not text",
        lambda: mod.parse_settings(json.dumps({**good_settings(), "ondemand": {"log_key": 1, "trusted_root": "r"}}), base),
        "the on-demand log key is a non-empty string",
    )
    c.refuses(
        "a root that is not text",
        lambda: mod.parse_settings(json.dumps({**good_settings(), "ondemand": {"log_key": "k", "trusted_root": ""}}), base),
        "the on-demand root is a non-empty string",
    )
    return c.bad


def _ondemand_files(directory: Path, world: Outside, mod: types.ModuleType, **kw: Any) -> Path:
    """The files of a league that has an on-demand ledger: its own origin, its own key and its own root (the same time-stamp authorities)."""
    origin = kw.get("origin", "hoshitori.example/ondemand-log")
    path = world_files(directory, world, **{k: v for k, v in kw.items() if k not in ("origin", "same_key", "vkey")})
    key_file = directory / "od.key"
    if kw.get("same_key"):
        key_file.write_bytes((directory / "log.key").read_bytes())
        key_file.chmod(0o600)
        vkey = Signer.from_seed(origin, mod.read_key(key_file)).verifier.vkey()
    else:
        vkey = mod.new_key(key_file, origin)
    document = R.make_document(
        1, root_keys=[R.new_signer(1)], eras=[{"vkey": kw.get("vkey", vkey), "from_size": 0, "through_size": None}], pinned=world.pinned()
    )
    document["log"]["origin"] = origin  # type: ignore[index]
    (directory / "od-root.json").write_bytes(canonical.dumps(document))  # type: ignore[arg-type]
    settings = json.loads(path.read_text())
    settings["ondemand"] = {"log_key": "od.key", "trusted_root": "od-root.json"}
    path.write_text(json.dumps(settings))
    return path


def check_ondemand_assembly(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    with outside(OffsetClock()) as world:
        (root / "ok").mkdir()
        path = _ondemand_files(root / "ok", world, mod)
        with mod.assemble(mod.load_settings(path), clock=OffsetClock()) as running:
            c.eq(
                "the on-demand ledger is open, apart from the league's",
                (running.ondemand is not None, running.ondemand is not running.ledger),
                (True, True),
            )
            c.eq("in a directory of its own", (root / "ok" / "run" / "ondemand").is_dir(), True)
            c.eq("the tick has a pass to run", running.config.on_demand is not None, True)
            first = scheduler.tick(running.services, running.config)
            c.eq(
                "a tick runs it, and with nothing to settle it writes nothing",
                (
                    first.on_demand is not None and first.on_demand.records,
                    first.on_demand_failed,
                    running.ondemand.size if running.ondemand else None,
                ),
                ((), None, 0),
            )
            c.eq("and the league's own cycle ran beside it", first.ran is not None and first.ran.state.value, "COMMITTED")
        (root / "off").mkdir()
        plain = world_files(root / "off", world)
        with mod.assemble(mod.load_settings(plain), clock=OffsetClock()) as running:
            c.eq(
                "without the settings there is no on-demand ledger and no pass", (running.ondemand, running.config.on_demand), (None, None)
            )
        (root / "dir").mkdir()
        shown = _ondemand_files(root / "dir", world, mod, origin="hoshitori.example/log")
        c.refuses(
            "the league's own origin is refused", lambda: _assemble(mod, shown), "the on-demand ledger has its own origin, not the league's"
        )
        (root / "key").mkdir()
        same = _ondemand_files(root / "key", world, mod, same_key=True)
        c.refuses(
            "the league's own key is refused",
            lambda: _assemble(mod, same),
            "the on-demand ledger has its own signing key, not the league's",
        )
        (root / "era").mkdir()
        other = _ondemand_files(
            root / "era", world, mod, vkey=Signer.from_seed("hoshitori.example/ondemand-log", b"\x09" * 32).verifier.vkey()
        )
        c.refuses(
            "a key the on-demand root does not name is refused",
            lambda: _assemble(mod, other),
            "the on-demand log's signing key is not allowed by its trusted root for the size the ledger has now",
        )
    return c.bad


def check_desk_settings(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    base = Path("/base")
    od = {"log_key": "od.key", "trusted_root": "od-root.json"}
    c.eq("without the member the Desk is off", mod.parse_settings(json.dumps(good_settings()), base).desk, False)
    both = {**good_settings(), "ondemand": od, "models": "models.json"}
    c.eq("on, with the on-demand ledger and the models", mod.parse_settings(json.dumps({**both, "desk": True}), base).desk, True)
    c.eq("off when it says so", mod.parse_settings(json.dumps({**both, "desk": False}), base).desk, False)
    c.eq("off needs neither", mod.parse_settings(json.dumps({**good_settings(), "desk": False}), base).desk, False)
    for label, value in (("a number", 1), ("text", "true"), ("nothing", None)):
        c.refuses(label, lambda value=value: mod.parse_settings(json.dumps({**both, "desk": value}), base), "desk is true or false")
    needs = "the Desk commits through the on-demand ledger and asks the model: it needs ondemand and models"
    for label, settings in (
        ("on without the on-demand ledger", {**good_settings(), "models": "models.json", "desk": True}),
        ("on without the models", {**good_settings(), "ondemand": od, "desk": True}),
        ("on with neither", {**good_settings(), "desk": True}),
    ):
        c.refuses(label, lambda settings=settings: mod.parse_settings(json.dumps(settings), base), needs)
    return c.bad


def check_desk_assembly(mod: types.ModuleType, root: Path) -> list[str]:
    """The Desk inside the league: its own ledger under the on-demand identity, a step on the tick, a run that commits through the on-demand
    ledger and settles there, the daily cap counted from the Desk's ledger across a restart, and no RYO call beyond its cap."""
    from hoshitori import desk_hook
    from hoshitori.collect import now_ms
    from tests.desk_hook_checks import cycle, f
    from tests.ryo_fake_server import KEY, FakeRyo

    c = Case(mod)
    clock = OffsetClock()
    saved = {name: os.environ.get(name) for name in (MODEL_KEY_ENV, RYO_KEY_ENV)}
    model = SmartModel().start()
    os.environ[MODEL_KEY_ENV] = model_world.KEY
    os.environ[RYO_KEY_ENV] = KEY
    ryo_fake = FakeRyo(script=ryo_script(clock)).start()

    def read(name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for segment in sorted((root / "a" / "run" / name / "segments").glob("*.jsonl")):
            for line in segment.read_text().splitlines():
                row = json.loads(line)
                if "rec" in row:
                    out.append(json.loads(row["rec"]))
        return out

    try:
        with outside(clock) as world:
            (root / "a").mkdir()
            path = _ondemand_files(
                root / "a", world, mod, models_doc=models_document(model.url), optional_sources=("ryo",), ryo=good_ryo(ryo_fake.url)
            )
            settings = json.loads(path.read_text())
            settings["desk"] = True
            path.write_text(json.dumps(settings))
            with mod.assemble(mod.load_settings(path), clock=clock) as running:
                c.eq("the tick has a Desk step", running.config.desk is not None, True)
                c.eq("the Desk keeps its own ledger, in a directory of its own", (root / "a" / "run" / "desk").is_dir(), True)
                first = scheduler.tick(running.services, running.config)
                c.eq(
                    "a cycle with no event starts no run",
                    (first.ran is not None and first.ran.state.value, first.desked, first.desk_failed),
                    ("COMMITTED", desk_hook.Passed((), (), ()), None),
                )
                ryo_before = sum(1 for s in ryo_fake.seen if s.method == "POST")
                now = now_ms(running.services.env.clock)
                planted = cycle(
                    "cx",
                    [
                        {"symbol": "BTC", "fields": f(ret1="0.05"), "seen_ms": now - 30 * 60_000},
                        {"symbol": "ETH", "fields": f(ret1="0.04"), "seen_ms": now - 30 * 60_000},
                        {"symbol": "ADA", "fields": f(ret1="0.03"), "seen_ms": now - 30 * 60_000},
                        {"symbol": "XRP", "fields": f(ret1="0.025"), "seen_ms": now - 30 * 60_000},
                    ],
                    at=now,
                )
                assert running.config.desk is not None
                got = running.config.desk(scheduler.read_all(running.services) + planted, "cx", now)
                c.eq(
                    "three events, the largest first: BTC commits, ETH abstains, ADA is a token the league does not list",
                    ([o[1] for o in got.outcomes], [x[1] for x in got.failed], len(got.triggers)),
                    (
                        ["COMMITTED", "ABSTAINED"],
                        ["LiveError: an on-demand forecast needs a token the registry resolves to one instrument"],
                        3,
                    ),
                )
                forecast_id = got.outcomes[0][2] if got.outcomes else None
                c.eq("the commitment names a forecast", isinstance(forecast_id, str), True)
                ryo_asked = sum(1 for s in ryo_fake.seen if s.method == "POST") - ryo_before
                asked = [q for q in model.requests if q["response_format"]["json_schema"]["name"] == "desk_propose"]
                c.eq("the model was asked once, by the run that committed (the abstention stopped before it)", len(asked), 1)
                desk_records = read("desk")
                ends = [(r["body"]["symbol"], r["body"]["state"]) for r in desk_records if r["type"] == "desk_run_state"]
                c.eq(
                    "the Desk ledger holds each run's start and end",
                    ends,
                    [("BTC", "RUNNING"), ("BTC", "COMMITTED"), ("ETH", "RUNNING"), ("ETH", "ABSTAINED")],
                )
                spent = [r["body"]["spent"]["calls"].get("ryo", 0) for r in desk_records if r["type"] == "desk_run_state"]
                c.eq("no run spent more RYO calls than its cap", max(spent, default=0) <= desk_hook.RYO_CALLS, True)
                c.eq("and RYO was asked exactly what the runs record", ryo_asked, sum(spent[1::2]))
                od_kinds = [r["type"] for r in read("ondemand")]
                c.eq(
                    "the forecast is on the on-demand ledger, not the league's",
                    (od_kinds.count("forecast"), forecast_id in json.dumps(scheduler.read_all(running.services))),
                    (1, False),
                )
            with mod.assemble(mod.load_settings(path), clock=clock) as running:
                now = now_ms(running.services.env.clock)
                assert running.config.desk is not None
                again = running.config.desk(scheduler.read_all(running.services) + planted, "cx", now)
                c.eq(
                    "after a restart the day's two runs are counted: one place is left, and the events already run are not run again",
                    [x[0] for x in again.failed] + [o[0] for o in again.outcomes],
                    [got.triggers[2]] if len(got.triggers) > 2 else [],
                )
                clock.jump(2 * 3600)
                late = scheduler.tick(running.services, running.config)
                kinds = [r["type"] for r in read("ondemand")]
                c.eq(
                    "after its horizon the league's tick settles the Desk's forecast on the on-demand ledger",
                    (late.on_demand_failed, kinds.count("settlement")),
                    (None, 1),
                )
            with mod.assemble_reading(mod.load_settings(path)) as reading:
                mod.export_site(reading, root / "site")
            shown = json.loads((root / "site" / "desk.json").read_text())
            c.eq(
                "the site shows the Desk's real runs from its own ledger, labelled live, the committed one with its forecast",
                [(r["symbol"], r["state"], r["label"], r["forecast_id"] == forecast_id) for r in shown],
                [("BTC", "COMMITTED", "live", True), ("ETH", "ABSTAINED", "live", False)],
            )
            alerts = json.loads((root / "site" / "alerts.json").read_text())
            c.eq(
                "the export writes the decision alerts of the league's own calls, the settlements among them",
                (isinstance(alerts, list), {a["kind"] for a in alerts} <= {"INVALIDATED", "THESIS_CHANGED", "RISK_STEP", "SETTLED"}),
                (True, True),
            )
            items = json.loads((root / "site" / "items.json").read_text())
            c.eq(
                "and Today's developments carry them, as an investigated call and an abstention, both live",
                sorted((i["symbol"], i["state"], i["label"]) for i in items if i["symbol"] in ("BTC", "ETH")),
                [("BTC", "INVESTIGATED_THESIS", "live"), ("ETH", "NO_RELIABLE_CONCLUSION", "live")],
            )
    finally:
        ryo_fake.stop()
        model.stop()
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return c.bad


def check_arms_of(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    rules = types.SimpleNamespace(arms=(("L0", "h0"),), model_arms=(("L1", "h1", ()), ("L2", "h2", ())))
    c.eq("every arm that forecasts, the rules arm first", mod._arms_of(rules), ["L0", "L1", "L2"])
    c.eq("a protocol with no model arms", mod._arms_of(types.SimpleNamespace(arms=(("L0", "h0"),), model_arms=())), ["L0"])
    return c.bad


def check_practice_configs(mod: types.ModuleType) -> list[str]:
    """The practice books of a league: one for each arm that forecasts, under the protocol's terms, and a shadow book for each model arm."""
    from decimal import Decimal

    from hoshitori import protocol

    c = Case(mod)
    terms = dict(protocol.PRACTICE_DEFAULTS)
    terms["fee_bps"] = "12"
    fake = types.SimpleNamespace(
        arms=(("L0", "h0"),),
        model_arms=(("L1", "h1", ()), ("L2", "h2", ())),
        practice_values=lambda: {k: Decimal(v) for k, v in terms.items()},
    )
    got = mod._practice_configs(fake)
    c.eq("own books, then a shadow book of each model arm", [g.arm for g in got], ["L0", "L1", "L2", "L1.shadow", "L2.shadow"])
    c.eq(
        "each follows the rules arm on the slots its model arm passed",
        [(g.follows, g.passed_by) for g in got],
        [(None, None)] * 3 + [("L0", "L1"), ("L0", "L2")],
    )
    c.eq("every book runs under the protocol's terms", {g.costs.fee_bps for g in got}, {Decimal(12)})
    only = types.SimpleNamespace(arms=(("L0", "h0"),), model_arms=(), practice_values=fake.practice_values)
    c.eq("no model arm, no shadow book", [g.arm for g in mod._practice_configs(only)], ["L0"])
    none = types.SimpleNamespace(arms=(), model_arms=(("L1", "h1", ()),), practice_values=fake.practice_values)
    c.eq("without a rules arm to follow there is no shadow book", [g.arm for g in mod._practice_configs(none)], ["L1"])
    return c.bad


def check_assembly(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    with outside(OffsetClock()) as world:
        path = world_files(root / "ok", world) if (root / "ok").mkdir() is None else root
        with mod.assemble(mod.load_settings(path), clock=OffsetClock()) as running:
            first = scheduler.tick(running.services, running.config)
            c.eq("a league that is assembled runs a cycle", first.ran is not None and first.ran.state.value, "COMMITTED")
            c.eq("and runs no practice portfolio unless the settings ask for it", (running.config.practice, first.practiced), ((), None))
            quiet = root / "site-without-practice"
            mod.export_site(running, quiet)
            c.eq(
                "a league that runs no practice portfolio publishes a document of no books",
                (quiet / "practice.json").read_text().strip(),
                "[]",
            )
            # a sweep page beside the sources it was made from says which swept files are still those; beside none it says nothing of it
            from tests.sweep_view_checks import entry, page, write_tree

            repo = root / "sweep-repo"
            (repo / "docs").mkdir(parents=True)
            write_tree(repo, {"hoshitori.kept": ("a = 1\n", "b = 2\n"), "hoshitori.moved": ("a = 1\n", "b = 2\n")})
            (repo / "src" / "hoshitori" / "moved.py").write_text("changed\n")
            sweeps_page = repo / "docs" / "SWEEP.md"
            sweeps_page.write_text(
                page([entry("hoshitori.kept", {"FAILED": 5}), entry("hoshitori.moved", {"FAILED": 5})]), encoding="utf-8"
            )
            with_sweeps = root / "site-with-sweeps"
            mod.export_site(running, with_sweeps, sweeps=sweeps_page)
            shown = json.loads((with_sweeps / "sweeps.json").read_text())
            c.eq(
                "the page's rows say which swept files are still the files there, gaps first",
                [(r["module"], r["current"], r["reason"]) for r in shown["rows"]],
                [("hoshitori.moved", False, "the module changed after its sweep"), ("hoshitori.kept", True, None)],
            )
            alone = root / "alone"
            alone.mkdir()
            (alone / "SWEEP.md").write_text(page([entry("hoshitori.kept", {"FAILED": 5})]), encoding="utf-8")
            mod.export_site(running, root / "site-alone", sweeps=alone / "SWEEP.md")
            c.eq(
                "a page with no sources beside it claims nothing either way",
                json.loads((root / "site-alone" / "sweeps.json").read_text())["rows"][0]["current"],
                None,
            )
            (alone / "broken.md").write_text("no data here\n", encoding="utf-8")
            c.refuses(
                "a sweep page with no data",
                lambda: mod.export_site(running, root / "site-broken", sweeps=alone / "broken.md"),
                "the sweep page cannot be shown: the page holds no sweep data",
            )
            c.refuses(
                "a sweep page that is not there",
                lambda: mod.export_site(running, root / "site-missing", sweeps=alone / "nowhere.md"),
                "the sweep page cannot be shown: [Errno 2] No such file or directory: '" + str(alone / "nowhere.md") + "'",
            )
            specs = root / "specs"
            specs.mkdir()
            from tests.coverage_view_checks import contract, rubric

            (specs / "RUBRIC_MATRIX.md").write_text(rubric(), encoding="utf-8")
            (specs / "CONTRACT_MATRIX.md").write_text(contract(), encoding="utf-8")
            mod.export_site(running, root / "site-covered", coverage=specs)
            c.eq(
                "the matrices in a folder are published as the page's coverage",
                json.loads((root / "site-covered" / "coverage.json").read_text())["contract_total"],
                6,
            )
            (specs / "CONTRACT_MATRIX.md").write_text("no tables\n", encoding="utf-8")
            c.refuses(
                "matrices that cannot be read",
                lambda: mod.export_site(running, root / "site-uncovered", coverage=specs),
                "the coverage matrices cannot be shown: the contract matrix has no summary for "
                "['CHECKS FOUND', 'CHECK NOT FOUND', 'PARTLY BUILT', 'NOT BUILT', 'OUTSIDE THIS REPOSITORY', 'NO CHECK NAMED']",
            )
            c.refuses(
                "a folder with no matrices",
                lambda: mod.export_site(running, root / "site-nomatrix", coverage=root / "nowhere"),
                "the coverage matrices cannot be shown: [Errno 2] No such file or directory: '"
                + str(root / "nowhere" / "RUBRIC_MATRIX.md")
                + "'",
            )
            judge = running.services.rejudge
            c.eq("the league can judge its stored time-stamps again", judge is not None and type(judge).__name__, "Rejudger")
            if judge is not None and first.ran is not None and first.ran.commitment is not None:
                forecasts = [r for r in scheduler.read_all(running.services) if r["type"] == "forecast"]
                judged = judge(forecasts)
                live = first.ran.commitment
                c.eq(
                    "the stored answers give the cycle's own commitment and level back, through the real gateway's captures",
                    judged is not None and (judged.commitment_id, judged.attestation.level) == (live.commitment_id, live.attestation.level),
                    True,
                )
                c.eq("and the commitment the live run saved is still the only one", len(list(running.config.store.iterdir())), 1)  # type: ignore[union-attr]
            state = mod.status(running)
            c.eq(
                "the status",
                (state["cycles"], state["forecasts"], state["failures"], state["settlements"], state["heartbeat"]),
                ({"COMMITTED": 1}, 6, 0, {}, []),
            )
            c.eq("its records", state["records"] > 40, True)
            c.eq("its slots", state["slots"], {"AWAITING_ORIGIN": 6})
            c.eq(
                "a league with no model arm replays its cycle too",
                mod.replay_cycle(running, first.ran.cycle_id),
                {
                    "cycle": first.ran.cycle_id,
                    "slots": 6,
                    "match": 6,
                    "different": 0,
                    "not_reconstructible": 0,
                    "not_compared": 0,
                    "clean": True,
                    "exceptions": [],
                },
            )
            assert first.ran is not None
            out = root / "bundle"
            done = mod.export_cycle(running, first.ran.cycle_id, out)
            c.eq(
                "the export",
                (done["cycle"], done["records"], done["directory"]),
                (first.ran.cycle_id, _batch_records(running, first.ran.cycle_id), str(out)),
            )
            c.refuses(
                "a cycle that does not exist",
                lambda: mod.export_cycle(running, "nope", root / "b2"),
                "only a committed cycle can be exported",
            )
        # the ledger written is the same ledger when it is opened again
        with mod.assemble(mod.load_settings(path), clock=OffsetClock()) as again:
            c.eq("the registry is rebuilt from the ledger", len(again.services.env.registry.records()), 2)
            c.eq("and the next tick does not run the slot again", scheduler.tick(again.services, again.config).ran, None)
        (root / "practice").mkdir()
        with mod.assemble(mod.load_settings(world_files(root / "practice", world, practice=True)), clock=OffsetClock()) as practising:
            from hoshitori import practice

            c.eq(
                "the practice flag gives the scheduler the default practice configuration", practising.config.practice, (practice.Config(),)
            )
            ticked = scheduler.tick(practising.services, practising.config)
            c.eq(
                "a tick with practice on runs the cycle and the practice pass",
                (ticked.ran is not None, ticked.practiced is not None),
                (True, True),
            )
            site = root / "practice-site"
            mod.export_site(practising, site)
            books = json.loads((site / "practice.json").read_text())
            c.eq(
                "the exported site holds the practice books, one for each cost level, opened with the league's equity",
                [(b["book"], b["opening_equity"], b["trades"], b["mode"]) for b in books],
                [("L0:BASE", "100000", [], "ACTIVE"), ("L0:DOUBLE_FEES", "100000", [], "ACTIVE"), ("L0:STRESS", "100000", [], "ACTIVE")],
            )
            c.eq(
                "with the costs of each level",
                [(b["level"], b["fee_bps"], b["slippage_bps"], b["adverse"]) for b in books],
                [("BASE", "10", "5", False), ("DOUBLE_FEES", "20", "5", False), ("STRESS", "10", "25", True)],
            )
            c.eq("and in the index", "practice.json" in json.loads((site / "index.json").read_text())["files"], True)
            # a practice record whose id is not the id of its content cannot be shown: the export refuses, it does not publish a book it cannot trust
            from hoshitori import book as book_module
            from hoshitori import practice_record

            odd = practice_record.to_record(
                "L0:BASE", book_module.Open("L0", "BASE", Decimal("1"), 0), None, "urn:hoshitori:league:test", 0
            )
            odd["id"] = "pr" + "a" * 26
            practising.ledger.submit(odd)
            practising.ledger.append_pending()
            c.refuses(
                "a practice record that is not what its id says",
                lambda: mod.export_site(practising, root / "practice-refused"),
                "the practice records cannot be shown: a practice record cannot be read: the id is not the id of what the record says",
            )
        # things the settings must agree with
        for label, over, says in (
            (
                "a protocol under another root",
                {"pins": {"root_sha256": "0" * 64}},
                "the protocol was pre-registered under another trusted root than the one given",
            ),
            (
                "another root version",
                {"pins": {"root_version": 2}},
                "the protocol was pre-registered under another trusted root than the one given",
            ),
            (
                "a venue that does not exist",
                {"venues": {"binance-spot": None, "nowhere": None}},
                "nowhere is not a venue (they are "
                + str(
                    sorted(
                        {
                            **__import__("hoshitori.venues", fromlist=["VENUES"]).VENUES,
                            **__import__("hoshitori.venues", fromlist=["SOURCES"]).SOURCES,
                        }
                    )
                )
                + ")",
            ),
            (
                "an optional source that is not among the venues",
                {"optional_sources": ("binance-funding",)},
                "the protocol's optional source binance-funding is not among the venues",
            ),
            (
                "funding without the list of the perpetuals it is joined through",
                {
                    "optional_sources": ("binance-funding",),
                    "venues": {"binance-spot": world.exchange.url, "binance-funding": world.exchange.url},
                    "lists": [{"venue": "binance-spot", "scope": "spot", "kind": "spot"}],
                },
                "funding is joined to a token through the perpetuals of binance-usdm: their list is read too",
            ),
            (
                "funding with the venue and the list on the wrong scope",
                {
                    "optional_sources": ("binance-funding",),
                    "venues": {
                        "binance-spot": world.exchange.url,
                        "binance-funding": world.exchange.url,
                        "binance-usdm": world.exchange.url,
                    },
                    "lists": [{"venue": "binance-usdm", "scope": "spot", "kind": "perpetual"}],
                },
                "funding is joined to a token through the perpetuals of binance-usdm: their list is read too",
            ),
            (
                "funding with the perpetual list and no kind",
                {
                    "optional_sources": ("binance-funding",),
                    "venues": {
                        "binance-spot": world.exchange.url,
                        "binance-funding": world.exchange.url,
                        "binance-usdm": world.exchange.url,
                    },
                    "lists": [{"venue": "binance-spot", "scope": "spot", "kind": "spot"}, {"venue": "binance-usdm", "scope": "perpetual"}],
                },
                'the list of the perpetuals of binance-usdm names its kind, "perpetual": without it the list also holds dated futures and the registry refuses it',
            ),
            (
                "funding with the perpetual list of the wrong kind",
                {
                    "optional_sources": ("binance-funding",),
                    "venues": {
                        "binance-spot": world.exchange.url,
                        "binance-funding": world.exchange.url,
                        "binance-usdm": world.exchange.url,
                    },
                    "lists": [
                        {"venue": "binance-spot", "scope": "spot", "kind": "spot"},
                        {"venue": "binance-usdm", "scope": "perpetual", "kind": "future"},
                    ],
                },
                'the list of the perpetuals of binance-usdm names its kind, "perpetual": without it the list also holds dated futures and the registry refuses it',
            ),
            (
                "funding with the perpetual list of every kind",
                {
                    "optional_sources": ("binance-funding",),
                    "venues": {
                        "binance-spot": world.exchange.url,
                        "binance-funding": world.exchange.url,
                        "binance-usdm": world.exchange.url,
                    },
                    "lists": [
                        {"venue": "binance-spot", "scope": "spot", "kind": "spot"},
                        {"venue": "binance-usdm", "scope": "perpetual", "kind": "all"},
                    ],
                },
                'the list of the perpetuals of binance-usdm names its kind, "perpetual": without it the list also holds dated futures and the registry refuses it',
            ),
            (
                # a venue the collector cannot read as an optional source: before this check it assembled, and every cycle was then
                # abandoned as PROCESS_LOST when the collector refused the source
                "an optional source among the venues that the collector does not read",
                {"optional_sources": ("okx",)},
                "the protocol's optional source okx is not one the collector reads (it reads binance-funding and ryo)",
            ),
            ("no primary venue", {"venues": {"okx": None}}, "the protocol's primary oracle venue binance-spot is not among the venues"),
            (
                "a list of a venue not given",
                {"lists": [{"venue": "okx", "scope": "spot", "kind": "spot"}], "venues": {"binance-spot": None}},
                "the instrument list of okx names a venue that is not among the venues",
            ),
            (
                "too few authorities",
                {"authorities": world.authorities[:1]},
                "the root needs 2 independent operators: there are too few authorities",
            ),
            (
                "an authority twice",
                {"authorities": [world.authorities[0], world.authorities[0], world.authorities[1]]},
                "authorities are named once each",
            ),
            ("a grace as long as the interval", {"start_grace_ms": 3_600_000}, "the start grace is less than the interval between slots"),
        ):
            d = root / label.replace(" ", "_")
            d.mkdir()
            p = world_files(d, world, **over)
            c.refuses(label, lambda p=p: _assemble(mod, p), says)
        # funding named, its venue and the perpetual list given: the league is assembled and holds the adapter
        d = root / "funding"
        d.mkdir()
        funded = world_files(
            d,
            world,
            optional_sources=("binance-funding",),
            venues={
                "binance-spot": world.exchange.url,
                "okx": world.exchange.url,
                "binance-funding": world.exchange.url,
                "binance-usdm": world.exchange.url,
            },
            lists=[
                {"venue": "binance-spot", "scope": "spot", "kind": "spot"},
                {"venue": "binance-usdm", "scope": "perpetual", "kind": "perpetual"},
            ],
        )
        with mod.assemble(mod.load_settings(funded), clock=OffsetClock()) as with_funding:
            c.eq(
                "the funding adapter is attached beside the exchanges and the protocol names it",
                (sorted(with_funding.services.env.adapters), with_funding.services.protocol.optional_sources),
                (["binance-funding", "binance-spot", "binance-usdm", "okx"], ("binance-funding",)),
            )
        # a log key the root does not allow
        d = root / "otherkey"
        d.mkdir()
        other_vkey = R.log_signer(9).verifier.vkey()
        c.refuses(
            "a log key the root does not know",
            lambda: _assemble(mod, world_files(d, world, vkey=other_vkey)),
            "the log's signing key is not allowed by the trusted root for the size the ledger has now",
        )
        # files that cannot be used
        d = root / "nofiles"
        d.mkdir()
        p = world_files(d, world)
        (d / "mapping.json").write_bytes(b"garbage")
        c.eq(
            "a mapping that is not one",
            _message(lambda: _assemble(mod, p)).startswith("a file named in the settings cannot be used: "),
            True,
        )
        os.remove(d / "root.json")
        c.eq(
            "a root that is not there",
            _message(lambda: _assemble(mod, p)).startswith("a file named in the settings cannot be used: "),
            True,
        )
    return c.bad


def check_make_protocol(mod: types.ModuleType, root: Path) -> list[str]:
    import argparse

    c = Case(mod)
    document = R.make_document(4, root_keys=[R.new_signer(1)])
    root_bytes = canonical.dumps(document)  # type: ignore[arg-type]
    (root / "root.json").write_bytes(root_bytes)
    mapping = graded(rules_arm)
    (root / "map.json").write_bytes(rules_arm.dump(mapping))

    def args(**over: Any) -> argparse.Namespace:
        values: dict[str, Any] = {
            "out": root / "protocol.json",
            "root": root / "root.json",
            "mapping": [f"L0={root / 'map.json'}"],
            "barrier_mapping": [],
            "name": "first",
            "start_ms": 1_793_275_200_000,
            "every_minutes": 60,
            "watchlist": " eth, btc ,,ETH",
            "primary": "binance-spot",
            "second": "okx",
            "models": None,
            "cutoff": [],
            "optional_source": [],
        }
        values.update(over)
        return argparse.Namespace(**values)

    done = mod.make_protocol(args())
    written = protocol.load((root / "protocol.json").read_bytes())
    parsed_root = __import__("hoshitori.trusted_root", fromlist=["parse_root"]).parse_root(root_bytes)
    c.eq(
        "the file is the standard protocol, pinned to the root and the mapping",
        written,
        protocol.standard(
            name="first",
            start_ms=1_793_275_200_000,
            every_ms=3_600_000,
            root_version=4,
            root_sha256=hashlib.sha256(root_bytes).hexdigest(),
            mappings={"L0": mapping.sha256},
            watchlist=("BTC", "ETH"),
        ),
    )
    c.eq(
        "what is said about it",
        done,
        {
            "file": str(root / "protocol.json"),
            "sha256": written.sha256,
            "slots_from_ms": 1_793_275_200_000,
            "root_sha256": parsed_root.sha256,
        },
    )
    c.eq("the interval is in minutes", written.schedule_every_ms, 3_600_000)
    c.eq("no optional source unless one is named", written.optional_sources, ())
    mod.make_protocol(args(out=root / "funded.json", optional_source=["binance-funding"]))
    funded = protocol.load((root / "funded.json").read_bytes())
    c.eq("an optional source named on the command line is in the protocol", funded.optional_sources, ("binance-funding",))
    unreadable = root / "unreadable-source.json"
    c.refuses(
        "an optional source the collector does not read",
        lambda: mod.make_protocol(args(out=unreadable, optional_source=["kraken"])),
        "the protocol's optional source kraken is not one the collector reads (it reads binance-funding and ryo)",
    )
    c.eq("and no protocol file is left behind for it", unreadable.exists(), False)
    c.refuses("never overwritten", lambda: mod.make_protocol(args()), "a protocol file is never overwritten")
    other = root / "second.json"
    c.refuses("a mapping without a file", lambda: mod.make_protocol(args(out=other, mapping=["L0"])), "a mapping is ARM=FILE")
    from tests.league_checks import barrier_map

    bm = barrier_map()
    (root / "barrier.json").write_bytes(barrier_rates.dump(bm))
    with_barrier = mod.make_protocol(args(out=root / "pinned.json", barrier_mapping=[f"L0={root / 'barrier.json'}"]))
    pinned = protocol.load((root / "pinned.json").read_bytes())
    c.eq(
        "a barrier mapping is pinned by its hash",
        (pinned.barrier_arms, with_barrier["sha256"] == pinned.sha256),
        ((("L0", bm.sha256),), True),
    )
    c.eq("and the directional pin is as before", pinned.arms, written.arms)
    c.refuses(
        "a barrier mapping without a file",
        lambda: mod.make_protocol(args(out=other, barrier_mapping=["L0"])),
        "a barrier mapping is ARM=FILE",
    )
    c.refuses(
        "a barrier mapping without an arm",
        lambda: mod.make_protocol(args(out=other, barrier_mapping=["=x"])),
        "a barrier mapping is ARM=FILE",
    )
    c.eq(
        "a barrier mapping file that is not one",
        _message(lambda: mod.make_protocol(args(out=other, barrier_mapping=[f"L0={root / 'map.json'}"]))).startswith(
            "the protocol cannot be made: "
        ),
        True,
    )
    c.refuses("a mapping without an arm", lambda: mod.make_protocol(args(out=other, mapping=["=x"])), "a mapping is ARM=FILE")
    c.eq(
        "an arm that is not built",
        _message(lambda: mod.make_protocol(args(out=other, mapping=[f"L9={root / 'map.json'}"]))).startswith(
            "the protocol cannot be made: the arms are some of"
        ),
        True,
    )
    c.eq(
        "a root that is not there",
        _message(lambda: mod.make_protocol(args(out=other, root=root / "none.json"))).startswith("the protocol cannot be made: "),
        True,
    )
    c.eq(
        "a mapping file that is not there",
        _message(lambda: mod.make_protocol(args(out=other, mapping=[f"L0={root / 'none.json'}"]))).startswith(
            "the protocol cannot be made: "
        ),
        True,
    )
    c.eq(
        "a symbol that is not allowed",
        _message(lambda: mod.make_protocol(args(out=other, watchlist="B" * 41))).startswith("the protocol cannot be made: the watchlist"),
        True,
    )
    c.eq(
        "none of those wrote a file",
        sorted(p.name for p in root.iterdir() if p.name.endswith(".json")),
        ["barrier.json", "funded.json", "map.json", "pinned.json", "protocol.json", "root.json"],
    )
    return c.bad


def check_killed_after_the_stamps(mod: types.ModuleType, root: Path) -> list[str]:
    """A league whose process died after its batch was stamped by the real authorities and before the cycle's steps: the next tick
    rolls the slots forward at the level the kept answers support, saves the commitment the dead process never saved, and the cycle's bundle
    can be exported and verified at C3."""
    from hoshitori import league as league_module
    from tests.recovery_checks import Died

    c = Case(mod)
    clock = OffsetClock()
    with outside(clock) as world:
        path = world_files(root / "k", world, ago_minutes=185) if (root / "k").mkdir() is None else root
        settings = mod.load_settings(path)
        with mod.assemble(settings, clock=clock) as running:
            real = league_module._after

            def die(*_args: Any, **_kwargs: Any) -> None:
                raise Died

            league_module._after = die
            try:
                with contextlib.suppress(Died):
                    scheduler.tick(running.services, running.config)
            finally:
                league_module._after = real
            state = mod.status(running)
            c.eq(
                "killed after the stamps: the cycle is mid-commit with its forecasts in the log",
                (state["cycles"], sum(1 for r in scheduler.read_all(running.services) if r["type"] == "forecast")),
                ({"COMMITTING": 1}, 6),
            )
            store = settings.directory / "commitments"
            c.eq("and the dead process saved no commitment", list(store.iterdir()) if store.exists() else [], [])
            c.eq(
                "though the log had signed the checkpoint that was stamped",
                len(list(running.ledger.directory.glob("checkpoints.jsonl"))),
                1,
            )
            after = scheduler.tick(running.services, running.config)
            c.eq(
                "the next tick rolls the six slots forward",
                [(x.state_was.value, x.slots_failed, x.slots_rolled) for x in after.closed],
                [("COMMITTING", 0, 6)],
            )
            records = scheduler.read_all(running.services)
            lost = cycle.rebuild([r for r in records if r["type"] == cycle.TYPE])
            first = min(lost.values(), key=lambda h: h[0].step.at_ms)
            slots = lifecycle.rebuild([r for r in records if r["type"] == lifecycle.TYPE])
            levels = {
                next(t.step.level.value for t in h if t.step.state is lifecycle.State.COMMITTED and t.step.level is not None)
                for h in slots.values()
                if h[0].step.at_ms <= first[-1].step.at_ms
            }
            c.eq("at the level the kept answers support, not at C1", levels, {"C3"})
            c.eq(
                "the cycle names the real commitment, and it is saved now",
                (first[-1].step.state.value, (store / str(first[-1].step.commitment_id)).is_dir()),
                ("COMMITTED", True),
            )
            out = root / "k-bundle"
            mod.export_cycle(running, first[0].cycle_id, out)
            c.eq("its bundle is exported and the offline verifier finds C3 and scorable", _verdict(root, out, "k"), ("C3", True, True))
    return c.bad


def check_running(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    clock = OffsetClock()
    with outside(clock) as world:
        path = world_files(root / "w", world, ago_minutes=185) if (root / "w").mkdir() is None else root
        settings = mod.load_settings(path)
        c.eq("nothing is made before it is asked for", settings.directory.exists(), False)
        with mod.assemble(settings, clock=clock) as running:
            c.eq("the clock given is the clock used", running.services.env.clock is clock, True)
            c.eq("the ledger takes its segment size from the constant", (running.ledger.segment_records, mod.SEGMENT_RECORDS), (5000, 5000))
            runners = [t for t in threading.enumerate() if t.name == "gateway-runner"]
            c.eq("one runner thread, alive, which does not hold the process", [(t.is_alive(), t.daemon) for t in runners], [(True, True)])
            first = scheduler.tick(running.services, running.config)
            c.eq("a cycle is committed", first.ran is not None and first.ran.state.value, "COMMITTED")
            c.eq(
                "at C3: the tokens reached the authorities",
                first.ran is not None and first.ran.commitment is not None and first.ran.commitment.level,
                "C3",
            )
            c.eq("what is kept, and where", sorted(os.listdir(settings.directory)), ["captures", "commitments", "dispatch.jsonl", "ledger"])
            assert first.ran is not None
            state = mod.status(running)
            c.eq(
                "the keys of the status",
                sorted(state),
                ["cycles", "failures", "forecasts", "heartbeat", "ledger", "records", "settlements", "slots"],
            )
            c.eq("the state of the ledger", state.get("ledger"), {"state": "OK", "detail": ""})
            c.eq("records", state["records"], running.ledger.size)
            c.eq(
                "cycles, slots, forecasts, failures, settlements",
                (state["cycles"], state["slots"], state["forecasts"], state["failures"], state["settlements"]),
                ({"COMMITTED": 1}, {"AWAITING_ORIGIN": 6}, 6, 0, {}),
            )
            rules = running.services.protocol
            missed = [{"slot": rules.slot(k), "finding": "MISSING", "cycle": None} for k in range(3)]
            c.eq("the slots that were missed", state["heartbeat"], missed)
            out = root / "bundle"
            exported_before = mod.export_cycle(running, first.ran.cycle_id, root / "bundle-before")
            c.eq("before the log signs another checkpoint the bundle has no later part", exported_before["later_records"], 0)
            early = root / "site-early"
            mod.export_site(running, early)
            kept_bundle = early / "bundles" / first.ran.cycle_id
            c.eq("and neither has the bundle of a site exported then", (kept_bundle / "consistency.json").exists(), False)
            running.ledger.checkpoint()  # what the next commitment, or the writer closing, does: sign the head after the later steps
            done = mod.export_cycle(running, first.ran.cycle_id, out)
            mod.export_site(running, early)
            c.eq("the same site exported again gains the later part in its bundle", (kept_bundle / "consistency.json").exists(), True)
            c.eq("which the verifier accepts", _verdict(root, kept_bundle), ("C3", True, True))
            stamp = (kept_bundle / "consistency.json").stat().st_mtime_ns
            mod.export_site(running, early)
            c.eq(
                "and exported once more with nothing new the bundle is left as it is",
                (kept_bundle / "consistency.json").stat().st_mtime_ns,
                stamp,
            )
            c.eq("with no partial or old bundle left", sorted(q.name for q in (early / "bundles").iterdir()), [first.ran.cycle_id])
            # a refresh that fails keeps the bundle that is there: a whole bundle without the newest later part is not a lost one
            (kept_bundle / "consistency.json").write_bytes(b"not json")  # unreadable: the bundle is to be made again
            real_export = mod.bundle.export_bundle

            def refuses(out_dir: Any, *args: Any, **kwargs: Any) -> None:
                raise mod.bundle.BundleError("the disk is full")

            mod.bundle.export_bundle = refuses  # type: ignore[assignment]
            try:
                kept = mod.export_site(running, early)
            finally:
                mod.bundle.export_bundle = real_export  # type: ignore[assignment]
            c.eq(
                "a refresh that fails leaves the bundle that was there and says it has one",
                (
                    kept["bundles"],
                    kept["without_bundle"],
                    (kept_bundle / "consistency.json").read_bytes(),
                    sorted(q.name for q in (early / "bundles").iterdir()),
                ),
                ([first.ran.cycle_id], [], b"not json", [first.ran.cycle_id]),
            )
            listing = json.loads((early / "bundles.json").read_text())
            c.eq("and still lists it for the page", [entry["cycle_id"] for entry in listing], [first.ran.cycle_id])
            leftover = early / "bundles" / f".{first.ran.cycle_id}.old"
            leftover.mkdir()
            (leftover / "half").write_text("what an interrupted refresh left")
            mod.export_site(running, early)
            c.eq("and the next export repairs it", _verdict(root, kept_bundle), ("C3", True, True))
            c.eq("whatever an interrupted refresh left behind", sorted(q.name for q in (early / "bundles").iterdir()), [first.ran.cycle_id])
            c.eq("a size that is not a number is no later part", mod._later_part_size(root), 0)
            (root / "later-size").mkdir()
            (root / "later-size" / "consistency.json").write_bytes(canonical.dumps({"to_size": True}))
            c.eq("nor is a size that is a boolean", mod._later_part_size(root / "later-size"), 0)
            (root / "later-size" / "consistency.json").write_bytes(canonical.dumps({"to_size": 9}))
            c.eq("a whole number is the size", mod._later_part_size(root / "later-size"), 9)
            (kept,) = os.listdir(settings.directory / "commitments")
            c.eq(
                "the export",
                done,
                {
                    "cycle": first.ran.cycle_id,
                    "commitment": kept,
                    "records": _batch_records(running, first.ran.cycle_id),
                    "later_records": 12,
                    "directory": str(out),
                },
            )
            c.eq("and a bundle was written", (out / "manifest.json").is_file() or any(out.iterdir()), True)
            c.eq("which the offline verifier finds C3 and scorable", _verdict(root, out), ("C3", True, True))
            later = _verdict_of(root, out)["cycle"]["later"]
            c.eq(
                "and which shows, under a later checkpoint, that each of the six forecasts was committed and waits for its origin",
                (
                    later["shown"],
                    later["failures"],
                    len(later["forecasts"]),
                    {(f["committed_level"], f["awaiting"], f["origin"]) for f in later["forecasts"].values()},
                ),
                (True, 0, 6, {("C3", "ok", "absent")}),
            )
            site = root / "site"
            made = mod.export_site(running, site)
            c.eq(
                "the site: its files, and the bundle of the one committed cycle",
                (made["directory"], made["files"] > 10, made["removed"], made["bundles"], made["without_bundle"]),
                (str(site), True, 0, [first.ran.cycle_id], []),
            )
            index = json.loads((site / "index.json").read_text())
            c.eq(
                "its index says what the period is",
                (index["mode"], index["counts"]["cycles"], index["counts"]["receipts"]),
                ("Exploratory run: not a scored period", 1, 6),
            )
            c.eq(
                "the bundle in the site is the one the verifier accepts",
                _verdict(root, site / "bundles" / first.ran.cycle_id),
                ("C3", True, True),
            )
            c.eq("no partial bundle is left", sorted(p.name for p in (site / "bundles").iterdir()), [first.ran.cycle_id])
            listing = json.loads((site / "bundles.json").read_text())
            folder = site / "bundles" / first.ran.cycle_id
            on_disk = sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file())
            c.eq(
                "the site lists its bundle, with every file in it",
                [(b["cycle_id"], b["path"], b["files"]) for b in listing],
                [(first.ran.cycle_id, f"bundles/{first.ran.cycle_id}", on_disk)],
            )
            c.eq(
                "and its folders",
                listing[0]["directories"],
                sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_dir()),
            )
            c.eq("the bundle has files to list", len(on_disk) > 5, True)
            c.eq("and the index counts it", index["counts"]["bundles"], 1)
            c.eq(
                "the trusted root is published as the league has it",
                (site / "trusted_root.json").read_bytes(),
                settings.trusted_root.read_bytes(),
            )
            before = (
                (site / "bundles" / first.ran.cycle_id / "manifest.json").stat().st_mtime_ns
                if (site / "bundles" / first.ran.cycle_id / "manifest.json").exists()
                else None
            )
            later = mod.export_site(running, site, scored=True)
            c.eq(
                "a second export keeps the bundle it has and says the period is scored",
                (later["bundles"], later["without_bundle"], json.loads((site / "index.json").read_text())["mode"]),
                ([first.ran.cycle_id], [], "Scored period"),
            )
            c.eq(
                "and the site still lists it, with its files",
                [(b["cycle_id"], b["files"]) for b in json.loads((site / "bundles.json").read_text())],
                [(first.ran.cycle_id, on_disk)],
            )
            c.eq(
                "and does not rewrite the bundle",
                before,
                (site / "bundles" / first.ran.cycle_id / "manifest.json").stat().st_mtime_ns if before is not None else None,
            )
            # the forged copy: someone with the log's key changes a call and signs a new log; the original time-stamps no longer fit it
            ledger_before = (running.ledger.size, running.ledger.root())
            forged_dir = root / "forged"
            forging = mod.forge_cycle(running, first.ran.cycle_id, forged_dir)
            c.eq("the forgery is of the cycle, and says which record, field and values", forging["cycle"], first.ran.cycle_id)
            c.eq(
                "the edit changes one chance",
                (
                    forging["edit"]["field"],
                    forging["edit"]["was"] != forging["edit"]["now"],
                    forging["edit"]["now"] in ("0.990000", "0.010000"),
                ),
                ("p", True, True),
            )
            c.eq("the two roots differ", forging["honest_root"] != forging["forged_root"], True)
            c.eq(
                "what the command reports",
                (sorted(forging), sorted(forging["edit"]), forging["directory"], forging["standin_vkey"].startswith("hoshitori.example/")),
                (
                    ["cycle", "directory", "edit", "forged_root", "honest_root", "standin_vkey"],
                    ["field", "now", "record", "was"],
                    str(forged_dir),
                    True,
                ),
            )
            c.eq("what is written", sorted(os.listdir(forged_dir)), ["bundle", "forgery.json", "trusted_root.json"])
            c.eq("the league's own ledger is as it was", (running.ledger.size, running.ledger.root()), ledger_before)
            c.eq(
                "the verifier finds the forged copy intact but not time-stamped, and not scorable, against the demonstration root",
                _verdict_against(forged_dir / "trusted_root.json", forged_dir / "bundle"),
                ("C1", False, True),
            )
            c.eq(
                "the league's real root does not accept the stand-in key",
                _verdict_against(root / "w" / "root.json", forged_dir / "bundle")[2],
                False,
            )
            real_root = json.loads((root / "w" / "root.json").read_text())
            demo_root = json.loads((forged_dir / "trusted_root.json").read_text())
            c.eq(
                "the demonstration root is the real one with the log's key replaced",
                ({k: v for k, v in demo_root.items() if k != "log"}, demo_root["log"]["origin"], len(demo_root["log"]["eras"])),
                ({k: v for k, v in real_root.items() if k != "log"}, real_root["log"]["origin"], 1),
            )
            c.eq(
                "and the real log key is not the one in it",
                demo_root["log"]["eras"][0]["vkey"] != real_root["log"]["eras"][0]["vkey"],
                True,
            )
            c.eq("the honest bundle is still C3 and scorable", _verdict(root, out), ("C3", True, True))
            empty_out = root / "f-empty"
            empty_out.mkdir()
            nested = mod.forge_cycle(running, first.ran.cycle_id, empty_out)
            c.eq("an output directory that exists and is empty is used", nested["forged_root"], forging["forged_root"])
            deep = root / "f-deep" / "a" / "b"
            mod.forge_cycle(running, first.ran.cycle_id, deep)
            c.eq(
                "and one whose parents do not exist is made",
                sorted(q.name for q in deep.iterdir()),
                ["bundle", "forgery.json", "trusted_root.json"],
            )
            junk = root / "junk-root.json"
            junk.write_text("[]")
            c.refuses(
                "a league whose own root cannot be read as a root document",
                lambda: mod.forge_cycle(
                    dataclasses.replace(running, settings=dataclasses.replace(running.settings, trusted_root=junk)),
                    first.ran.cycle_id,
                    root / "f-junk",
                ),
                "the forgery cannot be made: the real root cannot be read",
            )
            real_forge = mod.forgery.forge
            mod.forgery.forge = lambda *a, **k: (_ for _ in ()).throw(forgery.ForgeryError("a record cannot be replayed"))
            try:
                c.refuses(
                    "a forgery that cannot be made is a refusal, not a crash",
                    lambda: mod.forge_cycle(running, first.ran.cycle_id, root / "f-fails"),
                    "the forgery cannot be made: a record cannot be replayed",
                )
            finally:
                mod.forgery.forge = real_forge
            missing_root = root / "no-such-root.json"
            c.refuses(
                "a league whose root file is gone",
                lambda: mod.forge_cycle(
                    dataclasses.replace(running, settings=dataclasses.replace(running.settings, trusted_root=missing_root)),
                    first.ran.cycle_id,
                    root / "f-gone",
                ),
                f"the forgery cannot be made: [Errno 2] No such file or directory: {str(missing_root)!r}",
            )
            c.refuses(
                "a cycle that is not committed",
                lambda: mod.forge_cycle(running, "nope", root / "f2"),
                "only a committed cycle can be exported",
            )
            (root / "f3").mkdir()
            (root / "f3" / "x").write_text("x")
            c.refuses(
                "an output directory that is not empty",
                lambda: mod.forge_cycle(running, first.ran.cycle_id, root / "f3"),
                "the output directory must be new or empty",
            )
            kept_none = dataclasses.replace(running, config=dataclasses.replace(running.config, store=None))
            c.refuses(
                "a league that keeps no commitments",
                lambda: mod.forge_cycle(kept_none, first.ran.cycle_id, root / "f4"),
                "this league keeps no commitments",
            )
            shown = root / "site-forged"
            with_forged = mod.export_site(running, shown, forged=forged_dir)
            c.eq("the site holds the cycle's bundle", with_forged["bundles"], [first.ran.cycle_id])
            document = json.loads((shown / "forgery.json").read_text())
            c.eq(
                "the site's forgery document: the cycle, the change, where the copy is",
                (document["cycle_id"], document["edit"]["field"], document["path"]),
                (first.ran.cycle_id, "p", f"forged/{first.ran.cycle_id}"),
            )
            copy_dir = shown / "forged" / first.ran.cycle_id
            c.eq(
                "it lists every file of the copy",
                document["files"],
                sorted(q.relative_to(copy_dir).as_posix() for q in copy_dir.rglob("*") if q.is_file()),
            )
            c.eq("the copy on the site is the forged one", _verdict_against(shown / "forged_root.json", copy_dir), ("C1", False, True))
            leftover = root / "site-leftover"
            (leftover / "forged" / f".{first.ran.cycle_id}.part").mkdir(parents=True)
            (leftover / "forged" / f".{first.ran.cycle_id}.part" / "half").write_text("half a copy")
            mod.export_site(running, leftover, forged=forged_dir)
            c.eq(
                "a half-written copy left by an interrupted export is cleared and the copy made whole",
                (
                    sorted(q.name for q in (leftover / "forged").iterdir()),
                    _verdict_against(leftover / "forged_root.json", leftover / "forged" / first.ran.cycle_id),
                ),
                ([first.ran.cycle_id], ("C1", False, True)),
            )
            c.eq(
                "and the root beside it is the demonstration's, as it was made",
                (shown / "forged_root.json").read_bytes(),
                (forged_dir / "trusted_root.json").read_bytes(),
            )
            c.eq(
                "the honest bundle on the same site is still C3 and scorable",
                _verdict(root, shown / "bundles" / first.ran.cycle_id),
                ("C3", True, True),
            )
            c.eq(
                "the forged copy is not listed among the bundles",
                [b["cycle_id"] for b in json.loads((shown / "bundles.json").read_text())],
                [first.ran.cycle_id],
            )
            c.eq("and is not under bundles/", sorted(q.name for q in (shown / "bundles").iterdir()), [first.ran.cycle_id])
            c.eq("no partial copy is left", sorted(q.name for q in (shown / "forged").iterdir()), [first.ran.cycle_id])
            again = mod.export_site(running, shown, forged=forged_dir)
            c.eq("a second export keeps it", (again["bundles"], (shown / "forgery.json").exists()), ([first.ran.cycle_id], True))
            plain = mod.export_site(running, root / "site-plain")
            c.eq(
                "a site made without one has none",
                (root / "site-plain" / "forgery.json").exists() or (root / "site-plain" / "forged").exists(),
                False,
            )
            (root / "f5").mkdir()
            c.refuses(
                "a forgery directory with no document",
                lambda: mod.export_site(running, root / "site-f5", forged=root / "f5"),
                "the forgery cannot be shown: [Errno 2] No such file or directory: " + repr(str(root / "f5" / "forgery.json")),
            )
            noroot = root / "f9"
            shutil.copytree(forged_dir, noroot)
            (noroot / "trusted_root.json").unlink()
            c.refuses(
                "a forgery directory with no root",
                lambda: mod.export_site(running, root / "site-f9", forged=noroot),
                "the forgery cannot be shown: [Errno 2] No such file or directory: " + repr(str(noroot / "trusted_root.json")),
            )
            wrongkey = root / "f10"
            shutil.copytree(forged_dir, wrongkey)
            (wrongkey / "trusted_root.json").write_bytes((root / "w" / "root.json").read_bytes())
            c.refuses(
                "a forgery whose root names another key than its stand-in's",
                lambda: mod.export_site(running, root / "site-f10", forged=wrongkey),
                "the forgery's root does not name its stand-in key",
            )
            junkroot = root / "f11"
            shutil.copytree(forged_dir, junkroot)
            (junkroot / "trusted_root.json").write_text("{")
            c.refuses(
                "a forgery whose root is not a root",
                lambda: mod.export_site(running, root / "site-f11", forged=junkroot),
                "the forgery's root is not a trusted root: MALFORMED: document: object key must be a string at offset 1",
            )
            torn = root / "f6"
            shutil.copytree(forged_dir, torn)
            (torn / "forgery.json").write_text("{")
            c.refuses(
                "a forgery document that is not one",
                lambda: mod.export_site(running, root / "site-f6", forged=torn),
                "the forgery cannot be shown: " + forgery.DOCUMENT_REFUSAL,
            )
            elsewhere = root / "f7"
            shutil.copytree(forged_dir, elsewhere)
            other = json.loads((elsewhere / "forgery.json").read_text())
            other["cycle_id"] = "cyzzzz"
            (elsewhere / "forgery.json").write_text(json.dumps(other))
            c.refuses(
                "a forgery of a cycle the site holds no bundle of",
                lambda: mod.export_site(running, root / "site-f7", forged=elsewhere),
                "the forgery was made from a cycle whose bundle this site does not hold",
            )
            nobundle = root / "f8"
            shutil.copytree(forged_dir, nobundle)
            shutil.rmtree(nobundle / "bundle")
            c.refuses(
                "a forgery directory with no bundle",
                lambda: mod.export_site(running, root / "site-f8", forged=nobundle),
                "the forgery directory holds no bundle",
            )
            del plain
            keeps_none_site = dataclasses.replace(running, config=dataclasses.replace(running.config, store=None))
            none_made = mod.export_site(keeps_none_site, root / "site2")
            c.eq(
                "a league that keeps no commitments has a site and no bundle",
                (none_made["bundles"], none_made["without_bundle"]),
                ([], [first.ran.cycle_id]),
            )
            c.eq("and no partial bundle either", sorted(p.name for p in (root / "site2" / "bundles").iterdir()), [])
            c.eq("and lists none", json.loads((root / "site2" / "bundles.json").read_text()), [])
            table = root / "status.md"
            table.write_text("| Unit | Status | Evidence |\n|---|---|---|\n| A part | TESTED | its checks |\n", encoding="utf-8")
            with_status = mod.export_site(running, root / "site6", status=table)
            c.eq(
                "a site with a status table has the page's rows",
                json.loads((root / "site6" / "quality.json").read_text())[0]["unit"],
                "A part",
            )
            c.eq("and one without has none", (root / "site" / "quality.json").exists(), False)
            c.eq("the export still reports its bundles", with_status["bundles"], [first.ran.cycle_id])
            challenged = json.loads((root / "site6" / "challenges.json").read_text())
            c.eq(
                "the rules arm's challenges are written for the cycle whose bundle was written",
                ({r["cycle_id"] for r in challenged}, len(challenged) > 0, {r["outcome"] for r in challenged}),
                ({first.ran.cycle_id}, True, {"EVIDENCE_REQUIRED"}),
            )
            c.eq("and none for a site with no bundle", json.loads((root / "site2" / "challenges.json").read_text()), [])
            near = json.loads((root / "site6" / "boundaries.json").read_text())
            c.eq(
                "how far each call was from changing is written for the same cycle",
                ({r["cycle_id"] for r in near}, len(near) > 0, all(1 <= len(r["moves"]) <= 2 for r in near)),
                ({first.ran.cycle_id}, True, True),
            )
            c.eq("and none for a site with no bundle", json.loads((root / "site2" / "boundaries.json").read_text()), [])
            table.write_text("| Unit | Status | Evidence |\n|---|---|---|\n| A part | DONE | its checks |\n", encoding="utf-8")
            c.refuses(
                "a status table with a word that is not one of the five",
                lambda: mod.export_site(running, root / "site7", status=table),
                "the status file cannot be shown: 'DONE' does not start with one of the five status words",
            )
            c.refuses(
                "a status file that is not there",
                lambda: mod.export_site(running, root / "site8", status=root / "nowhere.md"),
                f"the status file cannot be shown: [Errno 2] No such file or directory: '{root / 'nowhere.md'}'",
            )
            c.eq("and nothing is written for either", (root / "site7").exists() or (root / "site8").exists(), False)
            observed = {"cycle": "Committed", "level": "C3", "slots": [["Open", 1]], "reasons": [], "forecasts": 1}
            record = [
                {
                    "id": "d",
                    "title": "t",
                    "injected": "i",
                    "expected": "e",
                    "observed": observed,
                    "expected_observed": observed,
                    "held": True,
                }
            ]
            drills = root / "drill.json"
            drills.write_text(json.dumps(record), encoding="utf-8")
            mod.export_site(running, root / "site10", drills=drills)
            c.eq("a site with drills has them", json.loads((root / "site10" / "drill.json").read_text())[0]["id"], "d")
            c.eq("and one without has none", (root / "site6" / "drill.json").exists(), False)
            drills.write_text("[]", encoding="utf-8")
            c.refuses(
                "a drill file with no drill",
                lambda: mod.export_site(running, root / "site11", drills=drills),
                "the drill file cannot be shown: the drill file is a list of at least one drill",
            )
            c.refuses(
                "a drill file that is not there",
                lambda: mod.export_site(running, root / "site12", drills=root / "nowhere.json"),
                f"the drill file cannot be shown: [Errno 2] No such file or directory: '{root / 'nowhere.json'}'",
            )
            c.eq("and nothing is written for either drill file", (root / "site11").exists() or (root / "site12").exists(), False)
            claims = root / "claims.json"
            claims.write_text(json.dumps([claims_made(claims_file)]), encoding="utf-8")
            mod.export_site(running, root / "site13", claims=claims)
            c.eq("a site with claims has them", json.loads((root / "site13" / "claims.json").read_text())[0]["id"], "golden")
            c.eq("and one without has none", (root / "site6" / "claims.json").exists(), False)
            c.eq("a claim with nothing about a move has no check", json.loads((root / "site13" / "claim_checks.json").read_text()), [])
            moving = claims_made(
                claims_file,
                tuple(
                    reply(
                        {
                            "nodes": [{"id": "a", "type": "OBSERVATION", "quote": "BTC fell 4% in the last 24 hours"}],
                            "links": [],
                            "implied": [],
                        },
                        i,
                    )
                    for i in (1, 2, 3)
                ),
                "BTC fell 4% in the last 24 hours",
                "move",
                ("BTC",),
            )
            claims.write_text(json.dumps([moving]), encoding="utf-8")
            mod.export_site(running, root / "site16", claims=claims)
            checked = json.loads((root / "site16" / "claim_checks.json").read_text())
            c.eq(
                "a claim about a move is checked against the cycle the site holds",
                (
                    [x["claim_id"] for x in checked],
                    [x["cycle_id"] for x in checked],
                    [x["status"] in ("SUPPORTED", "CONTRADICTED") for x in checked],
                ),
                (["move"], [first.ran.cycle_id], [True]),
            )
            c.eq("and one without claims has none", (root / "site6" / "claim_checks.json").exists(), False)
            real_read = mod.scheduler.read_ledger

            def stray_snapshot(ledger: Any) -> list[dict[str, Any]]:
                records = real_read(ledger)
                shot = next(r for r in records if r.get("type") == "snapshot")
                return [*records, {**shot, "body": {**shot["body"], "cycle_id": "cy-without-manifest"}}]

            # the records of a ledger that holds a snapshot of a cycle whose manifest it does not hold
            mod.scheduler.read_ledger = stray_snapshot
            try:
                c.refuses(
                    "claims on a ledger holding a snapshot without its manifest: the move check cannot be made, and the export says so",
                    lambda: mod.export_site(running, root / "site17", claims=claims),
                    "the claims cannot be checked against the ledger: the ledger holds a snapshot without its manifest",
                )
            finally:
                mod.scheduler.read_ledger = real_read
            claims.write_text("[]", encoding="utf-8")
            c.refuses(
                "a claims file with no claim",
                lambda: mod.export_site(running, root / "site14", claims=claims),
                "the claims file cannot be shown: the claims file is a list of at least one claim",
            )
            c.refuses(
                "a claims file that is not there",
                lambda: mod.export_site(running, root / "site15", claims=root / "nowhere.json"),
                f"the claims file cannot be shown: [Errno 2] No such file or directory: '{root / 'nowhere.json'}'",
            )
            c.eq("and nothing is written for either claims file", (root / "site14").exists() or (root / "site15").exists(), False)
            from hoshitori import desk as desk_module
            from hoshitori import desk_view
            from tests.desk_checks import World as DeskWorld

            desk_world = DeskWorld(desk_module)
            desk_world.run(binding="recorded")
            desk_file = root / "desk_runs.json"
            desk_file.write_bytes(desk_view.file_bytes(desk_world.records))
            mod.export_site(running, root / "site30", desk_runs=desk_file)
            shown = json.loads((root / "site30" / "desk.json").read_text())
            c.eq(
                "a site with a file of Desk runs has them, as recorded",
                [(r["run_id"], r["label"], r["state"]) for r in shown],
                [("run-1", "recorded", "COMMITTED")],
            )
            c.eq("and one without has none", (root / "site6" / "desk.json").exists(), False)
            desk_file.write_text("{}", encoding="utf-8")
            c.refuses(
                "a Desk runs file the reader refuses",
                lambda: mod.export_site(running, root / "site31", desk_runs=desk_file),
                "the Desk runs file cannot be shown: the file has the members format and records",
            )
            c.refuses(
                "a Desk runs file that is not there",
                lambda: mod.export_site(running, root / "site32", desk_runs=root / "nowhere.json"),
                f"the Desk runs file cannot be shown: [Errno 2] No such file or directory: '{root / 'nowhere.json'}'",
            )
            c.eq("and nothing is written for either", (root / "site31").exists() or (root / "site32").exists(), False)
            recorded = RECORDED / "BTC.json"
            nowhere = root / "nowhere-challenge.json"
            c.refuses(
                "a model challenge file that is not there",
                lambda: mod.export_site(running, root / "site18", model_challenges=[nowhere]),
                f"the model challenge file {nowhere} cannot be shown: [Errno 2] No such file or directory: '{nowhere}'",
            )
            torn = root / "torn-challenge.json"
            torn.write_bytes(recorded.read_bytes()[:-1])
            c.refuses(
                "a model challenge file the reader refuses, after one it reads",
                lambda: mod.export_site(running, root / "site19", model_challenges=[recorded, torn]),
                f"the model challenge file {torn} cannot be shown: the challenge file is not written in canonical form with one newline at its end",
            )
            c.refuses(
                "a model challenge of a forecast this ledger does not hold",
                lambda: mod.export_site(running, root / "site20", model_challenges=[recorded]),
                "the model challenges cannot be shown: fcy5uhjx4diplqmzlnwt4smyac37: this ledger holds no such forecast",
            )
            c.eq("and nothing is written for any of them", [(root / f"site{n}").exists() for n in (18, 19, 20)], [False] * 3)
            real_for_site = mod.model_challenge_file.for_site
            ledger_records = mod.scheduler.read_ledger(running.ledger)
            held: list[tuple[list[str], bool]] = []
            shown = model_challenge_file.row(model_challenge_file.read(recorded.read_bytes()))

            def stand_in(challenges: Any, records: Any) -> tuple[Any, ...]:
                held.append(([x.asked.symbol for x in challenges], list(records) == ledger_records))
                return (shown,)

            mod.model_challenge_file.for_site = stand_in
            try:
                mod.export_site(running, root / "site21", model_challenges=[recorded, RECORDED / "ETH.json"])
            finally:
                mod.model_challenge_file.for_site = real_for_site
            c.eq("the model challenge files are read in the order given and held to the ledger's records", held, [(["BTC", "ETH"], True)])
            c.eq(
                "what they give is the What if page's document, listed in the index",
                (
                    json.loads((root / "site21" / "model_challenges.json").read_text()),
                    "model_challenges.json" in json.loads((root / "site21" / "index.json").read_text())["files"],
                ),
                ([view_models.to_json(shown)], True),
            )
            c.eq("and a site without them has none", (root / "site6" / "model_challenges.json").exists(), False)
            good_signals = root / "signals-good.json"
            good_signals.write_text(json.dumps({"format": 1, "tokens": []}), encoding="utf-8")
            mod.export_site(running, root / "site-signals", signals=good_signals)
            c.eq(
                "a signals document is written as it is and listed in the index",
                (
                    json.loads((root / "site-signals" / "signals.json").read_text()),
                    "signals.json" in json.loads((root / "site-signals" / "index.json").read_text())["files"],
                ),
                ({"format": 1, "tokens": []}, True),
            )
            c.eq("and a site without one has none", (root / "site6" / "signals.json").exists(), False)
            from hoshitori import lab as lab_module

            lab_log = root / "lab.jsonl"
            lab_book = lab_module.Lab(lab_log)
            lab_book.freeze(
                lab_module.draft_from(
                    {
                        "statement": "BTC bounces after a low close",
                        "symbol": "BTC",
                        "direction": "UP",
                        "horizon_h": 4,
                        "condition": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 100}},
                        "invalidation": {"left": {"indicator": "close"}, "op": "<", "right": {"value": 90}},
                        "max_weight": "0.02",
                        "assumptions": [{"id": "a1", "kind": "OBSERVATION", "text": "the close is low"}],
                    }
                ),
                "check",
                at_ms=1_700_000_000_000,
            )
            mod.export_site(running, root / "site-lab", lab_log=lab_log)
            made_lab = json.loads((root / "site-lab" / "lab.json").read_text())
            c.eq(
                "a Laboratory log is written as its document and listed in the index",
                (len(made_lab["versions"]), "lab.json" in json.loads((root / "site-lab" / "index.json").read_text())["files"]),
                (1, True),
            )
            c.eq("and a site without one has none", (root / "site6" / "lab.json").exists(), False)
            missing_lab = root / "no-lab.jsonl"
            c.refuses(
                "a Laboratory log that is not there",
                lambda: mod.export_site(running, root / "site-lab2", lab_log=missing_lab),
                f"the Laboratory log {missing_lab} does not exist",
            )
            c.refuses(
                "candles without a log",
                lambda: mod.export_site(running, root / "site-lab3", lab_candles=[missing_lab]),
                "stored candles and runs are shown beside a Laboratory log; give the log",
            )
            c.refuses(
                "a run file without a log",
                lambda: mod.export_site(running, root / "site-lab4", lab_runs=missing_lab),
                "stored candles and runs are shown beside a Laboratory log; give the log",
            )
            torn_lab = root / "torn-lab.json"
            torn_lab.write_text("[")
            c.refuses(
                "a candle record that is not JSON",
                lambda: mod.export_site(running, root / "site-lab5", lab_log=lab_log, lab_candles=[torn_lab]),
                "the Laboratory cannot be shown: the candle record is not JSON: Expecting value: line 1 column 2 (char 1)",
            )
            c.refuses(
                "a run file that is not there",
                lambda: mod.export_site(running, root / "site-lab6", lab_log=lab_log, lab_runs=missing_lab),
                f"the Laboratory cannot be shown: [Errno 2] No such file or directory: '{missing_lab}'",
            )
            c.eq("and nothing is written for any of them", [(root / f"site-lab{n}").exists() for n in (2, 3, 4, 5, 6)], [False] * 5)
            nowhere_signals = root / "no-signals.json"
            torn_signals = root / "torn-signals.json"
            torn_signals.write_text("{")
            try:
                json.loads("{")
            except ValueError as why:
                torn_why = str(why)
            c.refuses(
                "a signals file that is not there",
                lambda: mod.export_site(running, root / "site-s1", signals=nowhere_signals),
                f"the signals file {nowhere_signals} cannot be shown: [Errno 2] No such file or directory: '{nowhere_signals}'",
            )
            c.refuses(
                "a signals file that is not JSON",
                lambda: mod.export_site(running, root / "site-s2", signals=torn_signals),
                f"the signals file {torn_signals} cannot be shown: {torn_why}",
            )
            for name, content in (("list", "[1]"), ("format2", json.dumps({"format": 2})), ("noformat", "{}")):
                bad_signals = root / f"signals-{name}.json"
                bad_signals.write_text(content)
                c.refuses(
                    f"a signals file that is {name}",
                    lambda bad_signals=bad_signals: mod.export_site(running, root / "site-s3", signals=bad_signals),
                    "the signals file is not a signals document of format 1",
                )
            c.eq("and nothing is written for any of them", [(root / n).exists() for n in ("site-s1", "site-s2", "site-s3")], [False] * 3)
            real_export = mod.bundle.export_bundle

            def broken(out_dir: Any, *a: Any, **k: Any) -> None:
                Path(out_dir).mkdir(parents=True)
                (Path(out_dir) / "half").write_text("half a bundle")
                raise mod.bundle.BundleError("the disk is full")

            mod.bundle.export_bundle = broken  # type: ignore[assignment]
            try:
                failed = mod.export_site(running, root / "site4")
            finally:
                mod.bundle.export_bundle = real_export  # type: ignore[assignment]
            c.eq(
                "a bundle that fails half way is reported and its remains removed",
                (failed["bundles"], failed["without_bundle"], sorted(p.name for p in (root / "site4" / "bundles").iterdir())),
                ([], [first.ran.cycle_id], []),
            )
            stale = root / "site3" / "bundles" / f".{first.ran.cycle_id}.part"
            stale.mkdir(parents=True)
            (stale / "junk").write_text("left over from a stopped export")
            redo = mod.export_site(running, root / "site3")
            c.eq(
                "a partial bundle left by a stopped export is replaced",
                (redo["bundles"], sorted(p.name for p in (root / "site3" / "bundles").iterdir())),
                ([first.ran.cycle_id], [first.ran.cycle_id]),
            )
            c.eq("and the bundle verifies", _verdict(root, root / "site3" / "bundles" / first.ran.cycle_id), ("C3", True, True))
            c.refuses(
                "a cycle that does not exist",
                lambda: mod.export_cycle(running, "nope", root / "b2"),
                "only a committed cycle can be exported",
            )
            keeps_none = dataclasses.replace(running, config=dataclasses.replace(running.config, store=None))
            c.refuses(
                "a league that keeps no commitments",
                lambda: mod.export_cycle(keeps_none, first.ran.cycle_id, root / "b3"),
                "this league keeps no commitments",
            )
            kept_dir = settings.directory / "commitments" / kept
            moved = settings.directory / "commitments" / "moved"
            kept_dir.rename(moved)
            c.refuses(
                "a commitment that is gone",
                lambda: mod.export_cycle(running, first.ran.cycle_id, root / "b4"),
                "the cycle's commitment is not kept: no such commitment is saved",
            )
            moved.rename(kept_dir)
            real_find = running.ledger.find
            running.ledger.find = lambda source, ident: None  # type: ignore[method-assign]
            try:
                c.refuses(
                    "a record the ledger does not have",
                    lambda: mod.export_cycle(running, first.ran.cycle_id, root / "b5"),
                    "a record the cycle cites is not in the ledger",
                )
            finally:
                running.ledger.find = real_find  # type: ignore[method-assign]
            clock.jump(62 * 60)
            second = scheduler.tick(running.services, running.config)
            c.eq(
                "an hour later: a second cycle, and the first hour's forecasts settled",
                (second.ran is not None and second.ran.state.value, second.passed is not None and len(second.passed.records)),
                ("COMMITTED", 12),
            )
            later = mod.status(running)
            c.eq(
                "two cycles, three kinds of slot, two settlements",
                (later["cycles"], later["slots"], later["settlements"], later["forecasts"]),
                ({"COMMITTED": 2}, {"AWAITING_ORIGIN": 6, "OPEN": 4, "SETTLED": 2}, {"SETTLED": 2}, 12),
            )
            c.eq("the slots that were missed stay missed", later["heartbeat"], missed)
            running.ledger.checkpoint()
            settled_out = root / "bundle-settled"
            mod.export_cycle(running, first.ran.cycle_id, settled_out)
            seen = _verdict_of(root, settled_out)["cycle"]["later"]
            states = sorted({f["settlement"] for f in seen["forecasts"].values()})
            c.eq(
                "the settlements that ended the first cycle's calls are in its bundle and agree with their prices and the clock",
                (seen["shown"], seen["failures"], states),
                (True, 0, ["none", "ok"]),
            )
        c.eq("when the block ends the runner is stopped", [t.is_alive() for t in threading.enumerate() if t.name == "gateway-runner"], [])
    return c.bad


def corrupt_one_byte(ledger_dir: Path) -> None:
    """Change one byte inside the record text of a middle line of the ledger's segment (a disk fault, or an edit): corruption, not a torn
    tail, so the ledger opens in INTEGRITY_FAILURE (LEDGER_SPEC, rules from the source study)."""
    (segment,) = sorted((ledger_dir / "segments").iterdir())
    lines = segment.read_bytes().split(b"\n")
    line = bytearray(lines[3])
    at = line.index(b'\\"body\\"') + 12
    line[at] = ord("9") if line[at] != ord("9") else ord("8")
    lines[3] = bytes(line)
    segment.write_bytes(b"\n".join(lines))


def check_clock_status(mod: types.ModuleType, root: Path) -> list[str]:
    """The status tells an operator that the writer has refused a stepped clock, and whether that refusal still stands; a league
    that never refused one has a status that is exactly what it was."""
    c = Case(mod)
    clock = OffsetClock()
    with outside(clock) as world:
        (root / "k").mkdir()
        settings = mod.load_settings(world_files(root / "k", world))
        with mod.assemble(settings, clock=clock) as running:
            scheduler.tick(running.services, running.config)
            c.eq("a league that never refused a clock has no clock entry", "clock" in mod.status(running), False)
            events = settings.directory / "ledger" / "clock_events.jsonl"
            step = {"kind": "forward_step", "at": "2026-10-29T13:00:00.000Z", "unexplained_seconds": "3600.000"}
            events.write_text(json.dumps(step) + "\n")
            c.eq(
                "a refused step is reported, and stands",
                mod.status(running).get("clock"),
                {"events": 1, "standing": True, "last": step},
            )
            rebase = {"kind": "rebase", "at": "2026-10-29T13:00:01.000Z", "reason": "confirmed"}
            events.write_text(json.dumps(step) + "\n" + json.dumps(rebase) + "\n")
            c.eq(
                "a confirmed rebase ends it",
                mod.status(running).get("clock"),
                {"events": 2, "standing": False, "last": rebase},
            )
    return c.bad


def check_integrity_failure(mod: types.ModuleType, root: Path) -> list[str]:
    """A ledger in INTEGRITY_FAILURE is said by every command that reads it, and none shows a record of it (ADR-026: visible on every surface;
    LEDGER_SPEC section 8: the product shows NOT RECONSTRUCTIBLE for affected records, never a pass)."""
    c = Case(mod)
    clock = OffsetClock()
    with outside(clock) as world:
        path = world_files(root / "f", world) if (root / "f").mkdir() is None else root
        settings = mod.load_settings(path)
        with mod.assemble(settings, clock=clock) as running:
            first = scheduler.tick(running.services, running.config)
            c.eq("a cycle is committed", first.ran is not None and first.ran.state.value, "COMMITTED")
            assert first.ran is not None
            cycle_id = first.ran.cycle_id
            c.eq("an intact ledger's status says it is OK", mod.status(running).get("ledger"), {"state": "OK", "detail": ""})
            made = mod.export_site(running, root / "site-ok")
            index = json.loads((root / "site-ok" / "index.json").read_text())
            c.eq("and so do its export and its site", (made.get("ledger"), index.get("ledger")), ({"state": "OK", "detail": ""},) * 2)
        corrupt_one_byte(settings.directory / "ledger")
        with mod.assemble(settings, clock=clock) as running:
            state, detail = running.ledger.state, running.ledger.state_detail
            c.eq(
                "the ledger opens in INTEGRITY_FAILURE, with its reason",
                (state, detail.startswith("segment ")),
                ("INTEGRITY_FAILURE", True),
            )
            said = {"state": state, "detail": detail}
            unknown = dict.fromkeys(("records", "cycles", "slots", "forecasts", "failures", "settlements", "heartbeat"))
            c.eq(
                "the status says so, and counts nothing it cannot read (no zero, no MISSING slot)",
                mod.status(running),
                {"ledger": said, **unknown},
            )
            site = root / "site-failed"
            made = mod.export_site(running, site)
            index = json.loads((site / "index.json").read_text())
            c.eq("the export says so, and so does the site's index", (made.get("ledger"), index.get("ledger")), (said, said))
            c.eq(
                "and the site holds no record of it",
                (index["counts"], made["bundles"], made["without_bundle"], json.loads((site / "today.json").read_text())["latest"]),
                ({"cycles": 0, "rows": 0, "receipts": 0, "bundles": 0}, [], [], None),
            )
            mod.export_site(running, root / "site-failed-2", model_challenges=[RECORDED / "BTC.json"])
            c.eq(
                "a model challenge is of the ledger's records: a file is read, and none is published from a ledger in that state",
                (root / "site-failed-2" / "model_challenges.json").exists(),
                False,
            )
            torn = root / "torn-challenge.json"
            torn.write_bytes((RECORDED / "BTC.json").read_bytes() + b"\n")
            c.refuses(
                "and a file the reader refuses is refused all the same",
                lambda: mod.export_site(running, root / "site-failed-3", model_challenges=[torn]),
                f"the model challenge file {torn} cannot be shown: the challenge file is not written in canonical form with one newline at its end",
            )
            c.refuses(
                "a bundle of a cycle it committed",
                lambda: mod.export_cycle(running, cycle_id, root / "b"),
                f"the log is in {state}: {detail}",
            )
            c.eq("and nothing is written for it", (root / "b").exists(), False)
            c.refuses("a replay of that cycle", lambda: mod.replay_cycle(running, cycle_id), f"the log is in {state}: {detail}")
    return c.bad


def _verdict_against(root_file: Path, bundle_dir: Path) -> tuple[object, object, object]:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        verify_main([str(bundle_dir), "--trusted-root", str(root_file)])
    verdict = json.loads(out.getvalue())
    return verdict["timing"]["level"], verdict["eligibility"]["scorable"], verdict["integrity"]["ok"]


def _verdict_of(root: Path, bundle_dir: Path, world: str = "w") -> dict[str, Any]:
    import contextlib
    import io

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        verify_main([str(bundle_dir), "--trusted-root", str(root / world / "root.json")])
    verdict: dict[str, Any] = json.loads(out.getvalue())
    return verdict


def _batch_records(running: Any, cycle_id: str) -> int:
    """How many records a cycle's bundle must hold: its whole batch (the committing step names how many records come before it) and the
    committing step itself."""
    steps = [
        r
        for r in scheduler.read_ledger(running.ledger)
        if r.get("type") == "cycle_state" and r["body"]["cycle_id"] == cycle_id and r["body"]["state"] == "COMMITTING"
    ]
    assert len(steps) == 1
    return int(steps[0]["body"]["batch_size"]) + 1


def _verdict(root: Path, bundle_dir: Path, world: str = "w") -> tuple[object, object, object]:
    import contextlib
    import io
    import json

    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        verify_main([str(bundle_dir), "--trusted-root", str(root / world / "root.json")])
    verdict = json.loads(out.getvalue())
    return verdict["timing"]["level"], verdict["eligibility"]["scorable"], verdict["integrity"]["ok"]


def check_cleanup(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    calls: list[str] = []
    real_stop, real_shutdown, real_run = mod.Runner.stop, mod.ThreadExecutor.shutdown, mod.Runner.run_forever

    def stop(self: Any) -> None:
        calls.append("stop")
        real_stop(self)

    def shutdown(self: Any, *args: Any, **kw: Any) -> Any:
        calls.append("shutdown")
        return real_shutdown(self, *args, **kw)

    def slow(self: Any) -> None:
        real_run(self)
        time.sleep(0.4)

    mod.Runner.stop, mod.ThreadExecutor.shutdown, mod.Runner.run_forever = stop, shutdown, slow
    try:
        with outside(OffsetClock()) as world:
            path = world_files(root / "c", world) if (root / "c").mkdir() is None else root
            with mod.assemble(mod.load_settings(path), clock=OffsetClock()):
                pass
            c.eq("the runner is stopped, then the pool", calls, ["stop", "shutdown"])
            c.eq(
                "and the runner has returned before the block ends",
                [t.is_alive() for t in threading.enumerate() if t.name == "gateway-runner"],
                [],
            )
            c.eq("the wait for it is five seconds", mod.STOP_WAIT_S, 5)
    finally:
        mod.Runner.stop, mod.ThreadExecutor.shutdown, mod.Runner.run_forever = real_stop, real_shutdown, real_run
    return c.bad


def check_assemble_edges(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    with outside(OffsetClock()) as world:
        (root / "a").mkdir()
        settings = mod.load_settings(world_files(root / "a", world))
        with mod.assemble(settings) as running:
            c.eq("without a clock, the system's", isinstance(running.services.env.clock, SystemClock), True)
        (root / "b").mkdir()
        two = mod.load_settings(world_files(root / "b", world, authorities=world.authorities[:2]))
        with mod.assemble(two, clock=OffsetClock()) as running:
            c.eq("exactly as many authorities as the root needs is enough", len(running.settings.authorities), 2)
        (root / "d").mkdir()
        c.refuses(
            "a cycle that has no time to finish",
            lambda: _assemble(mod, world_files(root / "d", world, finish_within_ms=0)),
            "the time a cycle has to finish is a positive number of milliseconds",
        )
    plain = [{"name": "alpha", "url": "http://a.example/tsr"}, {"name": "beta", "url": "http://b.example/tsr"}]
    with outside(OffsetClock()) as world:
        (root / "e").mkdir()
        c.refuses(
            "plain http to an authority, unless asked",
            lambda: _assemble(mod, world_files(root / "e", world, authorities=plain)),
            "plain http is allowed only for a loopback host, and only when asked for",
        )
        (root / "f").mkdir()
        settings = mod.load_settings(world_files(root / "f", world, authorities=plain, allow_plain_http_authorities=True))
        with mod.assemble(settings, clock=OffsetClock()) as running:
            c.eq("and with the flag the authorities are attached", [a.url for a in running.settings.authorities], [p["url"] for p in plain])
    c.eq("tally counts and orders", list(mod.tally(["b", "c", "a", "b"]).items()), [("a", 1), ("b", 2), ("c", 1)])
    c.eq("tally of nothing", mod.tally([]), {})
    return c.bad


def check_barrier_assembly(mod: types.ModuleType, root: Path) -> list[str]:
    from tests.league_checks import barrier_map

    c = Case(mod)
    bm = barrier_map()
    with outside(OffsetClock()) as world:
        (root / "a").mkdir()
        settings = mod.load_settings(world_files(root / "a", world, barrier=bm))
        c.eq("the settings name the barrier mapping", settings.barrier_mappings, {"L0": root / "a" / "barrier.json"})
        with mod.assemble(settings, clock=OffsetClock()) as running:
            c.eq(
                "the services carry it, as read from the file",
                {a: m.sha256 for a, m in running.services.barrier_mappings.items()},
                {"L0": bm.sha256},
            )
            first = scheduler.tick(running.services, running.config)
            c.eq(
                "a cycle with barrier slots is committed",
                (first.ran is not None and first.ran.state.value, first.ran is not None and len(first.ran.slots)),
                ("COMMITTED", 12),
            )
            c.eq("six of them contract B", first.ran is not None and sum(1 for s in first.ran.slots if s.contract == "B"), 6)
        (root / "b").mkdir()
        c.refuses(
            "the protocol pins a barrier mapping the settings do not give",
            lambda: _assemble(mod, world_files(root / "b", world, barrier=bm, barrier_mappings={})),
            "the barrier mappings are the ones the protocol pins, for its barrier arms and no others",
        )
        (root / "c").mkdir()
        c.refuses(
            "another barrier mapping than the pinned one",
            lambda: _assemble(mod, world_files(root / "c", world, barrier=bm, pinned="0" * 64)),
            "the barrier mappings are the ones the protocol pins, for its barrier arms and no others",
        )
        (root / "d").mkdir()
        c.refuses(
            "a barrier mapping the protocol does not pin",
            lambda: _assemble(mod, world_files(root / "d", world, unpinned_barrier=bm)),
            "the barrier mappings are the ones the protocol pins, for its barrier arms and no others",
        )
        (root / "e").mkdir()
        c.refuses(
            "another directional mapping than the pinned one",
            lambda: _assemble(mod, world_files(root / "e", world, arm_pin="0" * 64)),
            "the mappings are the ones the protocol pins, for its arms and no others",
        )
    return c.bad


def check_models_assembly(mod: types.ModuleType, root: Path) -> list[str]:
    c = Case(mod)
    clock = OffsetClock()
    previous = os.environ.get(MODEL_KEY_ENV)
    model = SmartModel().start()
    os.environ[MODEL_KEY_ENV] = model_world.KEY
    try:
        with outside(clock) as world:
            (root / "a").mkdir()
            path = world_files(root / "a", world, models_doc=models_document(model.url))
            settings = mod.load_settings(path)
            c.eq("the settings name the file of the model arms", settings.models, root / "a" / "models.json")
            with mod.assemble(settings, clock=clock) as running:
                arms = running.services.models
                c.eq(
                    "the services carry the arms, at the versions the protocol pins",
                    model_arms.versions(arms),
                    {a: v for a, v, _ in running.services.protocol.model_arms},
                )
                c.eq("and the number of workers", arms.workers, 4)
                first = scheduler.tick(running.services, running.config)
                c.eq("a cycle is committed", first.ran is not None and first.ran.state.value, "COMMITTED")
                assert first.ran is not None
                by_arm: dict[str, list[str]] = {}
                for slot in first.ran.slots:
                    by_arm.setdefault(slot.arm_id, []).append(f"{slot.fate.value}:{slot.level}")
                c.eq(
                    "the model arms made forecasts, committed at C3, beside the rules arm",
                    {a: (len(v), set(v)) for a, v in by_arm.items()},
                    {"L0": (6, {"FORECAST:C3"}), "L1": (6, {"FORECAST:C3"}), "L2": (2, {"FORECAST:C3"}), "L1x": (2, {"FORECAST:C3"})},
                )
                state = mod.status(running)
                c.eq("the status counts them", (state["forecasts"], state["failures"]), (16, 0))
                stages = sorted({r["response_format"]["json_schema"]["name"] for r in model.requests})
                c.eq("the model was asked at each stage", stages, ["l1_judgment", "l2_bear", "l2_bull", "l2_judge"])
                c.eq("with 6 + 6 + 6 requests", len(model.requests), 18)
                leaked = [str(f) for f in settings.directory.rglob("*") if f.is_file() and model_world.KEY.encode() in f.read_bytes()]
                c.eq("the key is nowhere in what was written", leaked, [])
                again = mod.replay_cycle(running, first.ran.cycle_id)
                c.eq(
                    "the cycle is rebuilt from the ledger and the stored answers, every slot as it was committed",
                    again,
                    {
                        "cycle": first.ran.cycle_id,
                        "slots": 16,
                        "match": 16,
                        "different": 0,
                        "not_reconstructible": 0,
                        "not_compared": 0,
                        "clean": True,
                        "exceptions": [],
                    },
                )
                import dataclasses as dc

                class Hidden:
                    """The captures with one of the model's answers missing (the store itself is append-only)."""

                    def __init__(self, inner: Any) -> None:
                        self.inner = inner
                        self.model = [i for i in inner.ids() if inner.get(i).source == running.model_provider]

                    def ids(self) -> list[str]:
                        return [i for i in self.inner.ids() if i != self.model[0]]

                    def get(self, capture_id: str) -> Any:
                        return self.inner.get(capture_id)

                lost = mod.replay_cycle(dc.replace(running, captures=Hidden(running.captures)), first.ran.cycle_id)  # type: ignore[arg-type]
                c.eq(
                    "an answer that is gone: the slot says so, and the cycle is not clean",
                    (lost["clean"], lost["slots"], lost["match"] + lost["not_reconstructible"], lost["not_reconstructible"] >= 1),
                    (False, 16, 16, True),
                )
                c.eq(
                    "each exception names the slot",
                    {tuple(sorted(e)) for e in lost["exceptions"]},
                    {("arm", "contract", "detail", "forecast", "hours", "status", "symbol")},
                )
                c.eq("with its status", {e["status"] for e in lost["exceptions"]}, {"NOT_RECONSTRUCTIBLE"})
                c.refuses(
                    "a cycle the ledger does not hold",
                    lambda: mod.replay_cycle(running, "cy-none"),
                    "the ledger holds no snapshot and manifest of this cycle",
                )
                out_dir = root / "bundle"
                exported = mod.export_cycle(running, first.ran.cycle_id, out_dir)
                c.eq(
                    "the bundle holds the cycle's whole batch: the forecasts of every arm, the evidence they cite and the rest",
                    exported["records"],
                    _batch_records(running, first.ran.cycle_id),
                )
                c.eq("which the offline verifier finds C3 and scorable", _verdict(root, out_dir, "a"), ("C3", True, True))
                clock.jump(62 * 60)
                second = scheduler.tick(running.services, running.config)
                c.eq(
                    "an hour later the forecasts of both arms that look an hour ahead are settled",
                    second.passed is not None and len(second.passed.records),
                    28,
                )
                later = mod.status(running)
                c.eq("by arm", later["settlements"], {"SETTLED": 4})
                c.eq("nor in any request the model saw", any(model_world.KEY in json.dumps(r) for r in model.requests), False)
            # the model key must be set
            del os.environ[MODEL_KEY_ENV]
            c.refuses(
                "no key in the environment",
                lambda: _assemble(mod, path),
                f"the model key is not set: the environment variable {MODEL_KEY_ENV} is empty",
            )
            os.environ[MODEL_KEY_ENV] = ""
            c.refuses(
                "an empty key",
                lambda: _assemble(mod, path),
                f"the model key is not set: the environment variable {MODEL_KEY_ENV} is empty",
            )
            os.environ[MODEL_KEY_ENV] = model_world.KEY
            (root / "b").mkdir()
            both = "the model settings are given exactly when the protocol has model arms"
            c.refuses(
                "model settings and a protocol with no model arm",
                lambda: _assemble(mod, world_files(root / "b", world, models="models.json")),
                "a file named in the settings cannot be used: the model settings cannot be read: No such file or directory",
            )
            (root / "c").mkdir()
            doc = models_document(model.url)
            path_c = world_files(root / "c", world, models_doc=doc)
            settings_c = json.loads(path_c.read_text())
            settings_c["models"] = None
            path_c.write_text(json.dumps(settings_c))
            c.refuses("a protocol with model arms and no settings for them", lambda: _assemble(mod, path_c), both)
            (root / "d").mkdir()
            path_d = world_files(root / "d", world, models_doc=doc)
            (root / "d" / "models.json").write_text(json.dumps(models_document(model.url, l1={**CONFIG, "model": "another/model"})))
            c.refuses(
                "another version than the protocol pins",
                lambda: _assemble(mod, path_d),
                "the model arms are the ones the protocol pins, at the versions it pins, and no others",
            )
            (root / "e").mkdir()
            path_e = world_files(root / "e", world, models_doc=doc)
            (root / "e" / "models.json").write_text(json.dumps(models_document(model.url, l2=None, l1x=None)))
            c.refuses(
                "fewer arms than the protocol pins",
                lambda: _assemble(mod, path_e),
                "the model arms are the ones the protocol pins, at the versions it pins, and no others",
            )
            (root / "f").mkdir()
            path_f = world_files(root / "f", world, models_doc=doc)
            (root / "f" / "models.json").write_text("{}")
            c.eq(
                "a settings file for the model arms that cannot be used",
                _message(lambda: _assemble(mod, path_f)),
                "a file named in the settings cannot be used: the model settings has the members " + str(sorted(models_document("x"))),
            )
    finally:
        model.stop()
        if previous is None:
            os.environ.pop(MODEL_KEY_ENV, None)
        else:
            os.environ[MODEL_KEY_ENV] = previous
    return c.bad


def check_models_member(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("no model settings", parse(mod).models, None)
    c.eq("a file against the file's directory", parse(mod, models="models.json").models, Path("/base/models.json"))
    for bad in (3, "", True, [], {}):
        c.refuses(f"models of {bad!r}", lambda bad=bad: parse(mod, models=bad), "the model settings file is a non-empty string")
    return c.bad


def check_make_protocol_models(mod: types.ModuleType, root: Path) -> list[str]:
    import argparse

    c = Case(mod)
    document = R.make_document(4, root_keys=[R.new_signer(1)])
    (root / "root.json").write_bytes(canonical.dumps(document))  # type: ignore[arg-type]
    (root / "map.json").write_bytes(rules_arm.dump(graded(rules_arm)))
    models_file = root / "models.json"
    models_file.write_text(json.dumps(models_document("http://127.0.0.1:1/v1")))
    versions = model_settings.load(models_file).versions()

    def args(**over: Any) -> argparse.Namespace:
        values: dict[str, Any] = {
            "out": root / "protocol.json",
            "root": root / "root.json",
            "mapping": [f"L0={root / 'map.json'}"],
            "barrier_mapping": [],
            "name": "first",
            "start_ms": 1_793_275_200_000,
            "every_minutes": 60,
            "watchlist": "BTC",
            "primary": "binance-spot",
            "second": "okx",
            "models": models_file,
            "cutoff": ["L1=1:200000,4:590000,24:590000", "L2=4:590000", "L1x=4:590000"],
            "optional_source": [],
        }
        values.update(over)
        return argparse.Namespace(**values)

    done = mod.make_protocol(args())
    written = protocol.load((root / "protocol.json").read_bytes())
    c.eq(
        "the arms are pinned at the versions the settings describe, with their cutoffs",
        written.model_arms,
        (
            ("L1", versions["L1"], ((1, 200_000), (4, 590_000), (24, 590_000))),
            ("L1x", versions["L1x"], ((4, 590_000),)),
            ("L2", versions["L2"], ((4, 590_000),)),
        ),
    )
    c.eq("and the file is the one said", done["sha256"], written.sha256)
    other = root / "other.json"
    c.refuses("a cutoff without hours", lambda: mod.make_protocol(args(out=other, cutoff=["L1=x"])), "a cutoff is ARM=HOURS:MS,HOURS:MS")
    c.refuses("a cutoff with nothing", lambda: mod.make_protocol(args(out=other, cutoff=["L1="])), "a cutoff is ARM=HOURS:MS,HOURS:MS")
    c.refuses("a cutoff with a word", lambda: mod.make_protocol(args(out=other, cutoff=["L1=1:soon"])), "a cutoff is ARM=HOURS:MS,HOURS:MS")
    c.refuses(
        "an arm with no cutoffs",
        lambda: mod.make_protocol(args(out=other, cutoff=["L1=1:200000"])),
        "each model arm in the model settings has its cutoffs, and no other arm has",
    )
    c.refuses(
        "cutoffs for an arm the settings do not have",
        lambda: mod.make_protocol(args(out=other, cutoff=["L1=1:200000", "L2=4:590000", "L1x=4:590000", "L9=4:1"])),
        "each model arm in the model settings has its cutoffs, and no other arm has",
    )
    c.refuses(
        "cutoffs and no settings",
        lambda: mod.make_protocol(args(out=other, models=None)),
        "cutoffs are given with the model settings they belong to",
    )
    c.eq(
        "a cutoff beyond what the gap allows is the protocol's refusal",
        _message(lambda: mod.make_protocol(args(out=other, cutoff=["L1=1:999999", "L2=4:590000", "L1x=4:590000"]))),
        "the protocol cannot be made: a decision cutoff is a whole number from 1 to 240000",
    )
    c.eq(
        "settings that cannot be used",
        _message(lambda: mod.make_protocol(args(out=other, models=root / "none.json"))).startswith("the protocol cannot be made: "),
        True,
    )
    c.eq(
        "none of those wrote a file",
        sorted(p.name for p in root.iterdir() if p.name.endswith(".json")),
        ["map.json", "models.json", "protocol.json", "root.json"],
    )
    return c.bad


def check_reading(mod: types.ModuleType, root: Path) -> list[str]:
    """``assemble_reading`` opens what a command that only looks needs: the same ledger and captures a running league holds, without the
    key, without a lock and without any way to write; a league that never ran has nothing to look at, and the reading says so."""
    from hoshitori import capture as capture_module

    c = Case(mod)
    clock = OffsetClock()
    with outside(clock) as world:
        (root / "a").mkdir()
        settings = mod.load_settings(world_files(root / "a", world))
        with mod.assemble(settings, clock=clock) as running:
            first = scheduler.tick(running.services, running.config)
            assert first.ran is not None
            key = settings.log_key
            hidden = key.with_name("log.key.hidden")
            os.rename(key, hidden)  # a command that only looks must not need the key
            try:
                with mod.assemble_reading(settings, clock=clock) as reading:
                    c.eq("a reading is a Reading, not a running league", type(reading).__name__, "Reading")
                    c.eq(
                        "it sees the ledger the running league writes",
                        (reading.ledger.size, reading.ledger.state),
                        (running.ledger.size, running.ledger.state),
                    )
                    c.eq("the ledger holds records", reading.ledger.size > 40, True)
                    c.eq("it sees the captures the running league stored", reading.captures.ids(), running.captures.ids())
                    c.eq("and there are some", len(reading.captures) > 0, True)
                    stored = capture_module.RawCapture(capture_module.new_capture_id(), "x", {}, 200, {}, b"x", clock.now(), clock.now())
                    try:
                        reading.captures.append(stored)
                    except capture_module.CaptureStoreError as exc:
                        c.eq("a capture added through a reading is refused", str(exc), "the store is read-only")
                    else:
                        c.bad.append("a capture added through a reading was accepted")
                    looked, ran = mod.status(reading), mod.status(running)
                    c.eq(
                        "what it reports is what the running league reports",
                        [looked[k] for k in ("cycles", "forecasts", "failures", "records")],
                        [ran[k] for k in ("cycles", "forecasts", "failures", "records")],
                    )
                    c.eq(
                        "a league with no model arm has no model settings and no provider",
                        (reading.model_configs, reading.model_provider),
                        (None, None),
                    )
                    c.eq("the source is the settings'", reading.source_urn, settings.source)
                    c.eq("the clock is the one asked for", reading.clock is clock, True)
                with mod.assemble_reading(settings) as reading:
                    c.eq("without a clock, the system's", isinstance(reading.clock, SystemClock), True)
            finally:
                os.rename(hidden, key)
        (root / "b").mkdir()
        fresh = mod.load_settings(world_files(root / "b", world))
        c.refuses(
            "a league that has never run",
            lambda: _reading(mod, fresh),
            f"there is no ledger in {fresh.directory / 'ledger'}",
        )
        c.eq("and none is made", (fresh.directory / "ledger").exists() or (fresh.directory / "captures").exists(), False)

    clockm = OffsetClock()
    previous = os.environ.get(MODEL_KEY_ENV)
    model = SmartModel().start()
    os.environ[MODEL_KEY_ENV] = model_world.KEY
    try:
        with outside(clockm) as world:
            (root / "m").mkdir()
            settings = mod.load_settings(world_files(root / "m", world, models_doc=models_document(model.url)))
            with mod.assemble(settings, clock=clockm) as running:
                scheduler.tick(running.services, running.config)
                provider = running.model_provider
            os.environ.pop(MODEL_KEY_ENV, None)  # reading a league with model arms asks for no model key either
            with mod.assemble_reading(settings, clock=clockm) as reading:
                configs = reading.model_configs
                c.eq(
                    "a league with model arms has their settings, in the order L1, L2, L1x", configs is not None and len(configs) == 3, True
                )
                c.eq("and the provider they are asked through", reading.model_provider is not None and bool(reading.model_provider), True)
                c.eq("the same provider as the running league's", reading.model_provider, provider)
    finally:
        model.stop()
        if previous is None:
            os.environ.pop(MODEL_KEY_ENV, None)
        else:
            os.environ[MODEL_KEY_ENV] = previous
    return c.bad


RYO_KEY_ENV = "HOSHITORI_TEST_RYO_KEY"


def good_ryo(url: str = "https://ryo.example/api/mcp") -> dict[str, Any]:
    return {"url": url, "key_env": RYO_KEY_ENV, "calls_per_hour": 40, "scan_wait_s": 200, "collect_wait_s": 360}


def ryo_script(clock: OffsetClock) -> Callable[[Any], Any]:
    """A RYO that answers each call for the symbol asked, as of the world's clock: the scan names BTC and ETH."""
    from hoshitori.timeutil import format_rfc3339_ms
    from tests import ryo_data
    from tests.ryo_fake_server import TRACE, Behaviour

    def script(seen: Any) -> Any:
        prefix, suffix = "/api/mcp/tools/", "/call"
        if seen.method != "POST" or not (seen.path.startswith(prefix) and seen.path.endswith(suffix)):
            return None
        name = seen.path[len(prefix) : -len(suffix)]
        args = json.loads(seen.body or b"{}")
        data = ryo_data.DATA[name]()
        if name == "scan_market":
            base = data["candidates"][0]
            data["candidates"] = [{**base, "rank": rank, "symbol": symbol} for rank, symbol in ((1, "BTC"), (2, "ETH"))]
            data["candidate_count"] = 2
        elif "symbol" in args:
            data["asset"]["symbol"] = args["symbol"]
        result = ryo_data.result(name, data=data, request=args, as_of=format_rfc3339_ms(clock.now()))
        return Behaviour(200, ryo_data.rest_body(name, result), {"content-type": "application/json", "x-trace-id": TRACE})

    return script


def check_ryo(mod: types.ModuleType, root: Path) -> list[str]:
    from tests.ryo_fake_server import KEY, FakeRyo

    c = Case(mod)
    c.eq("a league that does not name RYO has none", parse(mod).ryo, None)
    got = parse(mod, ryo=good_ryo())
    edge = parse(mod, ryo={**good_ryo(), "scan_wait_s": 30, "collect_wait_s": 30}).ryo
    c.eq("a wait of exactly one attempt is enough", (edge.scan_wait_s, edge.collect_wait_s), (30.0, 30.0))
    c.eq("one call an hour is allowed", parse(mod, ryo={**good_ryo(), "calls_per_hour": 1}).ryo.calls_per_hour, 1)
    c.eq("a league that does not say how many calls a minute RYO allows holds the measured five", got.ryo.calls_per_minute, 5.0)
    c.eq("and one that says is held to it", parse(mod, ryo={**good_ryo(), "calls_per_minute": 2.5}).ryo.calls_per_minute, 2.5)
    c.eq(
        "a whole number of calls a minute is a number", parse(mod, ryo={**good_ryo(), "calls_per_minute": 600}).ryo.calls_per_minute, 600.0
    )
    c.eq("a league that names no daily hour reads RYO in every cycle", got.ryo.daily_hour, None)
    for hour in (0, 7, 23):
        c.eq(f"a daily hour of {hour} is held", parse(mod, ryo={**good_ryo(), "daily_hour": hour}).ryo.daily_hour, hour)
    try:
        got_ryo = parse(mod, ryo=good_ryo()).ryo
        got_ryo.url = "https://elsewhere.example"  # type: ignore[misc]
        c.bad.append("the RYO settings can be changed after they are read")
    except dataclasses.FrozenInstanceError:
        pass
    c.eq(
        "the RYO settings",
        (got.ryo.url, got.ryo.key_env, got.ryo.calls_per_hour, got.ryo.scan_wait_s, got.ryo.collect_wait_s),
        ("https://ryo.example/api/mcp", RYO_KEY_ENV, 40, 200.0, 360.0),
    )
    members = "['calls_per_hour', 'collect_wait_s', 'key_env', 'scan_wait_s', 'url']"
    for label, value, says in (
        ("RYO settings that are not an object", [], "the RYO settings is an object"),
        (
            "a member missing",
            {k: v for k, v in good_ryo().items() if k != "url"},
            f"the RYO settings have the members {members}, not ['calls_per_hour', 'collect_wait_s', 'key_env', 'scan_wait_s']",
        ),
        (
            "a member more",
            {**good_ryo(), "extra": 1},
            f"the RYO settings have the members {members}, not ['calls_per_hour', 'collect_wait_s', 'extra', 'key_env', 'scan_wait_s', 'url']",
        ),
        ("no calls a minute", {**good_ryo(), "calls_per_minute": 0}, "the RYO calls a minute is a positive number"),
        ("a daily hour past the day", {**good_ryo(), "daily_hour": 24}, "the daily RYO hour is a UTC hour, 0 to 23"),
        ("a daily hour that is not whole", {**good_ryo(), "daily_hour": 7.5}, "the daily RYO hour is a whole number"),
        ("a daily hour below zero", {**good_ryo(), "daily_hour": -1}, "the daily RYO hour is a whole number"),
        ("a boolean daily hour", {**good_ryo(), "daily_hour": True}, "the daily RYO hour is a whole number"),
        ("calls a minute that are not a number", {**good_ryo(), "calls_per_minute": "five"}, "the RYO calls a minute is a positive number"),
        ("no calls an hour", {**good_ryo(), "calls_per_hour": 0}, "the RYO calls an hour is at least one"),
        ("calls an hour that are not whole", {**good_ryo(), "calls_per_hour": 1.5}, "the RYO calls an hour is a whole number"),
        ("a boolean for calls an hour", {**good_ryo(), "calls_per_hour": True}, "the RYO calls an hour is a whole number"),
        ("an empty url", {**good_ryo(), "url": ""}, "the RYO url is a non-empty string"),
        ("an empty key variable", {**good_ryo(), "key_env": ""}, "the name of the variable holding the RYO key is a non-empty string"),
        ("a scan wait of none", {**good_ryo(), "scan_wait_s": 0}, "the wait for the RYO scan is a positive number"),
        ("a collection wait of none", {**good_ryo(), "collect_wait_s": 0}, "the wait for the RYO evidence is a positive number"),
        (
            "a scan wait shorter than one attempt",
            {**good_ryo(), "scan_wait_s": 29.9},
            "the wait for the RYO scan is at least 30 seconds, the shortest attempt RYO is given",
        ),
        (
            "a collection wait shorter than one attempt",
            {**good_ryo(), "collect_wait_s": 10},
            "the wait for the RYO evidence is at least 30 seconds, the shortest attempt RYO is given",
        ),
    ):
        c.refuses(label, lambda value=value: parse(mod, ryo=value), says)  # type: ignore[misc]

    clock = OffsetClock()
    previous = os.environ.get(RYO_KEY_ENV)
    os.environ[RYO_KEY_ENV] = KEY
    ryo_fake = FakeRyo(script=ryo_script(clock)).start()
    try:
        with outside(clock) as world:
            (root / "a").mkdir()
            path = world_files(root / "a", world, optional_sources=("ryo",), ryo=good_ryo(ryo_fake.url))
            settings = mod.load_settings(path)
            with mod.assemble(settings, clock=clock) as running:
                c.eq(
                    "the league carries the RYO adapter and the wiring",
                    (running.ryo is not None, running.services.ryo is not None),
                    (True, True),
                )
                c.eq("its governor holds the five a minute RYO's stricter limit allows", running.ryo.governor.limit, 5.0)
                first = scheduler.tick(running.services, running.config)
                c.eq("a cycle that reads RYO is committed", first.ran is not None and first.ran.state.value, "COMMITTED")
                state = mod.status(running)
                c.eq(
                    "the status reports RYO's calls against the budget: seven tool calls and the key status read before them",
                    state["ryo"],
                    {"calls_last_hour": 8, "calls_per_hour": 40},
                )
                clock.jump(90 * 60)
                c.eq(
                    "calls older than an hour are not counted against the budget",
                    mod.status(running)["ryo"],
                    {"calls_last_hour": 0, "calls_per_hour": 40},
                )
                asked = sorted(s.path.rsplit("/", 2)[-2] for s in ryo_fake.seen if s.method == "POST")
                c.eq(
                    "RYO was asked the scan, both tools for each of the two candidates, and the two market tools",
                    asked,
                    sorted(["scan_market", "market_overview", "monitor_market_sentiment_shift"] + ["analyze_token", "deep_analysis"] * 2),
                )
                c.eq(
                    "with the key from the environment",
                    {s.headers.get("authorization") for s in ryo_fake.seen if s.method == "POST"},
                    {f"Bearer {KEY}"},
                )
            (root / "plain").mkdir()
            plain = mod.load_settings(world_files(root / "plain", world))
            with mod.assemble(plain, clock=clock) as running:
                c.eq(
                    "a league without RYO has no RYO and reports none",
                    (running.ryo, running.services.ryo, "ryo" in mod.status(running)),
                    (None, None, False),
                )
            os.environ.pop(RYO_KEY_ENV)
            c.refuses(
                "a league whose RYO key is not set",
                lambda: _assemble(mod, path),
                f"the RYO key is not set: the environment variable {RYO_KEY_ENV} is empty",
            )
            os.environ[RYO_KEY_ENV] = KEY
            (root / "b").mkdir()
            c.refuses(
                "settings that configure RYO for a protocol that does not list it",
                lambda: _assemble(mod, world_files(root / "b", world, ryo=good_ryo(ryo_fake.url))),
                "the settings configure RYO exactly when the protocol lists it as an optional source",
            )
            (root / "c").mkdir()
            c.refuses(
                "a protocol that lists RYO for settings that do not configure it",
                lambda: _assemble(mod, world_files(root / "c", world, optional_sources=("ryo",))),
                "the settings configure RYO exactly when the protocol lists it as an optional source",
            )
    finally:
        ryo_fake.stop()
        if previous is None:
            os.environ.pop(RYO_KEY_ENV, None)
        else:
            os.environ[RYO_KEY_ENV] = previous
    return c.bad


def _reading(mod: types.ModuleType, settings: Any) -> None:
    with mod.assemble_reading(settings):
        pass


def _assemble(mod: types.ModuleType, path: Path) -> None:
    with mod.assemble(mod.load_settings(path), clock=OffsetClock()):
        pass


def _message(call: Callable[[], object]) -> str:
    try:
        call()
    except Exception as exc:
        return str(exc)
    return ""


class _Located:
    """The part of a running league that ``_whole_batch`` asks about: where in the log a record sits."""

    def __init__(self, positions: dict[str, int]) -> None:
        self.source_urn = "urn:hoshitori:test"
        self.ledger = self
        self._positions = positions

    def find(self, source: str, ident: str) -> int | None:
        return self._positions.get(ident) if source == self.source_urn else None


def check_stored_checkpoints(mod: types.ModuleType, root: Path) -> list[str]:
    """The signed checkpoints a ledger stored, as the export reads them: smallest first, one for each size, and a line that is not a signed
    note of the log, or whose base64 is not strict, is left out."""
    from hoshitori import merkle
    from hoshitori.checkpoint import Checkpoint, sign_checkpoint

    c = Case(mod)
    signer = R.log_signer(7)
    folder = root / "ledger"
    folder.mkdir()
    c.eq("no file, no checkpoints", mod._stored_checkpoints(folder), [])
    tree = merkle.MerkleTree()
    notes: dict[int, bytes] = {}
    for size in range(1, 6):
        tree.append(f"record {size}".encode())
        notes[size] = sign_checkpoint(Checkpoint(R.LOG_ORIGIN, size, tree.root()), [signer])

    def line(note: bytes) -> bytes:
        return canonical.dumps({"note": base64.b64encode(note).decode()}) + b"\n"

    damaged = base64.b64encode(notes[3]).decode()
    lines = [
        line(notes[5]),
        b"not json\n",
        canonical.dumps({"note": damaged[:10] + "!" + damaged[10:]}) + b"\n",
        canonical.dumps({"other": "x"}) + b"\n",
        canonical.dumps({"note": base64.b64encode(b"not a note").decode()}) + b"\n",
        line(notes[2]),
        line(notes[2]),
    ]
    (folder / "checkpoints.jsonl").write_bytes(b"".join(lines))
    got = mod._stored_checkpoints(folder)
    c.eq("smallest first, one for each size, damaged lines left out", [size for size, _ in got], [2, 5])
    c.eq("and each with its own note", [note for _, note in got], [notes[2], notes[5]])
    return c.bad


def check_whole_batch(mod: types.ModuleType) -> list[str]:
    """The records a cycle's bundle must hold besides the forecasts and what they cite: the committing step and the
    ``batch_size`` records before it, all under the checkpoint that was time-stamped. A cycle that did not name its size, or one whose
    committing step is not where a batch can end, adds nothing, so its bundle is the older, not complete, kind."""
    c = Case(mod)

    def step(ident: str, state: str, cycle_id: str = "cy-a", **body: object) -> dict[str, Any]:
        return {"type": "cycle_state", "id": ident, "body": {"cycle_id": cycle_id, "state": state, **body}}

    where = {"s-commit": 10, "s-other": 25}
    running = _Located(where)

    def batch(records: list[dict[str, Any]], stamped: int = 11) -> object:
        return mod._whole_batch(running, records, "cy-a", stamped)

    c.eq("the batch and its committing step", batch([step("s-commit", "COMMITTING", batch_size=4)]), {6, 7, 8, 9, 10})
    c.eq("a batch of one", batch([step("s-commit", "COMMITTING", batch_size=1)]), {9, 10})
    c.eq("a batch that begins at the first record", batch([step("s-commit", "COMMITTING", batch_size=10)]), set(range(0, 11)))
    c.eq("a batch longer than the log before its step", batch([step("s-commit", "COMMITTING", batch_size=11)]), set())
    c.eq("a step of an older cycle that named no size", batch([step("s-commit", "COMMITTING")]), set())
    c.eq("a size of zero", batch([step("s-commit", "COMMITTING", batch_size=0)]), set())
    c.eq("a size below zero", batch([step("s-commit", "COMMITTING", batch_size=-1)]), set())
    c.eq("a size that is a boolean", batch([step("s-commit", "COMMITTING", batch_size=True)]), set())
    c.eq("a size that is text", batch([step("s-commit", "COMMITTING", batch_size="4")]), set())
    c.eq(
        "a step that is the last under the stamped checkpoint",
        batch([step("s-commit", "COMMITTING", batch_size=4)], stamped=11),
        {6, 7, 8, 9, 10},
    )
    c.eq("a step at the stamped size is outside it", batch([step("s-commit", "COMMITTING", batch_size=4)], stamped=10), set())
    c.eq("a step past the stamped size is outside it", batch([step("s-commit", "COMMITTING", batch_size=4)], stamped=5), set())
    c.eq("a step the ledger does not hold", batch([step("s-missing", "COMMITTING", batch_size=4)]), set())
    c.eq("another cycle's committing step", batch([step("s-commit", "COMMITTING", "cy-b", batch_size=4)]), set())
    c.eq(
        "two committing steps of one cycle name no batch",
        batch([step("s-commit", "COMMITTING", batch_size=4), step("s-other", "COMMITTING", batch_size=2)], stamped=30),
        set(),
    )
    c.eq(
        "a step of another state is not the committing step",
        batch([step("s-commit", "COMMITTED", batch_size=4), step("s-other", "COMMITTING", batch_size=2)], stamped=30),
        {23, 24, 25},
    )
    c.eq(
        "records that are not cycle steps are not read as one",
        batch([{"type": "forecast", "id": "s-commit", "body": {"cycle_id": "cy-a", "state": "COMMITTING", "batch_size": 4}}]),
        set(),
    )
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_constants,
        check_settings,
        check_files_and_keys,
        check_make_protocol,
        check_arms_of,
        check_practice_configs,
        check_assembly,
        check_ondemand_settings,
        check_ondemand_assembly,
        check_desk_settings,
        check_desk_assembly,
        check_barrier_assembly,
        check_models_assembly,
        check_models_member,
        check_make_protocol_models,
        check_killed_after_the_stamps,
        check_running,
        check_clock_status,
        check_cleanup,
        check_assemble_edges,
        check_integrity_failure,
        check_reading,
        check_ryo,
        check_stored_checkpoints,
        check_whole_batch,
    ):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                found = (
                    check(mod)
                    if check
                    in (
                        check_arms_of,
                        check_practice_configs,
                        check_constants,
                        check_settings,
                        check_ondemand_settings,
                        check_desk_settings,
                        check_models_member,
                        check_whole_batch,
                    )
                    else check(mod, Path(tmp))
                )  # type: ignore[call-arg]
            bad += [f"{check.__name__}: {message}" for message in found]
        except TimeoutError:
            raise
        except Exception as exc:
            import traceback

            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}\n{traceback.format_exc()[-1500:]}")
    return bad
