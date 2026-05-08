import yfinance as yf
from datetime import datetime

tickers = ['SPY','IWM','QQQ','AMD','TSLA','META','NVDA','INTC','TSM','CAR','F']

def check_rules(ticker):
    try:
        df = yf.Ticker(ticker).history(period='60d', interval='1d')
        if len(df) < 50:
            return None

        close = df['Close']
        high  = df['High']
        low   = df['Low']
        open_ = df['Open']

        price = float(close.iloc[-1])
        ema8  = float(close.ewm(span=8,  adjust=False).mean().iloc[-1])
        ema20 = float(close.ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(close.ewm(span=50, adjust=False).mean().iloc[-1])

        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = float((100 - (100 / (1 + gain/loss))).iloc[-1])

        low20 = float(close.rolling(20).min().iloc[-1])
        pct_below_ema20 = ((ema20 - price) / ema20) * 100

        r1 = price < ema8
        r2 = price < ema20
        r3 = price < ema50
        r4 = 38 <= rsi <= 60
        r5 = 0 <= pct_below_ema20 <= 2
        r6 = price > low20 * 1.005
        score = sum([r1,r2,r3,r4,r5,r6])

        # ── Pattern Detection ─────────────────────────────────────
        patterns = []
        c = close.values
        h = high.values
        l = low.values
        o = open_.values

        # Rejection candle (last 2 candles)
        for i in [-1, -2]:
            body    = abs(c[i] - o[i])
            candle  = h[i] - l[i]
            upper_w = h[i] - max(c[i], o[i])
            lower_w = min(c[i], o[i]) - l[i]
            if candle > 0:
                if upper_w > body * 1.8 and upper_w > candle * 0.45:
                    patterns.append("BEARISH REJECTION")
                if lower_w > body * 1.8 and lower_w > candle * 0.45:
                    patterns.append("BULLISH REJECTION")

        # Double Top — two highs within 1.5% over last 20 bars
        recent_h = h[-20:]
        sorted_h = sorted(enumerate(recent_h), key=lambda x: -x[1])
        if len(sorted_h) >= 2:
            h1_idx, h1 = sorted_h[0]
            h2_idx, h2 = sorted_h[1]
            if abs(h1_idx - h2_idx) >= 5 and abs(h1 - h2) / h1 < 0.015:
                valley = min(c[min(h1_idx, h2_idx)-20 : max(h1_idx, h2_idx)-20+1]) if max(h1_idx,h2_idx) > min(h1_idx,h2_idx) else c[-1]
                if valley < h1 * 0.97:
                    patterns.append("DOUBLE TOP")

        # Double Bottom — two lows within 1.5% over last 20 bars
        recent_l = l[-20:]
        sorted_l = sorted(enumerate(recent_l), key=lambda x: x[1])
        if len(sorted_l) >= 2:
            l1_idx, l1 = sorted_l[0]
            l2_idx, l2 = sorted_l[1]
            if abs(l1_idx - l2_idx) >= 5 and abs(l1 - l2) / l1 < 0.015:
                peak = max(c[min(l1_idx, l2_idx)-20 : max(l1_idx, l2_idx)-20+1]) if max(l1_idx,l2_idx) > min(l1_idx,l2_idx) else c[-1]
                if peak > l1 * 1.03:
                    patterns.append("DOUBLE BOTTOM")

        # Head and Shoulders / Inverse H&S — last 30 bars
        if len(h) >= 30:
            seg = 10
            left_h  = max(h[-30:-20])
            head_h  = max(h[-20:-10])
            right_h = max(h[-10:])
            left_l  = min(l[-30:-20])
            head_l  = min(l[-20:-10])
            right_l = min(l[-10:])

            # Head & Shoulders (bearish reversal)
            if (head_h > left_h * 1.01 and head_h > right_h * 1.01
                    and abs(left_h - right_h) / left_h < 0.04):
                patterns.append("HEAD & SHOULDERS")

            # Inverse Head & Shoulders (bullish reversal)
            if (head_l < left_l * 0.99 and head_l < right_l * 0.99
                    and abs(left_l - right_l) / left_l < 0.04):
                patterns.append("INVERSE H&S")

        return {
            'price':    round(price, 2),
            'rsi':      round(rsi, 1),
            'pct':      round(pct_below_ema20, 2),
            'rules':    [r1,r2,r3,r4,r5,r6],
            'score':    score,
            'patterns': list(set(patterns))
        }
    except Exception as e:
        return None


print("Full Scan - " + datetime.now().strftime("%I:%M %p"))
print("="*70)

results = []
for t in tickers:
    d = check_rules(t)
    if not d:
        print(t + ": no data")
        continue
    results.append((t, d))
    rules   = ''.join(['Y' if r else 'N' for r in d['rules']])
    label   = "*** 6/6 ***" if d['score'] == 6 else str(d['score']) + "/6"
    pat_str = "  >> " + ", ".join(d['patterns']) if d['patterns'] else ""
    print(t.ljust(6) + " $" + str(d['price']).rjust(8) +
          " | RSI:" + str(d['rsi']).rjust(5) +
          " | [" + rules + "] " + label + pat_str)

print()
print("Rules: [EMA8  EMA20  EMA50  RSI(38-60)  NotOver2%Extended  NotAtLow]")
print()

hits   = [(t,d) for t,d in results if d['score'] == 6]
near   = [(t,d) for t,d in results if 4 <= d['score'] < 6]
pats   = [(t,d) for t,d in results if d['patterns']]

if hits:
    print("6/6 SETUPS: " + ", ".join([t for t,d in hits]))
elif near:
    print("Closest to 6/6: " + ", ".join([t + " (" + str(d['score']) + "/6)" for t,d in near]))
else:
    print("No setups near 6/6 right now.")

if pats:
    print()
    print("PATTERNS DETECTED:")
    for t,d in pats:
        direction = ""
        p = d['patterns']
        bearish = [x for x in p if x in ["BEARISH REJECTION","DOUBLE TOP","HEAD & SHOULDERS"]]
        bullish = [x for x in p if x in ["BULLISH REJECTION","DOUBLE BOTTOM","INVERSE H&S"]]
        if bearish: direction = " --> PUTS"
        if bullish: direction = " --> CALLS"
        print("  " + t + ": " + ", ".join(p) + direction)
