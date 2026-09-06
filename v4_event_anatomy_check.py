"""Read-only price-path anatomy check for FAKEOUT events on TRAIN only."""

import v4_dukascopy_train_builder as train_builder
import v4_event_anatomy as anatomy
import v4_train_event_research as ter
from v4_event_strategy import SETUP_FAKEOUT

HORIZONS = (30, 60, 120, 180)


def main():
    connection, _manifest = ter.open_complete_train_database()
    try:
        all_records = []
        for symbol in ter.SYMBOLS:
            m30_rows, _quality = ter.load_verified_m30(connection, symbol)
            m1_rows = list(
                ter.iter_source_grid(
                    connection,
                    symbol,
                    train_builder.CONTEXT_START,
                    ter.TRAIN_END,
                )
            )
            records, _diagnostics = anatomy.build_event_anatomy(
                symbol=symbol,
                m30_rows=m30_rows,
                m1_rows=m1_rows,
                unlock_validation=False,
            )
            all_records.extend(records)

        fakeout_records = [
            row for row in all_records if row["setup"] == SETUP_FAKEOUT
        ]

        print("=" * 100)
        print("V4 EVENT ANATOMY | FAKEOUT | TRAIN ONLY | NO TP/SL/SPREAD ASSUMPTIONS")
        print("=" * 100)

        for horizon in HORIZONS:
            summary = anatomy.summarize(fakeout_records, horizon=horizon)
            if summary["evaluated"] == 0:
                print(f"HORIZON={horizon:>3}min | no evaluated events")
                continue
            print(
                f"HORIZON={horizon:>3}min | events={summary['events']:>5} "
                f"evaluated={summary['evaluated']:>5} "
                f"unique_days={summary['unique_days']:>4} "
                f"mean_fr_atr={summary['mean_fr_atr']:>+7.4f} "
                f"median_fr_atr={summary['median_fr_atr']:>+7.4f} "
                f"median_mfe_atr={summary['median_mfe_atr']:>7.4f} "
                f"median_mae_atr={summary['median_mae_atr']:>7.4f}"
            )

        print("-" * 100)
        print("BOOTSTRAP (day-block, 2000 reps) on mean forward return, ATR units:")
        for horizon in HORIZONS:
            field = f"fr_{horizon}m_atr"
            try:
                result = anatomy.day_block_bootstrap_mean(
                    fakeout_records, field=field
                )
            except ValueError as error:
                print(f"HORIZON={horizon:>3}min | {error}")
                continue
            lo90, hi90 = result["ci_90"]
            lo95, hi95 = result["ci_95"]
            straddles_zero = lo95 <= 0 <= hi95
            flag = "NO SIGNAL (CI crosses zero)" if straddles_zero else "SIGNAL (CI excludes zero)"
            print(
                f"HORIZON={horizon:>3}min | point={result['point']:>+7.4f} "
                f"CI90=({lo90:>+7.4f}, {hi90:>+7.4f}) "
                f"CI95=({lo95:>+7.4f}, {hi95:>+7.4f}) | {flag}"
            )

        print()
        print("V4_EVENT_ANATOMY_CHECK_OK")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
