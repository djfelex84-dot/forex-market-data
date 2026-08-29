import json
import lzma
import struct
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from unittest.mock import Mock, patch

import v4_dukascopy_forensic_audit as forensic
import v4_dukascopy_daily_m1_audit as daily_audit
import v4_dukascopy_train_builder as train_builder
import v4_event_anatomy as anatomy
import v4_research_data as research_data
import v4_event_strategy as strategy
import v4_train_event_anatomy as train_anatomy
import v4_train_event_research as train_research


def side_ohlc(open_price, high, low, close):
    return {
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


def m1_row(timestamp, *, open_price, high, low, close, spread=0.0001):
    half = spread / 2.0
    return {
        "timestamp": timestamp,
        "bid": side_ohlc(
            open_price - half,
            high - half,
            low - half,
            close - half,
        ),
        "ask": side_ohlc(
            open_price + half,
            high + half,
            low + half,
            close + half,
        ),
        "mid": side_ohlc(open_price, high, low, close),
        "tick_count": 1,
    }


def flat_m1_rows(start, count, price=1.1000):
    return [
        m1_row(
            start + timedelta(minutes=index),
            open_price=price,
            high=price + 0.0001,
            low=price - 0.0001,
            close=price,
        )
        for index in range(count)
    ]


def canonical_m30(timestamp, price=1.1000, *, usable=True):
    return {
        "timestamp": timestamp,
        "bid": side_ohlc(price - 0.00005, price + 0.00005, price - 0.00015, price - 0.00005),
        "ask": side_ohlc(price + 0.00005, price + 0.00015, price - 0.00005, price + 0.00005),
        "mid": side_ohlc(price, price + 0.0001, price - 0.0001, price),
        "m1_rows": 30 if usable else 29,
        "tick_count": 30 if usable else 29,
        "first_m1_time": timestamp,
        "last_m1_time": timestamp + timedelta(minutes=29 if usable else 28),
        "max_internal_gap_minutes": 1,
        "source_complete": usable,
        "quality_status": "USABLE" if usable else "MISSING_M1",
    }


def daily_m1_payload(
    side,
    *,
    rows=1_440,
    filler_minute=None,
    zero_volume_price_change_minute=None,
):
    base = 110_000 if side == "bid" else 110_010
    records = []
    for minute in range(rows):
        open_raw = base
        close_raw = base
        low_raw = base
        high_raw = base
        volume = 1.0
        if minute == filler_minute:
            volume = 0.0
        if minute == zero_volume_price_change_minute:
            close_raw = base + 1
            high_raw = base + 1
            volume = 0.0
        records.append(
            struct.pack(
                ">IIIIIf",
                minute * 60,
                open_raw,
                close_raw,
                low_raw,
                high_raw,
                volume,
            )
        )
    return lzma.compress(b"".join(records))


def event(signal_time, *, direction="BUY"):
    return {
        "setup": strategy.SETUP_BREAKOUT_RETEST,
        "direction": direction,
        "signal_time": signal_time,
        "entry_price": 1.1001,
        "stop_price": 1.0990 if direction == "BUY" else 1.1010,
        "level": 1.1000,
        "atr": 0.0010,
        "source": "SYNTHETIC",
    }


class DukascopyDataTests(unittest.TestCase):
    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return False

        def read(self):
            return self.payload

    def test_hour_url_uses_zero_based_month_and_aligned_hour(self):
        result = research_data.dukascopy_hour_url(
            "EUR/USD",
            datetime(2024, 11, 28, 12),
        )

        self.assertEqual(
            "https://datafeed.dukascopy.com/datafeed/"
            "EURUSD/2024/10/28/12h_ticks.bi5",
            result,
        )
        with self.assertRaises(ValueError):
            research_data.dukascopy_hour_url(
                "EUR/USD",
                datetime(2024, 11, 28, 12, 30),
            )

    def test_m1_day_url_uses_zero_based_month_and_offer_side(self):
        result = research_data.dukascopy_m1_day_url(
            "GBP/USD",
            datetime(2024, 11, 28),
            "ask",
        )

        self.assertEqual(
            "https://datafeed.dukascopy.com/datafeed/"
            "GBPUSD/2024/10/28/ASK_candles_min_1.bi5",
            result,
        )
        with self.assertRaises(ValueError):
            research_data.dukascopy_m1_day_url(
                "GBP/USD",
                datetime(2024, 11, 28, 1),
                "ask",
            )
        with self.assertRaises(ValueError):
            research_data.dukascopy_m1_day_url(
                "GBP/USD",
                datetime(2024, 11, 28),
                "mid",
            )

    def test_m1_bi5_decoder_preserves_field_order_scale_and_time(self):
        raw = b"".join(
            [
                struct.pack(
                    ">IIIIIf",
                    3_600,
                    110_000,
                    110_020,
                    109_980,
                    110_050,
                    12.5,
                ),
                struct.pack(
                    ">IIIIIf",
                    3_660,
                    110_020,
                    109_990,
                    109_970,
                    110_030,
                    8.25,
                ),
            ]
        )

        result = research_data.decode_bi5_m1_candles(
            lzma.compress(raw),
            symbol="EUR/USD",
            day_start=datetime(2024, 11, 28),
            side="bid",
        )

        self.assertEqual(2, len(result))
        self.assertEqual(datetime(2024, 11, 28, 1), result[0]["timestamp"])
        self.assertEqual("bid", result[0]["side"])
        self.assertEqual(
            side_ohlc(1.10000, 1.10050, 1.09980, 1.10020),
            result[0]["ohlc"],
        )
        self.assertEqual(12.5, result[0]["volume"])

    def test_m1_bi5_decoder_rejects_invalid_offset_and_geometry(self):
        unaligned = struct.pack(
            ">IIIIIf",
            61,
            110_000,
            110_020,
            109_980,
            110_050,
            1.0,
        )
        invalid_geometry = struct.pack(
            ">IIIIIf",
            60,
            110_000,
            110_020,
            109_980,
            110_010,
            1.0,
        )

        with self.assertRaisesRegex(RuntimeError, "offset"):
            research_data.decode_bi5_m1_candles(
                lzma.compress(unaligned),
                symbol="EUR/USD",
                day_start=datetime(2024, 11, 28),
                side="bid",
            )
        with self.assertRaisesRegex(RuntimeError, "geometry"):
            research_data.decode_bi5_m1_candles(
                lzma.compress(invalid_geometry),
                symbol="EUR/USD",
                day_start=datetime(2024, 11, 28),
                side="bid",
            )

    def test_daily_bid_ask_merge_builds_mid_proxy_without_fake_ticks(self):
        start = datetime(2024, 11, 28, 12)
        bid_rows = []
        ask_rows = []
        for minute in range(30):
            timestamp = start + timedelta(minutes=minute)
            bid_rows.append(
                {
                    "timestamp": timestamp,
                    "side": "bid",
                    "ohlc": side_ohlc(1.1000, 1.1002, 1.0998, 1.1001),
                    "volume": 10.0,
                }
            )
            ask_rows.append(
                {
                    "timestamp": timestamp,
                    "side": "ask",
                    "ohlc": side_ohlc(1.1002, 1.1004, 1.1000, 1.1003),
                    "volume": 12.0,
                }
            )

        merged = research_data.merge_bid_ask_m1(bid_rows, ask_rows)
        m30 = research_data.aggregate_m1_to_m30(merged)

        self.assertEqual(30, len(merged))
        self.assertEqual(
            side_ohlc(1.1001, 1.1003, 1.0999, 1.1002),
            merged[0]["mid"],
        )
        self.assertNotIn("tick_count", merged[0])
        self.assertEqual(0, m30[0]["tick_count"])
        self.assertEqual(60, m30[0]["source_bar_count"])
        self.assertEqual("USABLE", m30[0]["quality_status"])

    def test_daily_bid_ask_merge_drops_flat_zero_volume_fillers(self):
        timestamp = datetime(2025, 1, 2, 21, 20)
        bid = {
            "timestamp": timestamp,
            "side": "bid",
            "ohlc": side_ohlc(1.02626, 1.02626, 1.02626, 1.02626),
            "volume": 0.0,
        }
        ask = {
            "timestamp": timestamp,
            "side": "ask",
            "ohlc": side_ohlc(1.02628, 1.02628, 1.02628, 1.02628),
            "volume": 0.0,
        }

        self.assertEqual([], research_data.merge_bid_ask_m1([bid], [ask]))
        included = research_data.merge_bid_ask_m1(
            [bid],
            [ask],
            include_zero_volume_fillers=True,
        )
        self.assertEqual(1, len(included))
        self.assertFalse(included[0]["source_observed"])
        self.assertEqual("ZERO_VOLUME_FILLER", included[0]["quality_status"])

    def test_daily_bid_ask_merge_keeps_nonflat_zero_volume_row(self):
        timestamp = datetime(2025, 1, 2, 21, 20)
        bid = {
            "timestamp": timestamp,
            "side": "bid",
            "ohlc": side_ohlc(1.02626, 1.02627, 1.02626, 1.02627),
            "volume": 0.0,
        }
        ask = {
            "timestamp": timestamp,
            "side": "ask",
            "ohlc": side_ohlc(1.02628, 1.02628, 1.02628, 1.02628),
            "volume": 0.0,
        }

        merged = research_data.merge_bid_ask_m1([bid], [ask])

        self.assertEqual(1, len(merged))
        self.assertTrue(merged[0]["source_observed"])
        self.assertEqual(
            "OBSERVED_ZERO_VOLUME_PRICE_CHANGE",
            merged[0]["quality_status"],
        )

    def test_daily_bid_ask_merge_rejects_timestamp_mismatch(self):
        bid = {
            "timestamp": datetime(2024, 11, 28, 12),
            "side": "bid",
            "ohlc": side_ohlc(1.1000, 1.1002, 1.0998, 1.1001),
            "volume": 10.0,
        }
        ask = {
            "timestamp": datetime(2024, 11, 28, 12, 1),
            "side": "ask",
            "ohlc": side_ohlc(1.1002, 1.1004, 1.1000, 1.1003),
            "volume": 12.0,
        }

        with self.assertRaisesRegex(RuntimeError, "timestamps differ"):
            research_data.merge_bid_ask_m1([bid], [ask])

    def test_daily_bid_ask_merge_rejects_wrong_side_label(self):
        row = {
            "timestamp": datetime(2024, 11, 28, 12),
            "side": "ask",
            "ohlc": side_ohlc(1.1000, 1.1002, 1.0998, 1.1001),
            "volume": 10.0,
        }

        with self.assertRaisesRegex(RuntimeError, "non-BID"):
            research_data.merge_bid_ask_m1([row], [row])

    def test_daily_adapter_comparison_accepts_exact_tick_path(self):
        start = datetime(2024, 11, 28, 12)
        reference = flat_m1_rows(start, 2)
        candidate = [dict(row) for row in reference]

        result = daily_audit.compare_paths(reference, candidate, [start])

        self.assertTrue(result["adapter_accepted"])
        self.assertEqual(2, result["overlap_rows"])
        self.assertEqual([], result["missing_daily_timestamps"])
        self.assertEqual([], result["extra_daily_timestamps_in_verified_hours"])
        self.assertEqual(0.0, result["side_absolute_max_ohlc_diff_pips"])

    def test_daily_adapter_comparison_rejects_missing_observed_minute(self):
        start = datetime(2024, 11, 28, 12)
        reference = flat_m1_rows(start, 2)

        result = daily_audit.compare_paths(reference, reference[:1], [start])

        self.assertFalse(result["adapter_accepted"])
        self.assertEqual(
            [start + timedelta(minutes=1)],
            result["missing_daily_timestamps"],
        )

    def test_daily_adapter_comparison_rejects_synthetic_extra_minute(self):
        start = datetime(2024, 11, 28, 12)
        reference = flat_m1_rows(start, 2)
        candidate = [dict(row) for row in reference]
        candidate.append(m1_row(
            start + timedelta(hours=1),
            open_price=1.1000,
            high=1.1001,
            low=1.0999,
            close=1.1000,
        ))

        result = daily_audit.compare_paths(
            reference,
            candidate,
            [start, start + timedelta(hours=1)],
        )

        self.assertFalse(result["adapter_accepted"])
        self.assertEqual(
            [start + timedelta(hours=1)],
            result["extra_daily_timestamps_in_verified_hours"],
        )

    def test_daily_adapter_classifies_sparse_positive_extra_as_tick_gap(self):
        start = datetime(2024, 11, 28, 12)
        reference = [
            m1_row(
                start,
                open_price=1.1000,
                high=1.1001,
                low=1.0999,
                close=1.1000,
            ),
            m1_row(
                start + timedelta(hours=1),
                open_price=1.1000,
                high=1.1001,
                low=1.0999,
                close=1.1000,
            ),
        ]
        candidate = [dict(row) for row in reference]
        candidate.append(m1_row(
            start + timedelta(hours=1, minutes=1),
            open_price=1.1000,
            high=1.1001,
            low=1.0999,
            close=1.1000,
        ))

        with patch.object(daily_audit, "MIN_TICK_REFERENCE_COVERAGE", 0.5):
            result = daily_audit.compare_paths(
                reference,
                candidate,
                [start, start + timedelta(hours=1)],
            )

        self.assertTrue(result["adapter_accepted"])
        self.assertEqual(
            [start + timedelta(hours=1)],
            result["incomplete_tick_reference_hours"],
        )
        self.assertEqual(1, result["calibration_rows"])

    def test_daily_audit_defers_transient_failure_and_finishes_pass(self):
        start = datetime(2024, 11, 28)

        def fetch_side(day_start, side):
            if side == "bid":
                raise research_data.TransientDukascopyDownloadError("HTTP 503")
            return [
                {
                    "timestamp": start,
                    "side": "ask",
                    "ohlc": side_ohlc(1.1002, 1.1004, 1.1000, 1.1003),
                    "volume": 12.0,
                }
            ], {
                "day_utc": day_start,
                "side": side,
                "bytes": 100,
                "source": "SYNTHETIC",
            }

        with tempfile.TemporaryDirectory() as temporary:
            manifest_path = Path(temporary) / "manifest.json"
            raw_path = Path(temporary) / "missing.bi5"
            with patch.object(
                daily_audit,
                "read_forensic_manifest",
                return_value=({"raw_artifacts": [{}]}, "forensic-hash"),
            ), patch.object(
                daily_audit,
                "load_verified_tick_m1",
                return_value=([], [], [start]),
            ), patch.object(
                daily_audit,
                "fetch_or_read_day",
                side_effect=fetch_side,
            ) as mocked_fetch, patch.object(
                daily_audit,
                "daily_raw_path",
                return_value=raw_path,
            ), patch.object(
                daily_audit,
                "MANIFEST_PATH",
                manifest_path,
            ), patch.object(daily_audit.time, "sleep"), patch("builtins.print"):
                with self.assertRaisesRegex(RuntimeError, "were deferred"):
                    daily_audit.run_audit()

            self.assertEqual(2, mocked_fetch.call_count)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(
                "INCOMPLETE_TRANSIENT_DOWNLOADS",
                manifest["status"],
            )
            self.assertEqual(1, len(manifest["download_failures"]))
            self.assertTrue(manifest["rerun_safe"])

    def test_bi5_decoder_preserves_bid_ask_and_tick_order(self):
        raw = b"".join(
            [
                struct.pack(">IIIff", 1_000, 110_015, 110_005, 2.0, 3.0),
                struct.pack(">IIIff", 2_000, 110_025, 110_015, 4.0, 5.0),
            ]
        )
        payload = lzma.compress(raw)

        result = research_data.decode_bi5_ticks(
            payload,
            symbol="EUR/USD",
            hour_start=datetime(2024, 11, 28, 12),
        )

        self.assertEqual(2, len(result))
        self.assertEqual(datetime(2024, 11, 28, 12, 0, 1), result[0]["timestamp"])
        self.assertAlmostEqual(1.10005, result[0]["bid"], places=7)
        self.assertAlmostEqual(1.10015, result[0]["ask"], places=7)
        self.assertAlmostEqual(1.10010, result[0]["mid"], places=7)
        self.assertEqual(3.0, result[0]["bid_volume"])
        self.assertEqual(2.0, result[0]["ask_volume"])

    def test_bi5_decoder_rejects_bid_above_ask(self):
        raw = struct.pack(">IIIff", 1_000, 110_005, 110_015, 2.0, 3.0)

        with self.assertRaisesRegex(RuntimeError, "BID/ASK"):
            research_data.decode_bi5_ticks(
                lzma.compress(raw),
                symbol="EUR/USD",
                hour_start=datetime(2024, 11, 28, 12),
            )

    def test_transient_download_error_retries_then_succeeds(self):
        notices = []
        reset = URLError(ConnectionResetError(104, "reset by peer"))

        with patch.object(
            research_data,
            "urlopen",
            side_effect=[reset, self.FakeResponse(b"payload")],
        ) as mocked_open, patch.object(research_data.time, "sleep") as mocked_sleep:
            payload, url = research_data.download_hour(
                "EUR/USD",
                datetime(2024, 11, 28, 12),
                max_attempts=2,
                retry_backoff_seconds=0.5,
                retry_notifier=lambda *notice: notices.append(notice),
            )

        self.assertEqual(b"payload", payload)
        self.assertTrue(url.endswith("/2024/10/28/12h_ticks.bi5"))
        self.assertEqual(2, mocked_open.call_count)
        mocked_sleep.assert_called_once_with(0.5)
        self.assertEqual(1, notices[0][0])
        self.assertEqual(2, notices[0][1])
        self.assertIn("reset by peer", notices[0][3])

    def test_transient_download_error_exhaustion_fails_closed(self):
        reset = URLError(ConnectionResetError(104, "reset by peer"))

        with patch.object(
            research_data,
            "urlopen",
            side_effect=[reset, reset],
        ), patch.object(research_data.time, "sleep"):
            with self.assertRaisesRegex(
                research_data.TransientDukascopyDownloadError,
                "after 2 attempts",
            ):
                research_data.download_hour(
                    "EUR/USD",
                    datetime(2024, 11, 28, 12),
                    max_attempts=2,
                    retry_backoff_seconds=0,
                )

    def test_tick_midpoint_is_aggregated_at_tick_level(self):
        start = datetime(2024, 11, 28, 12)
        ticks = [
            {
                "timestamp": start + timedelta(seconds=1),
                "bid": 1.1000,
                "ask": 1.1002,
                "mid": 1.1001,
            },
            {
                "timestamp": start + timedelta(seconds=2),
                "bid": 1.1003,
                "ask": 1.1007,
                "mid": 1.1005,
            },
        ]

        result = research_data.aggregate_ticks_to_m1(ticks)

        self.assertEqual(1, len(result))
        self.assertEqual(2, result[0]["tick_count"])
        self.assertEqual(1.1001, result[0]["mid"]["open"])
        self.assertEqual(1.1005, result[0]["mid"]["high"])
        self.assertEqual(1.1005, result[0]["mid"]["close"])

    def test_complete_30_m1_rows_aggregate_to_exact_m30(self):
        start = datetime(2024, 11, 28, 12)
        rows = flat_m1_rows(start, 30)
        rows[0] = m1_row(
            start,
            open_price=1.1000,
            high=1.1002,
            low=1.0998,
            close=1.1001,
        )
        rows[10] = m1_row(
            start + timedelta(minutes=10),
            open_price=1.1001,
            high=1.1010,
            low=1.0999,
            close=1.1005,
        )
        rows[-1] = m1_row(
            start + timedelta(minutes=29),
            open_price=1.1004,
            high=1.1006,
            low=1.0997,
            close=1.1003,
        )

        result = research_data.aggregate_m1_to_m30(rows)

        self.assertEqual(1, len(result))
        self.assertEqual("USABLE", result[0]["quality_status"])
        self.assertEqual(30, result[0]["m1_rows"])
        self.assertEqual(1.1000, result[0]["mid"]["open"])
        self.assertEqual(1.1010, result[0]["mid"]["high"])
        self.assertEqual(1.0997, result[0]["mid"]["low"])
        self.assertEqual(1.1003, result[0]["mid"]["close"])

    def test_missing_m1_is_flagged_and_never_forward_filled(self):
        start = datetime(2024, 11, 28, 12)
        rows = flat_m1_rows(start, 30)
        del rows[15]

        result = research_data.aggregate_m1_to_m30(rows)

        self.assertEqual("MISSING_M1", result[0]["quality_status"])
        self.assertFalse(result[0]["source_complete"])
        self.assertEqual(29, result[0]["m1_rows"])
        self.assertEqual(2, result[0]["max_internal_gap_minutes"])
        self.assertEqual([], research_data.m30_strategy_rows(result))

    def test_verified_grid_uses_fillers_for_coverage_not_ohlc(self):
        start = datetime(2024, 11, 28, 12)
        rows = flat_m1_rows(start, 30, price=1.1000)
        rows[0]["source_observed"] = False
        rows[0]["bid"] = side_ohlc(1.0900, 1.0900, 1.0900, 1.0900)
        rows[0]["ask"] = side_ohlc(1.0902, 1.0902, 1.0902, 1.0902)
        rows[0]["mid"] = side_ohlc(1.0901, 1.0901, 1.0901, 1.0901)

        result = research_data.aggregate_verified_grid_to_m30(rows)

        self.assertEqual(1, len(result))
        self.assertEqual("USABLE", result[0]["quality_status"])
        self.assertEqual(30, result[0]["source_grid_rows"])
        self.assertEqual(29, result[0]["observed_m1_rows"])
        self.assertEqual(1, result[0]["filler_m1_rows"])
        self.assertEqual(1.09995, result[0]["bid"]["open"])

    def test_verified_grid_keeps_fully_silent_m30_unusable(self):
        start = datetime(2024, 11, 28, 12)
        rows = flat_m1_rows(start, 30)
        for row in rows:
            row["source_observed"] = False

        result = research_data.aggregate_verified_grid_to_m30(rows)

        self.assertEqual("NO_OBSERVED_QUOTES", result[0]["quality_status"])
        self.assertEqual(0, result[0]["observed_m1_rows"])
        self.assertNotIn("mid", result[0])

    def test_verified_grid_rejects_missing_source_minute(self):
        start = datetime(2024, 11, 28, 12)
        rows = flat_m1_rows(start, 30)
        del rows[12]

        result = research_data.aggregate_verified_grid_to_m30(rows)

        self.assertEqual("MISSING_SOURCE_GRID", result[0]["quality_status"])
        self.assertFalse(result[0]["source_complete"])

    def test_complete_raw_hour_allows_direct_m30_with_silent_minute(self):
        start = datetime(2025, 1, 2, 21)
        ticks = []
        for minute in range(30):
            if minute == 20:
                continue
            ticks.append(
                {
                    "timestamp": start + timedelta(minutes=minute, seconds=1),
                    "bid": 1.02610 + (minute * 0.000001),
                    "ask": 1.02620 + (minute * 0.000001),
                }
            )

        direct = research_data.aggregate_ticks_to_m30(
            ticks,
            complete_hours={start},
        )
        strict_m1 = research_data.aggregate_m1_to_m30(
            research_data.aggregate_ticks_to_m1(ticks)
        )

        self.assertEqual("USABLE", direct[0]["quality_status"])
        self.assertEqual(29, direct[0]["m1_rows"])
        self.assertEqual(
            [start + timedelta(minutes=20)],
            direct[0]["missing_minutes"],
        )
        self.assertEqual("DIRECT_TICKS_COMPLETE_HOUR", direct[0]["aggregation_policy"])
        self.assertEqual("MISSING_M1", strict_m1[0]["quality_status"])
        self.assertNotIn(
            start + timedelta(minutes=20),
            {
                row["timestamp"]
                for row in research_data.aggregate_ticks_to_m1(ticks)
            },
        )

    def test_direct_m30_rejects_unverified_raw_hour(self):
        start = datetime(2025, 1, 2, 21)
        ticks = [
            {
                "timestamp": start + timedelta(seconds=1),
                "bid": 1.0261,
                "ask": 1.0262,
            }
        ]

        result = research_data.aggregate_ticks_to_m30(
            ticks,
            complete_hours=set(),
        )

        self.assertEqual("MISSING_RAW_HOUR", result[0]["quality_status"])
        self.assertFalse(result[0]["source_complete"])
        self.assertEqual([], research_data.m30_strategy_rows(result))

    def test_duplicate_m1_timestamp_is_rejected(self):
        start = datetime(2024, 11, 28, 12)
        duplicate = flat_m1_rows(start, 2)
        duplicate[1]["timestamp"] = start

        with self.assertRaisesRegex(RuntimeError, "Duplicate/out-of-order"):
            research_data.aggregate_m1_to_m30(duplicate)

    def test_raw_artifact_refuses_conflicting_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "hour.bi5"
            first = research_data.write_raw_artifact(path, b"first")
            repeated = research_data.write_raw_artifact(path, b"first")
            with self.assertRaisesRegex(RuntimeError, "hash conflict"):
                research_data.write_raw_artifact(path, b"different")

        self.assertEqual(first, repeated)

    def test_forensic_hours_cover_full_five_m30_window(self):
        result = forensic.required_hours([datetime(2024, 11, 28, 12, 30)])

        self.assertEqual(
            [
                datetime(2024, 11, 28, 11),
                datetime(2024, 11, 28, 12),
                datetime(2024, 11, 28, 13),
            ],
            result,
        )

    def test_missing_forensic_hour_is_not_cached_as_valid_data(self):
        hour = datetime(2024, 11, 28, 12)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            forensic,
            "OUTPUT_DIR",
            Path(directory),
        ), patch.object(
            research_data,
            "download_hour",
            return_value=(b"", research_data.dukascopy_hour_url("EUR/USD", hour)),
        ):
            with self.assertRaisesRegex(RuntimeError, "Empty/missing"):
                forensic.fetch_or_read_hour(hour)

            self.assertFalse(forensic.raw_path(hour).exists())

    def test_second_forensic_process_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            forensic,
            "OUTPUT_DIR",
            Path(directory),
        ):
            first = forensic.acquire_run_lock()
            try:
                with self.assertRaisesRegex(RuntimeError, "already holds"):
                    forensic.acquire_run_lock()
            finally:
                forensic.fcntl.flock(first.fileno(), forensic.fcntl.LOCK_UN)
                first.close()


