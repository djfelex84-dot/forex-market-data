"""
V5 research: daily trend-following (time-series momentum) on FX majors.

Data: Federal Reserve H.10 daily noon rates (via github.com/datasets/exchange-rates).
Close-only, price-only (swap/carry not included).

Locked split (decided before looking at any result):
  TRAIN       1990-01-01 .. 2014-12-31  -> choose the rule here only
  VALIDATION  2015-01-01 .. 2020-12-31  -> check the chosen rule once
  TEST        2021-01-01 .. latest      -> final one-shot check

Rule family (frozen): go long if price > price N days ago, short otherwise.
Position changes only when the sign flips. N in {20, 60, 120, 250}.
Cost: 2 bp round trip per flip (roughly 2 pips on EUR/USD incl. slippage).
"""

import os
import sys
import urllib.request

import numpy as np
import pandas as pd

URL = "https://raw.githubusercontent.com/datasets/exchange-rates/master/data/daily.csv"
CACHE = os.environ.get("V5_DATA", "/tmp/fx_daily.csv")

# FRED quote convention: True = USD per 1 foreign unit (like EUR/USD)
PAIRS = {
    "Euro": ("EUR/USD", True),
    "United Kingdom": ("GBP/USD", True),
    "Australia": ("AUD/USD", True),
    "New Zealand": ("NZD/USD", True),
    "Japan": ("USD/JPY", False),
    "Switzerland": ("USD/CHF", False),
    "Canada": ("USD/CAD", False),
    "Norway": ("USD/NOK", False),
    "Sweden": ("USD/SEK", False),
}

LOOKBACKS = (20, 60, 120, 250)
COST = 0.0002

PERIODS = {
    "TRAIN": ("1990-01-01", "2014-12-31"),
    "VALIDATION": ("2015-01-01", "2020-12-31"),
    "TEST": ("2021-01-01", "2100-01-01"),
}


def load():
    if not os.path.exists(CACHE):
        urllib.request.urlretrieve(URL, CACHE)
    df = pd.read_csv(CACHE, parse_dates=["Date"])
    df = df[df["Country"].isin(PAIRS)]
    wide = df.pivot(index="Date", columns="Country", values="Exchange rate")
    wide = wide.apply(pd.to_numeric, errors="coerce")
    wide = wide[wide.index >= "1989-01-01"]
    return wide.rename(columns={k: v[0] for k, v in PAIRS.items()})


def trades_for(price, lookback):
    """Return list of (entry_date, exit_date, direction, log_return_net)."""
    p = price.dropna()
    sig = np.sign(np.log(p / p.shift(lookback))).dropna()
    sig = sig[sig != 0]
    trades = []
    cur_dir, entry_px, entry_dt = None, None, None
    for dt, s in sig.items():
        if cur_dir is None:
            cur_dir, entry_px, entry_dt = s, p[dt], dt
            continue
        if s != cur_dir:
            r = cur_dir * np.log(p[dt] / entry_px) - COST
            trades.append((entry_dt, dt, cur_dir, r))
            cur_dir, entry_px, entry_dt = s, p[dt], dt
    return trades


def daily_returns(price, lookback):
    p = price.dropna()
    ret = np.log(p / p.shift(1))
    pos = np.sign(np.log(p / p.shift(lookback))).shift(1)
    turns = pos.diff().abs().fillna(0) / 2  # 1 per flip
    return (pos * ret - turns * COST).dropna()


def stats(trades):
    if not trades:
        return None
    r = np.array([t[3] for t in trades])
    wins, losses = r[r > 0], r[r <= 0]
    pf = wins.sum() / -losses.sum() if losses.sum() < 0 else np.inf
    return {
        "n": len(r),
        "win%": 100 * len(wins) / len(r),
        "avg_win%": 100 * wins.mean() if len(wins) else 0,
        "avg_loss%": 100 * losses.mean() if len(losses) else 0,
        "PF": pf,
        "total%": 100 * r.sum(),
    }


def in_period(trades, period):
    a, b = PERIODS[period]
    return [t for t in trades if pd.Timestamp(a) <= t[1] <= pd.Timestamp(b)]


def portfolio(wide, lookback, period):
    a, b = PERIODS[period]
    rets = []
    for col in wide.columns:
        d = daily_returns(wide[col], lookback)
        # equal risk: scale each pair to ~10% annual vol using trailing vol
        vol = np.log(wide[col].dropna()).diff().rolling(60).std().shift(1) * np.sqrt(252)
        rets.append((d * (0.10 / vol)).rename(col))
    pr = pd.concat(rets, axis=1).loc[a:b].mean(axis=1, skipna=True).dropna()
    ann = pr.mean() * 252
    sharpe = ann / (pr.std() * np.sqrt(252))
    yearly = pr.groupby(pr.index.year).sum()
    return ann, sharpe, yearly


def fmt(s):
    return (f"n={s['n']:4d}  win={s['win%']:5.1f}%  avg_win={s['avg_win%']:+.2f}%  "
            f"avg_loss={s['avg_loss%']:+.2f}%  PF={s['PF']:.2f}  total={s['total%']:+.1f}%")


def main():
    wide = load()
    periods = sys.argv[1:] or ["TRAIN"]
    print(f"Data: {wide.index.min().date()} .. {wide.index.max().date()}")
    for period in periods:
        print(f"\n===== {period} {PERIODS[period]} =====")
        for lb in LOOKBACKS:
            all_t = []
            for col in wide.columns:
                all_t += in_period(trades_for(wide[col], lb), period)
            ann, sh, yearly = portfolio(wide, lb, period)
            pos_years = (yearly > 0).sum()
            print(f"N={lb:3d}  {fmt(stats(all_t))}  | portfolio ann={100*ann:+.1f}% "
                  f"Sharpe={sh:+.2f} positive_years={pos_years}/{len(yearly)}")


if __name__ == "__main__":
    main()
