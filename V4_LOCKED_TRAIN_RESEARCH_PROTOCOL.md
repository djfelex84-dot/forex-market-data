# V4 locked TRAIN research protocol

Protocol freeze date: 2026-08-29 UTC

This protocol is frozen before the complete Dukascopy TRAIN dataset is
available and before any strategy result from that dataset is inspected.

## Dataset gate

- Symbols: EUR/USD and GBP/USD only.
- Signal timeframe: M30.
- Context: 2020-12-01 through 2020-12-31 UTC.
- TRAIN: 2021-01-01 through 2024-12-31 UTC.
- VALIDATION 2025 remains physically locked and unread.
- The research runner refuses an incomplete builder manifest.
- The final SQLite SHA256, integrity check, exact symbol/day membership,
  normalized row counts, raw artifact hashes, and the 2025 boundary must all
  pass before strategy code runs.

## Price construction

- Canonical source: accepted Dukascopy daily M1 BID and ASK artifacts.
- A flat BID/ASK row with zero volume is a provider filler. It proves daily
  source-grid coverage but is not an observed quote update.
- A zero-volume row with real price movement remains observed.
- M30 OHLC is aggregated from observed M1 quotes only.
- An M30 bucket with a complete source grid but no observed quote is unusable
  and creates a real discontinuity for setup state.
- An incomplete 30-minute source grid fails closed.
- Mid OHLC is the accepted fieldwise BID/ASK proxy used for signal discovery;
  execution uses tradable BID/ASK sides.

## Frozen setup rules

No parameter search is authorized in this pass.

### BREAKOUT_RETEST

- Prior local range: 12 contiguous M30 candles.
- Breakout threshold: 0.10 ATR.
- Retest tolerance: 0.15 ATR.
- Retest failure distance: 0.20 ATR.
- Maximum retest wait: 4 M30 candles.
- Confirmation: the next M30 candle under the existing fixed V4 state machine.
- Structural stop buffer: 0.10 ATR beyond the retest extreme.

### FAKEOUT

- Levels: Previous Day High and Previous Day Low only.
- Minimum previous-day history: 24 M30 candles.
- Sweep threshold: 0.10 ATR.
- Confirmation: the next M30 candle under the existing fixed V4 state machine.
- Structural stop buffer: 0.10 ATR beyond the sweep extreme.
- One confirmed signal per previous-day level event.
- Asia High/Low is excluded.

## Frozen execution rules

- Entry: first observed M1 BID/ASK quote in the next M30 opening minute.
- A provider filler cannot create an entry.
- BUY enters at ASK and exits/triggers on BID.
- SELL enters at BID and exits/triggers on ASK.
- Spread is embedded in actual BID/ASK prices; no fixed spread is subtracted.
- Structural stop is retained, with a minimum distance of 5 pips.
- Target: 1.50R.
- Maximum holding time: 180 minutes.
- If SL and TP both occur inside one M1 candle, the outcome is the stop loss.
- A stop crossed at an M1 open is filled at that worse open price and may lose
  more than 1R.
- A filler at the timeout boundary cannot be used as an executable exit quote.
- If the timeout boundary is a filler, exit occurs at the first observed quote
  after the boundary; if none exists before TRAIN ends, the trade remains
  unresolved and blocks later same-family entries.
- Any missing source minute in the execution window fails closed as DATA_GAP.
- One open trade per setup family per symbol; the two setup families retain
  independent state.
- No trade or pending setup may cross the TRAIN boundary.

## Required report

For BREAKOUT_RETEST and FAKEOUT separately:

- confirmed event count before execution constraints;
- N, WR, PF, AvgR, NetR, and chronological maximum drawdown;
- EUR/USD and GBP/USD separately;
- BUY and SELL separately;
- symbol plus direction;
- each year from 2021 through 2024;
- execution attrition and outcome reasons;
- M30 observed/filler quality counts.

## Interpretation rule

The first run is descriptive, not a parameter-selection exercise. A poor
result does not authorize small ATR/RSI/threshold adjustments. Failure must be
explained through event anatomy, direction, symbol, year, execution attrition,
spread, and price-path behavior before a new event principle is proposed.

VALIDATION 2025 can be unlocked only after TRAIN interpretation and a written
decision that freezes the candidate and its decision criteria. It must not be
used to choose parameters or rescue a weak TRAIN result.
