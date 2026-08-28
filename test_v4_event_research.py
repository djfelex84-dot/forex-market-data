import io
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import v4_event_comparison_backtest as backtest
import v4_event_strategy as strategy


def candle(timestamp, open_price, high, low, close):
    return {
        "datetime": timestamp.strftime(strategy.TIME_FORMAT),
        "_time": timestamp,
        "open": float(open_price),
        "high": float(high),
        "low": float(low),
        "close": float(close),
    }


def range_rows(start, count, *, high=1.1010, low=1.0990, close=1.1000):
    return [
        candle(
            start + timedelta(minutes=strategy.M30_MINUTES * index),
            close - 0.0001,
            high,
            low,
            close,
        )
        for index in range(count)
    ]


def create_database(path, rows_by_symbol):
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE candles_30m (
            symbol TEXT, datetime TEXT, open REAL, high REAL, low REAL, close REAL
        )
        """
    )
    for symbol, rows in rows_by_symbol.items():
        for row in rows:
            connection.execute(
                "INSERT INTO candles_30m VALUES (?, ?, ?, ?, ?, ?)",
                (
                    symbol,
                    row["datetime"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                ),
            )
    connection.commit()
    connection.close()


class StrategyScannerTests(unittest.TestCase):
    def test_breakout_retest_does_not_require_an_unrequested_h1_filter(self):
        start = datetime(2021, 1, 4)
        rows = range_rows(start, strategy.ATR_PERIOD + strategy.RANGE_LOOKBACK)
        breakout_time = start + timedelta(minutes=30 * len(rows))
        rows.extend(
            [
                candle(breakout_time, 1.1005, 1.1020, 1.1004, 1.1018),
                candle(breakout_time + timedelta(minutes=30), 1.1017, 1.1019, 1.10095, 1.1013),
                candle(breakout_time + timedelta(minutes=60), 1.1012, 1.1021, 1.1010, 1.1020),
            ]
        )

        scan = strategy.generate_v4_events(rows)
        events = scan[strategy.SETUP_BREAKOUT_RETEST]

        self.assertEqual(1, len(events))
        self.assertEqual("BUY", events[0]["direction"])
        self.assertNotIn("h1_trend", events[0])
        self.assertEqual(rows[-1]["_time"] + timedelta(minutes=30), events[0]["signal_time"])

    def test_fakeout_uses_previous_day_low_and_one_confirmed_signal_per_level(self):
        day_one = datetime(2021, 1, 4)
        rows = range_rows(day_one, 48)
        day_two = datetime(2021, 1, 5)
        rows.extend(
            [
                candle(day_two, 1.0993, 1.0996, 1.0988, 1.0994),
                candle(day_two + timedelta(minutes=30), 1.0994, 1.1001, 1.0993, 1.1000),
                candle(day_two + timedelta(minutes=60), 1.1000, 1.1002, 1.0995, 1.0998),
                candle(day_two + timedelta(minutes=90), 1.0994, 1.0996, 1.0987, 1.0994),
                candle(day_two + timedelta(minutes=120), 1.0994, 1.1002, 1.0993, 1.1001),
            ]
        )

        scan = strategy.generate_v4_events(rows)
        events = scan[strategy.SETUP_FAKEOUT]

        self.assertEqual(1, len(events))
        self.assertEqual("BUY", events[0]["direction"])
        self.assertEqual("PDL::2021-01-04", events[0]["source"])
        self.assertLess(events[0]["stop_price"], 1.0988)

    def test_short_sunday_does_not_replace_friday_as_previous_day(self):
        friday = datetime(2021, 1, 1)
        rows = range_rows(friday, 48, high=1.1050, low=1.0950)
        sunday = datetime(2021, 1, 3, 22, 0)
        rows.extend(range_rows(sunday, 4, high=1.1020, low=1.1010, close=1.1015))
        monday = datetime(2021, 1, 4)
        rows.append(candle(monday, 1.1015, 1.1020, 1.1010, 1.1016))

        levels = strategy._previous_day_levels(rows)

        self.assertEqual(datetime(2021, 1, 1).date(), levels[monday.date()]["source_day"])
        self.assertEqual(1.1050, levels[monday.date()]["high"])
        self.assertEqual(1.0950, levels[monday.date()]["low"])

    def test_atr_values_have_no_future_dependency(self):
        start = datetime(2021, 1, 4)
        rows = range_rows(start, 30)
        prefix = strategy._wilder_atr_series(rows)
        rows.append(candle(start + timedelta(hours=15), 1.1000, 1.2000, 1.0000, 1.1500))
        extended = strategy._wilder_atr_series(rows)

        self.assertEqual(prefix, extended[:-1])


class BacktestSafetyTests(unittest.TestCase):
    def test_sql_read_excludes_candle_whose_close_reaches_2026(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "history.db"
            rows = [
                candle(datetime(2025, 12, 31, 23, 0), 1.1, 1.2, 1.0, 1.1),
                candle(datetime(2025, 12, 31, 23, 30), 1.1, 1.2, 1.0, 1.1),
                candle(datetime(2026, 1, 1), 1.1, 1.2, 1.0, 1.1),
            ]
            create_database(db_path, {"EUR/USD": rows})
            connection = sqlite3.connect(db_path)
            loaded = backtest.load_m30(connection, "EUR/USD")
            connection.close()

        self.assertEqual(1, len(loaded))
        self.assertEqual(datetime(2025, 12, 31, 23, 0), loaded[0]["_time"])

    def test_ambiguous_execution_bar_is_counted_as_worst_case_stop(self):
        signal_open = datetime(2021, 1, 4, 10, 0)
        rows = [
            candle(signal_open, 1.1000, 1.1002, 1.0998, 1.1000),
            candle(signal_open + timedelta(minutes=30), 1.1000, 1.1020, 1.0980, 1.1005),
        ]
        event = {
            "direction": "BUY",
            "entry_price": 1.1000,
            "signal_time": signal_open + timedelta(minutes=30),
            "stop_price": 1.0990,
        }

        result = backtest.execute_trade(
            rows=rows,
            index_by_time={row["_time"]: index for index, row in enumerate(rows)},
            event=event,
            symbol="EUR/USD",
            split_end=backtest.TRAIN_END,
        )

        self.assertEqual("AMBIGUOUS_WORST_SL", result["reason"])
        self.assertAlmostEqual(-1.1, result["r"], places=7)

    def test_split_boundary_guard_fires_before_future_candle_lookup(self):
        signal_open = datetime(2024, 12, 31, 21, 30)
        rows = [candle(signal_open, 1.1000, 1.1002, 1.0998, 1.1000)]
        event = {
            "direction": "BUY",
            "entry_price": 1.1000,
            "signal_time": signal_open + timedelta(minutes=30),
            "stop_price": 1.0990,
        }

        result = backtest.execute_trade(
            rows=rows,
            index_by_time={signal_open: 0},
            event=event,
            symbol="EUR/USD",
            split_end=backtest.TRAIN_END,
        )

        self.assertEqual("BOUNDARY_GUARD", result["reason"])
        self.assertIsNone(result["r"])

    def test_stop_on_wrong_side_of_entry_is_rejected(self):
        signal_open = datetime(2021, 1, 4, 10, 0)
        rows = [candle(signal_open, 1.1000, 1.1002, 1.0998, 1.1000)]
        event = {
            "direction": "BUY",
            "entry_price": 1.1000,
            "signal_time": signal_open + timedelta(minutes=30),
            "stop_price": 1.1010,
        }

        result = backtest.execute_trade(
            rows=rows,
            index_by_time={signal_open: 0},
            event=event,
            symbol="EUR/USD",
            split_end=backtest.TRAIN_END,
        )

        self.assertEqual("INVALID_STOP", result["reason"])
        self.assertIsNone(result["r"])

    def test_setup_families_have_independent_one_open_state(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "history.db"
            signal_open = datetime(2021, 1, 4, 10, 0)
            rows = [candle(signal_open, 1.1000, 1.1002, 1.0998, 1.1000)]
            for step in range(1, 7):
                rows.append(
                    candle(
                        signal_open + timedelta(minutes=30 * step),
                        1.1000,
                        1.1020,
                        1.0995,
                        1.1015,
                    )
                )
            create_database(db_path, {symbol: rows for symbol in backtest.SYMBOLS})

            event = {
                "direction": "BUY",
                "entry_price": 1.1000,
                "signal_time": signal_open + timedelta(minutes=30),
                "stop_price": 1.0990,
                "source": "SYNTHETIC",
            }
            scan = {
                strategy.SETUP_BREAKOUT_RETEST: [dict(event)],
                strategy.SETUP_FAKEOUT: [dict(event)],
                "diagnostics": {
                    strategy.SETUP_BREAKOUT_RETEST: {},
                    strategy.SETUP_FAKEOUT: {},
                },
            }

            db_uri = f"file:{db_path}?mode=ro"
            with patch.object(backtest, "DB_URI", db_uri), patch.object(
                backtest, "generate_v4_events", return_value=scan
            ), redirect_stdout(io.StringIO()):
                records, _, raw_events = backtest.build_records()

        self.assertEqual(4, len(records))
        self.assertEqual(4, len(raw_events))
        self.assertTrue(all(row["r"] is not None for row in records))
        self.assertEqual(set(backtest.SETUPS), {row["setup"] for row in records})

    def test_full_pipeline_runs_on_read_only_synthetic_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "history.db"
            rows = range_rows(datetime(2021, 1, 4), 96)
            create_database(db_path, {symbol: rows for symbol in backtest.SYMBOLS})
            db_uri = f"file:{db_path}?mode=ro"

            output = io.StringIO()
            with patch.object(backtest, "DB_URI", db_uri), redirect_stdout(output):
                backtest.main()

        self.assertIn("V4_EVENT_COMPARISON_OK", output.getvalue())
        self.assertIn("2026 NOT READ", output.getvalue())
        self.assertIn("NOT A QUALITY GATE", output.getvalue())


class MetricTests(unittest.TestCase):
    def test_metrics_report_pf_average_net_and_chronological_drawdown(self):
        start = datetime(2021, 1, 1)
        records = [
            {"r": 1.5, "signal_time": start, "symbol": "EUR/USD"},
            {"r": -1.0, "signal_time": start + timedelta(minutes=30), "symbol": "EUR/USD"},
            {"r": -1.0, "signal_time": start + timedelta(minutes=60), "symbol": "EUR/USD"},
        ]

        result = backtest.metrics(records)

        self.assertEqual(3, result["n"])
        self.assertAlmostEqual(33.3333333, result["wr"], places=5)
        self.assertAlmostEqual(0.75, result["pf"], places=7)
        self.assertAlmostEqual(-0.5 / 3.0, result["avg_r"], places=7)
        self.assertAlmostEqual(-0.5, result["net_r"], places=7)
        self.assertAlmostEqual(2.0, result["dd"], places=7)


if __name__ == "__main__":
    unittest.main(verbosity=2)
