"""Read-only VALIDATION-2025 event research runner.

Runs the FROZEN candidate strategy (FAKEOUT, hold=360min, target_r=1.5,
07:00-16:00 UTC session, both symbols combined) against the VALIDATION-2025
database only. Never touches the TRAIN database. This is a one-shot,
look-only-once check per the locked research protocol -- the candidate and
its decision criteria were written down before this script ever ran.
"""

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import v4_dukascopy_train_builder as train_builder
import v4_research_data as research_data
import v4_train_event_research as ter
from v4_event_strategy import SETUP_FAKEOUT, generate_v4_events

VALIDATION_DB = Path("/tmp/v4_dukascopy_validation/v4_validation_m1.sqlite3")
VALIDATION_MANIFEST = Path("/tmp/v4_dukascopy_validation/manifest.json")
VALIDATION_CONTEXT_START = datetime(2024, 12, 1)
VALIDATION_START = datetime(2025, 1, 1)
VALIDATION_END = datetime(2026, 1, 1)

HOLD_MINUTES = 360
TARGET_R = 1.5
SESSION_START_HOUR = 7
SESSION_END_HOUR = 16
SETUP = SETUP_FAKEOUT

train_builder.CONTEXT_START = VALIDATION_CONTEXT_START
train_builder.END_EXCLUSIVE = VALIDATION_END
ter.TRAIN_END = VALIDATION_END
ter.MAX_TRADE_MINUTES = HOLD_MINUTES
ter.TAKE_PROFIT_R_MULTIPLE = TARGET_R


def open_validation_database():
    manifest = json.loads(VALIDATION_MANIFEST.read_text())
    if Path(manifest.get("database_path", "")) != VALIDATION_DB:
        raise RuntimeError("VALIDATION database path mismatch")
    if not VALIDATION_DB.is_file():
        raise RuntimeError("VALIDATION database is missing")
    actual_hash = research_data.sha256_file(VALIDATION_DB)
    if actual_hash != manifest["database_sha256"]:
        raise RuntimeError("VALIDATION database SHA256 mismatch")

    connection = sqlite3.connect(f"file:{VALIDATION_DB}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"VALIDATION database integrity failure: {integrity}")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        for key, expected in train_builder._metadata_values().items():
            if metadata.get(key) != expected:
                raise RuntimeError(
                    f"VALIDATION database metadata mismatch for {key}: "
                    f"{metadata.get(key)!r} != {expected!r}"
                )
        days = []
        current = VALIDATION_CONTEXT_START
        while current < VALIDATION_END:
            days.append(current)
            current += timedelta(days=1)
        expected_keys = {
            (symbol, day.strftime("%Y-%m-%d"))
            for symbol in ter.SYMBOLS
            for day in days
        }
        missing = train_builder.validate_database(connection, expected_keys)
        if missing:
            raise RuntimeError(f"VALIDATION database is incomplete: {missing[:5]}")
    except Exception:
        connection.close()
        raise
    return connection, manifest


def build_validation_records(connection):
    records = []
    for symbol in ter.SYMBOLS:
        m30_rows, _quality = ter.load_verified_m30(connection, symbol)
        strategy_rows = research_data.m30_strategy_rows(m30_rows, side="mid")
        scan = generate_v4_events(strategy_rows)
        next_allowed_entry = datetime.min
        for event in scan[SETUP]:
            signal_time = research_data.parse_utc(event["signal_time"])
            if not VALIDATION_START <= signal_time < VALIDATION_END:
                continue
            if not (SESSION_START_HOUR <= signal_time.hour < SESSION_END_HOUR):
                continue
            if signal_time < next_allowed_entry:
                continue
            trade = ter.execute_trade_m1(
                connection=connection,
                symbol=symbol,
                event=event,
                split_end=VALIDATION_END,
            )
            if trade.get("exit_time") is not None:
                next_allowed_entry = trade["exit_time"]
            elif trade.get("entry_time") is not None:
                next_allowed_entry = VALIDATION_END
            records.append(
                {
                    "symbol": symbol,
                    "setup": SETUP,
                    "direction": event["direction"],
                    "signal_time": signal_time,
                    "year": signal_time.year,
                    **trade,
                }
            )
    return records


def main():
    connection, _manifest = open_validation_database()
    try:
        print("=" * 100)
        print("V4 VALIDATION (2025) | FROZEN CANDIDATE | LOOK-ONLY-ONCE")
        print(
            f"SETUP={SETUP} | HOLD={HOLD_MINUTES}min | TARGET_R={TARGET_R} | "
            f"SESSION={SESSION_START_HOUR:02d}-{SESSION_END_HOUR:02d} UTC"
        )
        print("=" * 100)

        records = build_validation_records(connection)
        evaluated = [row for row in records if row.get("r") is not None]

        combined = ter.metrics(evaluated)
        print(
            f"ALL 2025            n={combined['n']:>5} wr={combined['wr']:>6.2f}% "
            f"pf={combined['pf']:>6.3f} avg_r={combined['avg_r']:>+7.3f} "
            f"net_r={combined['net_r']:>+8.2f} dd={combined['dd']:>7.2f}R"
        )
        print("-" * 100)

        for symbol in ter.SYMBOLS:
            symbol_records = [row for row in evaluated if row["symbol"] == symbol]
            m = ter.metrics(symbol_records)
            print(
                f"{symbol:<20} n={m['n']:>5} wr={m['wr']:>6.2f}% "
                f"pf={m['pf']:>6.3f} avg_r={m['avg_r']:>+7.3f} "
                f"net_r={m['net_r']:>+8.2f} dd={m['dd']:>7.2f}R"
            )

        print("-" * 100)
        for year in (2025,):
            year_records = [row for row in evaluated if row["year"] == year]
            m = ter.metrics(year_records)
            print(
                f"{year:<20} n={m['n']:>5} wr={m['wr']:>6.2f}% "
                f"pf={m['pf']:>6.3f} avg_r={m['avg_r']:>+7.3f} "
                f"net_r={m['net_r']:>+8.2f} dd={m['dd']:>7.2f}R"
            )

        print()
        print("V4_VALIDATION_2025_RESEARCH_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
