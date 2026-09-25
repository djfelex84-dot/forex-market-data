"""
V7 research: daily trend rules on crypto (7 coins) and gold.

Pre-registered before any result was seen.

Data
  Crypto: Coin Metrics community data (github.com/coinmetrics/data), daily
          PriceUSD: BTC, ETH, XRP, BNB, LTC, ADA, DOGE. Ends 2026-05-23.
          Caveat: these coins are today's survivors (survivorship bias).
  Gold:   XAUUSD daily, github.com/ejtraderLabs/historical-data, 2012..2022-03.

Splits
  Crypto: TRAIN 2016-2020, VALIDATION 2021-2022, TEST 2023-01 .. 2026-05
  Gold:   TRAIN 2012-2017, VALIDATION 2018-2019, TEST 2020-01 .. 2022-03

Signals are LONG / FLAT only (spot, what a subscriber can actually do).
Decision at the daily close, position from the next day. Cost per position
change (one side): crypto 0.10%, gold 0.02%.

Families (variant chosen on TRAIN by portfolio Sharpe, nothing else):
  TSMOM    long if close > close N days ago, N in {20, 50, 100, 200}
  SMA      long if close > SMA(N),           N in {20, 50, 100, 200}
  DONCHIAN long on close > N-day high, exit on close < M-day low,
           (N, M) in {(20, 10), (50, 20), (100, 50)}

Portfolio: equal weight across assets that have data (and >= 200 days of
history) on that day; each asset's sleeve is either in the asset or in cash.

PASS rule (per family): on BOTH VALIDATION and TEST
  Sharpe > 0.5 AND max drawdown smaller than buy-and-hold's max drawdown
  AND total return > 0.
Buy-and-hold of the same equal-weight basket is reported as the benchmark.

Usage: python3 v7_crypto_gold_research.py /path/to/coinmetrics/csv /path/to/historical-data
"""

import os
import sys

import numpy as np
import pandas as pd

COINS = ["btc", "eth", "xrp", "bnb", "ltc", "ada", "doge"]
FAMILIES = {
    "TSMOM": [20, 50, 100, 200],
    "SMA": [20, 50, 100, 200],
    "DONCHIAN": [(20, 10), (50, 20), (100, 50)],
}
MARKETS = {
    "CRYPTO": {"cost": 0.0010, "ann": 365,
               "periods": {"TRAIN": ("2016-01-01", "2020-12-31"),
                           "VALIDATION": ("2021-01-01", "2022-12-31"),
                           "TEST": ("2023-01-01", "2026-12-31")}},
    "GOLD": {"cost": 0.0002, "ann": 252,
             "periods": {"TRAIN": ("2012-01-01", "2017-12-31"),
                         "VALIDATION": ("2018-01-01", "2019-12-31"),
                         "TEST": ("2020-01-01", "2022-12-31")}},
}


def load_crypto(folder):
    cols = {}
    for c in COINS:
        d = pd.read_csv(os.path.join(folder, f"{c}.csv"), usecols=["time", "PriceUSD"])
        d = d.dropna()
        d = d[d["PriceUSD"] > 0]
        cols[c.upper()] = d.set_index(pd.to_datetime(d["time"]))["PriceUSD"]
    return pd.DataFrame(cols).sort_index()


def load_gold(folder):
    d = pd.read_csv(os.path.join(folder, "XAUUSD", "XAUUSDd1.csv"), parse_dates=["Date"])
    return d.set_index("Date")[["close"]].rename(columns={"close": "XAU"}).astype(float)


def position(p, family, param):
    if family == "TSMOM":
        return (p > p.shift(param)).astype(float).where(p.shift(param).notna())
    if family == "SMA":
        s = p.rolling(param).mean()
        return (p > s).astype(float).where(s.notna())
    n, m = param
    hi = p.rolling(n).max().shift(1)
    lo = p.rolling(m).min().shift(1)
    pos = pd.Series(np.nan, index=p.index)
    cur = 0.0
    for i, (px, h, l) in enumerate(zip(p.values, hi.values, lo.values)):
        if np.isnan(h) or np.isnan(l):
            continue
        if cur == 0 and px > h:
            cur = 1.0
        elif cur == 1 and px < l:
            cur = 0.0
        pos.iloc[i] = cur
    return pos


