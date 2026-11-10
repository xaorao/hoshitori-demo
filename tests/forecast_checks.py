"""Checks of the forecast record (``hoshitori.forecast``), runnable against a mutant."""

from __future__ import annotations

import copy
import hashlib
import types
from collections.abc import Callable
from decimal import ROUND_CEILING, ROUND_FLOOR, Context, Decimal, Inexact, localcontext
from typing import Any

from hoshitori import canonical, scoring
from hoshitori.ids import natural_id

D = Decimal
H = 3_600_000
AS_OF = 1_793_311_000_000
HASH_A, HASH_B, HASH_C, HASH_D = "a" * 64, "b" * 64, "c" * 64, "d" * 64
ATR30 = "1.23456789012345678901234567891"  # thirty digits, the most a committed number may have
OFFSET31 = "1.851851835185185183518518518365"  # 1.5 times it, exactly: thirty-one digits, more than the default decimal context holds
ROUNDED28 = "1.851851835185185183518518518"  # the same offset rounded to the default context's 28 digits
NINES = "9" * 30
TINY = "0." + "0" * 29 + "1"  # 1E-30 written as a plain decimal: one digit


class Case:
    def __init__(self, mod: types.ModuleType) -> None:
        self.mod = mod
        self.bad: list[str] = []

    def eq(self, label: str, got: object, want: object) -> None:
        if got != want:
            self.bad.append(f"{label}: got {got!r}, expected {want!r}")

    def refuses(self, label: str, call: Callable[[], object], says: str | None = None) -> None:
        try:
            call()
        except self.mod.ForecastError as exc:
            if says is not None and says != str(exc):
                self.bad.append(f"{label}: refused with {str(exc)!r}, expected exactly {says!r}")
            return
        except Exception as exc:
            self.bad.append(f"{label}: raised {type(exc).__name__}: {exc}")
            return
        self.bad.append(f"{label}: was accepted")


def common(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "cycle_id": "cy-1",
        "arm_id": "L1",
        "agent_version_hash": HASH_A,
        "protocol_hash": HASH_B,
        "contract": "D",
        "asset_id": "tk-btc",
        "symbol": "BTC",
        "quote_class": "USDT",
        "instrument_kind": "spot",
        "oracle_pair": "A",
        "oracle_primary": "binance-spot/BTCUSDT",
        "oracle_second": "okx/BTC-USDT",
        "as_of_ms": AS_OF,
        "horizon_ms": 4 * H,
        "invalidation": ("close below 76000 on the 1h candle",),
        "evidence": (("ev-1", HASH_C), ("ev-2", HASH_D)),
        "observation_set_hash": HASH_C,
        "interpretation_visible": False,
    }
    values.update(over)
    return mod.Common(**values)


def directional(mod: types.ModuleType, p: str = "0.62", **over: Any) -> Any:
    return mod.DirectionalForecast(common(mod, **over), D(p))


def barrier(mod: types.ModuleType, **over: Any) -> Any:
    values: dict[str, Any] = {
        "side": mod.Side.LONG,
        "atr": D("123.456"),
        "k_target": D("1.5"),
        "k_stop": D("1.5"),
        "p_target": D("0.4"),
        "p_stop": D("0.35"),
        "p_timeout": D("0.25"),
    }
    values.update(over)
    return mod.BarrierForecast(common(mod, contract="B"), **values)


