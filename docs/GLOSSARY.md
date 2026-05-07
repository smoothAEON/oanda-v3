# Symbol And Timeframe Glossary

All aliases are case-insensitive.

## Supported Instruments

These are the only instruments accepted by command validation and the instrument registry:

| Symbol | Class | Pip size |
| --- | --- | --- |
| `XAU_USD` | metal | `0.01` |
| `XAG_USD` | metal | `0.0001` |
| `EUR_USD` | major FX | `0.0001` |
| `GBP_USD` | major FX | `0.0001` |
| `USD_JPY` | major FX | `0.01` |
| `AUD_USD` | major FX | `0.0001` |
| `USD_CAD` | major FX | `0.0001` |
| `USD_CHF` | major FX | `0.0001` |
| `NZD_USD` | major FX | `0.0001` |
| `EUR_GBP` | cross FX | `0.0001` |
| `EUR_JPY` | cross FX | `0.01` |
| `GBP_JPY` | cross FX | `0.01` |

## Direct Symbol Aliases

These aliases normalize to supported instruments and pass validation:

| Alias | Normalized symbol |
| --- | --- |
| `gold` | `XAU_USD` |
| `silver` | `XAG_USD` |

## Normalized But Still Rejected

The parser can normalize these aliases, but command validation rejects them because they are not in the current instrument registry:

| Alias | Normalized symbol | Current result |
| --- | --- | --- |
| `oil` | `WTICO_USD` | rejected |
| `btc` | `BTC_USD` | rejected |
| `eth` | `ETH_USD` | rejected |

## Flexible Symbol Formats

The normalizer accepts common input formats before registry validation:

| You type | Normalized result |
| --- | --- |
| `eurusd` | `EUR_USD` |
| `EUR/USD` | `EUR_USD` |
| `eur-usd` | `EUR_USD` |
| `EUR USD` | `EUR_USD` |
| `GBPJPY` | `GBP_JPY` |

Rule:

- any 6-letter alphabetic pair without separators is split after the first three characters

## Timeframe Aliases

| Alias | Canonical result |
| --- | --- |
| `1m`, `m1` | `M1` |
| `5m`, `m5` | `M5` |
| `15m`, `m15` | `M15` |
| `30m`, `m30` | `M30` |
| `1h`, `h1` | `H1` |
| `4h`, `h4` | `H4` |
| `1d`, `d`, `day`, `daily` | `D` |
| `1w`, `w`, `weekly` | `W` |

Current validation rule:

- `W` is normalized and then rejected as unsupported

## Timeframes By Subsystem

| Subsystem | Accepted timeframes |
| --- | --- |
| command parsing | `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D` |
| scheduled scan snapshots | `M15`, `H1`, `H4`, `D` |
| HTF bundle assembly | `D`, `H4`, `H1` |
| chart rendering | `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D` |
| extractor | `M1`, `M5`, `M15`, `M30`, `H1`, `H4`, `D` |

## Precision Notes

| Family | Examples | Price convention |
| --- | --- | --- |
| standard FX | `EUR_USD`, `GBP_USD`, `AUD_USD` | `0.0001` pip size |
| JPY FX | `USD_JPY`, `EUR_JPY`, `GBP_JPY` | `0.01` pip size |
| gold | `XAU_USD` | `0.01` pip size |
| silver | `XAG_USD` | `0.0001` pip size |

## Current Command Defaults

Current parser defaults from [`bot/parsing.py`](../bot/parsing.py):

- default instrument for parser helpers: `XAU_USD`
- default timeframe for parser helpers: `H1`
- default extractor timeframes: `M15`, `H1`, `H4`, `D`

Command-specific usage still controls whether those defaults are reachable. For example, `/smc` requires a symbol argument even though the parser helper itself has a default instrument.
