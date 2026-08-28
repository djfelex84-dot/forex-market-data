# V4 Dukascopy forensic result

Run date: 2026-08-28 UTC

Scope: EUR/USD M30 candles previously rejected for impossible OHLC geometry

Production database: `/app/data/v4_history.db`, opened `mode=ro` with `PRAGMA query_only = ON`

## Evidence

- Production database rows read: 53,711
- Invalid Twelve Data M30 targets: 31
- Independent Dukascopy hourly tick artifacts: 86
- Valid independent reconstructions: 31/31
- Healthy neighbouring M30 comparisons: 120
- Median maximum OHLC difference: 0.450 pips
- P95 maximum OHLC difference: 2.155 pips
- Median absolute deviation: 0.050 pips
- Database SHA256 before and after: `4fac2eadb4040b0c5c9dd3238d5a5d9afbf7200d3ca1ba96633dfa3ef35d9553`
- Final manifest SHA256: `3a112fe5488718d9300619d95921e730f7552a885b9c360f8c2b53f991cfa330`
- Final status: `V4_DUKASCOPY_FORENSIC_AUDIT_OK`
- Repair authorization: `False`

## Silent-minute finding

The initial strict M1-to-M30 policy resolved 30/31 targets. The remaining target,
`2025-01-02 21:00:00`, contained 432 real ticks from `21:00:00.075` through
`21:29:58.821`, with no quote update during minute `21:20` and a maximum tick
gap of 120.452 seconds. Its direct tick-derived midpoint M30 was valid:

`(1.026140, 1.026575, 1.026135, 1.026520)`

The research data layer now distinguishes a silent minute inside a complete raw
hour from a missing source artifact:

- M30 is aggregated directly from observed ticks in a verified complete hour.
- Silent minutes are recorded explicitly.
- No synthetic M1 candle is created and no price is forward-filled.
- Event-anatomy M1 paths continue to fail closed when an exact M1 path is absent.
- An unverified or missing raw hour remains unusable.

## Conclusion

The independent feed, timestamp convention, BID/ASK decoder, and tick-derived
aggregation are accepted for the targeted V4 research gate. The rejected Twelve
Data rows are genuine source-lineage defects, including several apparent 5-pip
and 50-pip digit truncations.

The existing Twelve Data database must remain immutable forensic evidence and
must not become the canonical V4 research dataset by patching only the 31 obvious
rows. Geometry checks cannot detect every plausible but incorrect OHLC value;
some healthy-neighbour comparisons also contain larger-than-baseline differences.

## Next research gate

Build a separate independent Dukascopy BID/ASK research dataset for EUR/USD and
GBP/USD. Use only 2021-2024 for development and event-anatomy analysis. Keep 2025
physically locked until the TRAIN hypothesis and decision criteria are frozen.
Do not touch live execution, production storage, Telegram, Docker, or the current
shadow system.
