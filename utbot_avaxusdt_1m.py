#!/usr/bin/env python3
"""UT Bot Alerts (Key=3, ATR=4) signal watcher — AVAXUSDT.P 1m (Binance USDT-M perp).

Matches the indicator configured on the user's TradingView chart:
    "UT Bot Alerts  3  4"   ->  Key Value = 3, ATR Period = 4

Prints a notification ONLY when a NEW signal has been confirmed on a CLOSED
1m candle. Empty stdout = nothing new (cron delivers silence as silence).

State (last notified candle) lives in state/utbot_avaxusdt_1m.json so a
signal is announced exactly once.
"""
import json
import os
import sys
import urllib.request
import datetime as dt

# ---------------------------------------------------------------- config
SYMBOL = "AVAXUSDT"
INTERVAL = "1m"
KEY = 3.0
ATR_PERIOD = 4
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "..", "state", "utbot_avaxusdt_1m.json")
STATE = os.path.normpath(STATE)
FAPI = "https://fapi.binance.com/fapi/v1"
BARS = 400          # plenty for ATR(4)/TS convergence
U = dt.timezone.utc


# ---------------------------------------------------------------- data
def fetch_klines(limit=500):
    url = f"{FAPI}/klines?symbol={SYMBOL}&interval={INTERVAL}&limit={limit}"
    with urllib.request.urlopen(url, timeout=25) as r:
        return json.load(r)


def rma(vals, n):
    out = [None] * len(vals)
    if len(vals) < n:
        return out
    s = sum(vals[:n]) / n
    out[n - 1] = s
    for i in range(n, len(vals)):
        s = (s * (n - 1) + vals[i]) / n
        out[i] = s
    return out


def atr_series(highs, lows, closes, n):
    tr = [highs[0] - lows[0]]
    for i in range(1, len(closes)):
        tr.append(max(highs[i] - lows[i],
                      abs(highs[i] - closes[i - 1]),
                      abs(lows[i] - closes[i - 1])))
    return rma(tr, n)


def ut_bot(closes, highs, lows, key, period):
    """Return (ts_line, buys, sells) — buys/sells are bar indices."""
    n = len(closes)
    a = atr_series(highs, lows, closes, period)
    ts = [0.0] * n
    for i in range(n):
        nloss = key * (a[i] if a[i] is not None else 0.0)
        prev = ts[i - 1] if i > 0 else 0.0
        pclose = closes[i - 1] if i > 0 else 0.0
        if closes[i] > prev and pclose > prev:
            ts[i] = max(prev, closes[i] - nloss)
        elif closes[i] < prev and pclose < prev:
            ts[i] = min(prev, closes[i] + nloss)
        elif closes[i] > prev:
            ts[i] = closes[i] - nloss
        else:
            ts[i] = closes[i] + nloss
    buys, sells = [], []
    for i in range(1, n):
        if a[i] is None:
            continue
        if closes[i] > ts[i] and closes[i - 1] <= ts[i - 1]:
            buys.append(i)
        if closes[i] < ts[i] and ts[i - 1] <= closes[i - 1]:
            sells.append(i)
    return ts, buys, sells


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
        # transient network problem: stay silent, do not spam
        print("", end="")
        return 0

    if len(kl) < ATR_PERIOD + 10:
        print("", end="")
        return 0

    ts_ms = [k[0] for k in kl]
    opens = [float(k[1]) for k in kl]
    highs = [float(k[2]) for k in kl]
    lows = [float(k[3]) for k in kl]
    closes = [float(k[4]) for k in kl]

    ts_line, buys, sells = ut_bot(closes, highs, lows, KEY, ATR_PERIOD)
    sig = {i: "BUY" for i in buys}
    sig.update({i: "SELL" for i in sells})

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

    # FIRST RUN: establish a baseline and stay silent. Without this, a fresh
    # state would announce every historical signal as if it were new.
    if last_seen is None:
        state["last_notified_bar"] = cur_bar
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

    # find any signals on closed candles newer than what we've announced
    fresh = []
    for i in closed:
        if sig.get(i) and ts_ms[i] > last_seen:
            fresh.append(i)

    state["last_notified_bar"] = cur_bar
    state["last_checked_utc"] = dt.datetime.now(tz=U).isoformat(timespec="seconds")
    state["last_close"] = closes[last_closed]

    if not fresh:
        save_state(state)
        print("", end="")
        return 0

    # announce (if several queued, lead with the newest, count the rest)
    out = []
    newest = fresh[-1]
    side = sig[newest]
    bar_dt = dt.datetime.fromtimestamp(ts_ms[newest] / 1000, U)
    bar_close = dt.datetime.fromtimestamp(int(kl[newest][6]) / 1000, U)
    px = closes[newest]
    lvl = lows[newest] if side == "BUY" else highs[newest]
    arrow = "🟢 BUY" if side == "BUY" else "🔴 SELL"

    out.append(f"{arrow} signal — {SYMBOL}.P  1m")
    out.append(f"Bar close: {bar_close.strftime('%Y-%m-%d %H:%M')} UTC")
    out.append(f"Price: {px:.4f}   (bar low/high {lvl:.4f})")
    out.append(f"UT Bot Alerts (Key {KEY:g}, ATR {ATR_PERIOD}) · Binance perp")
    if len(fresh) > 1:
        out.append(f"(+{len(fresh)-1} earlier signal(s) missed while offline)")

    state["last_notified_signal"] = {
        "side": side,
        "bar_open_utc": bar_dt.isoformat(timespec="minutes"),
        "bar_close_utc": bar_close.isoformat(timespec="minutes"),
        "price": round(px, 4),
    }
    save_state(state)

    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