def sleeve_returns(p, family, param, cost):
    """daily net return of one asset sleeve + list of trade returns."""
    p = p.dropna()
    ret = p.pct_change()
    pos = position(p, family, param).shift(1)          # trade next day
    turn = pos.diff().abs().fillna(0)
    net = (pos * ret - turn * cost)
    # need >= 200 days of history before the asset joins the portfolio
    net[: min(200, len(net))] = np.nan
    trades = []
    held, entry = False, None
    for t, x in pos.items():
        if x == 1 and not held:
            held, entry = True, t
        elif x == 0 and held:
            held = False
            seg = ret.loc[entry:t].iloc[:-1]
            trades.append((entry, (1 + seg).prod() * (1 - cost) ** 2 - 1))
    return net, trades


def portfolio(df, family, param, cost):
    sleeves, trades = [], []
    for col in df.columns:
        net, tr = sleeve_returns(df[col], family, param, cost)
        sleeves.append(net.rename(col))
        trades += tr
    return pd.concat(sleeves, axis=1).mean(axis=1, skipna=True), trades


def buy_hold(df):
    rets = []
    for col in df.columns:
        r = df[col].dropna().pct_change()
        r[: min(200, len(r))] = np.nan
        rets.append(r.rename(col))
    return pd.concat(rets, axis=1).mean(axis=1, skipna=True)


def metrics(r, ann):
    r = r.dropna()
    if len(r) < 20:
        return None
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    sharpe = r.mean() / r.std() * np.sqrt(ann) if r.std() > 0 else 0
    return {"total": eq.iloc[-1] - 1, "sharpe": sharpe, "maxdd": dd}


def trade_stats(trades, a, b):
    r = np.array([x for t, x in trades if pd.Timestamp(a) <= t <= pd.Timestamp(b)])
    if len(r) == 0:
        return "no trades"
    w = r[r > 0]
    lo = -r[r <= 0].sum()
    pf = w.sum() / lo if lo > 0 else float("inf")
    return (f"trades={len(r)} win={100*len(w)/len(r):.0f}% "
            f"avg_win={100*w.mean() if len(w) else 0:+.1f}% "
            f"avg_loss={100*r[r<=0].mean() if (r<=0).any() else 0:+.1f}% PF={pf:.2f}")


def fmt(m):
    return f"total={100*m['total']:+7.1f}% Sharpe={m['sharpe']:+.2f} maxDD={100*m['maxdd']:6.1f}%"


def run(name, df):
    cfg = MARKETS[name]
    per = cfg["periods"]
    cut = lambda r, k: r.loc[per[k][0]:per[k][1]]
    print(f"\n################ {name}  ({df.index.min().date()} .. {df.index.max().date()})")
    bh = buy_hold(df)
    for k in per:
        m = metrics(cut(bh, k), cfg["ann"])
        print(f"  BUY&HOLD {k:10s} {fmt(m)}")
    verdicts = {}
    for fam, params in FAMILIES.items():
        best, best_sh = None, -9
        res = {}
        for prm in params:
            r, tr = portfolio(df, fam, prm, cfg["cost"])
            res[prm] = (r, tr)
            m = metrics(cut(r, "TRAIN"), cfg["ann"])
            print(f"  {fam:8s} {str(prm):9s} TRAIN {fmt(m)}")
            if m["sharpe"] > best_sh:
                best, best_sh = prm, m["sharpe"]
        r, tr = res[best]
        ok = True
        print(f"  -> {fam} chosen {best}")
        for k in ("VALIDATION", "TEST"):
            m = metrics(cut(r, k), cfg["ann"])
            b = metrics(cut(bh, k), cfg["ann"])
            passed = m["sharpe"] > 0.5 and m["maxdd"] > b["maxdd"] and m["total"] > 0
            ok &= passed
            print(f"     {k:10s} {fmt(m)} | {trade_stats(tr, *per[k])} | {'ok' if passed else 'FAIL'}")
        verdicts[fam] = (best, ok)
        print(f"     VERDICT {fam}: {'PASS' if ok else 'FAIL'}")
    return verdicts


def main():
    crypto = load_crypto(sys.argv[1])
    gold = load_gold(sys.argv[2])
    v1 = run("CRYPTO", crypto)
    v2 = run("GOLD", gold)
    print("\nSUMMARY", {"CRYPTO": v1, "GOLD": v2})


if __name__ == "__main__":
    main()
