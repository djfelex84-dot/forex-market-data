# forex-market-data
AS | Forex & Crypto — Telegram signal engine

## What runs (`python main.py`)
- **Crypto signals** (`crypto_signals.py`): daily trend rule on BTC, ETH, XRP, BNB, LTC, ADA, DOGE
  (Twelve Data, `1day`). Buy on a daily close above the 20-day high, exit on a close below the
  10-day low. Spot, long only. Checked once a day after 00:10 UTC.
  - VIP channel: entry/exit signals with chart, daily trend board
  - Free channel: closed-trade results, weekly track record (Monday)
  - Private channel: alert if daily data is missing after 06:00 UTC
- Economic calendar and news digest (free channel)
- User bot: menu, VIP subscriptions and access

The strategy and its out-of-sample test: `V7_CRYPTO_GOLD_RESULT.md`.

## Switched off
The forex M5 EMA/RSI signal engine (`strategy.py`, `trade_manager.py`, forex daily/weekly reports,
market overview, health monitor) lost money on independent data (`BOT_STRATEGY_AUDIT_RESULT.md`)
and is no longer started. The files are kept for reference.

## Environment
`TWELVE_DATA_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHANNEL_ID` (private),
`TELEGRAM_FREE_CHANNEL_ID`, `TELEGRAM_VIP_CHANNEL_ID`, `DB_PATH` (default `/app/data/trading.db`).

## Tests
`python -m unittest test_crypto_signals test_v4_event_research test_v4_research_stack`
