import lzma
import struct
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import v4_dukascopy_forensic_audit as forensic
import v4_event_anatomy as anatomy
import v4_research_data as research_data
import v4_event_strategy as strategy


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