class TrainBuilderTests(unittest.TestCase):
    def test_research_days_include_context_and_never_expose_2025(self):
        days = train_builder.research_days()

        self.assertEqual(datetime(2020, 12, 1), days[0])
        self.assertEqual(datetime(2024, 12, 31), days[-1])
        self.assertTrue(all(day < datetime(2025, 1, 1) for day in days))

    def test_fetch_guard_rejects_2025_before_cache_or_network(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            train_builder,
            "RAW_DIR",
            Path(directory) / "raw",
        ), patch.object(
            train_builder.research_data,
            "download_m1_day",
        ) as mocked_download:
            with self.assertRaisesRegex(RuntimeError, "outside locked"):
                train_builder.fetch_or_read_side(
                    "EUR/USD",
                    datetime(2025, 1, 1),
                    "bid",
                )

        mocked_download.assert_not_called()

    def test_materialization_guard_rejects_2025_before_decoding(self):
        with tempfile.TemporaryDirectory() as directory:
            connection = train_builder.open_database(
                Path(directory) / "train.sqlite3"
            )
            try:
                with patch.object(
                    train_builder.research_data,
                    "decode_bi5_m1_candles",
                ) as mocked_decode:
                    with self.assertRaisesRegex(RuntimeError, "materialize outside"):
                        train_builder.materialize_day(
                            connection,
                            symbol="EUR/USD",
                            day_start=datetime(2025, 1, 1),
                            bid_payload=b"bid",
                            ask_payload=b"ask",
                            bid_url="bid-url",
                            ask_url="ask-url",
                            bid_source="TEST",
                            ask_source="TEST",
                        )
                mocked_decode.assert_not_called()
            finally:
                connection.close()

    def test_truncated_daily_grid_fails_closed(self):
        day = datetime(2024, 1, 2)
        with tempfile.TemporaryDirectory() as directory:
            connection = train_builder.open_database(
                Path(directory) / "train.sqlite3"
            )
            try:
                with self.assertRaisesRegex(RuntimeError, "1439/1440 rows"):
                    train_builder.materialize_day(
                        connection,
                        symbol="EUR/USD",
                        day_start=day,
                        bid_payload=daily_m1_payload("bid", rows=1_439),
                        ask_payload=daily_m1_payload("ask", rows=1_439),
                        bid_url="bid-url",
                        ask_url="ask-url",
                        bid_source="TEST",
                        ask_source="TEST",
                    )
            finally:
                connection.close()

    def test_materialization_is_idempotent_and_preserves_quality_classes(self):
        day = datetime(2024, 1, 2)
        bid_payload = daily_m1_payload(
            "bid",
            filler_minute=10,
            zero_volume_price_change_minute=11,
        )
        ask_payload = daily_m1_payload(
            "ask",
            filler_minute=10,
            zero_volume_price_change_minute=11,
        )
        with tempfile.TemporaryDirectory() as directory, patch.object(
            train_builder,
            "RAW_DIR",
            Path(directory) / "raw",
        ):
            connection = train_builder.open_database(
                Path(directory) / "train.sqlite3"
            )
            try:
                research_data.write_raw_artifact(
                    train_builder.raw_path("EUR/USD", day, "bid"),
                    bid_payload,
                )
                research_data.write_raw_artifact(
                    train_builder.raw_path("EUR/USD", day, "ask"),
                    ask_payload,
                )
                arguments = {
                    "symbol": "EUR/USD",
                    "day_start": day,
                    "bid_payload": bid_payload,
                    "ask_payload": ask_payload,
                    "bid_url": "bid-url",
                    "ask_url": "ask-url",
                    "bid_source": "TEST",
                    "ask_source": "TEST",
                }
                first = train_builder.materialize_day(connection, **arguments)
                second = train_builder.materialize_day(connection, **arguments)

                self.assertEqual("MATERIALIZED", first["status"])
                self.assertEqual(1_440, first["source_rows"])
                self.assertEqual(1_439, first["observed_rows"])
                self.assertEqual(1, first["filler_rows"])
                self.assertEqual(1, first["zero_volume_price_change_rows"])
                self.assertEqual("DB_CACHE", second["status"])
                self.assertEqual(
                    1_439,
                    connection.execute("SELECT COUNT(*) FROM m1_bars").fetchone()[0],
                )
                self.assertEqual(
                    1,
                    connection.execute("SELECT COUNT(*) FROM m1_gaps").fetchone()[0],
                )
                self.assertEqual(
                    1,
                    connection.execute(
                        """
                        SELECT COUNT(*) FROM m1_bars
                        WHERE quality_status =
                              'OBSERVED_ZERO_VOLUME_PRICE_CHANGE'
                        """
                    ).fetchone()[0],
                )
                self.assertEqual(
                    [],
                    train_builder.validate_database(
                        connection,
                        {("EUR/USD", "2024-01-02")},
                    ),
                )
                connection.execute(
                    """
                    DELETE FROM m1_bars
                    WHERE symbol = 'EUR/USD'
                      AND datetime = '2024-01-02 00:00:00'
                    """
                )
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "normalized row counts"):
                    train_builder.validate_database(
                        connection,
                        {("EUR/USD", "2024-01-02")},
                    )
            finally:
                connection.close()

    def test_missing_side_fails_without_partial_database_rows(self):
        day = datetime(2024, 1, 2)
        with tempfile.TemporaryDirectory() as directory:
            connection = train_builder.open_database(
                Path(directory) / "train.sqlite3"
            )
            try:
                with self.assertRaises(train_builder.RequiredSourceGapError):
                    train_builder.materialize_day(
                        connection,
                        symbol="EUR/USD",
                        day_start=day,
                        bid_payload=daily_m1_payload("bid"),
                        ask_payload=b"",
                        bid_url="bid-url",
                        ask_url="ask-url",
                        bid_source="TEST",
                        ask_source="TEST",
                    )
                self.assertEqual(
                    0,
                    connection.execute(
                        "SELECT COUNT(*) FROM processed_days"
                    ).fetchone()[0],
                )
            finally:
                connection.close()

    def test_zero_byte_404_marker_is_retried_not_cached(self):
        day = datetime(2024, 1, 2)
        with tempfile.TemporaryDirectory() as directory, patch.object(
            train_builder,
            "RAW_DIR",
            Path(directory) / "raw",
        ), patch.object(
            train_builder,
            "ADAPTER_AUDIT_CACHE",
            Path(directory) / "seed",
        ):
            path = train_builder.raw_path("EUR/USD", day, "bid")
            path.parent.mkdir(parents=True)
            path.write_bytes(b"")
            url = research_data.dukascopy_m1_day_url("EUR/USD", day, "bid")
            with patch.object(
                train_builder.research_data,
                "download_m1_day",
                return_value=(b"valid-payload", url),
            ) as mocked_download:
                payload, returned_url, source = train_builder.fetch_or_read_side(
                    "EUR/USD",
                    day,
                    "bid",
                )

            self.assertEqual(b"valid-payload", payload)
            self.assertEqual(url, returned_url)
            self.assertEqual("NETWORK", source)
            self.assertEqual(b"valid-payload", path.read_bytes())
            mocked_download.assert_called_once()

    def test_saturday_record_is_idempotent_but_provenance_checked(self):
        saturday = datetime(2024, 1, 6)
        with tempfile.TemporaryDirectory() as directory:
            connection = train_builder.open_database(
                Path(directory) / "train.sqlite3"
            )
            try:
                self.assertEqual(
                    "SATURDAY_CLOSED",
                    train_builder.record_saturday(
                        connection,
                        "EUR/USD",
                        saturday,
                    ),
                )
                self.assertEqual(
                    "DB_CACHE",
                    train_builder.record_saturday(
                        connection,
                        "EUR/USD",
                        saturday,
                    ),
                )
                connection.execute(
                    """
                    UPDATE processed_days SET status = 'MATERIALIZED'
                    WHERE symbol = 'EUR/USD' AND day_utc = '2024-01-06'
                    """
                )
                connection.commit()
                with self.assertRaisesRegex(RuntimeError, "provenance mismatch"):
                    train_builder.record_saturday(
                        connection,
                        "EUR/USD",
                        saturday,
                    )
            finally:
                connection.close()

    def test_database_metadata_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "train.sqlite3"
            connection = train_builder.open_database(path)
            connection.execute(
                "UPDATE metadata SET value = 'false' "
                "WHERE key = 'validation_2025_locked'"
            )
            connection.commit()
            connection.close()

            with self.assertRaisesRegex(RuntimeError, "metadata mismatch"):
                train_builder.open_database(path)

    def test_second_train_builder_process_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(
            train_builder,
            "OUTPUT_DIR",
            Path(directory),
        ):
            first = train_builder.acquire_run_lock()
            try:
                with self.assertRaisesRegex(RuntimeError, "already holds"):
                    train_builder.acquire_run_lock()
            finally:
                train_builder.fcntl.flock(
                    first.fileno(),
                    train_builder.fcntl.LOCK_UN,
                )
                first.close()

    def test_complete_checkpoint_hashes_checkpointed_database(self):
        saturday = datetime(2024, 1, 6)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database_path = root / "train.sqlite3"
            manifest_path = root / "manifest.json"
            with patch.object(
                train_builder,
                "DATABASE_PATH",
                database_path,
            ), patch.object(
                train_builder,
                "MANIFEST_PATH",
                manifest_path,
            ), patch.object(
                train_builder,
                "research_days",
                return_value=[saturday],
            ), patch("builtins.print"):
                train_builder.run_builder()

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual("COMPLETE", manifest["status"])
            self.assertTrue(manifest["validation_2025_locked"])
            self.assertEqual(
                research_data.sha256_file(database_path),
                manifest["database_sha256"],
            )
            self.assertFalse(Path(f"{database_path}-wal").exists())


