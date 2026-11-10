"""The forecast record: the one shape a directional (D) or barrier (B) forecast has when it is committed (contract section 1).

A forecast is a canonical record (``envelope``, ``canonical``) whose body holds only what the producer knows when it commits:
who made it, on what evidence time, about which asset and oracle pair, over which horizon, with which probabilities and which
invalidation conditions. What happens afterwards (the commitment time, the attested level, the origin, the outcome) is never in it:
those are records of their own (``lifecycle``, the ledger's ``committed_at``, ``settlement``), so a committed forecast cannot be
edited and a late fact cannot be mistaken for an early one.

* **Identity.** ``fc`` over the cycle, arm, asset, contract and horizon: one slot, one id. Producing it twice gives the same id; a
  slot that never became a forecast (no call, a failure) has the same id, which is what makes the attempted-forecast denominator
  countable.
* **Numbers are decimal strings**, never numbers: a probability, an ATR, a multiplier. A body is read back with the same strictness
  it was written: an unknown member, a missing member, a string that is not a plain decimal, a boolean where an integer belongs
  and a derived value that disagrees with its parts are all refused. A plain decimal has no sign, so a negative zero is refused when a
  forecast is made, and no body is written that its reader would refuse.
* **Derived values are recomputed.** A barrier's offsets are the committed multipliers times the ATR at the evidence time; the body
  carries them so a reader does not recompute, and a body whose offsets disagree is refused.
* **The caller's decimal context plays no part.** Every product, sum and shortest text here is computed in the module's own
  context, so a record, what reading it gives and every refusal are the same whatever context the writer or the reader is in.
* **Commitment levels** C0 to C4 (contract section 8) are ordered; only C3 and above count towards a headline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from enum import StrEnum
from typing import Final, cast

from hoshitori import canonical, envelope
from hoshitori.ids import natural_id
from hoshitori.settlement import Barrier as SettlementBarrier
from hoshitori.settlement import Directional as SettlementDirectional
from hoshitori.settlement import Side
from hoshitori.timeline import TimelineError, from_ms, horizon_ok, to_ms
from hoshitori.timeutil import TimeError, format_rfc3339_ms, parse_rfc3339

TYPE: Final = "forecast"
TYPE_VERSION: Final = 1
CONTRACT_VERSIONS: Final = {"D": "D/1", "B": "B/1"}
CANDLE_INTERVAL: Final = "1m"
INSTRUMENT_KINDS: Final = ("spot", "perpetual", "future")
ORACLE_PAIRS: Final = ("A", "B")
MAX_DIGITS: Final = 30  # so that a product of two committed numbers is exact at 60 digits
MAX_TEXT: Final = 500
MAX_ITEMS: Final = 64
MAX_INSTANT_MS: Final = 253_402_300_799_999  # the last millisecond of the year 9999, which a four-digit year can still write
MAX_HORIZON_MS: Final = 366 * 86_400_000  # far beyond any contract horizon (the longest is the arena's seven days)
# Never the caller's context: 60 digits hold a product of two numbers of at most MAX_DIGITS digits exactly, so an offset and its text
# are never rounded, and they are the digits, rounded half to even, in which scoring checks that the three probabilities sum to one.
_CTX: Final = Context(prec=60, rounding=ROUND_HALF_EVEN)

_DECIMAL = re.compile(r"(0|[1-9][0-9]*)(\.[0-9]+)?")
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ForecastError(ValueError):
    """A forecast that cannot be committed, or a body that is not one."""


class Level(StrEnum):
    """How firmly a forecast was fixed in time before its outcome window (contract section 8)."""

    C0 = "C0"  # recorded late; never counted
    C1 = "C1"  # in our ledger only
    C2 = "C2"  # plus a public reference, which offline is a claim and not a proof
    C3 = "C3"  # plus two counted time-stamp tokens from distinct operators
    C4 = "C4"  # plus witnesses and a public log, which add order and publicity but no time

    @property
    def rank(self) -> int:
        return tuple(Level).index(self)

    @property
    def independent(self) -> bool:
        """C3 and above: the only levels that count towards a headline."""
        return self.rank >= Level.C3.rank


def _text(value: str, what: str) -> None:
    if not isinstance(value, str) or not value or len(value) > MAX_TEXT:
        raise ForecastError(f"{what} is a non-empty string of at most {MAX_TEXT} characters")
    try:
        canonical.dumps({"value": value})
    except canonical.CanonicalError as exc:
        raise ForecastError(f"{what} is not valid canonical text: {exc}") from exc


def _hash(value: str, what: str) -> None:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise ForecastError(f"{what} is 64 lower-case hexadecimal characters")


def _ms(value: int, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_INSTANT_MS:
        raise ForecastError(f"{what} is a whole number of milliseconds up to the end of the year 9999")
    return value


def _decimal(value: Decimal, what: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0 or len(value.as_tuple().digits) > MAX_DIGITS:
        raise ForecastError(f"{what} is a decimal of zero or more with at most {MAX_DIGITS} digits")
    if value.is_signed():  # a negative zero, which ``decimal_text`` would write as "-0" and ``read_decimal`` refuses
        raise ForecastError(f"{what} is a negative zero, and a body's decimals have no sign")
    return value


def _probability(value: Decimal, what: str) -> Decimal:
    if _decimal(value, what) > 1:
        raise ForecastError(f"{what} is a probability from 0 to 1")
    return value


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def shortest_text(value: Decimal) -> str:
    """A derived number in its shortest plain form (no trailing zeros), so its text does not depend on the scales of its parts. A
    number of at most 60 digits, which every offset is, keeps all its digits."""
    with localcontext(_CTX):
        return format(value.normalize(), "f") if value else "0"


def read_decimal(text: object, what: str) -> Decimal:
    """A plain decimal string, nothing else: no sign, no exponent, no spaces, no number."""
    if not isinstance(text, str) or _DECIMAL.fullmatch(text) is None:
        raise ForecastError(f"{what} is a plain decimal string")
    return Decimal(text)


def forecast_id(cycle_id: str, arm_id: str, asset_id: str, contract: str, horizon_ms: int) -> str:
    """The id of a slot: the same cycle, arm, asset, contract and horizon are always the same forecast."""
    return natural_id("fc", {"cycle": cycle_id, "arm": arm_id, "asset": asset_id, "contract": contract, "horizon_ms": horizon_ms})


def barrier_offset(multiplier: Decimal, atr: Decimal) -> Decimal:
    with localcontext(_CTX):
        return multiplier * atr


@dataclass(frozen=True)
class Common:
    """What every forecast carries, whichever contract it is."""

    cycle_id: str
    arm_id: str
    agent_version_hash: str
    protocol_hash: str
    contract: str
    asset_id: str
    symbol: str
    quote_class: str
    instrument_kind: str
    oracle_pair: str
    oracle_primary: str
    oracle_second: str
    as_of_ms: int
    horizon_ms: int
    invalidation: tuple[str, ...]
    evidence: tuple[tuple[str, str], ...]  # (evidence id, sha256 of that record's canonical bytes)
    observation_set_hash: str
    interpretation_visible: bool

    def __post_init__(self) -> None:
        for value, what in (
            (self.cycle_id, "the cycle id"),
            (self.arm_id, "the arm id"),
            (self.asset_id, "the asset id"),
            (self.symbol, "the symbol"),
            (self.quote_class, "the quote class"),
            (self.oracle_primary, "the primary oracle"),
            (self.oracle_second, "the second oracle"),
        ):
            _text(value, what)
        _hash(self.agent_version_hash, "the agent version hash")
        _hash(self.protocol_hash, "the protocol hash")
        _hash(self.observation_set_hash, "the observation set hash")
        if self.contract not in CONTRACT_VERSIONS:
            raise ForecastError("a forecast is contract D or B")
        if self.instrument_kind not in INSTRUMENT_KINDS:
            raise ForecastError("an instrument is spot, perpetual or future")
        if self.oracle_pair not in ORACLE_PAIRS:
            raise ForecastError("an oracle pair is A or B")
        _ms(self.as_of_ms, "the evidence time")
        try:
            horizon_ok(self.horizon_ms)
        except TimelineError as exc:
            raise ForecastError(str(exc)) from exc
        if self.horizon_ms > MAX_HORIZON_MS:
            raise ForecastError("a horizon is at most 366 days")
        if not isinstance(self.interpretation_visible, bool):
            raise ForecastError("interpretation_visible is true or false")
        if not self.invalidation or len(self.invalidation) > MAX_ITEMS:
            raise ForecastError("a forecast has at least one invalidation condition")
        for condition in self.invalidation:
            _text(condition, "an invalidation condition")
        if len(self.evidence) > MAX_ITEMS or len({ident for ident, _ in self.evidence}) != len(self.evidence):
            raise ForecastError("evidence is a list of distinct ids")
        for ident, digest in self.evidence:
            _text(ident, "an evidence id")
            _hash(digest, "an evidence hash")

    @property
    def forecast_id(self) -> str:
        return forecast_id(self.cycle_id, self.arm_id, self.asset_id, self.contract, self.horizon_ms)

    def body(self) -> dict[str, canonical.JsonValue]:
        return {
            "cycle_id": self.cycle_id,
            "arm_id": self.arm_id,
            "agent_version_hash": self.agent_version_hash,
            "protocol_hash": self.protocol_hash,
            "contract": self.contract,
            "contract_version": CONTRACT_VERSIONS[self.contract],
            "asset_id": self.asset_id,
            "symbol": self.symbol,
            "quote_class": self.quote_class,
            "instrument_kind": self.instrument_kind,
            "oracle_pair": self.oracle_pair,
            "oracle_primary": self.oracle_primary,
            "oracle_second": self.oracle_second,
            "candle_interval": CANDLE_INTERVAL,
            "as_of": format_rfc3339_ms(from_ms(self.as_of_ms)),
            "horizon_ms": self.horizon_ms,
            "invalidation": list(self.invalidation),
            "evidence_ids": [ident for ident, _ in self.evidence],
            "observation_set_hash": self.observation_set_hash,
            "interpretation_visible": self.interpretation_visible,
        }


@dataclass(frozen=True)
class DirectionalForecast:
    """Contract D: the probability that the price at the horizon is above the reference price."""

    common: Common
    p: Decimal

    def __post_init__(self) -> None:
        if self.common.contract != "D":
            raise ForecastError("a directional forecast is contract D")
        _probability(self.p, "the probability")

    def to_body(self) -> dict[str, canonical.JsonValue]:
        return {**self.common.body(), "p": decimal_text(self.p)}

    def settlement_input(self, origin_ms: int) -> SettlementDirectional:
        """The forecast as the settlement rules read it, once the lifecycle has set its origin."""
        c = self.common
        return SettlementDirectional(c.forecast_id, origin_ms, c.horizon_ms, c.quote_class)


@dataclass(frozen=True)
class BarrierForecast:
    """Contract B: which of the target, the stop or neither is reached first within the horizon."""

    common: Common
    side: Side
    atr: Decimal
    k_target: Decimal
    k_stop: Decimal
    p_target: Decimal
    p_stop: Decimal
    p_timeout: Decimal

    def __post_init__(self) -> None:
        if self.common.contract != "B":
            raise ForecastError("a barrier forecast is contract B")
        if not isinstance(self.side, Side):
            raise ForecastError("a barrier forecast has a side")
        for value, what in ((self.atr, "the ATR"), (self.k_target, "the target multiplier"), (self.k_stop, "the stop multiplier")):
            if _decimal(value, what) == 0:
                raise ForecastError(f"{what} is positive")
        parts = (_probability(self.p_target, "p_target"), _probability(self.p_stop, "p_stop"), _probability(self.p_timeout, "p_timeout"))
        with localcontext(_CTX):  # the sum scoring checks, so that a forecast that is committed can be scored
            if sum(parts, Decimal(0)) != 1:
                raise ForecastError("the three probabilities sum to exactly one")

    @property
    def target_offset(self) -> Decimal:
        return barrier_offset(self.k_target, self.atr)

    @property
    def stop_offset(self) -> Decimal:
        return barrier_offset(self.k_stop, self.atr)

    @property
    def probabilities(self) -> tuple[Decimal, Decimal, Decimal]:
        return (self.p_target, self.p_stop, self.p_timeout)

    def to_body(self) -> dict[str, canonical.JsonValue]:
        return {
            **self.common.body(),
            "side": self.side.value,
            "atr": decimal_text(self.atr),
            "k_target": decimal_text(self.k_target),
            "k_stop": decimal_text(self.k_stop),
            "target_offset": shortest_text(self.target_offset),
            "stop_offset": shortest_text(self.stop_offset),
            "p_target": decimal_text(self.p_target),
            "p_stop": decimal_text(self.p_stop),
            "p_timeout": decimal_text(self.p_timeout),
        }

    def settlement_input(self, origin_ms: int) -> SettlementBarrier:
        c = self.common
        return SettlementBarrier(c.forecast_id, origin_ms, c.horizon_ms, c.quote_class, self.side, self.target_offset, self.stop_offset)


Forecast = DirectionalForecast | BarrierForecast


def to_record(forecast: Forecast, source: str, created_at_ms: int) -> dict[str, canonical.JsonValue]:
    """The record a producer submits to the ledger. Its creation time may not precede the evidence time it claims."""
    common = forecast.common
    if _ms(created_at_ms, "the creation time") < common.as_of_ms:
        raise ForecastError("a forecast is created at or after its evidence time")
    record: dict[str, canonical.JsonValue] = {
        "spec": envelope.SPEC,
        "type": TYPE,
        "type_version": TYPE_VERSION,
        "id": common.forecast_id,
        "source": source,
        "created_at": format_rfc3339_ms(from_ms(created_at_ms)),
        "body": forecast.to_body(),
        "refs": [{"id": ident, "sha256": digest} for ident, digest in common.evidence],
        "privacy": "public",
    }
    try:
        envelope.validate_producer_record(record)
    except envelope.EnvelopeError as exc:
        raise ForecastError(f"the record does not satisfy the envelope: {exc}") from exc
    return record


# --- reading a record back ------------------------------------------------------------------------------------------------------------
_COMMON_MEMBERS: Final = frozenset(
    {
        "cycle_id", "arm_id", "agent_version_hash", "protocol_hash", "contract", "contract_version", "asset_id", "symbol", "quote_class",
        "instrument_kind", "oracle_pair", "oracle_primary", "oracle_second", "candle_interval", "as_of", "horizon_ms", "invalidation",
        "evidence_ids", "observation_set_hash", "interpretation_visible",
    }
)  # fmt: skip
_BARRIER_MEMBERS: Final = frozenset(
    {"side", "atr", "k_target", "k_stop", "target_offset", "stop_offset", "p_target", "p_stop", "p_timeout"}
)


def _string_list(value: canonical.JsonValue, what: str) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ForecastError(f"{what} is a list of strings")
    return [item for item in value if isinstance(item, str)]


def _instant(text: canonical.JsonValue, what: str) -> int:
    if not isinstance(text, str):
        raise ForecastError(f"{what} is an RFC 3339 string")
    try:
        moment = parse_rfc3339(text)
        milliseconds = to_ms(moment)
    except (TimeError, TimelineError) as exc:
        raise ForecastError(f"{what}: {exc}") from exc
    if format_rfc3339_ms(from_ms(milliseconds)) != text:
        raise ForecastError(f"{what} is written as UTC with milliseconds")
    return milliseconds


def from_record(record: dict[str, canonical.JsonValue]) -> Forecast:
    """Read a forecast record exactly as strictly as it was written; anything else is refused, never repaired."""
    try:
        envelope.validate_producer_record({k: v for k, v in record.items() if k != "committed_at"})
    except envelope.EnvelopeError as exc:
        raise ForecastError(f"the envelope: {exc}") from exc
    if record["type"] != TYPE or record["type_version"] != TYPE_VERSION:
        raise ForecastError("not a forecast record of a version this reader knows")
    body = cast(dict[str, canonical.JsonValue], record["body"])  # the envelope check above guarantees an object
    contract = body.get("contract")
    if not isinstance(contract, str) or contract not in CONTRACT_VERSIONS:
        raise ForecastError("a forecast is contract D or B")
    expected = _COMMON_MEMBERS | ({"p"} if contract == "D" else _BARRIER_MEMBERS)
    if set(body) != expected:
        raise ForecastError(f"the body has the members {sorted(set(body) ^ expected)} wrong")
    if body["contract_version"] != CONTRACT_VERSIONS[contract] or body["candle_interval"] != CANDLE_INTERVAL:
        raise ForecastError("the contract version or the candle interval is not one this reader knows")
    refs = cast(list[dict[str, str]], record.get("refs", []))  # likewise: the envelope check guarantees {id, sha256} objects
    evidence_ids = _string_list(body["evidence_ids"], "evidence_ids")
    pairs = tuple((ref["id"], ref["sha256"]) for ref in refs)
    if [ident for ident, _ in pairs] != evidence_ids:
        raise ForecastError("the references and the evidence ids differ")
    horizon = body["horizon_ms"]
    if isinstance(horizon, bool) or not isinstance(horizon, int):
        raise ForecastError("horizon_ms is a whole number")
    flag = cast(bool, body["interpretation_visible"])  # Common refuses anything that is not a boolean
    common = Common(
        _as_text(body, "cycle_id"), _as_text(body, "arm_id"), _as_text(body, "agent_version_hash"), _as_text(body, "protocol_hash"),
        _as_text(body, "contract"), _as_text(body, "asset_id"), _as_text(body, "symbol"), _as_text(body, "quote_class"),
        _as_text(body, "instrument_kind"), _as_text(body, "oracle_pair"), _as_text(body, "oracle_primary"), _as_text(body, "oracle_second"),
        _instant(body["as_of"], "as_of"), horizon, tuple(_string_list(body["invalidation"], "invalidation")), pairs,
        _as_text(body, "observation_set_hash"), flag,
    )  # fmt: skip
    if record["id"] != common.forecast_id:
        raise ForecastError("the id is not the id of this slot")
    if contract == "D":
        return DirectionalForecast(common, read_decimal(body["p"], "p"))
    side = _as_text(body, "side")
    if side not in {member.value for member in Side}:
        raise ForecastError("a side is LONG or SHORT")
    forecast = BarrierForecast(
        common, Side(side), read_decimal(body["atr"], "atr"), read_decimal(body["k_target"], "k_target"),
        read_decimal(body["k_stop"], "k_stop"), read_decimal(body["p_target"], "p_target"), read_decimal(body["p_stop"], "p_stop"),
        read_decimal(body["p_timeout"], "p_timeout"),
    )  # fmt: skip
    for name, value in (("target_offset", forecast.target_offset), ("stop_offset", forecast.stop_offset)):
        if body[name] != shortest_text(value):
            raise ForecastError(f"{name} disagrees with the multiplier and the ATR it comes from")
    return forecast


def _as_text(body: dict[str, canonical.JsonValue], name: str) -> str:
    value = body[name]
    if not isinstance(value, str):
        raise ForecastError(f"{name} is a string")
    return value
