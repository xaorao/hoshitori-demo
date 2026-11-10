# Recorded fixtures

Every `*.json` file here is the unmodified body of a real response from the venue's public market-data
API, captured with `curl` at the UTC time in `CAPTURED_AT.txt`. They are used to test parsing and
indicator maths against real market data without depending on the network. They are labelled `recorded`
in code and docs and are never presented as live data.

| File | Request |
|---|---|
| `binance_SOLUSDT_1d.json`, `binance_SOLUSDT_4h.json`, `binance_BTCUSDT_1h.json` | `GET /api/v3/klines` (limit 300) |
| `coinbase_SOL-USD_1d.json`, `coinbase_SOL-USD_1h.json` | `GET /products/SOL-USD/candles` |
| `kraken_SOLUSD_1d.json`, `kraken_SOLUSD_4h.json` | `GET /0/public/OHLC` |
| `bybit_SOLUSDT_1d.json`, `bybit_SOLUSDT_4h.json` | `GET /v5/market/kline?category=spot` |
| `*_invalid.json` | the same endpoints for a symbol that does not exist (error-shape fixtures) |
