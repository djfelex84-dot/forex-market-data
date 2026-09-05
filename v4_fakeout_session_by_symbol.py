"""Read-only check: does the session-restricted FAKEOUT edge hold on BOTH symbols?"""

import v4_train_event_research as ter

HOLD_MINUTES = 360
TARGET_R = 1.5
SETUP = ter.SETUP_FAKEOUT
SESSION_START_HOUR = 7
SESSION_END_HOUR = 16


def main():
    connection, manifest = ter.open_complete_train_database()
    try:
        ter.MAX_TRADE_MINUTES = HOLD_MINUTES
        ter.TAKE_PROFIT_R_MULTIPLE = TARGET_R

        records, raw_events, diagnostics, m30_quality = ter.build_records(connection)
        setup_records = ter.subset(records, setup=SETUP)
        evaluated = [row for row in setup_records if row.get("r") is not None]
        session_records = [row for row in evaluated
                            if SESSION_START_HOUR <= row["signal_time"].hour < SESSION_END_HOUR]

        combined = ter.metrics(session_records)
        print(f"ALL n={combined['n']} wr={combined['wr']:.2f}% pf={combined['pf']:.3f} "
              f"avg_r={combined['avg_r']:+.3f} net_r={combined['net_r']:+.2f} dd={combined['dd']:.2f}R")

        for symbol in ("EUR/USD", "GBP/USD"):
            symbol_records = [row for row in session_records if row["symbol"] == symbol]
            m = ter.metrics(symbol_records)
            print(f"{symbol} n={m['n']} wr={m['wr']:.2f}% pf={m['pf']:.3f} "
                  f"avg_r={m['avg_r']:+.3f} net_r={m['net_r']:+.2f} dd={m['dd']:.2f}R")
            for year in range(2021, 2025):
                year_records = [row for row in symbol_records if row["year"] == year]
                ym = ter.metrics(year_records)
                print(f"  {year} n={ym['n']} wr={ym['wr']:.2f}% pf={ym['pf']:.3f} "
                      f"avg_r={ym['avg_r']:+.3f} net_r={ym['net_r']:+.2f}")

        print("V4_FAKEOUT_SESSION_BY_SYMBOL_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
