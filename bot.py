import requests
import pandas as pd
import time
from datetime import datetime
import pytz

# ── CONFIGURATION ──────────────────────────────────────
TWELVEDATA_API_KEY = "2974b48c520d4d3aa1d8a2e4a3067e25"
TELEGRAM_TOKEN     = "8634921035:AAHrHApMOfY_c0x0WrPUQdaSln1T-jtx8cM"
TELEGRAM_CHAT_ID   = "6278398993"
SYMBOL             = "XAU/USD"
INTERVAL           = "15min"
WAT                = pytz.timezone("Africa/Lagos")

# ── RISK SETTINGS ───────────────────────────────────────
RISK_RR            = 2.0       # Risk:Reward ratio
OB_LOOKBACK        = 6         # Candles to scan for OB
SMA_PERIOD         = 9
EMA_PERIOD         = 100

# ── KILL ZONES (WAT) ────────────────────────────────────
LONDON_START = 8
LONDON_END   = 11
NY_START     = 14
NY_END       = 17

# ── TELEGRAM ────────────────────────────────────────────
def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}
    try:
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

# ── FETCH CANDLES ────────────────────────────────────────
def get_candles(interval, outputsize=150):
    url = (
        f"https://api.twelvedata.com/time_series"
        f"?symbol={SYMBOL}&interval={interval}"
        f"&outputsize={outputsize}&apikey={TWELVEDATA_API_KEY}"
    )
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if "values" not in data:
            print("API error:", data)
            return None
        df = pd.DataFrame(data["values"])
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","datetime":"Time"})
        df[["Open","High","Low","Close"]] = df[["Open","High","Low","Close"]].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

# ── KILL ZONE CHECK ──────────────────────────────────────
def in_kill_zone():
    now = datetime.now(WAT)
    h = now.hour
    london = LONDON_START <= h < LONDON_END
    ny     = NY_START     <= h < NY_END
    return london or ny

def kill_zone_name():
    now = datetime.now(WAT)
    h = now.hour
    if LONDON_START <= h < LONDON_END: return "London"
    if NY_START     <= h < NY_END:     return "NY"
    return "None"

# ── H1 BIAS ──────────────────────────────────────────────
def get_h1_bias():
    df = get_candles("1h", outputsize=120)
    if df is None or len(df) < EMA_PERIOD:
        return 0
    df["EMA100"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    last  = df.iloc[-1]
    prev  = df.iloc[-2]
    bullish = last["Close"] > last["EMA100"] and prev["Close"] > prev["EMA100"]
    bearish = last["Close"] < last["EMA100"] and prev["Close"] < prev["EMA100"]
    if bullish: return 1
    if bearish: return -1
    return 0

# ── 15M CONFLUENCE ───────────────────────────────────────
def get_m15_signal(df, bias):
    if len(df) < EMA_PERIOD:
        return 0
    df["SMA9"]   = df["Close"].rolling(SMA_PERIOD).mean()
    df["EMA100"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    last = df.iloc[-1]
    if bias == 1  and last["Close"] > last["EMA100"] and last["SMA9"] > last["EMA100"]: return 1
    if bias == -1 and last["Close"] < last["EMA100"] and last["SMA9"] < last["EMA100"]: return -1
    return 0

# ── ORDER BLOCK DETECTION ────────────────────────────────
def find_order_block(df, bias):
    candles = df.iloc[-(OB_LOOKBACK+2):-1].reset_index(drop=True)
    for i in range(1, len(candles)-1):
        curr = candles.iloc[i]
        nxt  = candles.iloc[i+1]
        if bias == 1:
            is_bearish    = curr["Close"] < curr["Open"]
            next_bullish  = nxt["Close"]  > nxt["Open"]
            if is_bearish and next_bullish:
                return curr["High"], curr["Low"]
        elif bias == -1:
            is_bullish    = curr["Close"] > curr["Open"]
            next_bearish  = nxt["Close"]  < nxt["Open"]
            if is_bullish and next_bearish:
                return curr["High"], curr["Low"]
    return None, None

# ── MAIN LOOP ────────────────────────────────────────────
def run():
    send_telegram("✅ *SMC Gold Bot Started*\nMonitoring XAU/USD...")
    print("Bot running...")
    last_signal_time = None

    while True:
        try:
            now = datetime.now(WAT)

            if not in_kill_zone():
                print(f"[{now.strftime('%H:%M')} WAT] Outside kill zone — waiting...")
                time.sleep(60)
                continue

            df = get_candles("15min")
            if df is None:
                time.sleep(60)
                continue

            bias = get_h1_bias()
            if bias == 0:
                print(f"[{now.strftime('%H:%M')} WAT] No clear H1 bias")
                time.sleep(60)
                continue

            signal = get_m15_signal(df, bias)
            if signal == 0:
                print(f"[{now.strftime('%H:%M')} WAT] No 15M confluence")
                time.sleep(60)
                continue

            ob_high, ob_low = find_order_block(df, bias)
            if ob_high is None:
                print(f"[{now.strftime('%H:%M')} WAT] No OB found")
                time.sleep(60)
                continue

            price = df.iloc[-1]["Close"]
            at_ob = ob_low <= price <= ob_high

            if not at_ob:
                print(f"[{now.strftime('%H:%M')} WAT] Price not at OB yet")
                time.sleep(60)
                continue

            # Avoid duplicate signals
            current_time = now.strftime("%Y-%m-%d %H:%M")
            if last_signal_time == current_time:
                time.sleep(60)
                continue
            last_signal_time = current_time

            # Calculate SL and TP
            if bias == 1:
                entry = price
                sl    = round(ob_low  - 0.50, 2)
                tp    = round(entry + (entry - sl) * RISK_RR, 2)
                direction = "BUY 📈"
            else:
                entry = price
                sl    = round(ob_high + 0.50, 2)
                tp    = round(entry - (sl - entry) * RISK_RR, 2)
                direction = "SELL 📉"

            msg = (
                f"🔔 *SMC SIGNAL — XAU/USD*\n"
                f"Direction: *{direction}*\n"
                f"Entry: `{entry}`\n"
                f"SL: `{sl}`\n"
                f"TP: `{tp}`\n"
                f"RR: 1:{RISK_RR}\n"
                f"Kill Zone: {kill_zone_name()}\n"
                f"OB Zone: `{ob_low} – {ob_high}`\n"
                f"Time: {now.strftime('%H:%M')} WAT"
            )
            send_telegram(msg)
            print("Signal sent:", direction)

        except Exception as e:
            print(f"Loop error: {e}")

        time.sleep(60)

if __name__ == "__main__":
    run()
