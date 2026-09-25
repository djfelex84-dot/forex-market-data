import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

_tmp = tempfile.mkdtemp()
os.environ["DB_PATH"] = os.path.join(_tmp, "test.db")
os.environ["TELEGRAM_BOT_TOKEN"] = "test-token"
os.environ["TELEGRAM_FREE_CHANNEL_ID"] = "FREE"
os.environ["TELEGRAM_VIP_CHANNEL_ID"] = "VIP"
os.environ["TELEGRAM_CHANNEL_ID"] = "PRIVATE"

import crypto_signals as cs  # noqa: E402


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self.payload


class Market:
    """Serves daily candles for every symbol up to a movable 'now'."""

    def __init__(self, series):
        self.series = series          # symbol -> list of (date, close)
        self.now = None
        self.sent = []                # (method, chat_id, text)

    def get(self, url, params=None, timeout=None):
        rows = [r for r in self.series[params["symbol"]] if r[0] <= self.now.strftime("%Y-%m-%d")]
        rows = rows[-params["outputsize"]:]
        values = [{"datetime": d, "close": str(c)} for d, c in reversed(rows)]
        return FakeResponse({"values": values, "status": "ok"})

    def post(self, url, json=None, data=None, files=None, timeout=None):
        body = json or data
        self.sent.append((url.rsplit("/", 1)[-1], body["chat_id"], body.get("text") or body.get("caption")))
        return FakeResponse({"ok": True})


def make_series(start, closes):
    return [((start + timedelta(days=i)).strftime("%Y-%m-%d"), c) for i, c in enumerate(closes)]


class CryptoSignalFlowTest(unittest.TestCase):
    def setUp(self):
        if os.path.exists(os.environ["DB_PATH"]):
            os.remove(os.environ["DB_PATH"])
        cs.init_crypto_tables()
        cs._last_attempt.clear()
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        # 40 flat days, then a rally (entry), then a drop (exit), then flat.
        flat = [100.0] * 40
        rally = [101, 103, 106, 110, 115]
        drop = [112, 108, 104, 99, 95]
        tail = [95.0] * 10
        self.days = len(flat) + len(rally) + len(drop) + len(tail)
        base = make_series(start, flat + rally + drop + tail)
        # a second coin that never trends
        still = make_series(start, [50.0] * self.days)
        self.start = start
        self.market = Market({s: (base if s == "BTC/USD" else still) for s in cs.CRYPTO_ASSETS})

    def fake_datetime(self):
        market = self.market

        class FakeDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return market.now

        return FakeDateTime

    def run_day(self, index):
        """Run the engine at 00:15 UTC on the day after candle `index`."""
        self.market.now = self.start + timedelta(days=index + 1, minutes=15)
        cs._last_attempt.clear()
        with patch.object(cs.requests, "get", self.market.get), \
             patch.object(cs.requests, "post", self.market.post), \
             patch.object(cs.time, "sleep", lambda s: None), \
             patch.object(cs, "datetime", self.fake_datetime()):
            cs.process_crypto_signals()

    def texts(self, chat):
        return [t for _, c, t in self.market.sent if c == chat]

    def test_full_cycle(self):
        # Day 30: first run -> warm-up only, one launch message, no signals.
        self.run_day(30)
        vip = self.texts("VIP")
        self.assertTrue(any("SYSTEM START" in t for t in vip))
        self.assertFalse(any("· BUY" in t for t in vip))
        self.assertEqual(cs.get_position("BTC/USD")["status"], "FLAT")

        # Running twice on the same day sends nothing new.
        count = len(self.market.sent)
        self.run_day(30)
        self.assertEqual(len(self.market.sent), count)

        # Walk forward through the rally and drop.
        for day in range(31, self.days):
            self.run_day(day)

        vip = self.texts("VIP")
        buys = [t for t in vip if "BTC/USD · BUY" in t]
        exits = [t for t in vip if "BTC/USD · EXIT" in t]
        self.assertEqual(len(buys), 1, "exactly one entry signal")
        self.assertEqual(len(exits), 1, "exactly one exit signal")
        self.assertIn("101", buys[0])                      # first close above the 20-day high
        self.assertTrue(any("CRYPTO TREND BOARD" in t for t in vip))

        trades = cs.get_trades()
        self.assertEqual(len(trades), 1)
        self.assertEqual(trades[0]["entry_price"], 101)
        # 104 is still above the previous 10-day low (100); 99 is the first close below it.
        self.assertEqual(trades[0]["exit_price"], 99)
        self.assertAlmostEqual(trades[0]["return_pct"], (99 / 101 * 0.999 ** 2 - 1) * 100)

        free = self.texts("FREE")
        self.assertTrue(any("VIP signal closed · BTC/USD" in t for t in free))
        self.assertFalse(any("· BUY" in t for t in free), "entries stay VIP-only")
        # Other coins never trended.
        self.assertFalse(any("ETH/USD · BUY" in t for t in vip))

    def test_weekly_report_on_monday(self):
        self.run_day(30)
        monday = next(i for i in range(31, 45)
                      if (self.start + timedelta(days=i + 1)).weekday() == 0)
        self.run_day(monday - 1)
        self.market.now = self.start + timedelta(days=monday + 1, hours=1)
        with patch.object(cs.requests, "post", self.market.post):
            cs.send_weekly_report_if_due(self.market.now)
            cs.send_weekly_report_if_due(self.market.now)
        weekly = [t for t in self.texts("FREE") if "WEEKLY TRACK RECORD" in t]
        self.assertEqual(len(weekly), 1)

    def test_missing_candle_is_retried_not_skipped(self):
        self.run_day(30)
        self.market.series["BTC/USD"] = [r for r in self.market.series["BTC/USD"] if r[0] != (self.start + timedelta(days=31)).strftime("%Y-%m-%d")]
        self.run_day(31)
        self.assertFalse(cs.symbol_processed((self.start + timedelta(days=31)).strftime("%Y-%m-%d"), "BTC/USD"))


if __name__ == "__main__":
    unittest.main()
