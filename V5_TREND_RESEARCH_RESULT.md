# V5 — Daily trend-following on 9 FX majors (result)

Run date: 2026-09-24. Script: `v5_trend_research.py`.

Data: Federal Reserve H.10 daily rates (1989-01-02 .. 2026-09-18), close-only,
price-only (swap/carry not included). Pairs: EUR/USD, GBP/USD, AUD/USD,
NZD/USD, USD/JPY, USD/CHF, USD/CAD, USD/NOK, USD/SEK.

Rule: long if price > price N days ago, else short; flip on sign change.
Cost 2 bp round trip per flip. Split locked before any result was seen:
TRAIN 1990-2014, VALIDATION 2015-2020, TEST 2021-2026.

## Choice on TRAIN
N=250 selected (best PF 1.32, portfolio Sharpe 0.46, 16/25 positive years).

## One-shot checks of N=250
| Period | Trades | Win % | Avg win | Avg loss | PF | Portfolio Sharpe |
|---|---|---|---|---|---|---|
| TRAIN 1990-2014 | 1217 | 30.7 | +3.15% | -1.06% | 1.32 | +0.46 |
| VALIDATION 2015-2020 | 469 | 35.6 | +1.93% | -0.83% | 1.29 | +0.19 |
| TEST 2021-2026 | 445 | 29.2 | +1.87% | -0.87% | 0.89 | -0.24 |

No lookback (20/60/120/250) is positive on TEST.

## Conclusion
Classic FX trend-following had a real edge historically (1990-2020), but it
has decayed and is negative in 2021-2026 on price alone. Not suitable as a
subscriber signal product in this form. Together with V4 (M30 FAKEOUT /
BREAKOUT_RETEST rejected) this means: simple price-only rules on FX majors
do not currently deliver a verifiable positive expectancy.
