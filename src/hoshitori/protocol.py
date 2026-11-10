"""The protocol document: everything a cycle may vary, in one canonical record whose SHA-256 is the ``protocol_hash`` of
everything it writes.

``CYCLE_RUNNER_SPEC`` section 1 is the rule. Changing any member makes another protocol, so other cycle ids; nothing inside a
scored period is ever edited. The document is read strictly: every member once, every value in range, the watchlist sorted,
the hash of the arm's mapping named. ``dump`` and ``load`` are the file; ``Protocol`` is the checked value.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Final

from hoshitori import canonical
from hoshitori.collect import Window
from hoshitori.evidence import FamilyKind
from hoshitori.manifest import Rules
from hoshitori.rules_arm import HORIZONS
from hoshitori.timeline import CUTOFF_MARGIN_MS, gap_bound

FORMAT: Final = 1
_PLAIN: Final = re.compile(r"[0-9]+(?:\.[0-9]+)?", re.ASCII)
PRACTICE_VERSION: Final = 1  # the layout of the practice terms below; a protocol without any keeps the hash it had before there were any
# what a practice book is run under, fixed before the period: the opening equity, a request's share, the cost levels and the risk limits.
# The keys and their order are the layout; the values are decimals as text.
PRACTICE_KEYS: Final = (
    "equity",
    "request_weight",
    "fee_bps",
    "slip_bps",
    "stress_slip_bps",
    "latency_ms",
    "per_token",
    "gross",
    "net",
    "cluster",
    "halve_at",
    "flat_at",
    "halt_at",
)
PRACTICE_DEFAULTS: Final = (
    ("equity", "100000"),
    ("request_weight", "0.02"),
    ("fee_bps", "10"),
    ("slip_bps", "5"),
    ("stress_slip_bps", "25"),
    ("latency_ms", "15000"),
    ("per_token", "0.05"),
    ("gross", "0.30"),
    ("net", "0.20"),
    ("cluster", "0.10"),
    ("halve_at", "0.05"),
    ("flat_at", "0.075"),
    ("halt_at", "0.12"),
)
MAX_INSTANT_MS: Final = 253_402_300_799_999
MINUTE_MS: Final = 60_000
ARMS: Final = ("L0",)
MODEL_ARMS: Final = ("L1", "L1x", "L2")
HOUR_MS: Final = 3_600_000
SNAPSHOT_MARGIN_MS: Final = 10_000  # what a cycle needs, after the last arm answers, to record the answers before the snapshot is too old
PAIRS: Final = ("A",)
KINDS: Final = ("spot",)


class ProtocolError(ValueError):
    """A protocol document that is not valid, or a slot asked of a schedule that does not have it."""


def _hash(value: object, what: str) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise ProtocolError(f"{what} is 64 lower-case hexadecimal characters")


def _text(value: object, what: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 200:
        raise ProtocolError(f"{what} is a non-empty string of at most 200 characters")
    try:
        canonical.dumps(dict.fromkeys([value]))
    except canonical.CanonicalError as exc:
        raise ProtocolError(f"{what} is not valid canonical text: {exc}") from exc


def _whole(value: object, what: str, low: int, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise ProtocolError(f"{what} is a whole number from {low} to {high}")
    return value


@dataclass(frozen=True)
class Protocol:
    name: str
    schedule_start_ms: int
    schedule_every_ms: int
    horizons_hours: tuple[int, ...]
    scan_n: int
    event_cap: int
    scan_rule_version: str
    scan_rule_hash: str
    event_rule_version: str
    event_rule_hash: str
    watchlist: tuple[str, ...]
    oracle_pair: str
    primary_venue: str
    second_venue: str
    quote_class: str
    instrument_kind: str
    windows: tuple[tuple[int, int], ...]
    wait_s: int
    freshness_ms: tuple[tuple[str, int], ...]
    required_sources: tuple[str, ...]
    arms: tuple[tuple[str, str], ...]  # (arm, sha256 of its frozen mapping)
    root_version: int
    root_sha256: str
    clock_offset_limit_ms: int
    snapshot_freshness_ms: int
    barrier_arms: tuple[tuple[str, str], ...] = ()  # (arm, sha256 of its frozen barrier mapping): the arms that also forecast contract B
    # (arm, its agent version hash, ((hours, cutoff in ms after the snapshot), ...)): the model arms and the horizons each is admitted to
    model_arms: tuple[tuple[str, str, tuple[tuple[int, int], ...]], ...] = ()
    # sources read when they can be and never required: a candidate without one is not failed for it (the reading is optional evidence)
    optional_sources: tuple[str, ...] = ()
    # the terms of the practice books (fees, slippage, latency, sizing, risk limits) as (key, decimal text), in the order of PRACTICE_KEYS;
    # none: a protocol written before they were recorded, whose books ran under the defaults
    practice_terms: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        _text(self.name, "the name")
        _whole(self.schedule_start_ms, "the schedule start", 0, MAX_INSTANT_MS)
        if _whole(self.schedule_every_ms, "the interval between slots", MINUTE_MS, 366 * 86_400_000) % MINUTE_MS:
            raise ProtocolError("the interval between slots is a whole number of minutes")
        if (
            not self.horizons_hours
            or list(self.horizons_hours) != sorted(set(self.horizons_hours))
            or not set(self.horizons_hours) <= set(HORIZONS)
        ):
            raise ProtocolError(f"horizons are some of {HORIZONS} hours, sorted and once each")
        _whole(self.scan_n, "the scan cutoff", 1, 1000)
        _whole(self.event_cap, "the event cap", 0, 1000)
        for what, text in (("the scan rule version", self.scan_rule_version), ("the event rule version", self.event_rule_version)):
            _text(text, what)
        _hash(self.scan_rule_hash, "the scan rule hash")
        _hash(self.event_rule_hash, "the event rule hash")
        if list(self.watchlist) != sorted(set(self.watchlist)) or any(not s or s != s.upper() or len(s) > 40 for s in self.watchlist):
            raise ProtocolError("the watchlist is upper-case symbols, sorted and once each")
        if self.oracle_pair not in PAIRS or self.instrument_kind not in KINDS:
            raise ProtocolError(f"the oracle pair is one of {PAIRS} and the instrument kind one of {KINDS}")
        _text(self.primary_venue, "the primary venue")
        _text(self.second_venue, "the second venue")
        _text(self.quote_class, "the quote class")
        if self.primary_venue == self.second_venue:
            raise ProtocolError("the two oracles are different venues")
        if not self.windows or len({w[0] for w in self.windows}) != len(self.windows):
            raise ProtocolError("there is at least one window and no interval twice")
        for interval, bars in self.windows:
            _whole(interval, "a window's interval", 1, 86_400)
            _whole(bars, "a window's bars", 1, 300)
        _whole(self.wait_s, "the wait", 1, 3600)
        names = [name for name, _ in self.freshness_ms]
        if names != sorted(set(names)) or not set(names) <= {kind.value for kind in FamilyKind}:
            raise ProtocolError("freshness limits are by family kind, sorted and once each")
        for _, limit in self.freshness_ms:
            _whole(limit, "a freshness limit", 1, 7 * 86_400_000)
        if list(self.required_sources) != sorted(set(self.required_sources)):
            raise ProtocolError("required sources are sorted and once each")
        for source in self.required_sources:
            _text(source, "a required source")
        if list(self.optional_sources) != sorted(set(self.optional_sources)) or set(self.optional_sources) & set(self.required_sources):
            raise ProtocolError("optional sources are sorted, once each, and none of them is also required")
        for source in self.optional_sources:
            _text(source, "an optional source")
        arm_names = [name for name, _ in self.arms]
        if not arm_names or arm_names != sorted(set(arm_names)) or not set(arm_names) <= set(ARMS):
            raise ProtocolError(f"the arms are some of {ARMS}, sorted and once each")
        for _, mapping in self.arms:
            _hash(mapping, "an arm's mapping hash")
        barrier_names = [name for name, _ in self.barrier_arms]
        if barrier_names != sorted(set(barrier_names)) or not set(barrier_names) <= set(arm_names):
            raise ProtocolError("the barrier arms are some of the arms, sorted and once each")
        for _, mapping in self.barrier_arms:
            _hash(mapping, "an arm's barrier mapping hash")
        _whole(self.root_version, "the trusted-root version", 1, 10**9)
        _hash(self.root_sha256, "the trusted-root hash")
        _whole(self.clock_offset_limit_ms, "the clock offset limit", 1, 3_600_000)
        _whole(self.snapshot_freshness_ms, "the snapshot freshness limit", 1, 86_400_000)
        self._check_model_arms()

    def slot_plan(self) -> tuple[tuple[str, str, int], ...]:
        """``(arm, contract, hours)`` of every slot one candidate has in a cycle, in the order the cycle creates them: for each rules arm
        and each horizon the slot of contract D, then the slot of contract B if the arm's barrier mapping is pinned; then, for each model
        arm, the slot of contract D at each horizon the arm is admitted to. The cycle and the recovery of a lost cycle both read it."""
        barrier = {name for name, _ in self.barrier_arms}
        plan = [
            (arm, contract, hours)
            for arm, _ in self.arms
            for hours in self.horizons_hours
            for contract in (("D", "B") if arm in barrier else ("D",))
        ]
        plan += [(arm, "D", hours) for arm, _, horizons in self.model_arms for hours, _ in horizons]
        return tuple(plan)

    def _check_model_arms(self) -> None:
        names = [name for name, _, _ in self.model_arms]
        if names != sorted(set(names)) or not set(names) <= set(MODEL_ARMS):
            raise ProtocolError(f"the model arms are some of {MODEL_ARMS}, sorted and once each")
        cutoffs = {name: dict(horizons) for name, _, horizons in self.model_arms}
        for _, version, horizons in self.model_arms:
            _hash(version, "a model arm's version hash")
            hours = [h for h, _ in horizons]
            if not hours or hours != sorted(set(hours)) or not set(hours) <= set(self.horizons_hours):
                raise ProtocolError("a model arm is admitted to some of the protocol's horizons, sorted and once each")
            for h, cutoff in horizons:
                _whole(cutoff, "a decision cutoff", 1, gap_bound(h * HOUR_MS) - CUTOFF_MARGIN_MS)
                if cutoff > self.snapshot_freshness_ms - SNAPSHOT_MARGIN_MS:
                    raise ProtocolError("a decision cutoff leaves the snapshot time to be recorded before it is too old to commit")
        if "L1x" in cutoffs:
            if "L2" not in cutoffs or not set(cutoffs["L1x"]) <= set(cutoffs["L2"]):
                raise ProtocolError("the matched-compute control runs only where the debate it is matched to runs")
            if any(cutoff < cutoffs["L2"][h] for h, cutoff in cutoffs["L1x"].items()):
                raise ProtocolError("the control starts when the debate has finished, so its cutoff is no earlier than the debate's")

        self.practice_terms_checked()

    def practice_terms_checked(self) -> None:
        """The terms are the layout, each a decimal the books accept (the checks are the constructors of what they configure)."""
        if not self.practice_terms:
            return
        if tuple(k for k, _ in self.practice_terms) != PRACTICE_KEYS:
            raise ProtocolError(f"the practice terms are {', '.join(PRACTICE_KEYS)}, in that order")
        try:
            self.practice_values()
        except (ValueError, ArithmeticError) as exc:
            raise ProtocolError(f"the practice terms are not accepted by the books: {exc}") from exc

    def practice_values(self) -> dict[str, Decimal]:
        """The terms as decimals, the defaults if the protocol records none; refuses a term the books would refuse."""
        from hoshitori import fills, risk_kernel  # here, not above: the books import modules that read the protocol

        terms = dict(self.practice_terms or PRACTICE_DEFAULTS)
        if not all(_PLAIN.fullmatch(v) for v in terms.values()):
            raise ValueError("a term is a plain non-negative decimal")
        values = {k: Decimal(v) for k, v in terms.items()}
        fills.Costs(values["fee_bps"], values["slip_bps"], values["stress_slip_bps"], int(values["latency_ms"]))
        risk_kernel.Limits(*(values[k] for k in ("per_token", "gross", "net", "cluster", "halve_at", "flat_at", "halt_at")))
        if values["equity"] <= 0:
            raise ValueError("the opening equity is positive")
        if not 0 < values["request_weight"] <= 1:
            raise ValueError("a request is a share above zero and at most one")
        if values["latency_ms"] != int(values["latency_ms"]):
            raise ValueError("the latency is a whole number of milliseconds")
        return values

    def body(self) -> dict[str, canonical.JsonValue]:
        body = self._core()
        if self.practice_terms:  # likewise: a protocol that records none keeps the hash it had before they were recorded
            body["practice"] = {"version": PRACTICE_VERSION, "terms": dict(self.practice_terms)}
        if self.barrier_arms:  # a protocol with no barrier arm keeps the body, and so the hash, it had before there were any
            body["barrier_arms"] = dict(self.barrier_arms)
        if self.optional_sources:  # likewise: a protocol that names none keeps the hash it had before there were any
            body["optional_sources"] = list(self.optional_sources)
        if self.model_arms:  # likewise: a protocol with no model arm keeps the hash it had before there were any
            body["model_arms"] = {
                name: {"version": version, "cutoffs_ms": {str(h): cutoff for h, cutoff in horizons}}
                for name, version, horizons in self.model_arms
            }
        return body

    def _core(self) -> dict[str, canonical.JsonValue]:
        return {
            "format": FORMAT,
            "name": self.name,
            "schedule": {"start_ms": self.schedule_start_ms, "every_ms": self.schedule_every_ms},
            "horizons_hours": list(self.horizons_hours),
            "manifest": {
                "scan_n": self.scan_n,
                "event_cap": self.event_cap,
                "scan_rule": {"version": self.scan_rule_version, "hash": self.scan_rule_hash},
                "event_rule": {"version": self.event_rule_version, "hash": self.event_rule_hash},
                "watchlist": list(self.watchlist),
            },
            "oracle": {
                "pair": self.oracle_pair,
                "primary": self.primary_venue,
                "second": self.second_venue,
                "quote_class": self.quote_class,
                "kind": self.instrument_kind,
            },
            "collect": {
                "windows": [{"interval_s": i, "bars": b} for i, b in self.windows],
                "wait_s": self.wait_s,
                "freshness_ms": dict(self.freshness_ms),
                "required_sources": list(self.required_sources),
            },
            "arms": dict(self.arms),
            "timing": {
                "root_version": self.root_version,
                "root_sha256": self.root_sha256,
                "clock_offset_limit_ms": self.clock_offset_limit_ms,
                "snapshot_freshness_ms": self.snapshot_freshness_ms,
            },
        }

    @property
    def sha256(self) -> str:
        return hashlib.sha256(canonical.dumps(self.body())).hexdigest()

    @property
    def watchlist_hash(self) -> str:
        return hashlib.sha256(canonical.dumps({"watchlist": list(self.watchlist)})).hexdigest()

    def slot(self, k: int) -> int:
        """The instant of slot ``k`` (from 0)."""
        return self.schedule_start_ms + _whole(k, "a slot number", 0, 10**7) * self.schedule_every_ms

    def is_slot(self, instant_ms: int) -> bool:
        """Whether ``instant_ms`` is exactly a slot of the schedule."""
        return instant_ms >= self.schedule_start_ms and (instant_ms - self.schedule_start_ms) % self.schedule_every_ms == 0

    def slots_through(self, until_ms: int) -> tuple[int, ...]:
        """Every slot from the first to ``until_ms``, inclusive."""
        if until_ms < self.schedule_start_ms:
            return ()
        return tuple(
            self.schedule_start_ms + k * self.schedule_every_ms
            for k in range((until_ms - self.schedule_start_ms) // self.schedule_every_ms + 1)
        )

    def slots_between(self, after_ms: int, until_ms: int) -> tuple[int, ...]:
        """The slots in ``(after_ms, until_ms]``."""
        if until_ms < after_ms:
            raise ProtocolError("the window starts no later than it ends")
        first = max(0, -(-(after_ms + 1 - self.schedule_start_ms) // self.schedule_every_ms))
        out = []
        k = first
        while self.slot(k) <= until_ms:
            out.append(self.slot(k))
            k += 1
        return tuple(out)

    def manifest_rules(self) -> Rules:
        return Rules(
            self.scan_rule_version,
            self.scan_rule_hash,
            self.scan_n,
            self.event_rule_version,
            self.event_rule_hash,
            self.event_cap,
            self.watchlist_hash,
        )

    def collect_windows(self) -> tuple[Window, ...]:
        return tuple(Window(interval, bars) for interval, bars in self.windows)

    def barrier_mapping_of(self, arm: str) -> str:
        for name, mapping in self.barrier_arms:
            if name == arm:
                return mapping
        raise ProtocolError(f"the protocol has no barrier arm {arm!r}")

    def model_arm_of(self, arm: str) -> tuple[str, tuple[tuple[int, int], ...]]:
        """The version hash a model arm is pinned to, and its (hours, cutoff in ms) pairs."""
        for name, version, horizons in self.model_arms:
            if name == arm:
                return version, horizons
        raise ProtocolError(f"the protocol has no model arm {arm!r}")

    def cutoff_ms(self, arm: str, hours: int) -> int | None:
        """How long after the snapshot a model arm has at a horizon, or ``None`` when it is not admitted there."""
        return dict(self.model_arm_of(arm)[1]).get(hours)

    def mapping_of(self, arm: str) -> str:
        for name, mapping in self.arms:
            if name == arm:
                return mapping
        raise ProtocolError(f"the protocol has no arm {arm!r}")


def dump(protocol: Protocol) -> bytes:
    return canonical.dumps({**protocol.body(), "sha256": protocol.sha256})


def _object(value: object, names: set[str], what: str) -> dict[str, canonical.JsonValue]:
    if not isinstance(value, dict) or set(value) != names:
        raise ProtocolError(f"{what} has the members {sorted(names)}")
    return value


def load(raw: bytes) -> Protocol:
    """A protocol from its file, or an error: canonical bytes, every member once, a hash that is the hash of what the file says."""
    try:
        record = canonical.parse_record(raw)
    except canonical.CanonicalError as exc:
        raise ProtocolError(f"the protocol file is not a canonical record: {exc}") from exc
    if canonical.dumps(record) != raw:
        raise ProtocolError("the protocol file is not in canonical form")
    claimed = record.pop("sha256", None)
    barrier = _dict(record.pop("barrier_arms")) if "barrier_arms" in record else {}
    models = _dict(record.pop("model_arms")) if "model_arms" in record else {}
    optional = _list(record.pop("optional_sources")) if "optional_sources" in record else []
    practice = _practice_member(record.pop("practice")) if "practice" in record else ()
    top = _object(
        record, {"format", "name", "schedule", "horizons_hours", "manifest", "oracle", "collect", "arms", "timing"}, "the protocol"
    )
    if top["format"] != FORMAT:
        raise ProtocolError(f"the protocol format is {FORMAT}")
    schedule = _object(top["schedule"], {"start_ms", "every_ms"}, "the schedule")
    manifest = _object(top["manifest"], {"scan_n", "event_cap", "scan_rule", "event_rule", "watchlist"}, "the manifest settings")
    scan, event = (
        _object(manifest["scan_rule"], {"version", "hash"}, "the scan rule"),
        _object(manifest["event_rule"], {"version", "hash"}, "the event rule"),
    )
    oracle = _object(top["oracle"], {"pair", "primary", "second", "quote_class", "kind"}, "the oracle")
    collect_ = _object(top["collect"], {"windows", "wait_s", "freshness_ms", "required_sources"}, "the collection settings")
    timing = _object(
        top["timing"], {"root_version", "root_sha256", "clock_offset_limit_ms", "snapshot_freshness_ms"}, "the timing settings"
    )
    horizons, watchlist = _list(top["horizons_hours"]), _list(manifest["watchlist"])
    windows, sources = _list(collect_["windows"]), _list(collect_["required_sources"])
    freshness, arms = _dict(collect_["freshness_ms"]), _dict(top["arms"])
    protocol = Protocol(
        name=top["name"],  # type: ignore[arg-type]
        schedule_start_ms=schedule["start_ms"],  # type: ignore[arg-type]
        schedule_every_ms=schedule["every_ms"],  # type: ignore[arg-type]
        horizons_hours=tuple(horizons),  # type: ignore[arg-type]
        scan_n=manifest["scan_n"],  # type: ignore[arg-type]
        event_cap=manifest["event_cap"],  # type: ignore[arg-type]
        scan_rule_version=scan["version"],  # type: ignore[arg-type]
        scan_rule_hash=scan["hash"],  # type: ignore[arg-type]
        event_rule_version=event["version"],  # type: ignore[arg-type]
        event_rule_hash=event["hash"],  # type: ignore[arg-type]
        watchlist=tuple(watchlist),  # type: ignore[arg-type]
        oracle_pair=oracle["pair"],  # type: ignore[arg-type]
        primary_venue=oracle["primary"],  # type: ignore[arg-type]
        second_venue=oracle["second"],  # type: ignore[arg-type]
        quote_class=oracle["quote_class"],  # type: ignore[arg-type]
        instrument_kind=oracle["kind"],  # type: ignore[arg-type]
        windows=tuple(_window(w) for w in windows),
        wait_s=collect_["wait_s"],  # type: ignore[arg-type]
        freshness_ms=tuple(freshness.items()),  # type: ignore[arg-type]
        required_sources=tuple(sources),  # type: ignore[arg-type]
        arms=tuple(arms.items()),  # type: ignore[arg-type]
        root_version=timing["root_version"],  # type: ignore[arg-type]
        root_sha256=timing["root_sha256"],  # type: ignore[arg-type]
        clock_offset_limit_ms=timing["clock_offset_limit_ms"],  # type: ignore[arg-type]
        snapshot_freshness_ms=timing["snapshot_freshness_ms"],  # type: ignore[arg-type]
        barrier_arms=tuple(barrier.items()),  # type: ignore[arg-type]
        model_arms=tuple(_model_arm(name, value) for name, value in models.items()),
        optional_sources=tuple(optional),  # type: ignore[arg-type]
        practice_terms=practice,
    )
    if claimed != protocol.sha256:
        raise ProtocolError("the protocol file's hash is not the hash of its contents")
    if dump(protocol) != raw:  # an empty member the writer leaves out, or an hour written "01": other bytes for the same protocol
        raise ProtocolError("the protocol file is not the one its writer makes for what it says")
    return protocol


_SHAPE: Final = "horizons, the watchlist, windows and sources are lists, and freshness and arms are objects"


def _list(value: canonical.JsonValue) -> list[canonical.JsonValue]:
    if not isinstance(value, list):
        raise ProtocolError(_SHAPE)
    return value


def _dict(value: canonical.JsonValue) -> dict[str, canonical.JsonValue]:
    if not isinstance(value, dict):
        raise ProtocolError(_SHAPE)
    return value


def _model_arm(name: str, value: canonical.JsonValue) -> tuple[str, str, tuple[tuple[int, int], ...]]:
    arm = _object(value, {"version", "cutoffs_ms"}, "a model arm")
    cutoffs = _dict(arm["cutoffs_ms"])
    try:
        horizons = tuple(
            sorted((int(hours), cutoff) for hours, cutoff in cutoffs.items())
        )  # the keys come back in text order: "1", "24", "4"
    except ValueError:
        raise ProtocolError("a model arm's cutoffs are named by the hours of the horizon") from None
    return name, arm["version"], horizons  # type: ignore[return-value]


def _practice_member(value: canonical.JsonValue) -> tuple[tuple[str, str], ...]:
    member = _object(value, {"version", "terms"}, "the practice terms")
    if member["version"] != PRACTICE_VERSION:
        raise ProtocolError(f"the practice terms are version {PRACTICE_VERSION}")
    terms = _dict(member["terms"])
    if any(not isinstance(v, str) for v in terms.values()):
        raise ProtocolError("a practice term is a decimal written as text")
    if set(terms) != set(PRACTICE_KEYS):
        raise ProtocolError(f"the practice terms are {', '.join(PRACTICE_KEYS)}")
    return tuple((k, terms[k]) for k in PRACTICE_KEYS)  # type: ignore[misc]  # the file's keys come back in text order


def _window(value: canonical.JsonValue) -> tuple[int, int]:
    window = _object(value, {"interval_s", "bars"}, "a window")
    return window["interval_s"], window["bars"]  # type: ignore[return-value]


# --- the protocol of the first league -----------------------------------------------------------------------------------------------------
def _rule_hash(rule: str, version: str, text: str) -> str:
    return hashlib.sha256(canonical.dumps({"rule": rule, "version": version, "text": text})).hexdigest()


SCAN_RULE: Final = ("scan/1", _rule_hash("ryo_scan", "scan/1", "the first N rows of RYO's own scan, by its rank"))
EVENT_RULE: Final = (
    "event/1",
    _rule_hash("event", "event/1", "registry changes known since the last discovery, earliest first, at most the cap"),
)


def standard(
    *,
    name: str,
    start_ms: int,
    every_ms: int,
    root_version: int,
    root_sha256: str,
    mappings: dict[str, str],
    watchlist: tuple[str, ...],
    primary_venue: str = "binance-spot",
    second_venue: str = "okx",
    barrier_mappings: dict[str, str] | None = None,
    model_arms: tuple[tuple[str, str, tuple[tuple[int, int], ...]], ...] = (),
    optional_sources: tuple[str, ...] = (),
    practice_terms: tuple[tuple[str, str], ...] = PRACTICE_DEFAULTS,
) -> Protocol:
    """The protocol of the first league: the controlled arm on the exchanges' candles, over the three horizons, with the priors of the
    contract (a scan of ten, at most five events, two windows, the freshness limits, a clock that may differ by two seconds)."""
    return Protocol(
        name=name,
        schedule_start_ms=start_ms,
        schedule_every_ms=every_ms,
        horizons_hours=tuple(HORIZONS),
        scan_n=10,
        event_cap=5,
        scan_rule_version=SCAN_RULE[0],
        scan_rule_hash=SCAN_RULE[1],
        event_rule_version=EVENT_RULE[0],
        event_rule_hash=EVENT_RULE[1],
        watchlist=tuple(sorted(set(watchlist))),
        oracle_pair="A",
        primary_venue=primary_venue,
        second_venue=second_venue,
        quote_class="USDT",
        instrument_kind="spot",
        windows=((60, 120), (3600, 40)),
        wait_s=30,
        freshness_ms=(("EXCHANGE", 60_000), ("NEWS", 900_000), ("RYO", 300_000)),
        required_sources=("hoshitori.features",),
        arms=tuple(sorted(mappings.items())),
        root_version=root_version,
        root_sha256=root_sha256,
        clock_offset_limit_ms=2_000,
        snapshot_freshness_ms=600_000,
        barrier_arms=tuple(sorted((barrier_mappings or {}).items())),
        model_arms=tuple(sorted(model_arms)),
        optional_sources=tuple(sorted(set(optional_sources))),
        practice_terms=practice_terms,
    )
