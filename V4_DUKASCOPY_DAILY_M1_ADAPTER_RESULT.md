# V4 Dukascopy daily M1 adapter result

Run date: 2026-08-29 UTC

Scope: candidate daily Dukascopy BID/ASK M1 binary adapter checked against
independently decoded hourly ticks around the previously identified EUR/USD
data defects.

## Result

- Verified hourly tick artifacts: 86
- Tick-derived M1 rows: 5,155
- Daily/tick overlap: 5,155 M1 rows
- Calibration rows after isolating one incomplete tick hour: 5,096
- Missing daily M1 rows: 0
- Tick-reference coverage: 0.999806
- BID/ASK maximum OHLC difference: 0.000 pips
- BID/ASK P95 maximum OHLC difference: 0.000 pips
- Midpoint-proxy P95 maximum OHLC difference: 0.050 pips
- Midpoint-proxy absolute maximum OHLC difference: 0.400 pips
- Flat zero-volume provider fillers excluded: 506
- Zero-volume rows with actual price movement retained: 10
- Incomplete hourly tick reference: `2025-02-03 19:00:00 UTC`
- Final manifest SHA256:
  `d781328db867d7da67e1088d0cab36c85d885e26522f146a36f9b463abc89160`
- Final status: `V4_DUKASCOPY_DAILY_M1_ADAPTER_AUDIT_OK`
- Adapter accepted: `True`

## Normalization policy

- BID and ASK are retained separately as the canonical tradable sides.
- The M1 midpoint is a fieldwise BID/ASK OHLC proxy, not a claim that side
  extrema happened on the same tick.
- A flat row with zero BID and ASK volume is classified as a provider filler
  and is not treated as an observed M1 path.
- A zero-volume row with actual price movement is retained and marked
  `OBSERVED_ZERO_VOLUME_PRICE_CHANGE`.
- A positive-volume daily row absent from a downloaded hourly tick artifact
  reclassifies that tick hour as an incomplete reference; the whole hour is
  excluded from adapter calibration.
- A real tick-derived minute missing from daily M1 still rejects the adapter.

## Research boundary

The audit used 2025 only to diagnose already known source defects. It does not
unlock, inspect, or authorize any V4 strategy result from VALIDATION 2025.
The next dataset build remains physically limited to context from December 2020
and TRAIN 2021-2024. Production storage, live execution, Telegram, Docker, and
the current shadow system remain unchanged.
