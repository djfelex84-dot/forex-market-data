"""Standalone VALIDATION-2025 Dukascopy M1 builder.

Completely isolated from the existing TRAIN builder and TRAIN database.
Reuses only the generic, date-range-agnostic download/materialization
helpers from v4_dukascopy_train_builder.py. Deliberately does NOT call
research_days()/run_builder() from that module -- those carry a hard-coded
assertion locking them to the TRAIN boundary. Drives its own day loop for
2025-01-01..2025-12-31 (VALIDATION year) with a small Dec-2024 warm-up
buffer. The TRAIN raw cache and TRAIN database are never opened.
"""

import time
from datetime import datetime, timedelta
from pathlib import Path

import v4_dukascopy_train_builder as builder
import v4_research_data as research_data

NEW_OUTPUT_DIR = Path("/tmp/v4_dukascopy_validation")

builder.CONTEXT_START = datetime(2024, 12, 1)
builder.END_EXCLUSIVE = datetime(2026, 1, 1)
builder.OUTPUT_DIR = NEW_OUTPUT_DIR
builder.RAW_DIR = NEW_OUTPUT_DIR / "raw"
builder.DATABASE_PATH = NEW_OUTPUT_DIR / "v4_validation_m1.sqlite3"
builder.MANIFEST_PATH = NEW_OUTPUT_DIR / "manifest.json"

CONTEXT_START = builder.CONTEXT_START
END_EXCLUSIVE = builder.END_EXCLUSIVE


def validation_days():
    result = []
    current = CONTEXT_START
    while current < END_EXCLUSIVE:
        result.append(current)
        current += timedelta(days=1)
    return result


def run_validation_builder():
    lock = builder.acquire_run_lock()
    try:
        days = validation_days()
        tasks = [(symbol, day) for symbol in builder.SYMBOLS for day in days]
        expected_keys = {(symbol, day.strftime("%Y-%m-%d")) for symbol, day in tasks}
        expected_days = len(tasks)
        connection = builder.open_database()
        failures = []
        last_network_request = None

        print("=" * 118)
        print("V4 DUKASCOPY VALIDATION (2025) BUILDER | EUR/USD + GBP/USD | M1 BID/ASK")
        print("=" * 118)
        print(f"CONTEXT_START={CONTEXT_START} | END_EXCLUSIVE={END_EXCLUSIVE}")
        print("TRAIN_DATABASE_OPENED=False")
        print("TRAIN_DATABASE_CHANGED=False")
        print(f"SYMBOL_DAYS={expected_days}")
        print(f"DATABASE={builder.DATABASE_PATH}")

        try:
            for position, (symbol, day_start) in enumerate(tasks, start=1):
                if day_start.weekday() == 5:
                    status = builder.record_saturday(connection, symbol, day_start)
                    print(f"DAY {position:04d}/{expected_days:04d} | {symbol} | "
                          f"{day_start:%Y-%m-%d} | {status}", flush=True)
                    continue

                payloads = {}
                metadata = {}
                transient_failure = False
                for side in research_data.OFFER_SIDES:
                    path = builder.raw_path(symbol, day_start, side)
                    seed = builder.adapter_seed_path(symbol, day_start, side)
                    has_raw_cache = path.is_file() and path.stat().st_size > 0
                    needs_network = not has_raw_cache and not (
                        seed.is_file() and seed.stat().st_size > 0
                    )
                    if needs_network and last_network_request is not None:
                        elapsed = time.monotonic() - last_network_request
                        wait_seconds = max(0.0, builder.REQUEST_INTERVAL_SECONDS - elapsed)
                        if wait_seconds:
                            time.sleep(wait_seconds)
                    if needs_network:
                        last_network_request = time.monotonic()
                    try:
                        payload, url, source = builder.fetch_or_read_side(symbol, day_start, side)
                    except research_data.TransientDukascopyDownloadError as error:
                        failures.append({"type": "TRANSIENT_DOWNLOAD", "symbol": symbol,
                                          "day_utc": day_start, "side": side, "error": str(error)})
                        transient_failure = True
                        print(f"DEFERRED | {symbol} | {day_start:%Y-%m-%d} | "
                              f"{side.upper()} | {error}", flush=True)
                        time.sleep(builder.EXHAUSTED_REQUEST_COOLDOWN_SECONDS)
                        continue
                    payloads[side] = payload
                    metadata[side] = (url, source)

                if transient_failure or set(payloads) != set(research_data.OFFER_SIDES):
                    continue
                try:
                    result = builder.materialize_day(
                        connection, symbol=symbol, day_start=day_start,
                        bid_payload=payloads["bid"], ask_payload=payloads["ask"],
                        bid_url=metadata["bid"][0], ask_url=metadata["ask"][0],
                        bid_source=metadata["bid"][1], ask_source=metadata["ask"][1],
                    )
                except builder.RequiredSourceGapError as error:
                    failures.append({"type": "REQUIRED_SOURCE_GAP", "symbol": symbol,
                                      "day_utc": day_start, "error": str(error)})
                    print(f"SOURCE_GAP | {error}", flush=True)
                    continue

                print(f"DAY {position:04d}/{expected_days:04d} | {symbol} | "
                      f"{day_start:%Y-%m-%d} | {result['status']} | "
                      f"Observed={result['observed_rows']} | Fillers={result['filler_rows']}", flush=True)
                if position % builder.CHECKPOINT_EVERY_DAYS == 0:
                    summary, digest = builder.write_checkpoint(connection, failures=failures)
                    print(f"CHECKPOINT | Processed={summary['processed_days']}/"
                          f"{expected_days} | M1={summary['observed_m1_rows']} | "
                          f"ManifestSHA={digest}", flush=True)

            outside_rows = connection.execute(
                "SELECT COUNT(*) FROM m1_bars WHERE datetime < '2025-01-01 00:00:00' "
                "OR datetime >= '2026-01-01 00:00:00'"
            ).fetchone()[0]
            if outside_rows:
                raise RuntimeError(f"VALIDATION rows outside the 2025 window: {outside_rows}")
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                raise RuntimeError(f"Validation database integrity failure: {integrity}")
            missing_keys = builder.validate_database(connection, expected_keys)
            summary = builder.progress_summary(connection)
            complete = not missing_keys and not failures
        finally:
            connection.commit()
            connection.close()

        connection = builder.open_database()
        try:
            summary, manifest_hash = builder.write_checkpoint(connection, failures=failures, complete=complete)
        finally:
            connection.close()

        print()
        print("VALIDATION (2025) BUILD SUMMARY")
        print("=" * 118)
        print(f"PROCESSED_DAYS={summary['processed_days']}/{expected_days}")
        print(f"OBSERVED_M1_ROWS={summary['observed_m1_rows']}")
        print(f"FILLER_M1_ROWS={summary['filler_m1_rows']}")
        print(f"RAW_BYTES={summary['raw_bytes']}")
        print(f"FAILURES={len(failures)}")
        print(f"MANIFEST={builder.MANIFEST_PATH}")
        print(f"MANIFEST_SHA256={manifest_hash}")
        print("TRAIN_DATABASE_OPENED=False")
        print("TRAIN_DATABASE_CHANGED=False")
        if not complete:
            raise RuntimeError("VALIDATION build is incomplete; rerun the same command to "
                                "reuse the verified cache and fetch only missing artifacts")
        print("V4_DUKASCOPY_VALIDATION_BUILD_OK")
    finally:
        lock.close()


if __name__ == "__main__":
    run_validation_builder()
