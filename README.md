# Binance Futures Trade Scanner V2

## Recommended starting settings
- Liquid contracts in first pass: 60
- Minimum 24h volume: $20M
- Full multi-timeframe analysis: 20
- Show: A+, A, B

## Timeframe hierarchy
- 4H: regime
- 1H: main trend
- 15m: setup
- 5m: closed-candle entry trigger

The scanner intentionally uses the last fully CLOSED 5m candle for its trigger. It gives a trigger about 15 minutes of validity, while also requiring live price to remain close to the entry zone.

## Status meanings
- ENTER WINDOW: setup + closed 5m trigger + live price in entry zone
- READY / WAIT RETEST: setup confirmed, wait for price to return to entry zone
- WAIT 5m CLOSE: no completed 5m entry confirmation yet
- MISSED / DO NOT CHASE / EXPIRED / REJECT: no new entry

## Start on Windows
Double-click START_SCANNER.cmd.

The launcher tries the Windows `py` launcher first, so it works with installations where `py --version` works but `python` is not on PATH.

## Important
A+/A are quality grades, not probabilities. This is decision-support software, not a guarantee of profitable trades. Paper-test and backtest before considering leveraged execution.


## V2.1 diagnostics
V2.1 never silently hides scan exceptions. It always shows:
- Binance API connectivity status
- eligible contract count
- successfully analyzed contracts
- all analyzed rows regardless of selected grade
- per-symbol error diagnostics

If no trading rows appear, open the Errors / diagnostics table and copy the first error.


## V2.2 bug fix
Fixed a pandas naming collision: `Series.hist` is a plotting method, so using `x.hist`
returned a method instead of the numeric MACD histogram column. V2.2 now uses explicit
bracket column access (`x["hist"]`, `x["close"]`, etc.) throughout the signal logic.
