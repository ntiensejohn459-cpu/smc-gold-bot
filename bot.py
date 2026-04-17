import requests
import pandas as pd
from datetime import datetime
import pytz

TWELVEDATA_API_KEY = "2974b48c520d4d3aa1d8a2e4a3067e25"
TELEGRAM_TOKEN = "8634921035:AAHrHApMOfY_c0x0WrPUQdaSln1T-jtx8cM"
TELEGRAM_CHAT_ID = "6278398993"
SYMBOL = "XAU/USD"
WAT = pytz.timezone("Africa/Lagos")
RISK_RR = 2.0
OB_LOOKBACK = 6
SMA_PERIOD = 9
EMA_PERIOD = 100
LONDON_START = 8
LONDON_END = 11
NY_START = 14
NY_END = 17

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def get_candles(interval, outputsize=150):
    url = f"https://api.twelvedata.com/time_series?symbol={SYMBOL}&interval={interval}&outputsize={outputsize}&apikey={TWELVEDATA_API_KEY}"
    try:
        r = requests.get(url, timeout=15)
        data = r.json()
        if "values" not in data:
            return None
        df = pd.DataFrame(data["values"])
        df = df.rename(columns={"open":"Open","high":"High","low":"Low","close":"Close","datetime":"Time"})
        df[["Open","High","Low","Close"]] = df[["Open","High","Low","Close"]].astype(float)
        df = df.iloc[::-1].reset_index(drop=True)
        return df
    except Exception as e:
        print(f"Fetch error: {e}")
        return None

def in_kill_zone():
    now = datetime.now(WAT)
    h = now.hour
    return (LONDON_START <= h < LONDON_END) or (NY_START <= h < NY_END)

def kill_zone_name():
    now = datetime.now(WAT)
    h = now.hour
    if LONDON_START <= h < LONDON_END:
        return "London"
    if NY_START <= h < NY_END:
        return "NY"
    return "None"

def get_h1_bias():
    df = get_candles("1h", outputsize=120)
    if df is None or len(df) < EMA_PERIOD:
        return 0
    df["EMA100"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    last = df.iloc[-1]
    prev = df.iloc[-2]
    if last["Close"] > last["EMA100"] and prev["Close"] > prev["EMA100"]:
        return 1
    if last["Close"] < last["EMA100"] and prev["Close"] < prev["EMA100"]:
        return -1
    return 0

def get_m15_signal(df, bias):
    if len(df) < EMA_PERIOD:
        return 0
    df["SMA9"] = df["Close"].rolling(SMA_PERIOD).mean()
    df["EMA100"] = df["Close"].ewm(span=EMA_PERIOD, adjust=False).mean()
    last = df.iloc[-1]
    if bias == 1 and last["Close"] > last["EMA100"] and last["SMA9"] > last["EMA100"]:
        return 1
    if bias == -1 and last["Close"] < last["EMA100"] and last["SMA9"] < last["EMA100"]:
        return -1
    return 0

def find_order_block(df, bias):
    candles = df.iloc[-(OB_LOOKBACK+2):-1].reset_index(drop=True)
    for i in range(1, len(candles)-1):
        curr = candles.iloc[i]
        nxt = candles.iloc[i+1]
        if bias == 1:
            if curr["Close"] < curr["Open"] and nxt["Close"] > nxt["Open"]:
                return curr["High"], curr["Low"]
        elif bias == -1:
            if curr["Close"] > curr["Open"] and nxt["Close"] < nxt["Open"]:
                return curr["High"], curr["Low"]
    return None, None

if __name__ == "__main__":
    send_telegram("SMC Bot checking market...")
    if not in_kill_zone():
        print("Outside kill zone.")
        exit()
    df = get_candles("15min")
    if df is None:
        exit()
    bias = get_h1_bias()
    if bias == 0:
        print("No H1 bias.")
        exit()
    signal = get_m15_signal(df, bias)
    if signal == 0:
        print("No 15M confluence.")
        exit()
    ob_high, ob_low = find_order_block(df, bias)
    if ob_high is None:
        print("No OB found.")
        exit()
    price = df.iloc[-1]["Close"]
    if not (ob_low <= price <= ob_high):
        print("Price not at OB.")
        exit()
    if bias == 1:
        entry = price
        sl = round(ob_low - 0.50, 2)
        tp = round(entry + (entry - sl) * RISK_RR, 2)
        direction = "BUY"
    else:
        entry = price
        sl = round(ob_high + 0.50, 2)
        tp = round(entry - (sl - entry) * RISK_RR, 2)
        direction = "SELL"
    msg = f"SMC SIGNAL XAU/USD\nDirection: {direction}\nEntry: {entry}\nSL: {sl}\nTP: {tp}\nRR: 1:{RISK_RR}\nKill Zone: {kill_zone_name()}\nOB: {ob_low} to {ob_high}\nTime: {datetime.now(WAT).strftime('%H:%M')} WAT"
    send_telegram(msg)
    print("Signal sent:", direction)
