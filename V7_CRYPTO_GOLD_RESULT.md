# V7 — Daily trend rules on crypto and gold (result)

Run date: 2026-09-25. Scripts: `v7_crypto_gold_research.py`, `v7_robustness.py`.
Rules, splits and PASS criteria were fixed in the script docstring before any result.

Data: Coin Metrics community daily PriceUSD (BTC, ETH, XRP, BNB, LTC, ADA, DOGE,
to 2026-05-23); XAUUSD daily 2012-11 .. 2022-03. Long/flat only, cost 0.10% per
side (crypto), 0.02% (gold). Equal-weight portfolio, coin joins after 200 days.

## Crypto — PASS (all three families)
Chosen on TRAIN 2016-2020: TSMOM(20), SMA(20), DONCHIAN(20,10).

| DONCHIAN(20,10), 7 coins | Return | Sharpe | Max DD |
|---|---|---|---|
| VALIDATION 2021-22, strategy | +301% | 1.69 | -46% |
| VALIDATION 2021-22, buy & hold | +280% | 1.17 | -77% |
| TEST 2023-05/2026, strategy | +123% | 0.94 | -33% |
| TEST 2023-05/2026, buy & hold | +152% | 0.76 | -59% |

TEST trades: 182, win 35%, avg win +27.8%, avg loss -6.9%, PF 2.19.
By year: 2023 +33% (hold +78%), 2024 +108% (hold +133%), 2025 -9% (hold -22%),
2026 to May -12% (hold -22%).

Robustness (info only, no re-selection):
- Neighbouring parameters (15,7)..(40,20): all positive on VALIDATION and TEST,
  all with max DD 31-42% vs 59% for hold. Not a knife edge.
- BTC alone TEST: +245% / DD -28% vs hold +364% / DD -49%.
- Single coins vary a lot (LTC negative on both periods); diversification matters.

What the edge is: it does NOT reliably beat holding in bull markets. It keeps a
large part of the upside and cuts drawdowns roughly in half, losing less in
down years. Caveats: survivorship bias (today's surviving coins), past results
do not guarantee future ones, spot/long-only, daily-close execution assumed.

## Gold — not convincing
All variants were ~zero or negative on TRAIN (best TSMOM(50) Sharpe 0.09).
TSMOM(50) formally passed with 12 and 16 trades — too few to trust. SMA and
DONCHIAN failed. Not recommended as a signal product on this evidence.

## Recommendation
Crypto daily trend-following (long/flat, several coins) is the first candidate
in this project that survived a locked out-of-sample test. Next step: forward
paper-trading (public, timestamped signals) for 2-3 months before any claim.
