"""Reconstruct invalid V4 M30 candles from independently fetched M15 rows.

This script reads the production V4 database in query-only mode and fetches
only the calendar months containing invalid direct-M30 candles.  It never
writes to the database.  Reconstructed reference rows are cached under /tmp.
"""

import json
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import v4_history_integrity_audit as integrity


REFERENCE_INTERVAL = "15min"
CACHE_PATH = Path("/tmp/v4_history_m15_reconstruction.json")


def reconstruct_m30(rows_15m, timestamp):
    start = datetime.strptime(timestamp, integrity.TIME_FORMAT)
    second_timestamp = (start + timedelta(minutes=15)).strftime(
        integrity.TIME_FORMAT
    )
    first = rows_15m.get(timestamp)
    second = rows_15m.get(second_timestamp)
    if first is None or second is None:
        return None
    return (
        first[0],
        max(first[1], second[1]),
        min(first[2], second[2]),
        second[3],
    )


def compare_reconstructed(db_rows, reference_15m, months):
    missing_pairs = 0
    material_mismatches = 0
    over_one_pip = 0
    largest = []

    for row in db_rows:
        timestamp = row[0]
        if timestamp[:7] not in months:
            continue

        reconstructed = reconstruct_m30(reference_15m, timestamp)
        if reconstructed is None:
            missing_pairs += 1
            continue

        actual = integrity._ohlc(row)
        difference_pips = max(
            abs(actual_value - reference_value)
            for actual_value, reference_value in zip(actual, reconstructed)
        ) / integrity.PIP_SIZE

        if difference_pips > 0.1:
            material_mismatches += 1
        if difference_pips > 1.0:
            over_one_pip += 1
        if difference_pips > 0.1:
            largest.append((difference_pips, timestamp, actual, reconstructed))

    largest.sort(reverse=True)
    return {
        "missing_pairs": missing_pairs,
        "material_mismatches": material_mismatches,
        "over_one_pip": over_one_pip,
        "largest": largest,
    }


