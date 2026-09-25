"""
Crypto trend signals (daily, long / flat, spot).

Strategy validated in V7 research (see V7_CRYPTO_GOLD_RESULT.md):
  ENTRY: daily close above the highest close of the previous 20 days
  EXIT:  daily close below the lowest close of the previous 10 days
No shorts, no leverage. One check per day, after the UTC daily candle closes.

Channels:
  VIP channel     : entry / exit signals (with chart) + daily trend board
  Free channel    : closed-trade results + weekly track record
  Private channel : data problems

Every signal is stored in SQLite, so the public track record is built only from
signals that were actually published (paper trading, costs included).
"""

import io
import os
import sqlite3
import time
from datetime import (
    datetime,
    timedelta,
    timezone,
)

import requests

from config import (
    TWELVE_DATA_API_KEY,
    CRYPTO_ASSETS,
    CRYPTO_ENTRY_DAYS,
    CRYPTO_EXIT_DAYS,
    CRYPTO_COST_PER_SIDE,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
PRIVATE_CHANNEL_ID = os.getenv("TELEGRAM_CHANNEL_ID")
FREE_CHANNEL_ID = os.getenv("TELEGRAM_FREE_CHANNEL_ID")
VIP_CHANNEL_ID = os.getenv("TELEGRAM_VIP_CHANNEL_ID")
DB_PATH = os.getenv("DB_PATH", "/app/data/trading.db")

API_URL = "https://api.twelvedata.com/time_series"
DATE_FORMAT = "%Y-%m-%d"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"

# The daily candle closes at 00:00 UTC. Wait a little so the data
# provider has the finished candle.
RUN_AFTER_UTC = (0, 10)
# If a coin still has no data by this hour, warn the private channel.
DATA_ALERT_AFTER_HOUR = 6
# History used to warm up the position state on the very first run.
HISTORY_DAYS = 300
# Twelve Data free plan: 8 requests / minute.
REQUEST_PAUSE_SECONDS = 8
WEEKLY_REPORT_WEEKDAY = 0      # Monday
WEEKLY_REPORT_AFTER_UTC = (0, 30)

BRAND = "<b>AS | Forex & Crypto</b>\n@ASForexCrypto"
# When the daily candle is not available yet, try again at most this often.
RETRY_SECONDS = 30 * 60

_last_attempt = {}


# =========================
# DATABASE
# =========================

def get_connection():
    connection = sqlite3.connect(DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_crypto_tables():
    with get_connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS crypto_positions (
                symbol TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                entry_date TEXT,
                entry_price REAL,
                from_launch INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS crypto_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                entry_date TEXT NOT NULL,
                entry_price REAL NOT NULL,
                exit_date TEXT NOT NULL,
                exit_price REAL NOT NULL,
                return_pct REAL NOT NULL,
                from_launch INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS crypto_signal_log (
                day TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                close REAL,
                next_entry_level REAL,
                next_exit_level REAL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (day, symbol)
            );
            CREATE TABLE IF NOT EXISTS crypto_report_log (
                report_key TEXT PRIMARY KEY,
                created_at TEXT NOT NULL
            );
            """
        )


def now_text():
    return datetime.now(timezone.utc).strftime(TIME_FORMAT)


def report_sent(key):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM crypto_report_log WHERE report_key = ?",
            (key,),
        ).fetchone()
    return row is not None


def mark_report_sent(key):
    with get_connection() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO crypto_report_log (report_key, created_at) VALUES (?, ?)",
            (key, now_text()),
        )


def symbol_processed(day, symbol):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT 1 FROM crypto_signal_log WHERE day = ? AND symbol = ?",
            (day, symbol),
        ).fetchone()
    return row is not None


def log_symbol(day, symbol, action, closes):
    # Levels that apply to TOMORROW's close (built from closes up to today).
    next_entry = max(closes[-CRYPTO_ENTRY_DAYS:])
    next_exit = min(closes[-CRYPTO_EXIT_DAYS:])
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO crypto_signal_log "
            "(day, symbol, action, close, next_entry_level, next_exit_level, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (day, symbol, action, closes[-1], next_entry, next_exit, now_text()),
        )


def get_position(symbol):
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM crypto_positions WHERE symbol = ?",
            (symbol,),
        ).fetchone()


def save_position(symbol, status, entry_date=None, entry_price=None, from_launch=0):
    with get_connection() as connection:
        connection.execute(
            "INSERT OR REPLACE INTO crypto_positions "
            "(symbol, status, entry_date, entry_price, from_launch, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (symbol, status, entry_date, entry_price, from_launch, now_text()),
        )


def save_trade(symbol, entry_date, entry_price, exit_date, exit_price, from_launch):
    return_pct = trade_return_pct(entry_price, exit_price)
    with get_connection() as connection:
        connection.execute(
            "INSERT INTO crypto_trades (symbol, entry_date, entry_price, exit_date, "
            "exit_price, return_pct, from_launch, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (symbol, entry_date, entry_price, exit_date, exit_price, return_pct,
             from_launch, now_text()),
        )
    return return_pct


def get_trades():
    with get_connection() as connection:
        return connection.execute(
            "SELECT * FROM crypto_trades ORDER BY exit_date, id"
        ).fetchall()


# =========================
# STRATEGY
# =========================

def trade_return_pct(entry_price, exit_price):
    gross = exit_price / entry_price
    net = gross * (1 - CRYPTO_COST_PER_SIDE) ** 2
    return (net - 1) * 100


def channel_levels(closes):
    """Levels for the LAST close, built from the previous closes only."""
    entry_level = max(closes[-CRYPTO_ENTRY_DAYS - 1:-1])
    exit_level = min(closes[-CRYPTO_EXIT_DAYS - 1:-1])
    return entry_level, exit_level


def decide(in_position, closes):
    """Return 'ENTRY', 'EXIT' or 'HOLD' / 'WAIT' for the last close."""
    if len(closes) < CRYPTO_ENTRY_DAYS + 1:
        return "WAIT"
    entry_level, exit_level = channel_levels(closes)
    close = closes[-1]
    if not in_position and close > entry_level:
        return "ENTRY"
    if in_position and close < exit_level:
        return "EXIT"
    return "HOLD" if in_position else "WAIT"


def replay_state(closes):
    """Walk the rule through history; True if the rule is long at the end."""
    in_position = False
    for end in range(CRYPTO_ENTRY_DAYS + 1, len(closes) + 1):
        action = decide(in_position, closes[:end])
        if action == "ENTRY":
            in_position = True
        elif action == "EXIT":
            in_position = False
    return in_position


# =========================
# MARKET DATA
# =========================

def fetch_daily_closes(symbol, outputsize):
    """[(date_text, close), ...] old -> new, closed days only."""
    params = {
        "symbol": symbol,
        "interval": "1day",
        "outputsize": outputsize,
        "timezone": "UTC",
        "apikey": TWELVE_DATA_API_KEY,
    }
    response = requests.get(API_URL, params=params, timeout=20)
    response.raise_for_status()
    data = response.json()
    if data.get("status") == "error":
        raise RuntimeError(data.get("message", "Twelve Data API error"))
    values = data.get("values") or []
    today = datetime.now(timezone.utc).strftime(DATE_FORMAT)
    rows = []
    for value in values:
        day = value["datetime"][:10]
        if day >= today:
            continue            # today's candle is still open
        rows.append((day, float(value["close"])))
    rows.sort()
    return rows


# =========================
# TELEGRAM
# =========================

def send_text(channel_id, text):
    if not BOT_TOKEN or not channel_id:
        print("CRYPTO SIGNALS WARNING | Telegram token or channel ID is missing", flush=True)
        return False
    try:
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": channel_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        return bool(response.json().get("ok"))
    except Exception as error:
        print(f"CRYPTO TELEGRAM ERROR | {type(error).__name__}: {error}", flush=True)
        return False


def send_photo(channel_id, caption, image):
    if not BOT_TOKEN or not channel_id or image is None:
        return send_text(channel_id, caption)
    try:
        image.seek(0)
        response = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
            data={"chat_id": channel_id, "caption": caption, "parse_mode": "HTML"},
            files={"photo": ("crypto_signal.png", image, "image/png")},
            timeout=30,
        )
        if response.json().get("ok"):
            return True
    except Exception as error:
        print(f"CRYPTO TELEGRAM PHOTO ERROR | {type(error).__name__}: {error}", flush=True)
    return send_text(channel_id, caption)


def create_chart(symbol, rows, action):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return None
    rows = rows[-120:]
    days = [datetime.strptime(d, DATE_FORMAT) for d, _ in rows]
    closes = [c for _, c in rows]
    highs, lows = [], []
    for i in range(len(closes)):
        if i < CRYPTO_ENTRY_DAYS:
            highs.append(None)
            lows.append(None)
            continue
        highs.append(max(closes[i - CRYPTO_ENTRY_DAYS:i]))
        lows.append(min(closes[i - CRYPTO_EXIT_DAYS:i]))
    fig, ax = plt.subplots(figsize=(9, 4.8), dpi=120)
    fig.patch.set_facecolor("#0f172a")
    ax.set_facecolor("#0f172a")
    ax.plot(days, closes, color="#e2e8f0", linewidth=1.6, label="Daily close")
    ax.plot(days, highs, color="#22c55e", linewidth=1, linestyle="--",
            label=f"{CRYPTO_ENTRY_DAYS}-day high (entry)")
    ax.plot(days, lows, color="#ef4444", linewidth=1, linestyle="--",
            label=f"{CRYPTO_EXIT_DAYS}-day low (exit)")
    colour = "#22c55e" if action == "ENTRY" else "#ef4444"
    ax.scatter([days[-1]], [closes[-1]], color=colour, s=80, zorder=5)
    ax.set_title(f"{symbol} · {'BUY' if action == 'ENTRY' else 'EXIT'} · daily",
                 color="#f8fafc", fontsize=13, loc="left")
    ax.tick_params(colors="#94a3b8")
    for spine in ax.spines.values():
        spine.set_color("#334155")
    ax.grid(color="#1e293b")
    legend = ax.legend(loc="upper left", fontsize=8, facecolor="#0f172a", edgecolor="#334155")
    for text in legend.get_texts():
        text.set_color("#cbd5e1")
    fig.autofmt_xdate()
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buffer.seek(0)
    return buffer


def fmt_price(price):
    if price >= 1000:
        return f"{price:,.0f}"
    if price >= 1:
        return f"{price:,.2f}"
    return f"{price:.5f}"


def build_entry_text(symbol, day, close, exit_level):
    return (
        f"🟢 <b>{symbol} · BUY (spot)</b>\n\n"
        f"🎯 Entry (daily close {day}): <code>{fmt_price(close)}</code>\n"
        f"🚪 Exit rule: daily close below the {CRYPTO_EXIT_DAYS}-day low\n"
        f"   (today: <code>{fmt_price(exit_level)}</code>, moves up with the price)\n\n"
        f"📐 Strategy: {CRYPTO_ENTRY_DAYS}-day breakout · long only · no leverage\n"
        "⚖️ Split your crypto budget equally between signals — never all-in on one coin.\n\n"
        "<i>Trend-following: most trades are small losses, a few big trends pay for them. "
        "Not financial advice.</i>\n\n"
        f"{BRAND}"
    )


def build_exit_text(symbol, day, close, entry_date, entry_price, return_pct):
    icon = "✅" if return_pct > 0 else "❌"
    return (
        f"🔴 <b>{symbol} · EXIT</b>\n\n"
        f"📥 Entry {entry_date}: <code>{fmt_price(entry_price)}</code>\n"
        f"📤 Exit {day}: <code>{fmt_price(close)}</code>\n"
        f"{icon} Result: <b>{return_pct:+.1f}%</b> (after {CRYPTO_COST_PER_SIDE * 200:.1f}% costs)\n\n"
        f"{BRAND}"
    )


def build_result_text(symbol, entry_date, exit_date, return_pct):
    icon = "✅" if return_pct > 0 else "❌"
    return (
        f"{icon} <b>VIP signal closed · {symbol}</b>\n\n"
        f"{entry_date} → {exit_date}\n"
        f"Result: <b>{return_pct:+.1f}%</b> (costs included)\n\n"
        "Every VIP signal is published with its result — wins and losses.\n\n"
        f"{BRAND}"
    )


# =========================
# DAILY RUN
# =========================

def process_symbol(symbol, day):
    """Returns (action, rows, levels) or raises on data problems."""
    position = get_position(symbol)
    first_run = position is None
    outputsize = HISTORY_DAYS if first_run else CRYPTO_ENTRY_DAYS + 40
    rows = fetch_daily_closes(symbol, outputsize)
    if not rows or rows[-1][0] != day:
        raise RuntimeError(f"daily candle {day} not available yet (last: {rows[-1][0] if rows else None})")
    closes = [c for _, c in rows]
    close = closes[-1]

    if first_run:
        # Warm-up: find where the rule stands today without publishing old signals.
        # A position that is already open is tracked from TODAY's close (launch price).
        in_position = replay_state(closes)
        if in_position:
            save_position(symbol, "LONG", day, close, from_launch=1)
            action = "LAUNCH_LONG"
        else:
            save_position(symbol, "FLAT")
            action = "LAUNCH_FLAT"
        log_symbol(day, symbol, action, closes)
        return action, rows

    in_position = position["status"] == "LONG"
    action = decide(in_position, closes)
    if action == "ENTRY":
        save_position(symbol, "LONG", day, close)
        _, exit_level = channel_levels(closes)
        chart = create_chart(symbol, rows, "ENTRY")
        send_photo(VIP_CHANNEL_ID, build_entry_text(symbol, day, close, exit_level), chart)
    elif action == "EXIT":
        return_pct = save_trade(symbol, position["entry_date"], position["entry_price"],
                                day, close, position["from_launch"])
        save_position(symbol, "FLAT")
        chart = create_chart(symbol, rows, "EXIT")
        send_photo(VIP_CHANNEL_ID,
                   build_exit_text(symbol, day, close, position["entry_date"],
                                   position["entry_price"], return_pct), chart)
        send_text(FREE_CHANNEL_ID,
                  build_result_text(symbol, position["entry_date"], day, return_pct))
    log_symbol(day, symbol, action, closes)
    print(f"CRYPTO SIGNAL | {symbol} | {day} | {action} | close={close}", flush=True)
    return action, rows


def build_board_text(day, board):
    lines = [f"📋 <b>CRYPTO TREND BOARD · {day}</b>", ""]
    for symbol, status, close, entry_level, exit_level, entry_price in board:
        if status == "LONG":
            change = (close / entry_price - 1) * 100 if entry_price else 0
            lines.append(
                f"🟢 <b>{symbol}</b> in trade · {change:+.1f}% · exit below {fmt_price(exit_level)}"
            )
        else:
            gap = (entry_level / close - 1) * 100
            lines.append(
                f"⚪ <b>{symbol}</b> waiting · buy above {fmt_price(entry_level)} ({gap:+.1f}%)"
            )
    lines += ["", f"<i>Rules: buy on a close above the {CRYPTO_ENTRY_DAYS}-day high, "
                  f"exit on a close below the {CRYPTO_EXIT_DAYS}-day low.</i>", "", BRAND]
    return "\n".join(lines)


def process_crypto_signals():
    now = datetime.now(timezone.utc)
    if (now.hour, now.minute) < RUN_AFTER_UTC:
        return
    day = (now - timedelta(days=1)).strftime(DATE_FORMAT)
    pending = [s for s in CRYPTO_ASSETS if not symbol_processed(day, s)]
    if pending and time.time() - _last_attempt.get(day, 0) < RETRY_SECONDS:
        pending = []
    if pending:
        _last_attempt[day] = time.time()
    launch_lines = []
    failures = []
    for index, symbol in enumerate(pending):
        if index:
            time.sleep(REQUEST_PAUSE_SECONDS)
        try:
            action, _ = process_symbol(symbol, day)
            if action.startswith("LAUNCH"):
                launch_lines.append((symbol, action))
        except Exception as error:
            failures.append(symbol)
            print(f"CRYPTO SIGNAL ERROR | {symbol} | {type(error).__name__}: {error}", flush=True)

    if launch_lines:
        send_launch_message(day, launch_lines)

    if failures and now.hour >= DATA_ALERT_AFTER_HOUR and not report_sent(f"alert:{day}"):
        send_text(PRIVATE_CHANNEL_ID,
                  f"⚠️ <b>Crypto signals</b>: no daily data for {day} yet: {', '.join(failures)}")
        mark_report_sent(f"alert:{day}")

    all_done = all(symbol_processed(day, s) for s in CRYPTO_ASSETS)
    if all_done and not report_sent(f"board:{day}"):
        send_daily_board(day)
        mark_report_sent(f"board:{day}")

    send_weekly_report_if_due(now)


def send_launch_message(day, launch_lines):
    lines = ["🚀 <b>CRYPTO SIGNALS · SYSTEM START</b>", "",
             f"Strategy: buy on a daily close above the {CRYPTO_ENTRY_DAYS}-day high, "
             f"exit on a close below the {CRYPTO_EXIT_DAYS}-day low. Spot, long only.", ""]
    for symbol, action in launch_lines:
        position = get_position(symbol)
        if action == "LAUNCH_LONG":
            lines.append(f"🟢 <b>{symbol}</b> — already in an uptrend; tracked from "
                         f"{fmt_price(position['entry_price'])} ({day})")
        else:
            lines.append(f"⚪ <b>{symbol}</b> — no trend, waiting for a breakout")
    lines += ["", "<i>The track record starts today. Every signal and result is published.</i>",
              "", BRAND]
    send_text(VIP_CHANNEL_ID, "\n".join(lines))


def send_daily_board(day):
    board = []
    with get_connection() as connection:
        for symbol in CRYPTO_ASSETS:
            row = connection.execute(
                "SELECT close, next_entry_level, next_exit_level FROM crypto_signal_log "
                "WHERE day = ? AND symbol = ?",
                (day, symbol),
            ).fetchone()
            position = get_position(symbol)
            if row is None or position is None or row["next_entry_level"] is None:
                continue
            board.append((symbol, position["status"], row["close"], row["next_entry_level"],
                          row["next_exit_level"], position["entry_price"]))
    if board:
        send_text(VIP_CHANNEL_ID, build_board_text(day, board))


# =========================
# WEEKLY TRACK RECORD
# =========================

def track_record():
    trades = get_trades()
    sleeves = {symbol: 1.0 for symbol in CRYPTO_ASSETS}
    for trade in trades:
        sleeves[trade["symbol"]] = sleeves.get(trade["symbol"], 1.0) * (1 + trade["return_pct"] / 100)
    returns = [t["return_pct"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]
    return {
        "trades": len(returns),
        "wins": len(wins),
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": sum(losses) / len(losses) if losses else 0.0,
        "portfolio_pct": (sum(sleeves.values()) / len(sleeves) - 1) * 100 if sleeves else 0.0,
        "first_day": trades[0]["entry_date"] if trades else None,
    }


def send_weekly_report_if_due(now):
    if now.weekday() != WEEKLY_REPORT_WEEKDAY or (now.hour, now.minute) < WEEKLY_REPORT_AFTER_UTC:
        return
    key = f"weekly:{now.strftime(DATE_FORMAT)}"
    if report_sent(key):
        return
    stats = track_record()
    with get_connection() as connection:
        open_positions = connection.execute(
            "SELECT symbol, entry_date FROM crypto_positions WHERE status = 'LONG' ORDER BY symbol"
        ).fetchall()
    lines = ["📊 <b>AS · CRYPTO SIGNALS · WEEKLY TRACK RECORD</b>", ""]
    if stats["trades"] == 0:
        lines.append("No closed trades yet — trend trades can stay open for weeks.")
    else:
        lines += [
            f"Closed trades: <b>{stats['trades']}</b>",
            f"Winners: <b>{stats['wins']}</b> ({100 * stats['wins'] / stats['trades']:.0f}%)",
            f"Average win: <b>{stats['avg_win']:+.1f}%</b> · average loss: <b>{stats['avg_loss']:+.1f}%</b>",
            f"Equal-split portfolio (closed trades): <b>{stats['portfolio_pct']:+.1f}%</b>",
        ]
    lines += ["", f"Open trades: <b>{len(open_positions)}</b>"
              + (" · " + ", ".join(p["symbol"] for p in open_positions) if open_positions else "")]
    lines += ["", "<i>Paper track record of published signals, costs included. "
                  "Past results do not guarantee future results.</i>", "", BRAND]
    text = "\n".join(lines)
    send_text(FREE_CHANNEL_ID, text)
    send_text(VIP_CHANNEL_ID, text)
    mark_report_sent(key)
