"""Read-only check: does either V4 pattern show a real signal once split by volatility regime?"""

from datetime import timedelta

import v4_event_anatomy as anatomy
import v4_event_strategy as strat
import v4_research_data as research_data
import v4_train_event_research as ter
from v4_event_strategy import (
    SETUP_BREAKOUT_RETEST,
    SETUP_FAKEOUT,
    TRAIN_END,
    TRAIN_START,
    generate_v4_events,
)

HORIZONS = anatomy.HORIZONS_MINUTES
MAX_HORIZON = max(HORIZONS)
SETUPS = (SETUP_FAKEOUT, SETUP_BREAKOUT_RETEST)

TRAILING_WINDOW_BARS = 500
MIN_TRAILING_SAMPLE = 100
BUCKET_LOW, BUCKET_MID, BUCKET_HIGH = "LOW_VOL", "MID_VOL", "HIGH_VOL"


def classify_volatility(event_atr, trailing_atr_values):
    sample = [value for value in trailing_atr_values if value is not None]
    if len(sample) < MIN_TRAILING_SAMPLE:
        return None
    sample.sort()
    rank = sum(1 for value in sample if value <= event_atr) / len(sample)
    if rank < 1 / 3:
        return BUCKET_LOW
    if rank < 2 / 3:
        return BUCKET_MID
    return BUCKET_HIGH


def build_records_with_volatility(connection, symbol):
    m30_rows, _quality = ter.load_verified_m30(connection, symbol)
    strategy_rows = research_data.m30_strategy_rows(m30_rows, side="mid")
    atr_series = strat._wilder_atr_series(strategy_rows)
    time_to_index = {
        row["_time"] + timedelta(minutes=30): index
        for index, row in enumerate(strategy_rows)
    }

    scan = generate_v4_events(strategy_rows)

    records = []
    for setup in SETUPS:
        for event in scan[setup]:
            signal_time = research_data.parse_utc(event["signal_time"])
            if not TRAIN_START <= signal_time < TRAIN_END:
                continue

            bar_index = time_to_index.get(signal_time)
            if bar_index is None:
                continue
            start = max(0, bar_index - TRAILING_WINDOW_BARS)
            trailing = atr_series[start:bar_index]
            bucket = classify_volatility(event["atr"], trailing)
            if bucket is None:
                continue

            window_end = signal_time + timedelta(minutes=MAX_HORIZON)
            if window_end > TRAIN_END:
                m1_index = {}
            else:
                window_rows = ter.iter_source_grid(
                    connection, symbol, signal_time, window_end
                )
                m1_index = anatomy.index_m1(list(window_rows))

            record = anatomy.analyze_event(
                symbol=symbol,
                event=event,
                m1_index=m1_index,
                split_end=TRAIN_END,
                horizons=HORIZONS,
            )
            record["volatility_bucket"] = bucket
            records.append(record)
    return records


def report_bucket(records, setup, bucket, horizon):
    subset = [
        row
        for row in records
        if row["setup"] == setup and row["volatility_bucket"] == bucket
    ]
    summary = anatomy.summarize(subset, horizon=horizon)
    if summary["evaluated"] == 0:
        print(f"{setup:<16} {bucket:<9} HORIZON={horizon:>3}min | no evaluated events")
        return
    field = f"fr_{horizon}m_atr"
    try:
        boot = anatomy.day_block_bootstrap_mean(subset, field=field)
        lo95, hi95 = boot["ci_95"]
        flag = "NO SIGNAL" if lo95 <= 0 <= hi95 else "SIGNAL"
        ci_text = f"CI95=({lo95:+.4f},{hi95:+.4f}) {flag}"
    except ValueError as error:
        ci_text = str(error)
    print(
        f"{setup:<16} {bucket:<9} HORIZON={horizon:>3}min | "
        f"n={summary['evaluated']:>4} mean_fr_atr={summary['mean_fr_atr']:>+7.4f} | {ci_text}"
    )


def main():
    connection, _manifest = ter.open_complete_train_database()
    try:
        all_records = []
        for symbol in ter.SYMBOLS:
            all_records.extend(build_records_with_volatility(connection, symbol))

        print("=" * 110)
        print("V4 EVENT ANATOMY BY VOLATILITY REGIME | TRAIN ONLY | TRAILING ATR TERCILE")
        print("=" * 110)

        for setup in SETUPS:
            for bucket in (BUCKET_LOW, BUCKET_MID, BUCKET_HIGH):
                for horizon in HORIZONS:
                    report_bucket(all_records, setup, bucket, horizon)
            print("-" * 110)

        print("V4_EVENT_ANATOMY_BY_VOLATILITY_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