class TrainEventResearchTests(unittest.TestCase):
    @staticmethod
    def trade_event(start, direction="BUY", stop_price=None):
        if stop_price is None:
            stop_price = 1.09905 if direction == "BUY" else 1.10095
        return {
            "setup": strategy.SETUP_BREAKOUT_RETEST,
            "direction": direction,
            "signal_time": start,
            "entry_price": 1.1000,
            "stop_price": stop_price,
            "level": 1.1000,
            "atr": 0.0010,
            "source": "SYNTHETIC",
        }

    def test_partial_manifest_is_rejected_before_research(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manifest.json"
            path.write_text(
                json.dumps(
                    {
                        "status": "IN_PROGRESS",
                        "validation_2025_locked": True,
                    }
                ),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RuntimeError, "manifest gate failed"):
                train_research._load_manifest(path)

    def test_train_grid_read_rejects_2025_before_sql(self):
        connection = Mock()

        with self.assertRaisesRegex(RuntimeError, "outside lock"):
            list(
                train_research.iter_source_grid(
                    connection,
                    "EUR/USD",
                    datetime(2025, 1, 1),
                    datetime(2025, 1, 2),
                )
            )

        connection.execute.assert_not_called()

    def test_source_grid_merges_observed_and_filler_rows_in_order(self):
        start = datetime(2024, 1, 2, 10)
        with tempfile.TemporaryDirectory() as directory:
            connection = train_builder.open_database(
                Path(directory) / "train.sqlite3"
            )
            try:
                connection.execute(
                    """
                    INSERT INTO m1_bars(
                        symbol, datetime,
                        bid_open, bid_high, bid_low, bid_close,
                        ask_open, ask_high, ask_low, ask_close,
                        mid_open, mid_high, mid_low, mid_close,
                        bid_volume, ask_volume, quality_status
                    ) VALUES (
                        'EUR/USD', '2024-01-02 10:00:00',
                        1.1000, 1.1002, 1.0998, 1.1001,
                        1.1002, 1.1004, 1.1000, 1.1003,
                        1.1001, 1.1003, 1.0999, 1.1002,
                        10.0, 12.0, 'OBSERVED'
                    )
                    """
                )
                connection.execute(
                    """
                    INSERT INTO m1_gaps(
                        symbol, datetime, reason, bid_open, ask_open
                    ) VALUES (
                        'EUR/USD', '2024-01-02 10:01:00',
                        'ZERO_VOLUME_FILLER', 1.1001, 1.1003
                    )
                    """
                )
                connection.commit()

                rows = list(
                    train_research.iter_source_grid(
                        connection,
                        "EUR/USD",
                        start,
                        start + timedelta(minutes=2),
                    )
                )
            finally:
                connection.close()

        self.assertEqual(2, len(rows))
        self.assertTrue(rows[0]["source_observed"])
        self.assertFalse(rows[1]["source_observed"])
        self.assertEqual("ZERO_VOLUME_FILLER", rows[1]["quality_status"])
        self.assertAlmostEqual(1.1002, rows[1]["mid"]["open"])

    def test_buy_execution_uses_ask_entry_and_bid_target(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[1] = m1_row(
            start + timedelta(minutes=1),
            open_price=1.1000,
            high=1.1017,
            low=1.0998,
            close=1.1010,
        )

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("TAKE_PROFIT", result["reason"])
        self.assertEqual(1.5, result["r"])
        self.assertAlmostEqual(1.10005, result["entry_price"])
        self.assertEqual("bid", result["execution_side"])
        self.assertAlmostEqual(1.0, result["entry_spread_pips"])

    def test_sell_execution_uses_bid_entry_and_ask_target(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[1] = m1_row(
            start + timedelta(minutes=1),
            open_price=1.1000,
            high=1.1002,
            low=1.0983,
            close=1.0990,
        )

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start, direction="SELL"),
            )

        self.assertEqual("TAKE_PROFIT", result["reason"])
        self.assertEqual(1.5, result["r"])
        self.assertAlmostEqual(1.09995, result["entry_price"])
        self.assertEqual("ask", result["execution_side"])

    def test_ambiguous_m1_execution_is_worst_case_stop(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[1] = m1_row(
            start + timedelta(minutes=1),
            open_price=1.1000,
            high=1.1017,
            low=1.0989,
            close=1.1000,
        )

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("AMBIGUOUS_M1_WORST_SL", result["reason"])
        self.assertEqual(-1.0, result["r"])

    def test_stop_gap_uses_worse_open_instead_of_capping_loss(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[1] = m1_row(
            start + timedelta(minutes=1),
            open_price=1.0988,
            high=1.0990,
            low=1.0985,
            close=1.0987,
        )

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("STOP_GAP", result["reason"])
        self.assertLess(result["r"], -1.0)
        self.assertAlmostEqual(1.09875, result["exit_price"])

    def test_timeout_contains_real_bid_ask_spread(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("TIMEOUT", result["reason"])
        self.assertAlmostEqual(-0.1, result["r"])

    def test_filler_at_timeout_without_later_quote_stays_unresolved(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[-1]["source_observed"] = False

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ), patch.object(
            train_research,
            "load_first_observed_quote",
            return_value=None,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual(
            "NO_TIMEOUT_QUOTE_BEFORE_BOUNDARY",
            result["reason"],
        )
        self.assertIsNone(result["r"])
        self.assertEqual(
            start + timedelta(minutes=178),
            result["last_observed_quote_time"],
        )

    def test_filler_at_timeout_exits_at_first_later_observed_quote(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[-1]["source_observed"] = False
        delayed = m1_row(
            start + timedelta(minutes=182),
            open_price=1.1002,
            high=1.1003,
            low=1.1001,
            close=1.1002,
        )

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ), patch.object(
            train_research,
            "load_first_observed_quote",
            return_value=delayed,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("TIMEOUT_DELAYED_TO_NEXT_QUOTE", result["reason"])
        self.assertEqual(delayed["timestamp"], result["exit_time"])
        self.assertAlmostEqual(delayed["bid"]["open"], result["exit_price"])

    def test_filler_at_entry_cannot_be_treated_as_fill(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        path[0]["source_observed"] = False

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("NO_ENTRY_QUOTE", result["reason"])
        self.assertIsNone(result["r"])

    def test_incomplete_execution_path_is_rejected(self):
        start = datetime(2024, 1, 2, 10)
        path = flat_m1_rows(start, train_research.MAX_TRADE_MINUTES)
        del path[20]

        with patch.object(
            train_research,
            "load_grid_window",
            return_value=path,
        ):
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("DATA_GAP", result["reason"])

    def test_train_boundary_fires_before_m1_lookup(self):
        start = datetime(2024, 12, 31, 21)

        with patch.object(
            train_research,
            "load_grid_window",
        ) as mocked_load:
            result = train_research.execute_trade_m1(
                connection=None,
                symbol="EUR/USD",
                event=self.trade_event(start),
            )

        self.assertEqual("BOUNDARY_GUARD", result["reason"])
        mocked_load.assert_not_called()

    def test_setup_families_keep_independent_open_trade_state(self):
        start = datetime(2024, 1, 2, 10)
        breakout_events = [
            self.trade_event(start),
            self.trade_event(start + timedelta(minutes=30)),
        ]
        fakeout_events = [
            {
                **self.trade_event(start),
                "setup": strategy.SETUP_FAKEOUT,
            },
            {
                **self.trade_event(start + timedelta(minutes=30)),
                "setup": strategy.SETUP_FAKEOUT,
            },
        ]
        scan = {
            strategy.SETUP_BREAKOUT_RETEST: breakout_events,
            strategy.SETUP_FAKEOUT: fakeout_events,
            "diagnostics": {
                strategy.SETUP_BREAKOUT_RETEST: {},
                strategy.SETUP_FAKEOUT: {},
            },
        }

        def completed_trade(*, event, **_kwargs):
            return {
                "reason": "TIMEOUT",
                "r": 0.0,
                "exit_time": event["signal_time"] + timedelta(minutes=60),
            }

        with patch.object(
            train_research,
            "SYMBOLS",
            ("EUR/USD",),
        ), patch.object(
            train_research,
            "load_verified_m30",
            return_value=([], Counter()),
        ), patch.object(
            train_research.research_data,
            "m30_strategy_rows",
            return_value=[],
        ), patch.object(
            train_research,
            "generate_v4_events",
            return_value=scan,
        ), patch.object(
            train_research,
            "execute_trade_m1",
            side_effect=completed_trade,
        ) as mocked_execute:
            records, raw_events, diagnostics, _quality = (
                train_research.build_records(connection=None)
            )

        self.assertEqual(4, len(raw_events))
        self.assertEqual(2, len(records))
        self.assertEqual(2, mocked_execute.call_count)
        self.assertEqual(
            1,
            diagnostics["EUR/USD"][strategy.SETUP_BREAKOUT_RETEST][
                "SKIP_OPEN_TRADE"
            ],
        )
        self.assertEqual(
            1,
            diagnostics["EUR/USD"][strategy.SETUP_FAKEOUT][
                "SKIP_OPEN_TRADE"
            ],
        )

    def test_unresolved_filled_trade_blocks_later_same_family_events(self):
        start = datetime(2024, 1, 2, 10)
        scan = {
            strategy.SETUP_BREAKOUT_RETEST: [
                self.trade_event(start),
                self.trade_event(start + timedelta(days=1)),
            ],
            strategy.SETUP_FAKEOUT: [],
            "diagnostics": {
                strategy.SETUP_BREAKOUT_RETEST: {},
                strategy.SETUP_FAKEOUT: {},
            },
        }
        unresolved = {
            "reason": "NO_TIMEOUT_QUOTE_BEFORE_BOUNDARY",
            "r": None,
            "entry_time": start,
            "exit_time": None,
        }

        with patch.object(
            train_research,
            "SYMBOLS",
            ("EUR/USD",),
        ), patch.object(
            train_research,
            "load_verified_m30",
            return_value=([], Counter()),
        ), patch.object(
            train_research.research_data,
            "m30_strategy_rows",
            return_value=[],
        ), patch.object(
            train_research,
            "generate_v4_events",
            return_value=scan,
        ), patch.object(
            train_research,
            "execute_trade_m1",
            return_value=unresolved,
        ) as mocked_execute:
            records, _raw_events, diagnostics, _quality = (
                train_research.build_records(connection=None)
            )

        self.assertEqual(1, len(records))
        self.assertEqual(1, mocked_execute.call_count)
        self.assertEqual(
            1,
            diagnostics["EUR/USD"][strategy.SETUP_BREAKOUT_RETEST][
                "UNRESOLVED_OPEN_TRADE_TO_BOUNDARY"
            ],
        )
        self.assertEqual(
            1,
            diagnostics["EUR/USD"][strategy.SETUP_BREAKOUT_RETEST][
                "SKIP_OPEN_TRADE"
            ],
        )


class TrainEventAnatomyTests(unittest.TestCase):
    def test_filler_is_coverage_only_and_cannot_create_extreme(self):
        start = datetime(2024, 1, 2, 10)
        grid = flat_m1_rows(
            start,
            train_anatomy.PRIMARY_HORIZON_MINUTES,
        )
        grid[10] = m1_row(
            start + timedelta(minutes=10),
            open_price=1.1000,
            high=1.1200,
            low=1.0800,
            close=1.1000,
        )
        grid[10]["source_observed"] = False
        grid[11] = m1_row(
            start + timedelta(minutes=11),
            open_price=1.1000,
            high=1.1006,
            low=1.0999,
            close=1.1005,
        )

        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=grid,
        ):
            result = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )

        self.assertEqual("EVALUATED", result["status"])
        self.assertEqual(29, result["observed_quotes_30m"])
        self.assertEqual(1, result["filler_minutes_30m"])
        self.assertAlmostEqual(0.6, result["mfe_30m_atr"])
        self.assertEqual(12, result["time_to_mfe_30m"])
        self.assertEqual("FAVORABLE", result["first_passage_050atr"])
        self.assertEqual(12, result["first_passage_050atr_minute"])

    def test_filler_at_event_entry_rejects_anatomy_entry(self):
        start = datetime(2024, 1, 2, 10)
        grid = flat_m1_rows(
            start,
            train_anatomy.PRIMARY_HORIZON_MINUTES,
        )
        grid[0]["source_observed"] = False

        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=grid,
        ):
            result = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )

        self.assertEqual("NO_ENTRY_QUOTE", result["status"])
        self.assertNotIn("event_entry_mid", result)

    def test_sell_anatomy_reverses_direction_and_keeps_real_minute(self):
        start = datetime(2024, 1, 2, 10)
        grid = flat_m1_rows(
            start,
            train_anatomy.PRIMARY_HORIZON_MINUTES,
        )
        grid[4] = m1_row(
            start + timedelta(minutes=4),
            open_price=1.1000,
            high=1.1001,
            low=1.0994,
            close=1.0995,
        )

        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=grid,
        ):
            result = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start, direction="SELL"),
            )

        self.assertEqual("EVALUATED", result["status"])
        self.assertAlmostEqual(0.6, result["mfe_30m_atr"])
        self.assertEqual(5, result["time_to_mfe_30m"])
        self.assertEqual("FAVORABLE", result["first_passage_050atr"])
        self.assertEqual(5, result["first_passage_050atr_minute"])

    def test_endpoint_age_reports_trailing_fillers_without_using_them(self):
        start = datetime(2024, 1, 2, 10)
        grid = flat_m1_rows(
            start,
            train_anatomy.PRIMARY_HORIZON_MINUTES,
        )
        grid[28]["source_observed"] = False
        grid[29]["source_observed"] = False

        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=grid,
        ):
            result = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )

        self.assertEqual(28, result["observed_quotes_30m"])
        self.assertEqual(2, result["filler_minutes_30m"])
        self.assertEqual(2, result["endpoint_quote_age_30m"])

    def test_anatomy_boundary_guard_fires_before_source_lookup(self):
        start = strategy.TRAIN_END - timedelta(minutes=180)

        with patch.object(
            train_anatomy,
            "load_event_grid",
        ) as mocked_load:
            result = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )

        self.assertEqual("BOUNDARY_GUARD", result["status"])
        mocked_load.assert_not_called()

    def test_anatomy_source_gap_fails_closed(self):
        start = datetime(2024, 1, 2, 10)

        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=[],
        ):
            result = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )

        self.assertEqual("SOURCE_GRID_GAP", result["status"])

    def test_anatomy_does_not_use_row_after_maximum_horizon(self):
        start = datetime(2024, 1, 2, 10)
        baseline = flat_m1_rows(
            start,
            train_anatomy.PRIMARY_HORIZON_MINUTES,
        )
        future = baseline + [
            m1_row(
                start + timedelta(
                    minutes=train_anatomy.PRIMARY_HORIZON_MINUTES
                ),
                open_price=1.1000,
                high=1.1500,
                low=1.0500,
                close=1.1400,
            )
        ]

        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=baseline,
        ):
            expected = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )
        with patch.object(
            train_anatomy,
            "load_event_grid",
            return_value=future,
        ):
            rejected = train_anatomy.analyze_verified_event(
                connection=None,
                symbol="EUR/USD",
                event=event(start),
            )

        self.assertEqual("EVALUATED", expected["status"])
        self.assertEqual("SOURCE_GRID_GAP", rejected["status"])

    def test_anatomy_summary_keeps_attrition_out_of_price_statistics(self):
        start = datetime(2024, 1, 2, 10)
        evaluated = {
            "status": "EVALUATED",
            "signal_time": start,
            "fr_180m_atr": 0.4,
            "mfe_180m_atr": 0.8,
            "mae_180m_atr": 0.2,
            "observed_quotes_180m": 179,
            "first_passage_025atr": "FAVORABLE",
            "first_passage_050atr": "FAVORABLE",
            "first_passage_100atr": "NONE",
        }
        rejected = {
            "status": "NO_ENTRY_QUOTE",
            "signal_time": start + timedelta(minutes=30),
        }

        result = train_anatomy.summarize_group([evaluated, rejected])

        self.assertEqual(2, result["events"])
        self.assertEqual(1, result["evaluated"])
        self.assertEqual({"EVALUATED": 1, "NO_ENTRY_QUOTE": 1}, result["attrition"])
        self.assertAlmostEqual(0.4, result["mean_fr_atr"])


