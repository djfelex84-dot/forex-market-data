# Live bot strategy audit (EMA20/50 + RSI, M5)

Run date: 2026-09-24. Script: `bot_strategy_audit.py`. No parameters changed.

Data: Oanda 1-minute mid candles 2016-01 .. 2020-05 (independent public
archive, github.com/FutureSharks/financial-data), resampled to M5.
Rules copied from `config.py`, `strategy.py`, `trade_manager.py` (V2 model):
stop max(1 ATR, 5 pips), target 1.5R, max hold 180 min, spread 1 pip.

## Result
| Slice | Trades | Win % | Avg R | PF |
|---|---|---|---|---|
| All | 65,059 | 38.2 | -0.220 | 0.71 |
| EUR/USD | 32,450 | 38.5 | -0.222 | 0.69 |
| GBP/USD | 32,609 | 38.0 | -0.218 | 0.72 |
| All, spread = 0 | 65,059 | 38.9 | -0.042 | 0.94 |

Every year 2016-2020 is negative (PF 0.65-0.78). Every UTC hour is negative.

## Conclusion
- Breakeven win rate at 1.5R plus 1 pip spread on a ~5-pip stop is ~48%;
  the strategy wins ~38%.
- Even with zero spread the signals are slightly negative: the entry rule
  itself has no edge.
- The spread is ~0.18R per trade because M5 stops are tiny (~5 pips).
  Any M5 strategy with 5-pip stops needs an unusually strong edge to cover it.
- Caveat: the live bot also has quality gates / dedup that may reduce the
  number of signals; they cannot turn a no-edge entry into a positive one
  unless they filter on something predictive, which V4 research did not find.
