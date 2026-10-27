#!/usr/bin/env python3
"""
AVAXUSDT.P 1m — price close above 100 EMA crossover watcher.

Signals when:
  - current close > EMA(100)
  - previous close <= EMA(100)  [first close above the line]

Only acts on closed 1m candles. State tracks last notified bar so each
signal is announced exactly once. Empty stdout = nothing new.
"""
import json
import os
import sys
import urllib.request
import datetime as dt

# ---------------------------------------------------------------- config
SYMBOL = "AVAXUSDT"
INTERVAL = "1m"
EMA_PERIOD = 100
STATE = "state/avax_ema100_1m.json"
FAPI = "https://data-api.binance.vision/api/v3"
BARS = 250  # enough for EMA(100) warmup + buffer
U = dt.timezone.utc


# ---------------------------------------------------------------- data
def fetch_klines(limit=500):
    url = f"{FAPI}/klines?symbol={SYMBOL}&interval={INTERVAL}&limit={limit}"
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r)


def ema(vals, n):
    """Simple EMA series. Returns list same length as vals; first n-1 entries are None."""
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    k = 2.0 / (n + 1)
    # seed with SMA of first n
    out[n - 1] = sum(vals[:n]) / n
    for i in range(n, len(vals)):
        out[i] = vals[i] * k + out[i - 1] * (1 - k)
    return out


# ---------------------------------------------------------------- state
def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(s):
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(s, f, indent=2)


# ---------------------------------------------------------------- main
def main():
    try:
        kl = fetch_klines()
    except Exception as e:
        print("", end="")
        return 0

    if len(kl) < EMA_PERIOD + 5:
        print("", end="")
        return 0

    ts_ms = [k[0] for k in kl]
    closes = [float(k[4]) for k in kl]

    ema_line = ema(closes, EMA_PERIOD)

    # last CLOSED candle = last element whose close time has passed
    now_ms = int(dt.datetime.now(tz=U).timestamp() * 1000)
    closed = [i for i in range(len(kl)) if int(kl[i][6]) <= now_ms]
    if not closed:
        print("", end="")
        return 0
    last_closed = closed[-1]

    state = load_state()
    last_seen = state.get("last_notified_bar")
    cur_bar = ts_ms[last_closed]

    # FIRST RUN: prime baseline, stay silent
    if last_seen is None:
        state["last_notified_bar"] = cur_bar - 60000  # 1 min back
        state["last_checked_utc"] = dt.datetime.now(tz=U).isoformat(timespec="seconds")
        state["last_close"] = closes[last_closed]
        state["primed_utc"] = dt.datetime.now(tz=U).isoformat(timespec="seconds")
        save_state(state)
        print("", end="")
        return 0

    # nothing new
    if last_seen == cur_bar:
        print("", end="")
        return 0

    # detect crossover: close crosses above EMA(100)
    # we need at least 2 closed bars to compare prev vs current
    fresh = []
    for i in closed:
        if ts_ms[i] <= last_seen:
            continue
        if ema_line[i] is None:
            continue
        prev_i = i - 1
        if prev_i < 0 or ema_line[prev_i] is None:
            continue
        # crossover: prev close <= prev EMA, current close > current EMA
        if closes[prev_i] <= ema_line[prev_i] and closes[i] > ema_line[i]:
            fresh.append(i)

    state["last_notified_bar"] = cur_bar
    state["last_checked_utc"] = dt.datetime.now(tz=U).isoformat(timespec="seconds")
    state["last_close"] = closes[last_closed]

    if not fresh:
        save_state(state)
        print("", end="")
        return 0

    # announce newest signal only (count backlog if multiple)
    newest = fresh[-1]
    bar_dt = dt.datetime.fromtimestamp(ts_ms[newest] / 1000, U)
    bar_close = dt.datetime.fromtimestamp(int(kl[newest][6]) / 1000, U)
    px = closes[newest]
    ema_val = ema_line[newest]

    out = []
    out.append(f"🟢 EMA(100) crossover — {SYMBOL}.P  1m")
    out.append(f"Bar close: {bar_close.strftime('%Y-%m-%d %H:%M')} UTC")
    out.append(f"Price: {px:.4f}   EMA(100): {ema_val:.4f}")
    out.append(f"Distance above EMA: {(px - ema_val):.4f} ({(px/ema_val - 1)*100:.2f}%)")
    if len(fresh) > 1:
        out.append(f"(+{len(fresh)-1} earlier crossover(s) missed while offline)")

    state["last_notified_signal"] = {
        "bar_open_utc": bar_dt.isoformat(timespec="minutes"),
        "bar_close_utc": bar_close.isoformat(timespec="minutes"),
        "price": round(px, 4),
        "ema100": round(ema_val, 4),
    }
    save_state(state)

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
