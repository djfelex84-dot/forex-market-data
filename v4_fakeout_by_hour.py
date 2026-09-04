"""Read-only breakdown of the best FAKEOUT combo by hour-of-day and weekday."""

from collections import defaultdict
import v4_train_event_research as ter

HOLD_MINUTES = 360
TARGET_R = 1.5
SETUP = ter.SETUP_FAKEOUT
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def main():
    connection, manifest = ter.open_complete_train_database()
    try:
        ter.MAX_TRADE_MINUTES = HOLD_MINUTES
        ter.TAKE_PROFIT_R_MULTIPLE = TARGET_R

        records, raw_events, diagnostics, m30_quality = ter.build_records(connection)
        setup_records = ter.subset(records, setup=SETUP)
        evaluated = [row for row in setup_records if row.get("r") is not None]

        print("BY HOUR (UTC)")
        by_hour = defaultdict(list)
        for row in evaluated:
            by_hour[row["signal_time"].hour].append(row)
        for hour in sorted(by_hour):
            m = ter.metrics(by_hour[hour])
            print(f"HOUR={hour:02d} n={m['n']} wr={m['wr']:.2f}% pf={m['pf']:.3f} "
                  f"avg_r={m['avg_r']:+.3f} net_r={m['net_r']:+.2f}")

        print()
        print("BY WEEKDAY (UTC)")
        by_weekday = defaultdict(list)
        for row in evaluated:
            by_weekday[row["signal_time"].weekday()].append(row)
        for wd in sorted(by_weekday):
            m = ter.metrics(by_weekday[wd])
            print(f"{WEEKDAY_NAMES[wd]} n={m['n']} wr={m['wr']:.2f}% pf={m['pf']:.3f} "
                  f"avg_r={m['avg_r']:+.3f} net_r={m['net_r']:+.2f}")

        print("V4_FAKEOUT_BY_HOUR_WEEKDAY_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
