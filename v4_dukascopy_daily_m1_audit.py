"""Approve or reject Dukascopy's candidate daily M1 BI5 adapter.

This research-only gate compares daily BID and ASK candle files field by field
against the already verified hourly tick artifacts from the V4 forensic audit.
It never opens or writes the production database.  A daily adapter is accepted
only when timestamps and both price sides reproduce the tick-derived M1 path.
"""

import fcntl
import json
import math
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

import v4_research_data as research_data


SYMBOL = "EUR/USD"
PIP_SIZE = 0.0001
REQUEST_INTERVAL_SECONDS = 5.0
FORENSIC_DIR = Path("/tmp/v4_dukascopy_forensic")
FORENSIC_MANIFEST = FORENSIC_DIR / "manifest.json"
EXPECTED_FORENSIC_MANIFEST_SHA256 = (
    "3a112fe5488718d9300619d95921e730f7552a885b9c360f8c2b53f991cfa330"
)
OUTPUT_DIR = Path("/tmp/v4_dukascopy_daily_m1_audit")
MANIFEST_PATH = OUTPUT_DIR / "manifest.json"

# These approve a data adapter, not a trading rule.  Daily bars and hourly
# ticks come from the same provider, so their BID/ASK path should be nearly
# identical.  Midpoint-proxy tolerance is wider because separate side extrema
# need not occur on the same tick.
MAX_SIDE_P95_PIPS = 0.10
MAX_SIDE_ABSOLUTE_PIPS = 0.50
MAX_MID_P95_PIPS = 0.25
MAX_MID_ABSOLUTE_PIPS = 1.00


def percentile(values, probability):
    if not values:
        raise ValueError("Cannot calculate percentile of empty values")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def acquire_run_lock():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "run.lock"
    handle = path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise RuntimeError(
            f"Another V4 daily M1 adapter audit already holds {path}"
        ) from None
    handle.seek(0)
    handle.truncate()
    handle.write(f"pid={os.getpid()}\n")
    handle.flush()
    return handle


def daily_raw_path(day_start, side):
    return (
        OUTPUT_DIR
        / "raw"
        / SYMBOL.replace("/", "")
        / f"{day_start:%Y}"
        / f"{day_start:%m}"
        / f"{day_start:%d}"
        / f"{side.upper()}_candles_min_1.bi5"
    )


