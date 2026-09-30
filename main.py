import os
import time
import requests
import yfinance as yf
import pandas as pd
from flask import Flask
from threading import Thread
from datetime import datetime, timezone, timedelta

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
SYMBOLS = ["GBPUSD=X", "GBPJPY=X"]
DXY_SYMBOL = "DX-Y.NYB"

app = Flask(__name__)
@app.route('/')
def home():
    is_open = is_market_open()
    status = "OPEN" if is_open else "CLOSED"
    return f"KOBBY GBP BOT RUNNING | Market: {status} | Pairs: {SYMBOLS}"

def send_telegram(msg):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    try:
        requests.post(url, data={"chat_id": CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def is_market_open():
    now = datetime.now(timezone.utc)
    day = now.weekday()
    hour = now.hour
    if day == 5 and hour >= 22: return False
    if day == 6 and hour < 22: return False
    return True

# === NEW: RED NEWS FILTER ===
def is_red_news_near():
    try:
        # ForexFactory this week calendar (faireconomy)
        r = requests.get("https://nfs.faireconomy.media/ff_calendar_thisweek.json", timeout=10)
        data = r.json()
        now = datetime.now(timezone.utc)
        for event in data:
            # Only High impact + USD/GBP
            if event.get('impact') != 'High': continue
            if event.get('currency') not in ['USD', 'GBP']: continue
            
            # event date format
            event_time_str = event.get('date') + " " + event.get('time', '12:00am')
            try:
                # FF time is EST, convert roughly
                event_dt = datetime.strptime(event_time_str, "%Y-%m-%d %I:%M%p")
                event_dt = event_dt.replace(tzinfo=timezone.utc) - timedelta(hours=5) # EST to UTC
            except:
                continue
            
            diff = (event_dt - now).total_seconds() / 60 # mins
            # If news in next 60 mins or past 30 mins -> skip
            if -30 <= diff <= 60:
                print(f"RED NEWS BLOCK: {event.get('currency')} - {event.get('title')} in {diff:.0f} mins")
                return True, f"{event.get('currency')} {event.get('title')}"
        return False, ""
    except Exception as e:
        print(f"News filter error: {e}")
        return False, "" # If API fail, allow trading

def get_data(symbol):
    try:
        df_4h = yf.download(symbol, period="10d", interval="4h", progress=False)
        df_1h = yf.download(symbol, period="10d", interval="1h", progress=False)
        df_15m = yf.download(symbol, period="3d", interval="15m", progress=False)
        df_dxy = yf.download(DXY_SYMBOL, period="5d", interval="1h", progress=False)
        return df_4h, df_1h, df_15m, df_dxy
    except:
        return None, None, None, None

def check_fundamentals(df_dxy):
    try:
        dxy_trend = "UP" if df_dxy['Close'].iloc[-1] > df_dxy['Close'].iloc[-5] else "DOWN"
        return dxy_trend
    except:
        return "NEUTRAL"

def analyze_symbol(symbol):
    df_4h, df_1h, df_15m, df_dxy = get_data(symbol)
    if df_4h is None or len(df_4h) < 10: return

    # 4H BOS
    high_4h = df_4h['High'].iloc[-6:-1].max()
    low_4h = df_4h['Low'].iloc[-6:-1].min()
    last_close_4h = float(df_4h['Close'].iloc[-1])
    trend = None
    if last_close_4h > high_4h: trend = "BULLISH"
    elif last_close_4h < low_4h: trend = "BEARISH"
    else: return

    # 1H Sweep
    last_1h = df_1h.iloc[-1]
    prev_high_1h = df_1h['High'].iloc[-10:-1].max()
    prev_low_1h = df_1h['Low'].iloc[-10:-1].min()
    sweep = (trend=="BULLISH" and last_1h['Low'] < prev_low_1h) or (trend=="BEARISH" and last_1h['High'] > prev_high_1h)
    if not sweep: return

    # 15M Confirm
    high_15m = df_15m['High'].iloc[-20:-1].max()
    low_15m = df_15m['Low'].iloc[-20:-1].min()
    last_close_15m = float(df_15m['Close'].iloc[-1])
    confirm = (trend=="BULLISH" and last_close_15m > high_15m) or (trend=="BEARISH" and last_close_15m < low_15m)
    if not confirm: return

    # FUNDAMENTALS CHECK
    dxy_trend = check_fundamentals(df_dxy)
    if symbol == "GBPUSD=X" and trend == "BULLISH" and dxy_trend == "UP": return
    if symbol == "GBPUSD=X" and trend == "BEARISH" and dxy_trend == "DOWN": return

    # RED NEWS CHECK - REAL MARKET FILTER
    is_news, news_title = is_red_news_near()
    if is_news:
        print(f"SKIP {symbol} - News: {news_title}")
        return # NO ENTRY during red news

    price = float(yf.download(symbol, period="1d", interval="1m", progress=False)['Close'].iloc[-1])
    sl = float(df_15m['Low'].iloc[-5:].min()) if trend == "BULLISH" else float(df_15m['High'].iloc[-5:].max())
    risk = abs(price - sl)
    if risk == 0: return
    tp1 = price + (risk*2) if trend == "BULLISH" else price - (risk*2)
    tp2 = price + (risk*3) if trend == "BULLISH" else price - (risk*3)
    tp3 = price + (risk*5) if trend == "BULLISH" else price - (risk*5)

    pair_name = symbol.replace("=X","")
    msg = (
        f"📈 *{pair_name} SMC ENTRY*\n\n"
        f"*Technical:* {trend}\n"
        f"4H BOS + 1H Sweep + 15M BOS/OB\n"
        f"*Fundamentals:* DXY {dxy_trend} | News: Clear ✅\n"
        f"*Price:* {price:.5f}\n\n"
        f"*Entry:* {price:.5f}\n*SL:* {sl:.5f}\n"
        f"*TP1* 1:2 - {tp1:.5f}\n*TP2* 1:3 - {tp2:.5f}\n*TP3* 1:5 - {tp3:.5f}\n\n"
        f"_{datetime.now().strftime('%Y-%m-%d %H:%M UTC')}_"
    )
    send_telegram(msg)

last_market_status = None

def start():
    Thread(target=lambda: app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 10000)))).start()
    time.sleep(3)
    send_telegram("✅ *kobby_gbpbot LIVE*\nGBPUSD + GBPJPY | SMC + DXY + Red News Filter")
    global last_market_status
    while True:
        open_now = is_market_open()
        if last_market_status is None or open_now != last_market_status:
            last_market_status = open_now
            status_text = "🟢 *FOREX OPEN* - GBP hunting" if open_now else "🔴 *FOREX CLOSED*"
            send_telegram(status_text)

        if open_now:
            for sym in SYMBOLS:
                try:
                    analyze_symbol(sym)
                    time.sleep(10)
                except Exception as e:
                    print(e)
        time.sleep(900)

if __name__ == "__main__":
    start()