def check_basics(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    c.eq("record type", (mod.TYPE, mod.TYPE_VERSION, mod.CANDLE_INTERVAL), ("forecast", 1, "1m"))
    c.eq("contract versions", mod.CONTRACT_VERSIONS, {"D": "D/1", "B": "B/1"})
    c.eq("kinds and pairs", (mod.INSTRUMENT_KINDS, mod.ORACLE_PAIRS), (("spot", "perpetual", "future"), ("A", "B")))
    c.eq("limits", (mod.MAX_DIGITS, mod.MAX_TEXT, mod.MAX_ITEMS), (30, 500, 64))
    c.eq(
        "levels",
        [(level.value, level.rank, level.independent) for level in mod.Level],
        [("C0", 0, False), ("C1", 1, False), ("C2", 2, False), ("C3", 3, True), ("C4", 4, True)],
    )
    for cls in (mod.Common, mod.DirectionalForecast, mod.BarrierForecast):
        c.eq(f"{cls.__name__} is frozen", cls.__dataclass_params__.frozen, True)
    key = {"cycle": "cy-1", "arm": "L1", "asset": "tk-btc", "contract": "D", "horizon_ms": 4 * H}
    c.eq("the id is the natural id of the slot", mod.forecast_id("cy-1", "L1", "tk-btc", "D", 4 * H), natural_id("fc", key))
    c.eq("a forecast has the id of its slot", directional(mod).common.forecast_id, mod.forecast_id("cy-1", "L1", "tk-btc", "D", 4 * H))
    base = mod.forecast_id("cy-1", "L1", "tk-btc", "D", 4 * H)
    others = {
        mod.forecast_id("cy-2", "L1", "tk-btc", "D", 4 * H),
        mod.forecast_id("cy-1", "L2", "tk-btc", "D", 4 * H),
        mod.forecast_id("cy-1", "L1", "tk-eth", "D", 4 * H),
        mod.forecast_id("cy-1", "L1", "tk-btc", "B", 4 * H),
        mod.forecast_id("cy-1", "L1", "tk-btc", "D", 24 * H),
    }
    c.eq("every part of the key matters", len(others | {base}), 6)
    c.eq(
        "the id does not depend on the probability", directional(mod, "0.1").common.forecast_id, directional(mod, "0.9").common.forecast_id
    )
    for text, want in (("0", D(0)), ("0.0", D("0.0")), ("10.25", D("10.25")), ("0.50", D("0.50"))):
        c.eq(f"read {text}", mod.read_decimal(text, "x"), want)
    for text in ("", "-1", "+1", "1e3", "1E3", "1.", ".5", "01", " 1", "1 ", "٣", "1,5", "0x1", "NaN", "Infinity", "1_0"):
        c.refuses(f"read {text!r}", lambda t=text: mod.read_decimal(t, "x"), "x is a plain decimal string")
    for value in (0.5, 1, None, D("0.5")):
        c.refuses(f"read a non-string {value!r}", lambda v=value: mod.read_decimal(v, "x"), "x is a plain decimal string")
    c.eq(
        "decimal text keeps its digits",
        (mod.decimal_text(D("0.50")), mod.decimal_text(D("12")), mod.decimal_text(D("0.0000005"))),
        ("0.50", "12", "0.0000005"),
    )
    c.eq("offsets are exact", mod.barrier_offset(D("1.5"), D("123.456")), D("185.184"))
    c.eq(
        "shortest text",
        [mod.shortest_text(D(x)) for x in ("185.1840", "100", "1E+2", "0", "0.000", "0.50", "12", "1000.0100")],
        ["185.184", "100", "100", "0", "0", "0.5", "12", "1000.01"],
    )
    c.eq("offsets of thirty digits are exact", mod.barrier_offset(D("9" * 30), D("9" * 30)), D(int("9" * 30) ** 2))
    c.eq("an offset of thirty-one digits is exact", mod.barrier_offset(D("1.5"), D(ATR30)), D(OFFSET31))
    c.eq("and its shortest text has every digit", mod.shortest_text(D(OFFSET31)), OFFSET31)
    c.eq("so does that of an offset of sixty digits", mod.shortest_text(D(int(NINES) ** 2)), str(int(NINES) ** 2))
    return c.bad


def check_construction(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    bad_values: list[tuple[str, dict[str, Any], str]] = [
        ("an empty cycle", {"cycle_id": ""}, "the cycle id is a non-empty string of at most 500 characters"),
        ("an empty arm", {"arm_id": ""}, "the arm id is a non-empty string of at most 500 characters"),
        ("an empty asset", {"asset_id": ""}, "the asset id is a non-empty string of at most 500 characters"),
        ("an empty symbol", {"symbol": ""}, "the symbol is a non-empty string of at most 500 characters"),
        ("an empty quote class", {"quote_class": ""}, "the quote class is a non-empty string of at most 500 characters"),
        ("an empty primary oracle", {"oracle_primary": ""}, "the primary oracle is a non-empty string of at most 500 characters"),
        ("an empty second oracle", {"oracle_second": ""}, "the second oracle is a non-empty string of at most 500 characters"),
        ("a number as text", {"cycle_id": 7}, "the cycle id is a non-empty string of at most 500 characters"),
        ("text that is too long", {"symbol": "x" * 501}, "the symbol is a non-empty string of at most 500 characters"),
        ("text that is not canonical", {"symbol": "é"}, "the symbol is not valid canonical text: string is not NFC"),
        ("an upper-case hash", {"agent_version_hash": "A" * 64}, "the agent version hash is 64 lower-case hexadecimal characters"),
        ("a short hash", {"protocol_hash": "a" * 63}, "the protocol hash is 64 lower-case hexadecimal characters"),
        ("a long hash", {"observation_set_hash": "a" * 65}, "the observation set hash is 64 lower-case hexadecimal characters"),
        ("a contract that is neither", {"contract": "P"}, "a forecast is contract D or B"),
        ("an unknown instrument", {"instrument_kind": "option"}, "an instrument is spot, perpetual or future"),
        ("an unknown pair", {"oracle_pair": "C"}, "an oracle pair is A or B"),
        (
            "a negative evidence time",
            {"as_of_ms": -1},
            "the evidence time is a whole number of milliseconds up to the end of the year 9999",
        ),
        (
            "a boolean evidence time",
            {"as_of_ms": True},
            "the evidence time is a whole number of milliseconds up to the end of the year 9999",
        ),
        ("a horizon of 59 minutes", {"horizon_ms": 59 * 60_000}, "a horizon is a whole number of minutes, at least one hour"),
        ("a horizon that is not whole minutes", {"horizon_ms": H + 1}, "a horizon is a whole number of minutes, at least one hour"),
        ("a visible flag that is a number", {"interpretation_visible": 1}, "interpretation_visible is true or false"),
        ("no invalidation", {"invalidation": ()}, "a forecast has at least one invalidation condition"),
        ("too many conditions", {"invalidation": ("c",) * 65}, "a forecast has at least one invalidation condition"),
        ("an empty condition", {"invalidation": ("ok", "")}, "an invalidation condition is a non-empty string of at most 500 characters"),
        ("a repeated evidence id", {"evidence": (("e", HASH_A), ("e", HASH_B))}, "evidence is a list of distinct ids"),
        ("too much evidence", {"evidence": tuple((f"e{i}", HASH_A) for i in range(65))}, "evidence is a list of distinct ids"),
        ("an empty evidence id", {"evidence": (("", HASH_A),)}, "an evidence id is a non-empty string of at most 500 characters"),
        ("a bad evidence hash", {"evidence": (("e", "xyz"),)}, "an evidence hash is 64 lower-case hexadecimal characters"),
    ]
    for label, over, says in bad_values:
        c.refuses(label, lambda o=over: common(mod, **o), says)
    c.eq("no evidence is allowed", common(mod, evidence=()).evidence, ())
    c.eq(
        "sixty-four conditions and items are allowed",
        (
            len(common(mod, invalidation=("c",) * 64).invalidation),
            len(common(mod, evidence=tuple((f"e{i}", HASH_A) for i in range(64))).evidence),
        ),
        (64, 64),
    )
    c.eq("a horizon of exactly one hour", common(mod, horizon_ms=H).horizon_ms, H)
    c.eq("an evidence time of zero", common(mod, as_of_ms=0).as_of_ms, 0)
    c.eq("a text of exactly 500 characters", len(common(mod, symbol="x" * 500).symbol), 500)
    c.refuses(
        "a directional forecast on a barrier common",
        lambda: mod.DirectionalForecast(common(mod, contract="B"), D("0.5")),
        "a directional forecast is contract D",
    )
    c.refuses(
        "a barrier forecast on a directional common",
        lambda: mod.BarrierForecast(common(mod), mod.Side.LONG, D(1), D(1), D(1), D("0.5"), D("0.25"), D("0.25")),
        "a barrier forecast is contract B",
    )
    for label, p, says in (
        ("a probability above one", D("1.01"), "the probability is a probability from 0 to 1"),
        ("a negative probability", D("-0.1"), "the probability is a decimal of zero or more with at most 30 digits"),
        ("a float probability", 0.5, "the probability is a decimal of zero or more with at most 30 digits"),
        ("a nan probability", D("NaN"), "the probability is a decimal of zero or more with at most 30 digits"),
        ("an infinite probability", D("Infinity"), "the probability is a decimal of zero or more with at most 30 digits"),
        ("a probability of thirty-one digits", D("0." + "1" * 31), "the probability is a decimal of zero or more with at most 30 digits"),
    ):
        c.refuses(label, lambda v=p: mod.DirectionalForecast(common(mod), v), says)
    c.eq(
        "probabilities of zero and one are allowed",
        (mod.DirectionalForecast(common(mod), D(0)).p, mod.DirectionalForecast(common(mod), D(1)).p),
        (D(0), D(1)),
    )
    c.eq("a probability of thirty digits", mod.DirectionalForecast(common(mod), D("0." + "1" * 30)).p, D("0." + "1" * 30))
    for label, over, says in (
        ("a zero ATR", {"atr": D(0)}, "the ATR is positive"),
        ("a zero target multiplier", {"k_target": D(0)}, "the target multiplier is positive"),
        ("a zero stop multiplier", {"k_stop": D(0)}, "the stop multiplier is positive"),
        ("a negative ATR", {"atr": D("-1")}, "the ATR is a decimal of zero or more with at most 30 digits"),
        ("probabilities that do not sum", {"p_timeout": D("0.3")}, "the three probabilities sum to exactly one"),
        ("probabilities that sum to less", {"p_timeout": D("0.2")}, "the three probabilities sum to exactly one"),
        (
            "a probability above one",
            {"p_target": D("1.5"), "p_stop": D("-0.25"), "p_timeout": D("-0.25")},
            "p_target is a probability from 0 to 1",
        ),
        ("a side that is text", {"side": "LONG"}, "a barrier forecast has a side"),
    ):
        c.refuses(label, lambda o=over: barrier(mod, **o), says)
    c.refuses(
        "a negative stop probability",
        lambda: barrier(mod, p_stop=D("-0.1"), p_timeout=D("0.7")),
        "p_stop is a decimal of zero or more with at most 30 digits",
    )
    c.refuses(
        "a timeout probability above one",
        lambda: barrier(mod, p_target=D(0), p_stop=D(0), p_timeout=D("1.5")),
        "p_timeout is a probability from 0 to 1",
    )
    c.refuses(
        "a stop probability above one",
        lambda: barrier(mod, p_target=D(0), p_stop=D("1.5"), p_timeout=D(0)),
        "p_stop is a probability from 0 to 1",
    )
    c.eq("a horizon of 366 days", common(mod, horizon_ms=366 * 86_400_000).horizon_ms, 366 * 86_400_000)
    c.refuses("a horizon a minute longer", lambda: common(mod, horizon_ms=366 * 86_400_000 + 60_000), "a horizon is at most 366 days")
    c.eq("the last millisecond of the year 9999", common(mod, as_of_ms=253_402_300_799_999).as_of_ms, 253_402_300_799_999)
    c.refuses(
        "one millisecond more",
        lambda: common(mod, as_of_ms=253_402_300_800_000),
        "the evidence time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a creation time past the last millisecond",
        lambda: mod.to_record(directional(mod), "urn:x", 253_402_300_800_000),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.eq("a short barrier", barrier(mod, side=mod.Side.SHORT).side, mod.Side.SHORT)
    c.eq("probabilities of a barrier", barrier(mod).probabilities, (D("0.4"), D("0.35"), D("0.25")))
    c.eq("a certain barrier", barrier(mod, p_target=D(1), p_stop=D(0), p_timeout=D(0)).probabilities, (D(1), D(0), D(0)))
    b = barrier(mod)
    c.eq("offsets", (b.target_offset, b.stop_offset), (D("185.184"), D("185.184")))
    b2 = barrier(mod, k_target=D("2"), k_stop=D("0.5"))
    c.eq("different multipliers", (b2.target_offset, b2.stop_offset), (D("246.912"), D("61.728")))
    return c.bad


def check_bodies_and_records(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    d = directional(mod)
    want_common = {
        "cycle_id": "cy-1", "arm_id": "L1", "agent_version_hash": HASH_A, "protocol_hash": HASH_B, "contract": "D", "contract_version": "D/1",
        "asset_id": "tk-btc", "symbol": "BTC", "quote_class": "USDT", "instrument_kind": "spot", "oracle_pair": "A",
        "oracle_primary": "binance-spot/BTCUSDT", "oracle_second": "okx/BTC-USDT", "candle_interval": "1m", "as_of": "2026-10-29T21:56:40.000Z",
        "horizon_ms": 4 * H, "invalidation": ["close below 76000 on the 1h candle"], "evidence_ids": ["ev-1", "ev-2"], "observation_set_hash": HASH_C,
        "interpretation_visible": False,
    }  # fmt: skip
    c.eq("a directional body", d.to_body(), {**want_common, "p": "0.62"})
    b = barrier(mod)
    want_b = {**want_common, "contract": "B", "contract_version": "B/1", "side": "LONG", "atr": "123.456", "k_target": "1.5", "k_stop": "1.5",
              "target_offset": "185.184", "stop_offset": "185.184", "p_target": "0.4", "p_stop": "0.35", "p_timeout": "0.25"}  # fmt: skip
    c.eq("a barrier body", b.to_body(), want_b)
    long_b = barrier(mod, atr=D(ATR30))
    c.eq(
        "the offsets of a body hold all thirty-one digits",
        (long_b.to_body()["target_offset"], long_b.to_body()["stop_offset"]),
        (OFFSET31, OFFSET31),
    )
    widest = barrier(mod, atr=D(NINES), k_stop=D(NINES))
    c.eq(
        "and all sixty of the widest product",
        (widest.to_body()["target_offset"], widest.to_body()["stop_offset"]),
        ("1499999999999999999999999999998.5", str(int(NINES) ** 2)),
    )
    record = mod.to_record(d, "urn:hoshitori:agent:L1:v1", AS_OF + 5_000)
    c.eq("the envelope", {k: record[k] for k in ("spec", "type", "type_version", "id", "source", "created_at", "refs", "privacy")},
         {"spec": "hoshitori.record/1", "type": "forecast", "type_version": 1, "id": d.common.forecast_id, "source": "urn:hoshitori:agent:L1:v1",
          "created_at": "2026-10-29T21:56:45.000Z", "refs": [{"id": "ev-1", "sha256": HASH_C}, {"id": "ev-2", "sha256": HASH_D}], "privacy": "public"})  # fmt: skip
    c.eq("the body of the record", record["body"], d.to_body())
    c.eq("a record made at the evidence time", mod.to_record(d, "urn:x", AS_OF)["created_at"], "2026-10-29T21:56:40.000Z")
    c.refuses(
        "a record made before the evidence time",
        lambda: mod.to_record(d, "urn:x", AS_OF - 1),
        "a forecast is created at or after its evidence time",
    )
    c.refuses(
        "a negative creation time",
        lambda: mod.to_record(d, "urn:x", -1),
        "the creation time is a whole number of milliseconds up to the end of the year 9999",
    )
    c.refuses(
        "a source that is not a urn",
        lambda: mod.to_record(d, "agent", AS_OF),
        "the record does not satisfy the envelope: source must be an ASCII urn",
    )
    for forecast in (
        d,
        b,
        barrier(mod, side=mod.Side.SHORT, atr=D("0.0000005"), k_target=D("0.5"), k_stop=D("10")),
        directional(mod, "0", evidence=(), interpretation_visible=True),
        long_b,
        widest,
    ):
        rec = mod.to_record(forecast, "urn:hoshitori:agent:test", AS_OF + 1)
        again = mod.from_record(canonical.parse_record(canonical.dumps(rec)))
        c.eq("a record reads back as the same forecast", again, forecast)
        c.eq(
            "and writes back the same bytes",
            canonical.dumps(mod.to_record(again, "urn:hoshitori:agent:test", AS_OF + 1)),
            canonical.dumps(rec),
        )
    committed = {**record, "committed_at": "2026-10-29T21:56:50.000Z"}
    c.eq("the ledger's copy, with its commit time, is read", mod.from_record(committed), d)
    return c.bad


def check_reading_is_strict(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    d_record = mod.to_record(directional(mod), "urn:x", AS_OF)
    b_record = mod.to_record(barrier(mod), "urn:x", AS_OF)
    long_record = mod.to_record(barrier(mod, atr=D(ATR30)), "urn:x", AS_OF)

    def changed(record: dict[str, Any], path: tuple[str, ...], value: Any) -> dict[str, Any]:
        out = copy.deepcopy(record)
        node = out
        for key in path[:-1]:
            node = node[key]
        if value is _GONE:
            del node[path[-1]]
        else:
            node[path[-1]] = value
        return out

    c.eq("the originals read", (mod.from_record(d_record) is not None, mod.from_record(b_record) is not None), (True, True))
    cases: list[tuple[str, dict[str, Any], str]] = [
        ("an unknown envelope member", changed(d_record, ("extra",), 1), "the envelope: unknown envelope members: ['extra']"),
        ("a missing envelope member", changed(d_record, ("source",), _GONE), "the envelope: missing envelope members: ['source']"),
        ("another type", changed(d_record, ("type",), "decision"), "not a forecast record of a version this reader knows"),
        ("another version", changed(d_record, ("type_version",), 2), "not a forecast record of a version this reader knows"),
        ("a body that is not an object", changed(d_record, ("body",), []), "the envelope: body must be an object"),
        ("a contract that is unhashable", changed(d_record, ("body", "contract"), []), "a forecast is contract D or B"),
        ("a contract that is unknown", changed(d_record, ("body", "contract"), "P"), "a forecast is contract D or B"),
        ("an extra body member", changed(d_record, ("body", "extra"), 1), "the body has the members ['extra'] wrong"),
        ("a missing body member", changed(d_record, ("body", "symbol"), _GONE), "the body has the members ['symbol'] wrong"),
        ("a barrier member in a directional body", changed(d_record, ("body", "side"), "LONG"), "the body has the members ['side'] wrong"),
        ("a missing probability", changed(d_record, ("body", "p"), _GONE), "the body has the members ['p'] wrong"),
        ("a missing barrier offset", changed(b_record, ("body", "stop_offset"), _GONE), "the body has the members ['stop_offset'] wrong"),
        ("a directional member in a barrier body", changed(b_record, ("body", "p"), "0.5"), "the body has the members ['p'] wrong"),
        (
            "a contract version of another contract",
            changed(d_record, ("body", "contract_version"), "B/1"),
            "the contract version or the candle interval is not one this reader knows",
        ),
        (
            "a contract version that is unknown",
            changed(d_record, ("body", "contract_version"), "D/2"),
            "the contract version or the candle interval is not one this reader knows",
        ),
        (
            "another candle interval",
            changed(d_record, ("body", "candle_interval"), "5m"),
            "the contract version or the candle interval is not one this reader knows",
        ),
        ("a probability that is a number", changed(d_record, ("body", "p"), 1), "p is a plain decimal string"),
        ("a probability with an exponent", changed(d_record, ("body", "p"), "5E-1"), "p is a plain decimal string"),
        ("a probability with a leading zero", changed(d_record, ("body", "p"), "00.5"), "p is a plain decimal string"),
        ("a probability with a sign", changed(d_record, ("body", "p"), "+0.5"), "p is a plain decimal string"),
        ("a probability above one", changed(d_record, ("body", "p"), "1.5"), "the probability is a probability from 0 to 1"),
        (
            "an evidence time without milliseconds",
            changed(d_record, ("body", "as_of"), "2026-10-29T10:56:40Z"),
            "as_of is written as UTC with milliseconds",
        ),
        (
            "an evidence time with an offset",
            changed(d_record, ("body", "as_of"), "2026-10-29T11:56:40.000+01:00"),
            "as_of is written as UTC with milliseconds",
        ),
        (
            "an evidence time in lower case",
            changed(d_record, ("body", "as_of"), "2026-10-29t10:56:40.000z"),
            "as_of: not an RFC 3339 timestamp with an explicit offset: '2026-10-29t10:56:40.000z'",
        ),
        ("an evidence time that is a number", changed(d_record, ("body", "as_of"), 5), "as_of is an RFC 3339 string"),
        (
            "an evidence time that is nonsense",
            changed(d_record, ("body", "as_of"), "yesterday"),
            "as_of: not an RFC 3339 timestamp with an explicit offset: 'yesterday'",
        ),
        ("a horizon that is a string", changed(d_record, ("body", "horizon_ms"), "4"), "horizon_ms is a whole number"),
        ("a horizon that is a boolean", changed(d_record, ("body", "horizon_ms"), True), "horizon_ms is a whole number"),
        ("a flag that is a number", changed(d_record, ("body", "interpretation_visible"), 0), "interpretation_visible is true or false"),
        ("a text member that is a number", changed(d_record, ("body", "symbol"), 5), "symbol is a string"),
        ("invalidation that is a string", changed(d_record, ("body", "invalidation"), "x"), "invalidation is a list of strings"),
        ("invalidation with a number", changed(d_record, ("body", "invalidation"), ["x", 1]), "invalidation is a list of strings"),
        ("evidence ids that are a string", changed(d_record, ("body", "evidence_ids"), "ev-1"), "evidence_ids is a list of strings"),
        (
            "evidence ids that differ from the references",
            changed(d_record, ("body", "evidence_ids"), ["ev-2", "ev-1"]),
            "the references and the evidence ids differ",
        ),
        ("references that are not a list", changed(d_record, ("refs",), {}), "the envelope: refs must be a list"),
        (
            "a reference that is not an object",
            changed(d_record, ("refs",), ["ev-1"]),
            "the envelope: each ref is {id, sha256 (64 lower-case hex)}",
        ),
        (
            "a reference without a hash",
            changed(d_record, ("refs",), [{"id": "ev-1"}, {"id": "ev-2"}]),
            "the envelope: each ref is {id, sha256 (64 lower-case hex)}",
        ),
        (
            "a reference with a number as id",
            changed(d_record, ("refs",), [{"id": 1, "sha256": HASH_C}, {"id": "ev-2", "sha256": HASH_D}]),
            "the envelope: each ref is {id, sha256 (64 lower-case hex)}",
        ),
        ("an id of another slot", changed(d_record, ("id",), "fcAAAAAAAAAAAAAAAAAAAAAAAAAA"), "the id is not the id of this slot"),
        ("a side that is unknown", changed(b_record, ("body", "side"), "UP"), "a side is LONG or SHORT"),
        ("a side that is a number", changed(b_record, ("body", "side"), 1), "side is a string"),
        (
            "an offset that disagrees",
            changed(b_record, ("body", "target_offset"), "185.185"),
            "target_offset disagrees with the multiplier and the ATR it comes from",
        ),
        (
            "a stop offset that disagrees",
            changed(b_record, ("body", "stop_offset"), "185"),
            "stop_offset disagrees with the multiplier and the ATR it comes from",
        ),
        (
            "an offset written with another scale",
            changed(b_record, ("body", "target_offset"), "185.1840"),
            "target_offset disagrees with the multiplier and the ATR it comes from",
        ),
        ("probabilities that do not sum", changed(b_record, ("body", "p_timeout"), "0.26"), "the three probabilities sum to exactly one"),
        ("an ATR with an exponent", changed(b_record, ("body", "atr"), "1.23456E+2"), "atr is a plain decimal string"),
        ("a target multiplier with an exponent", changed(b_record, ("body", "k_target"), "1.5E0"), "k_target is a plain decimal string"),
        ("a stop multiplier with an exponent", changed(b_record, ("body", "k_stop"), "1.5E0"), "k_stop is a plain decimal string"),
        ("a target probability with an exponent", changed(b_record, ("body", "p_target"), "4E-1"), "p_target is a plain decimal string"),
        ("a stop probability with an exponent", changed(b_record, ("body", "p_stop"), "35E-2"), "p_stop is a plain decimal string"),
        (
            "a timeout probability with an exponent",
            changed(b_record, ("body", "p_timeout"), "25E-2"),
            "p_timeout is a plain decimal string",
        ),
        (
            "an offset of thirty-one digits rounded to twenty-eight",
            changed(long_record, ("body", "target_offset"), ROUNDED28),
            "target_offset disagrees with the multiplier and the ATR it comes from",
        ),
        (
            "a stop offset of thirty-one digits rounded to twenty-eight",
            changed(long_record, ("body", "stop_offset"), ROUNDED28),
            "stop_offset disagrees with the multiplier and the ATR it comes from",
        ),
        (
            "probabilities that sum to one and 1E-30",
            changed(changed(changed(b_record, ("body", "p_target"), "0.5"), ("body", "p_stop"), "0.5"), ("body", "p_timeout"), TINY),
            "the three probabilities sum to exactly one",
        ),
    ]
    for label, record, says in cases:
        c.refuses(label, lambda r=record: mod.from_record(r), says)
    return c.bad


_GONE = object()


def check_settlement_input(mod: types.ModuleType) -> list[str]:
    c = Case(mod)
    origin = AS_OF + 3 * 60_000
    d = directional(mod).settlement_input(origin)
    c.eq(
        "a directional input",
        (type(d).__name__, d.forecast_id, d.origin_ms, d.horizon_ms, d.quote_class),
        ("Directional", directional(mod).common.forecast_id, origin, 4 * H, "USDT"),
    )
    b = barrier(mod, side=mod.Side.SHORT).settlement_input(origin)
    c.eq(
        "a barrier input",
        (type(b).__name__, b.forecast_id, b.origin_ms, b.horizon_ms, b.quote_class, b.side, b.target_offset, b.stop_offset),
        ("Barrier", barrier(mod).common.forecast_id, origin, 4 * H, "USDT", mod.Side.SHORT, D("185.184"), D("185.184")),
    )
    long_b = barrier(mod, atr=D(ATR30))
    c.eq(
        "the offsets settled on are the ones the body carries",
        (long_b.settlement_input(origin).target_offset, D(long_b.to_body()["target_offset"]), D(long_b.to_body()["stop_offset"])),
        (D(OFFSET31), D(OFFSET31), D(OFFSET31)),
    )
    return c.bad


# Probability triples near the edges of the rule "the three probabilities sum to exactly one" (contract section 3).
SUMS: tuple[tuple[str, tuple[Decimal, Decimal, Decimal]], ...] = (
    ("one and 1E-30", (D("0.5"), D("0.5"), D(TINY))),
    ("1.0000001", (D("0.5"), D("0.5"), D("0.0000001"))),
    ("exactly one in seven digits", (D("0.3333334"), D("0.3333333"), D("0.3333333"))),
    ("thirds of thirty digits", (D("0." + "3" * 30),) * 3),
    ("one and 1E-59", (D("0.5"), D("0.5"), D("0." + "0" * 58 + "1"))),
    ("one and 1E-60", (D("0.5"), D("0.5"), D("0." + "0" * 59 + "1"))),
    ("exactly one", (D("0.4"), D("0.35"), D("0.25"))),
    ("a certain outcome", (D(1), D(0), D(0))),
)


def _verdict(call: Callable[[], object], errors: tuple[type[Exception], ...]) -> str:
    try:
        call()
    except errors as exc:
        return f"refused: {exc}"
    return "accepted"


def check_probability_sum(mod: types.ModuleType) -> list[str]:
    """The sum is checked by the rule scoring checks it by (sixty digits, half to even), so a forecast that is committed can be scored."""
    c = Case(mod)
    says = "the three probabilities sum to exactly one"
    for label in ("one and 1E-30", "1.0000001", "thirds of thirty digits", "one and 1E-59"):
        p = dict(SUMS)[label]
        c.refuses(f"probabilities that sum to {label}", lambda v=p: barrier(mod, p_target=v[0], p_stop=v[1], p_timeout=v[2]), says)
    c.eq(
        "probabilities of seven digits that sum to exactly one",
        barrier(mod, p_target=D("0.3333334"), p_stop=D("0.3333333"), p_timeout=D("0.3333333")).probabilities,
        (D("0.3333334"), D("0.3333333"), D("0.3333333")),
    )
    for label, p in SUMS:
        made = _verdict(lambda v=p: barrier(mod, p_target=v[0], p_stop=v[1], p_timeout=v[2]), (mod.ForecastError,))
        scored = _verdict(lambda v=p: scoring.brier3(v, "TARGET"), (scoring.ScoringError,))
        c.eq(f"probabilities that sum to {label}: accepted exactly when scoring can score them", made, scored)
    return c.bad


def _caller_contexts() -> dict[str, Context]:
    """Decimal contexts a caller may be in when it writes or reads a forecast: narrower than the default, rounding another way, or
    trapping any result that is not exact."""
    trapping = Context()
    trapping.traps[Inexact] = True
    return {
        "6 digits": Context(prec=6),
        "9 digits": Context(prec=9),
        "ROUND_FLOOR": Context(rounding=ROUND_FLOOR),
        "ROUND_CEILING": Context(rounding=ROUND_CEILING),
        "a context that traps Inexact": trapping,
    }


def _happens(call: Callable[[], object]) -> str:
    try:
        return f"gives {call()}"
    except Exception as exc:  # what happens, whatever it is, is the result being compared
        return f"raises {type(exc).__name__}: {exc}"


def _written(mod: types.ModuleType, made: Any) -> str:
    record = mod.to_record(made, "urn:x", AS_OF)
    body = record["body"]
    offsets = f"offsets {body['target_offset']} and {body['stop_offset']}, " if "target_offset" in body else ""
    return f"{offsets}sha256 {hashlib.sha256(canonical.dumps(record)).hexdigest()[:16]}"


def _read(mod: types.ModuleType, record: dict[str, Any]) -> str:
    made = mod.from_record(copy.deepcopy(record))
    if isinstance(made, mod.DirectionalForecast):
        return f"p {format(made.p, 'f')}"
    numbers = (made.target_offset, made.stop_offset, *made.probabilities)
    return "offsets {} and {}, probabilities {}, {}, {}".format(*(format(n, "f") for n in numbers))


def _what_happens(mod: types.ModuleType, records: dict[str, dict[str, Any]]) -> list[tuple[str, str]]:
    """What must not depend on the caller's decimal context: the bytes of records, what reading them gives, and every refusal."""
    out = [
        ("the record of a nine-digit offset", _happens(lambda: _written(mod, barrier(mod, atr=D("2.3456789"))))),
        ("the record of a thirty-one-digit offset", _happens(lambda: _written(mod, barrier(mod, atr=D(ATR30))))),
        ("the record of a sixty-digit offset", _happens(lambda: _written(mod, barrier(mod, atr=D(NINES), k_stop=D(NINES))))),
        ("the record of a directional forecast", _happens(lambda: _written(mod, directional(mod, "0." + "1" * 30)))),
        ("an offset of thirty-one digits", _happens(lambda: mod.barrier_offset(D("1.5"), D(ATR30)))),
        ("the shortest text of 3.51851835", _happens(lambda: mod.shortest_text(D("3.51851835")))),
        ("the shortest text of an offset of thirty-one digits", _happens(lambda: mod.shortest_text(D(OFFSET31)))),
    ]
    out += [(f"reading {name}", _happens(lambda r=record: _read(mod, r))) for name, record in records.items()]
    out += [
        (f"probabilities that sum to {label}", _happens(lambda v=p: barrier(mod, p_target=v[0], p_stop=v[1], p_timeout=v[2]).probabilities))
        for label, p in SUMS
    ]
    return out


def check_caller_context(mod: types.ModuleType) -> list[str]:
    """A record, what reading it gives and every refusal are the same whatever decimal context the caller is in: each result in a
    narrow context, in one that rounds another way and in one that traps an inexact result equals the result in the default context."""
    c = Case(mod)
    with localcontext(Context()):
        nine = mod.to_record(barrier(mod, atr=D("2.3456789")), "urn:x", AS_OF)
        wide = mod.to_record(barrier(mod, atr=D(ATR30)), "urn:x", AS_OF)
        directional_record = mod.to_record(directional(mod, "0." + "1" * 30), "urn:x", AS_OF)
        sums_past_one = copy.deepcopy(nine)
        sums_past_one["body"].update(p_target="0.5", p_stop="0.5", p_timeout=TINY)
        sevenths = copy.deepcopy(nine)
        sevenths["body"].update(p_target="0.3333334", p_stop="0.3333333", p_timeout="0.3333333")
        records = {
            "a record of a nine-digit offset": nine,
            "a record of a thirty-one-digit offset": wide,
            "a record of a directional forecast": directional_record,
            "a record whose offsets are rounded to six digits": _with_offsets(nine, "3.51852"),
            "a record whose offsets are rounded to twenty-eight digits": _with_offsets(wide, ROUNDED28),
            "a record whose probabilities sum to one and 1E-30": sums_past_one,
            "a record whose probabilities of seven digits sum to exactly one": sevenths,
        }
        base = _what_happens(mod, records)
    c.eq("a nine-digit offset is written whole", nine["body"]["target_offset"], "3.51851835")
    c.eq(
        "the records that must be refused are refused",
        [result for name, result in base if "rounded" in name or "1E-30" in name],
        ["raises ForecastError: target_offset disagrees with the multiplier and the ATR it comes from"] * 2
        + ["raises ForecastError: the three probabilities sum to exactly one"] * 2,
    )
    for label, ctx in _caller_contexts().items():
        with localcontext(ctx):
            got = _what_happens(mod, records)
        for (case, want), (_, have) in zip(base, got, strict=True):
            c.eq(f"{case}, in a caller context of {label}", have, want)
    return c.bad


def _with_offsets(record: dict[str, Any], text: str) -> dict[str, Any]:
    out = copy.deepcopy(record)
    out["body"].update(target_offset=text, stop_offset=text)
    return out


def check_no_negative_zero(mod: types.ModuleType) -> list[str]:
    """A body's decimals are plain decimal strings with no sign (``read_decimal``), so a negative zero, which ``decimal_text`` would write
    as "-0", is refused where a forecast is made, and every probability a forecast can be made with is written as a body its reader takes."""
    c = Case(mod)
    tail = "is a negative zero, and a body's decimals have no sign"
    for text in ("-0", "-0.0", "-0.000000", "-0E-3", "-0E+2"):
        c.refuses(f"a probability of {text}", lambda text=text: mod.DirectionalForecast(common(mod), D(text)), f"the probability {tail}")
    for name, over in (
        ("p_target", {"p_target": D("-0"), "p_stop": D("0.75"), "p_timeout": D("0.25")}),
        ("p_stop", {"p_target": D("0.75"), "p_stop": D("-0.0"), "p_timeout": D("0.25")}),
        ("p_timeout", {"p_target": D("0.75"), "p_stop": D("0.25"), "p_timeout": D("-0")}),
    ):
        c.refuses(f"a barrier's {name} of -0", lambda over=over: barrier(mod, **over), f"{name} {tail}")
    c.refuses("an ATR of -0", lambda: barrier(mod, atr=D("-0")), f"the ATR {tail}")
    c.refuses("a target multiplier of -0", lambda: barrier(mod, k_target=D("-0")), f"the target multiplier {tail}")
    for text in ("0", "0.0", "0.000000", "0E+2", "1", "1.000", "0.62"):
        try:
            made = mod.DirectionalForecast(common(mod), D(text))
            back = mod.from_record(canonical.parse_record(canonical.dumps(mod.to_record(made, "urn:hoshitori:test", AS_OF))))
            c.eq(
                f"a probability of {text} is written as a body its reader takes",
                (back.p, made.to_body()["p"]),
                (D(text), mod.decimal_text(D(text))),
            )
        except Exception as exc:
            c.bad.append(f"a probability of {text}: {type(exc).__name__}: {exc}")
    zero = mod.BarrierForecast(common(mod, contract="B"), mod.Side.LONG, D("1"), D("1"), D("1"), D("0.0"), D("0"), D("1"))
    try:
        mod.from_record(canonical.parse_record(canonical.dumps(mod.to_record(zero, "urn:hoshitori:test", AS_OF))))
    except Exception as exc:
        c.bad.append(f"a barrier with zero probabilities does not read back: {type(exc).__name__}: {exc}")
    return c.bad


def failures(mod: types.ModuleType) -> list[str]:
    bad: list[str] = []
    for check in (
        check_basics,
        check_construction,
        check_bodies_and_records,
        check_reading_is_strict,
        check_settlement_input,
        check_probability_sum,
        check_caller_context,
        check_no_negative_zero,
    ):
        try:
            bad += [f"{check.__name__}: {message}" for message in check(mod)]
        except TimeoutError:
            raise
        except Exception as exc:
            bad.append(f"{check.__name__}: raised {type(exc).__name__}: {exc}")
    return bad
