"""
Audit of the LIVE bot strategy (strategy.py + trade_manager.py V2 model)
on independent historical data. No parameters are changed or tuned.

Rules replicated from config.py / strategy.py:
  M5 candles, EMA20/EMA50, RSI14, ATR14 (Wilder).
  BUY : EMA20>EMA50, both rising, close above both, RSI 52..68,
        EMA distance >= 0.15 ATR, ATR >= min_atr.   SELL mirrored (RSI 32..48).
  New signal event only when the previous candle was not already a VALID
  signal in the same direction (storage.create_signal_event_if_new).
  Entry = signal candle close. Stop = max(1.0 ATR, 5 pips).
  Target = 1.5R. Max hold 180 min, then exit at close. Spread 1 pip.
  If stop and target are both touched in one candle, stop is assumed.

Data: Oanda 1-minute mid candles, 2016-01 .. 2020-05
(github.com/FutureSharks/financial-data), resampled to M5.

Usage: python3 bot_strategy_audit.py /path/to/oanda_root
"""

import glob
import os
import sys

import numpy as np
import pandas as pd

PIP = 0.0001
MIN_ATR = 0.00010
MIN_STOP = 5 * PIP
SPREAD_PIPS = 1.0
TP_R = 1.5
MAX_BARS = 180 // 5


def load_m5(root, symbol):
    files = sorted(glob.glob(os.path.join(root, symbol, "*", "*.csv")))
    df = pd.concat((pd.read_csv(f, parse_dates=["time"]) for f in files))
    df = df.set_index("time").sort_index()
    df = df[~df.index.duplicated()]
    m5 = df.resample("5min").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}
    )
    return m5.dropna()


def signals(m5):
    c = m5["close"]
    ef = c.ewm(span=20, adjust=False).mean()
    es = c.ewm(span=50, adjust=False).mean()
    d = c.diff()
    gain = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    loss = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + gain / loss)
    pc = c.shift(1)
    tr = pd.concat(
        [m5["high"] - m5["low"], (m5["high"] - pc).abs(), (m5["low"] - pc).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean()

    sep = (ef - es).abs() / atr >= 0.15
    vol = atr >= MIN_ATR
    buy = (
        (ef > es) & (ef.diff() > 0) & (es.diff() > 0)
        & (c > ef) & (c > es) & rsi.between(52, 68) & sep & vol
    )
    sell = (
        (ef < es) & (ef.diff() < 0) & (es.diff() < 0)
        & (c < ef) & (c < es) & rsi.between(32, 48) & sep & vol
    )
    sig = pd.Series(0, index=m5.index)
    sig[buy] = 1
    sig[sell] = -1
    sig.iloc[:100] = 0  # warm-up
    new = (sig != 0) & (sig != sig.shift(1))
    return sig[new], atr


def simulate(m5, sig, atr):
    idx = {t: i for i, t in enumerate(m5.index)}
    hi, lo, cl = m5["high"].values, m5["low"].values, m5["close"].values
    times = m5.index
    out = []
    for t, direction in sig.items():
        i = idx[t]
        entry = cl[i]
        risk = max(atr.iloc[i], MIN_STOP)
        stop = entry - direction * risk
        target = entry + direction * risk * TP_R
        result, reason = None, "TIMEOUT"
        last = min(i + MAX_BARS, len(cl) - 1)
        for j in range(i + 1, last + 1):
            # stop at gap across a weekend/long pause
            if (times[j] - times[j - 1]).total_seconds() > 3600:
                result = direction * (cl[j - 1] - entry)
                reason = "GAP_EXIT"
                break
            hit_stop = lo[j] <= stop if direction > 0 else hi[j] >= stop
            hit_tp = hi[j] >= target if direction > 0 else lo[j] <= target
            if hit_stop:
                result, reason = -risk, "STOP"
                break
            if hit_tp:
                result, reason = risk * TP_R, "TARGET"
                break
        if result is None:
            result = direction * (cl[last] - entry)
        pips = result / PIP - SPREAD_PIPS
        out.append((t, direction, reason, pips, pips / (risk / PIP), risk / PIP))
    return pd.DataFrame(out, columns=["time", "dir", "reason", "pips", "R", "risk_pips"])


def summary(name, t):
    wins = t[t["pips"] > 0]
    pf = wins["pips"].sum() / -t.loc[t["pips"] <= 0, "pips"].sum()
    print(
        f"{name:14s} n={len(t):5d}  win={100*len(wins)/len(t):5.1f}%  "
        f"avgR={t['R'].mean():+.3f}  PF={pf:.2f}  total={t['pips'].sum():+8.0f} pips"
    )


def main():
    root = sys.argv[1]
    allt = []
    for sym in ("EUR_USD", "GBP_USD"):
        m5 = load_m5(root, sym)
        sig, atr = signals(m5)
        t = simulate(m5, sig, atr)
        t["symbol"] = sym
        allt.append(t)
        print(f"{sym}: M5 {m5.index.min()} .. {m5.index.max()}, bars={len(m5)}")
    t = pd.concat(allt)
    print()
    summary("ALL", t)
    for sym, g in t.groupby("symbol"):
        summary(sym, g)
    for y, g in t.groupby(t["time"].dt.year):
        summary(str(y), g)
    print("\nExit reasons:", t["reason"].value_counts().to_dict())
    print("\nWithout spread (pure signal quality):")
    t2 = t.copy()
    t2["pips"] += SPREAD_PIPS
    t2["R"] = t2["pips"] / t2["risk_pips"]
    summary("ALL no-spread", t2)
    best = t.groupby(t["time"].dt.hour)["R"].agg(["count", "mean"])
    print("\nAvgR by UTC hour (info only, not a tuned filter):")
    print(best.round(3).T.to_string())


if __name__ == "__main__":
    main()