class EventAnatomyTests(unittest.TestCase):
    def test_buy_event_reports_forward_mfe_mae_and_first_passage(self):
        start = datetime(2021, 1, 4, 10)
        rows = [
            m1_row(start, open_price=1.1000, high=1.1002, low=1.0998, close=1.1001),
            m1_row(
                start + timedelta(minutes=1),
                open_price=1.1001,
                high=1.1006,
                low=1.1000,
                close=1.1005,
            ),
            m1_row(
                start + timedelta(minutes=2),
                open_price=1.1005,
                high=1.1007,
                low=1.0997,
                close=1.0998,
            ),
            m1_row(
                start + timedelta(minutes=3),
                open_price=1.0998,
                high=1.1005,
                low=1.0998,
                close=1.1004,
            ),
        ]

        result = anatomy.analyze_event(
            symbol="EUR/USD",
            event=event(start),
            m1_index=anatomy.index_m1(rows),
            split_end=strategy.TRAIN_END,
            horizons=(2, 4),
        )

        self.assertEqual("EVALUATED", result["status"])
        self.assertEqual(1.1001, result["signal_close_price"])
        self.assertEqual(1.1000, result["event_entry_mid"])
        self.assertAlmostEqual(0.4, result["fr_4m_atr"], places=7)
        self.assertAlmostEqual(0.7, result["mfe_4m_atr"], places=7)
        self.assertAlmostEqual(0.3, result["mae_4m_atr"], places=7)
        self.assertEqual(3, result["time_to_mfe_4m"])
        self.assertEqual(3, result["time_to_mae_4m"])
        self.assertEqual("FAVORABLE", result["first_passage_025atr"])
        self.assertEqual("FAVORABLE", result["first_passage_050atr"])
        self.assertEqual("NONE", result["first_passage_100atr"])

    def test_sell_event_reverses_directional_return_and_excursions(self):
        start = datetime(2021, 1, 4, 10)
        rows = [
            m1_row(start, open_price=1.1000, high=1.1002, low=1.0998, close=1.1001),
            m1_row(
                start + timedelta(minutes=1),
                open_price=1.1001,
                high=1.1003,
                low=1.0994,
                close=1.0995,
            ),
        ]

        result = anatomy.analyze_event(
            symbol="EUR/USD",
            event=event(start, direction="SELL"),
            m1_index=anatomy.index_m1(rows),
            split_end=strategy.TRAIN_END,
            horizons=(2,),
        )

        self.assertAlmostEqual(0.5, result["fr_2m_atr"], places=7)
        self.assertAlmostEqual(0.6, result["mfe_2m_atr"], places=7)
        self.assertAlmostEqual(0.3, result["mae_2m_atr"], places=7)

    def test_same_m1_first_passage_is_marked_ambiguous(self):
        start = datetime(2021, 1, 4, 10)
        rows = [
            m1_row(start, open_price=1.1000, high=1.1006, low=1.0994, close=1.1000),
        ]

        result = anatomy.first_passage(
            rows,
            direction="BUY",
            entry=1.1000,
            atr=0.0010,
            threshold_atr=0.5,
        )

        self.assertEqual(("AMBIGUOUS_M1", 1), result)

    def test_event_outcome_cannot_cross_split_boundary(self):
        signal_time = strategy.TRAIN_END - timedelta(minutes=120)
        rows = flat_m1_rows(signal_time, 180)

        result = anatomy.analyze_event(
            symbol="EUR/USD",
            event=event(signal_time),
            m1_index=anatomy.index_m1(rows),
            split_end=strategy.TRAIN_END,
        )

        self.assertEqual("BOUNDARY_GUARD", result["status"])
        self.assertNotIn("event_entry_mid", result)

    def test_validation_is_locked_by_default(self):
        signal_time = datetime(2025, 6, 1)

        self.assertEqual((None, None), anatomy.event_cohort(signal_time))
        self.assertEqual(
            ("VALIDATION_2025", strategy.HOLDOUT_START),
            anatomy.event_cohort(signal_time, unlock_validation=True),
        )

    def test_locked_build_does_not_pass_2025_rows_to_scanner_or_m1_index(self):
        train_open = datetime(2024, 12, 31, 23, 0)
        boundary_open = datetime(2024, 12, 31, 23, 30)
        validation_open = datetime(2025, 1, 1, 0, 0)
        m30 = [
            canonical_m30(train_open),
            canonical_m30(boundary_open),
            canonical_m30(validation_open),
        ]
        m1 = [
            m1_row(
                datetime(2024, 12, 31, 23, 59),
                open_price=1.1000,
                high=1.1001,
                low=1.0999,
                close=1.1000,
            ),
            m1_row(
                datetime(2025, 1, 1, 0, 0),
                open_price=9.0000,
                high=9.0001,
                low=8.9999,
                close=9.0000,
            ),
        ]
        captured = {}

        def fake_scan(rows):
            captured["scanner_times"] = [row["_time"] for row in rows]
            return {
                strategy.SETUP_BREAKOUT_RETEST: [],
                strategy.SETUP_FAKEOUT: [],
                "diagnostics": {
                    strategy.SETUP_BREAKOUT_RETEST: {},
                    strategy.SETUP_FAKEOUT: {},
                },
            }

        original_index = anatomy.index_m1

        def capture_index(rows):
            captured["m1_times"] = [row["timestamp"] for row in rows]
            return original_index(rows)

        with patch.object(anatomy, "generate_v4_events", side_effect=fake_scan), patch.object(
            anatomy,
            "index_m1",
            side_effect=capture_index,
        ):
            anatomy.build_event_anatomy(
                symbol="EUR/USD",
                m30_rows=m30,
                m1_rows=m1,
            )

        self.assertEqual([train_open], captured["scanner_times"])
        self.assertEqual(
            [datetime(2024, 12, 31, 23, 59)],
            captured["m1_times"],
        )

    def test_event_anatomy_has_no_dependency_after_requested_horizon(self):
        start = datetime(2021, 1, 4, 10)
        prefix = flat_m1_rows(start, 180)
        extended = prefix + [
            m1_row(
                start + timedelta(minutes=180),
                open_price=5.0000,
                high=6.0000,
                low=4.0000,
                close=5.0000,
            )
        ]

        prefix_result = anatomy.analyze_event(
            symbol="EUR/USD",
            event=event(start),
            m1_index=anatomy.index_m1(prefix),
            split_end=strategy.TRAIN_END,
        )
        extended_result = anatomy.analyze_event(
            symbol="EUR/USD",
            event=event(start),
            m1_index=anatomy.index_m1(extended),
            split_end=strategy.TRAIN_END,
        )

        self.assertEqual(prefix_result, extended_result)

    def test_build_anatomy_preserves_overlapping_events(self):
        start = datetime(2021, 1, 4, 10)
        first = event(start)
        second = event(start + timedelta(minutes=30))
        scan = {
            strategy.SETUP_BREAKOUT_RETEST: [first, second],
            strategy.SETUP_FAKEOUT: [],
            "diagnostics": {
                strategy.SETUP_BREAKOUT_RETEST: {},
                strategy.SETUP_FAKEOUT: {},
            },
        }
        m30 = [canonical_m30(start - timedelta(minutes=30))]
        m1 = flat_m1_rows(start, 210)

        with patch.object(anatomy, "generate_v4_events", return_value=scan):
            records, _ = anatomy.build_event_anatomy(
                symbol="EUR/USD",
                m30_rows=m30,
                m1_rows=m1,
            )

        self.assertEqual(2, len(records))
        self.assertTrue(all(row["status"] == "EVALUATED" for row in records))
        self.assertNotEqual(records[0]["event_id"], records[1]["event_id"])

    def test_day_block_bootstrap_is_deterministic_and_keeps_day_blocks(self):
        start = datetime(2021, 1, 4, 10)
        records = [
            {
                "status": "EVALUATED",
                "signal_time": start,
                "fr_180m_atr": 1.0,
            },
            {
                "status": "EVALUATED",
                "signal_time": start + timedelta(hours=1),
                "fr_180m_atr": 3.0,
            },
            {
                "status": "EVALUATED",
                "signal_time": start + timedelta(days=1),
                "fr_180m_atr": -2.0,
            },
        ]

        first = anatomy.day_block_bootstrap_mean(records, replications=100, seed=7)
        second = anatomy.day_block_bootstrap_mean(records, replications=100, seed=7)

        self.assertEqual(first, second)
        self.assertEqual(2, first["event_days"])
        self.assertAlmostEqual(2.0 / 3.0, first["point"], places=7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
