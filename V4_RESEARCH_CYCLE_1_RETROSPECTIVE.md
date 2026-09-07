
# V4 Research Cycle 1 — Retrospective (FAKEOUT / BREAKOUT_RETEST)

## Scope
TRAIN = 2021-01-01..2024-12-31 (EUR/USD, GBP/USD, M30).
VALIDATION 2025 was unlocked exactly once, per protocol, after the TRAIN
candidate was frozen in writing.

## What was tested, in order
1. Parameter sweep (hold time x target R) on TRAIN for both setups.
   BREAKOUT_RETEST: negative AvgR at every combination tested (9/9).
   FAKEOUT: best combo (hold=360min, target=1.5R) gave AvgR=+0.014, PF=1.030.
2. Year-by-year check (FAKEOUT, best combo): positive in 4/4 years
   (2021..2024), AvgR ranging +0.007 to +0.024. Looked stable.
3. Session filter (07:00-16:00 UTC): AvgR improved to +0.058, PF=1.122,
   positive in 3/4 years (2023 negative).
4. Per-symbol check of the session-filtered candidate: both EUR/USD
   (+0.059) and GBP/USD (+0.056) contributed similarly. This candidate
   was FROZEN in writing before touching VALIDATION.

## VALIDATION 2025 result (one-shot, look-only-once)
FAKEOUT, hold=360min, target=1.5R, session 07:00-16:00 UTC, both symbols:
  AvgR = -0.093, PF = 0.822, n=153 (EUR/USD -0.042, GBP/USD -0.133).
Both symbols negative. Candidate REJECTED. Per protocol, no parameter
rescue was attempted after seeing this result.

## Follow-up diagnostics (TRAIN only, VALIDATION still locked for future use)
- Price-path anatomy (no TP/SL/spread assumptions), FAKEOUT: no significant
  drift at 60/120/180min; a small, statistically significant NEGATIVE drift
  at 30min (CI95 excludes zero). No usable positive signal at any horizon.
- Price-path anatomy, BREAKOUT_RETEST: no significant drift at any horizon
  (30/60/120/180min); all confidence intervals cross zero.
- Volatility-regime split (trailing 500-bar ATR tercile), both setups,
  both patterns, 24 total slices: 3/24 showed CI excluding zero, roughly
  consistent with the false-positive rate expected from chance alone at
  this many comparisons (~1.2 expected), with no coherent horizon-by-horizon
  progression and no plausible economic explanation for the pattern of
  results (e.g. BREAKOUT_RETEST flips from positive in LOW_VOL, to
  strongly negative in MID_VOL, back to positive in HIGH_VOL). Treated as
  noise, not a real conditional edge.
- Cross-pair directional alignment (does the other symbol move the same
  direction over the same 30-min bar), both setups, 16 total slices: 1/16
  showed CI excluding zero (BREAKOUT_RETEST, NOT_ALIGNED, 30min only,
  n=176), with no consistency across the other three horizons in that same
  bucket (60/120/180min all cross zero despite similar point estimates).
  Consistent with chance alone (~0.8 false positives expected at 16
  comparisons). Treated as noise, not a real conditional edge.

## Conclusion
Neither FAKEOUT nor BREAKOUT_RETEST, in any parameterization or simple
conditioning variable tested so far (session, weekday, direction, symbol,
volatility regime, cross-pair alignment), shows a real, economically
coherent directional edge on EUR/USD or GBP/USD M30 price action alone.
The one candidate that looked promising on TRAIN did not survive the
locked VALIDATION check.

## What was NOT tested and why
- News-calendar proximity filter: not currently feasible. The existing
  economic_calendar.py module only fetches the CURRENT week
  (ff_calendar_thisweek.json, a free ForexFactory feed with no historical
  backfill). Testing this filter on 2021-2024 data would require a paid
  historical economic-calendar data source. This is a cost/business
  decision, not a coding task, and has not been approved or purchased.

## Recommendation for Cycle 2 (not yet started)
Pure single-symbol price-pattern signals on M30, including simple
cross-pair confirmation, appear to be a dead end with the data and tools
currently available. Plausible next directions, none yet attempted:
  (a) Purchase a historical economic-calendar data source and retest event
      proximity as a filter (real cost, real added complexity).
  (b) Reconsider whether a from-scratch technical-pattern strategy is a
      viable path at all for this business, versus other approaches
      (e.g., licensing/following an independently verified, disclosed
      track record instead of discovering an edge from scratch).
This document is the written decision point required before starting any
new candidate, per the locked research protocol.