def main():
    api_key = os.getenv("TWELVE_DATA_RESEARCH_API_KEY") or os.getenv(
        "TWELVE_DATA_API_KEY"
    )
    if not api_key:
        raise RuntimeError("TWELVE_DATA_API_KEY is not configured")

    connection = sqlite3.connect(integrity.DB_URI, uri=True)
    connection.execute("PRAGMA query_only = ON")
    try:
        db_rows = {
            symbol: integrity.load_m30(connection, symbol)
            for symbol in integrity.SYMBOLS
        }
    finally:
        connection.close()

    invalid = {
        symbol: [
            row
            for row in db_rows[symbol]
            if integrity.invalid_geometry(integrity._ohlc(row))
        ]
        for symbol in integrity.SYMBOLS
    }
    affected = [
        (symbol, month)
        for symbol in integrity.SYMBOLS
        for month in sorted({row[0][:7] for row in invalid[symbol]})
    ]

    print("=" * 110)
    print("V4 M15 RECONSTRUCTION AUDIT | DATABASE READ-ONLY | NO REPAIR WRITES")
    print("=" * 110)
    for symbol in integrity.SYMBOLS:
        print(
            f"{symbol} | InvalidM30={len(invalid[symbol])} | "
            f"Months={sorted({row[0][:7] for row in invalid[symbol]})}"
        )

    if not affected:
        print("NO_INVALID_M30_TO_RECONSTRUCT")
        print("V4_M15_RECONSTRUCTION_AUDIT_OK")
        return

    fetched = {symbol: {} for symbol in integrity.SYMBOLS}
    last_request_started = None
    total_invalid_m15 = 0

    for position, (symbol, month) in enumerate(affected, start=1):
        if last_request_started is not None:
            elapsed = time.monotonic() - last_request_started
            wait_seconds = max(
                0.0,
                integrity.REQUEST_INTERVAL_SECONDS - elapsed,
            )
            if wait_seconds:
                time.sleep(wait_seconds)

        print(
            f"FETCH {position}/{len(affected)} | "
            f"{symbol} | {REFERENCE_INTERVAL} | {month}",
            flush=True,
        )
        last_request_started = time.monotonic()
        month_rows = integrity.fetch_month(
            api_key,
            symbol,
            month,
            interval=REFERENCE_INTERVAL,
        )
        fetched[symbol][month] = month_rows
        invalid_m15_rows = [
            (timestamp, values)
            for timestamp, values in month_rows.items()
            if integrity.invalid_geometry(values)
        ]
        total_invalid_m15 += len(invalid_m15_rows)
        print(
            f"FETCHED {symbol} {month} | Rows={len(month_rows)} | "
            f"InvalidM15={len(invalid_m15_rows)}",
            flush=True,
        )
        for timestamp, values in invalid_m15_rows[:20]:
            print(
                f"INVALID_M15 {symbol} | {timestamp} | OHLC={values}",
                flush=True,
            )

    cache_payload = {
        "source": "Twelve Data",
        "fetched_at_utc": datetime.now(timezone.utc).strftime(
            integrity.TIME_FORMAT
        ),
        "interval": REFERENCE_INTERVAL,
        "read_limit": integrity.READ_LIMIT,
        "symbols": {
            symbol: {
                month: {
                    timestamp: list(values)
                    for timestamp, values in sorted(month_rows.items())
                }
                for month, month_rows in fetched[symbol].items()
            }
            for symbol in integrity.SYMBOLS
        },
    }
    CACHE_PATH.write_text(
        json.dumps(cache_payload, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(f"REFERENCE_CACHE={CACHE_PATH}")

    print()
    print("INVALID M30 RECONSTRUCTION")
    print("=" * 110)

    total_invalid = 0
    total_reconstructable = 0
    total_missing_pairs = 0

    for symbol in integrity.SYMBOLS:
        reference_15m = {
            timestamp: values
            for month_rows in fetched[symbol].values()
            for timestamp, values in month_rows.items()
        }
        months = set(fetched[symbol])

        for row in invalid[symbol]:
            total_invalid += 1
            reconstructed = reconstruct_m30(reference_15m, row[0])
            valid = reconstructed is not None and not integrity.invalid_geometry(
                reconstructed
            )
            if valid:
                total_reconstructable += 1
            print(
                f"{symbol} | {row[0]} | DB={integrity._ohlc(row)} | "
                f"FROM_M15={reconstructed} | RECONSTRUCTED_VALID={valid}"
            )

        if not months:
            continue

        comparison = compare_reconstructed(
            db_rows[symbol],
            reference_15m,
            months,
        )
        total_missing_pairs += comparison["missing_pairs"]
        print()
        print(
            f"{symbol} M30-vs-M15 | MissingPairs={comparison['missing_pairs']} | "
            f">0.1pip={comparison['material_mismatches']} | "
            f">1pip={comparison['over_one_pip']}"
        )
        for difference, timestamp, actual, reconstructed in comparison[
            "largest"
        ][:40]:
            print(
                f"DIFF {difference:.3f} pips | {timestamp} | "
                f"DB={actual} | FROM_M15={reconstructed}"
            )

    print()
    print(
        f"RECONSTRUCTABLE_INVALID_M30={total_reconstructable}/{total_invalid}"
    )
    print("DATABASE_UNCHANGED")

    if total_invalid_m15:
        raise RuntimeError(
            f"M15 reference contains {total_invalid_m15} invalid candles"
        )
    if total_missing_pairs:
        raise RuntimeError(
            f"M15 reference is missing {total_missing_pairs} M30 candle pairs"
        )
    if total_reconstructable != total_invalid:
        raise RuntimeError(
            "Not every invalid M30 candle has a valid M15 reconstruction"
        )

    print("V4_M15_RECONSTRUCTION_AUDIT_OK")


if __name__ == "__main__":
    main()
