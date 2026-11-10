"""A made-up venue on the shared adapter, so the shared rules can be tested (and mutated) without any real venue's quirks.

Wire format: ``GET /time`` -> ``{"t": <ms>}``; ``GET /candles?...`` -> ``[[open_ms, o, h, l, c, volume], ...]`` (newest first, like Coinbase);
``GET /instruments`` -> ``{"instruments": [{"id", "base", "quote", "state"}...]}``; an error is ``{"error": "<text>"}`` with any status.
"""

from __future__ import annotations

import re
import types
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from hoshitori import jsonnum


def toy_class(mod: types.ModuleType) -> Any:
    """The toy venue built on the (possibly mutated) ``venue_base`` module ``mod``."""
    base_module = __import__("hoshitori.venue_data", fromlist=["x"])

    class Toy(mod.VenueAdapter):
        venue = "toy"
        intervals = {60: "1m", 300: "5m"}  # noqa: RUF012
        instrument_kinds = ("spot", "perp")
        rate = mod.VenueRate(mod.RateModel.FIXED_WINDOW, 10.0, 1.0)

        def valid_instrument(self, raw_id: str) -> bool:
            return re.fullmatch(r"[A-Z]{3,10}", raw_id) is not None

        def build_request(self, operation: str, args: Mapping[str, Any]) -> Any:
            if operation == "time":
                return mod.VenueRequest("GET", "/time")
            if operation == "candles":
                return mod.VenueRequest(
                    "GET", f"/candles?id={args['instrument']}&i={self.intervals[args['interval_s']]}&from={args['start']}&to={args['end']}"
                )
            if operation == "instruments":
                return mod.VenueRequest("POST", "/instruments", b'{"kind":"' + str(args.get("kind", "all")).encode() + b'"}')
            raise KeyError(operation)

        def parse_time_body(self, body: bytes) -> datetime:
            data = jsonnum.parse(body)
            return datetime.fromtimestamp(base_module.whole_number(data["t"]) / 1000, UTC)

        def parse_candles_body(self, body: bytes, args: Mapping[str, Any]) -> list[Any]:
            rows = jsonnum.parse(body)
            if not isinstance(rows, list):
                raise base_module.VenueDataError("not a list")
            out = []
            step = timedelta(seconds=int(args["interval_s"]))
            for row in rows:
                opened = datetime.fromtimestamp(base_module.whole_number(row[0]) / 1000, UTC)
                prices = [base_module.decimal_text(v) for v in row[1:5]]
                out.append(base_module.Candle(opened, opened + step, *prices, volume=base_module.decimal_text(row[5])))
            return out

        def parse_instruments_body(self, body: bytes, args: Mapping[str, Any]) -> Any:
            data = jsonnum.parse(body)
            rows = data["instruments"]
            items = tuple(
                base_module.Instrument(
                    "toy",
                    r["id"],
                    base_module.InstrumentKind.SPOT,
                    r["base"],
                    r["quote"],
                    base_module.quote_class(r["quote"]),
                    "1",
                    None,
                    None,
                    None,
                    None,
                    base_module.InstrumentStatus.TRADING if r["state"] == "on" else base_module.InstrumentStatus.HALTED,
                )
                for r in rows
            )
            return base_module.InstrumentList("toy", items, ())

        def error_in_body(self, status: int, body: bytes, headers: Mapping[str, str], now: datetime) -> Any:
            try:
                data = jsonnum.parse(body)
            except jsonnum.JsonError:
                return None
            if isinstance(data, dict) and isinstance(data.get("error"), str):
                text = data["error"]
                if text.startswith("slow"):
                    return mod.ConnectorError(mod.ErrorClass.RATE_LIMIT, text, retry_after_s=7.0)
                return mod.ConnectorError(mod.ErrorClass.INVALID_ARGS, text)
            return None

        def cost(self, operation: str, args: Mapping[str, Any]) -> float:
            return 5.0 if operation == "instruments" else 1.0

        def observe_rate(self, headers: Mapping[str, str]) -> Any:
            if "x-toy" in headers:
                return mod.RateObservation(remaining=float(headers["x-toy"]))
            return None

    return Toy