def read_forensic_manifest():
    if not FORENSIC_MANIFEST.is_file():
        raise RuntimeError(
            f"Verified V4 forensic manifest is missing: {FORENSIC_MANIFEST}"
        )
    manifest_hash = research_data.sha256_file(FORENSIC_MANIFEST)
    if manifest_hash != EXPECTED_FORENSIC_MANIFEST_SHA256:
        raise RuntimeError(
            "Unexpected V4 forensic manifest hash: "
            f"{manifest_hash} != {EXPECTED_FORENSIC_MANIFEST_SHA256}"
        )
    manifest = json.loads(FORENSIC_MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("audit") != "V4_DUKASCOPY_TARGETED_FORENSIC":
        raise RuntimeError("Unexpected forensic audit identity")
    if manifest.get("symbol") != SYMBOL:
        raise RuntimeError("Unexpected forensic audit symbol")
    if manifest.get("repair_authorized") is not False:
        raise RuntimeError("Forensic manifest has an unsafe repair flag")
    if not manifest.get("raw_artifacts"):
        raise RuntimeError("Forensic manifest has no raw tick artifacts")
    return manifest, manifest_hash


def load_verified_tick_m1(manifest):
    expected_root = (FORENSIC_DIR / "raw").resolve()
    tick_rows = []
    verified_artifacts = []
    seen_hours = set()
    for artifact in manifest["raw_artifacts"]:
        hour = research_data.parse_utc(artifact["hour_utc"])
        if hour in seen_hours:
            raise RuntimeError(f"Duplicate forensic hour in manifest: {hour}")
        seen_hours.add(hour)

        path = Path(artifact["path"])
        try:
            path.resolve().relative_to(expected_root)
        except ValueError:
            raise RuntimeError(
                f"Forensic raw artifact escapes expected directory: {path}"
            ) from None
        if not path.is_file():
            raise RuntimeError(f"Forensic raw artifact is missing: {path}")
        payload = path.read_bytes()
        digest = research_data.sha256_bytes(payload)
        if digest != artifact["sha256"]:
            raise RuntimeError(f"Forensic raw artifact hash mismatch: {path}")
        if len(payload) != int(artifact["bytes"]):
            raise RuntimeError(f"Forensic raw artifact size mismatch: {path}")
        expected_url = research_data.dukascopy_hour_url(SYMBOL, hour)
        if artifact["url"] != expected_url:
            raise RuntimeError(f"Forensic raw artifact URL mismatch: {hour}")

        ticks = research_data.decode_bi5_ticks(
            payload,
            symbol=SYMBOL,
            hour_start=hour,
        )
        if len(ticks) != int(artifact["ticks"]):
            raise RuntimeError(f"Forensic tick count mismatch: {path}")
        tick_rows.extend(research_data.aggregate_ticks_to_m1(ticks))
        verified_artifacts.append(
            {
                "hour_utc": hour,
                "path": str(path),
                "sha256": digest,
                "bytes": len(payload),
                "ticks": len(ticks),
            }
        )

    tick_rows.sort(key=lambda row: row["timestamp"])
    research_data.validate_m1_rows(tick_rows)
    return tick_rows, verified_artifacts, sorted(seen_hours)


def fetch_or_read_day(day_start, side):
    path = daily_raw_path(day_start, side)
    url = research_data.dukascopy_m1_day_url(SYMBOL, day_start, side)
    if path.exists():
        payload = path.read_bytes()
        source = "CACHE"
    else:
        def retry_notice(attempt, max_attempts, delay, reason):
            print(
                f"RETRY {attempt}/{max_attempts} | {day_start:%Y-%m-%d} "
                f"{side.upper()} | Wait={delay:.1f}s | {reason}",
                flush=True,
            )

        payload, returned_url = research_data.download_m1_day(
            SYMBOL,
            day_start,
            side,
            retry_notifier=retry_notice,
        )
        if returned_url != url:
            raise RuntimeError("Dukascopy daily M1 URL mismatch")
        if not payload:
            raise RuntimeError(
                f"Missing candidate daily M1 artifact: {day_start} {side}"
            )
        research_data.write_raw_artifact(path, payload)
        source = "NETWORK"

    if not payload:
        raise RuntimeError(f"Empty cached daily M1 artifact: {path}")
    rows = research_data.decode_bi5_m1_candles(
        payload,
        symbol=SYMBOL,
        day_start=day_start,
        side=side,
    )
    if not rows:
        raise RuntimeError(f"Decoded daily M1 artifact is empty: {path}")
    return rows, {
        "day_utc": day_start,
        "side": side,
        "url": url,
        "path": str(path),
        "sha256": research_data.sha256_bytes(payload),
        "bytes": len(payload),
        "rows": len(rows),
        "source": source,
    }


def max_ohlc_difference_pips(left, right):
    return max(
        abs(float(left[field]) - float(right[field]))
        for field in research_data.OHLC_FIELDS
    ) / PIP_SIZE


def compare_paths(tick_rows, daily_rows, audited_hours):
    tick_by_time = {
        research_data.parse_utc(row["timestamp"]): row
        for row in tick_rows
    }
    daily_by_time = {
        research_data.parse_utc(row["timestamp"]): row
        for row in daily_rows
    }
    if len(tick_by_time) != len(tick_rows):
        raise RuntimeError("Duplicate timestamp in tick-derived M1 reference")
    if len(daily_by_time) != len(daily_rows):
        raise RuntimeError("Duplicate timestamp in daily M1 candidate")

    missing_daily = sorted(set(tick_by_time) - set(daily_by_time))
    audited_hour_set = set(audited_hours)
    extra_daily = sorted(
        timestamp
        for timestamp in set(daily_by_time) - set(tick_by_time)
        if timestamp.replace(minute=0, second=0, microsecond=0)
        in audited_hour_set
    )

    side_differences = []
    mid_differences = []
    details = []
    for timestamp in sorted(set(tick_by_time) & set(daily_by_time)):
        reference = tick_by_time[timestamp]
        candidate = daily_by_time[timestamp]
        bid_difference = max_ohlc_difference_pips(
            reference["bid"],
            candidate["bid"],
        )
        ask_difference = max_ohlc_difference_pips(
            reference["ask"],
            candidate["ask"],
        )
        mid_difference = max_ohlc_difference_pips(
            reference["mid"],
            candidate["mid"],
        )
        side_differences.extend((bid_difference, ask_difference))
        mid_differences.append(mid_difference)
        details.append(
            {
                "timestamp": timestamp,
                "bid_max_diff_pips": bid_difference,
                "ask_max_diff_pips": ask_difference,
                "mid_proxy_max_diff_pips": mid_difference,
            }
        )

    if not side_differences or not mid_differences:
        raise RuntimeError("No overlapping M1 candles for adapter comparison")
    side_p95 = percentile(side_differences, 0.95)
    side_max = max(side_differences)
    mid_p95 = percentile(mid_differences, 0.95)
    mid_max = max(mid_differences)
    accepted = (
        not missing_daily
        and not extra_daily
        and side_p95 <= MAX_SIDE_P95_PIPS
        and side_max <= MAX_SIDE_ABSOLUTE_PIPS
        and mid_p95 <= MAX_MID_P95_PIPS
        and mid_max <= MAX_MID_ABSOLUTE_PIPS
    )
    return {
        "tick_m1_rows": len(tick_rows),
        "daily_m1_rows_all_downloaded_days": len(daily_rows),
        "overlap_rows": len(details),
        "missing_daily_timestamps": missing_daily,
        "extra_daily_timestamps_in_verified_hours": extra_daily,
        "side_median_max_ohlc_diff_pips": statistics.median(side_differences),
        "side_p95_max_ohlc_diff_pips": side_p95,
        "side_absolute_max_ohlc_diff_pips": side_max,
        "mid_proxy_median_max_ohlc_diff_pips": statistics.median(mid_differences),
        "mid_proxy_p95_max_ohlc_diff_pips": mid_p95,
        "mid_proxy_absolute_max_ohlc_diff_pips": mid_max,
        "largest_differences": sorted(
            details,
            key=lambda row: max(
                row["bid_max_diff_pips"],
                row["ask_max_diff_pips"],
                row["mid_proxy_max_diff_pips"],
            ),
            reverse=True,
        )[:20],
        "adapter_accepted": accepted,
    }


def run_audit():
    forensic, forensic_hash = read_forensic_manifest()
    tick_rows, tick_artifacts, audited_hours = load_verified_tick_m1(forensic)
    days = sorted({hour.replace(hour=0) for hour in audited_hours})

    print("=" * 118)
    print("V4 DUKASCOPY DAILY M1 ADAPTER AUDIT | BID/ASK vs VERIFIED TICKS")
    print("=" * 118)
    print(f"FORENSIC_MANIFEST_SHA256={forensic_hash}")
    print(
        f"TICK_HOURS={len(audited_hours)} | TICK_M1={len(tick_rows)} | "
        f"UNIQUE_DAYS={len(days)}"
    )

    daily_rows = []
    daily_artifacts = []
    last_network_request = None
    total_requests = len(days) * 2
    position = 0
    for day_start in days:
        side_rows = {}
        for side in research_data.OFFER_SIDES:
            position += 1
            path = daily_raw_path(day_start, side)
            if not path.exists() and last_network_request is not None:
                elapsed = time.monotonic() - last_network_request
                wait_seconds = max(0.0, REQUEST_INTERVAL_SECONDS - elapsed)
                if wait_seconds:
                    time.sleep(wait_seconds)
            if not path.exists():
                last_network_request = time.monotonic()
            rows, artifact = fetch_or_read_day(day_start, side)
            side_rows[side] = rows
            daily_artifacts.append(artifact)
            print(
                f"DAY {position:03d}/{total_requests:03d} | "
                f"{day_start:%Y-%m-%d} | {side.upper()} | "
                f"Rows={len(rows):4d} | Bytes={artifact['bytes']:7d} | "
                f"{artifact['source']}",
                flush=True,
            )
        daily_rows.extend(
            research_data.merge_bid_ask_m1(
                side_rows["bid"],
                side_rows["ask"],
            )
        )

    daily_rows.sort(key=lambda row: row["timestamp"])
    research_data.validate_m1_rows(daily_rows)
    comparison = compare_paths(tick_rows, daily_rows, audited_hours)

    manifest = {
        "schema_version": 1,
        "audit": "V4_DUKASCOPY_DAILY_M1_ADAPTER",
        "created_at_utc": datetime.utcnow().strftime(research_data.TIME_FORMAT),
        "symbol": SYMBOL,
        "candidate_format": (
            "daily BID/ASK BI5; big-endian seconds/open/close/low/high/volume"
        ),
        "midpoint_policy": "fieldwise average of aligned BID/ASK M1 OHLC",
        "forensic_manifest": {
            "path": str(FORENSIC_MANIFEST),
            "sha256": forensic_hash,
        },
        "thresholds": {
            "side_p95_pips": MAX_SIDE_P95_PIPS,
            "side_absolute_pips": MAX_SIDE_ABSOLUTE_PIPS,
            "mid_proxy_p95_pips": MAX_MID_P95_PIPS,
            "mid_proxy_absolute_pips": MAX_MID_ABSOLUTE_PIPS,
            "missing_or_extra_verified_minutes": 0,
        },
        "tick_artifacts": tick_artifacts,
        "daily_artifacts": daily_artifacts,
        "comparison": comparison,
        "adapter_accepted": comparison["adapter_accepted"],
        "production_database_opened": False,
        "production_database_changed": False,
    }
    manifest_hash = research_data.write_json_artifact(MANIFEST_PATH, manifest)

    print()
    print("ADAPTER COMPARISON")
    print("=" * 118)
    print(f"OVERLAP_M1={comparison['overlap_rows']}")
    print(f"MISSING_DAILY_M1={len(comparison['missing_daily_timestamps'])}")
    print(
        "EXTRA_DAILY_M1_IN_VERIFIED_HOURS="
        f"{len(comparison['extra_daily_timestamps_in_verified_hours'])}"
    )
    print(
        "SIDE_DIFF_PIPS | "
        f"Median={comparison['side_median_max_ohlc_diff_pips']:.4f} | "
        f"P95={comparison['side_p95_max_ohlc_diff_pips']:.4f} | "
        f"Max={comparison['side_absolute_max_ohlc_diff_pips']:.4f}"
    )
    print(
        "MID_PROXY_DIFF_PIPS | "
        f"Median={comparison['mid_proxy_median_max_ohlc_diff_pips']:.4f} | "
        f"P95={comparison['mid_proxy_p95_max_ohlc_diff_pips']:.4f} | "
        f"Max={comparison['mid_proxy_absolute_max_ohlc_diff_pips']:.4f}"
    )
    print(f"MANIFEST={MANIFEST_PATH}")
    print(f"MANIFEST_SHA256={manifest_hash}")
    print(f"ADAPTER_ACCEPTED={comparison['adapter_accepted']}")
    print("PRODUCTION_DATABASE_OPENED=False")
    print("PRODUCTION_DATABASE_CHANGED=False")

    if not comparison["adapter_accepted"]:
        raise RuntimeError(
            "Daily M1 adapter rejected; inspect manifest before changing format "
            "or thresholds"
        )
    print("V4_DUKASCOPY_DAILY_M1_ADAPTER_AUDIT_OK")


def main():
    lock = acquire_run_lock()
    try:
        run_audit()
    finally:
        fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
        lock.close()


if __name__ == "__main__":
    main()
