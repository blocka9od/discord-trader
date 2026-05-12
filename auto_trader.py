"""
Real-time auto-trader.
Reads live 5-min bars from Alpaca every 5 minutes during trading hours.
Detects tops (PUTS) and bottoms (CALLS) and places orders automatically.
"""
import os, sys, time, json, smtplib
import datetime
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
import pandas as pd
import pytz
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest
from alpaca.trading.enums import ContractType, OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

# ── Credentials ───────────────────────────────────────────────────────────────
API_KEY    = os.environ.get("ALPACA_KEY",    "PK2G5C5BQQ7AP5WNWBEUSKXOTI")
SECRET_KEY = os.environ.get("ALPACA_SECRET", "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP")
EMAIL      = os.environ.get("EMAIL",         "Blocka9od@gmail.com")
EMAIL_PASS = os.environ.get("EMAIL_PASS",    "dnlw dleb ryxs cljg")

tc          = TradingClient(API_KEY, SECRET_KEY, paper=True)
data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
CT          = pytz.timezone("America/Chicago")

BOT_DIR        = os.path.dirname(os.path.abspath(__file__))
WATCHLIST_FILE = os.path.join(BOT_DIR, "watchlist.json")
WEEK_FILE      = os.path.join(BOT_DIR, "week_trades.json")

# ── Config ────────────────────────────────────────────────────────────────────
DAY_TRADE_TICKERS  = ["SPY", "IWM", "QQQ"]   # day trades only
SWING_TICKERS      = ["IWM", "AMD", "TSLA", "META", "NVDA", "INTC", "TSM", "CAR", "F"]  # swings
BASE_TICKERS       = list(dict.fromkeys(DAY_TRADE_TICKERS + SWING_TICKERS))  # all unique

MAX_DAY_TRADES     = 2   # IWM/SPY/QQQ — same-day exit, Mon-Thu only
MAX_SWING_TRADES   = 5   # 2 JR + 2 Friday straddle + 1 stock swing
MAX_IWM_SWINGS     = 1   # only 1 IWM swing per week (not counting straddle)
MAX_STOCK_SWINGS   = 1   # max 1 stock swing per week
MAX_DAY_TRADE_COST   = 350   # max total cost for day trades ($)
MAX_SWING_TRADE_COST = 650   # max total cost for swing trades ($)

TRADE_CONFIG = {
    # Day trades — $1 OTM, 3 contracts, 1-day DTE, Mon-Thu only
    "SPY":  {"type": "day",   "contracts": 3, "dte_days": 1, "otm": 1},
    "QQQ":  {"type": "day",   "contracts": 3, "dte_days": 1, "otm": 1},
    # IWM — day trade or swing
    "IWM":  {"type": "both",  "contracts": 3, "dte_day_days": 1, "dte_swing_days": 7, "otm_day": 1, "otm_swing": 1},
    # Stock swings — 1 contract, $1 OTM, max $550, hold up to 3 weeks
    "AMD":  {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "TSLA": {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "META": {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "NVDA": {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "INTC": {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "TSM":  {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "CAR":  {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
    "F":    {"type": "swing", "contracts": 1, "dte_days": 21, "otm": 1, "max_cost": 550},
}
RSI_TOP_THRESHOLD   = 65   # RSI at or above → look for PUT
RSI_BOT_THRESHOLD   = 38   # RSI at or below → look for CALL
MAX_ABOVE_EMA20     = 3.0  # max % above EMA20 for PUT entry
MAX_BELOW_EMA20     = 2.0  # max % below EMA20 for CALL entry


# ── Helpers ───────────────────────────────────────────────────────────────────
def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL; msg["To"] = EMAIL; msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain", "utf-8"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL, EMAIL_PASS)
            s.send_message(msg)
        print(f"  email sent: {subject}")
    except Exception as e:
        print(f"  email error: {e}")


def now_ct():
    return datetime.datetime.now(CT)


def is_trading_hours():
    n = now_ct()
    if n.weekday() >= 5:
        return False
    h, m = n.hour, n.minute
    after_open  = (h == 8 and m >= 45) or h >= 9
    before_cut  = h < 14 or (h == 14 and m <= 30)
    return after_open and before_cut


def get_week_counts():
    week = datetime.date.today().strftime("%Y-W%W")
    try:
        with open(WEEK_FILE) as f:
            d = json.load(f)
        if d.get("week") != week:
            return {"day": 0, "iwm_swing": 0, "stock_swing": 0, "daily_trade_date": ""}
        return {
            "day":              d.get("day", 0),
            "iwm_swing":        d.get("iwm_swing", 0),
            "stock_swing":      d.get("stock_swing", 0),
            "daily_trade_date": d.get("daily_trade_date", ""),
        }
    except:
        return {"day": 0, "iwm_swing": 0, "stock_swing": 0, "daily_trade_date": ""}


def already_traded_today():
    """Check Alpaca directly — max 1 trade per day. Survives container restarts."""
    try:
        import requests
        today = datetime.datetime.utcnow().strftime("%Y-%m-%dT00:00:00Z")
        r = requests.get(
            f"https://paper-api.alpaca.markets/v2/account/activities/FILL?after={today}",
            headers={"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": SECRET_KEY}
        )
        fills = r.json()
        buys = [f for f in fills if isinstance(f, dict) and f.get("side") == "buy"]
        if buys:
            print(f"  Already traded today ({len(buys)} fills) — skipping")
            return True
        return False
    except Exception as e:
        print(f"  already_traded_today check failed: {e}")
        return False


def log_trade(trade_type):
    week  = datetime.date.today().strftime("%Y-W%W")
    counts = get_week_counts()
    counts[trade_type] = counts.get(trade_type, 0) + 1
    counts["week"] = week
    counts["daily_trade_date"] = datetime.date.today().strftime("%Y-%m-%d")
    with open(WEEK_FILE, "w") as f:
        json.dump(counts, f)
    return counts


def can_trade(symbol):
    """Return (allowed, trade_type) or (False, reason)."""
    counts = get_week_counts()
    today  = datetime.date.today()
    cfg    = TRADE_CONFIG.get(symbol, {})
    t      = cfg.get("type", "swing")

    # MAX 1 TRADE PER DAY — pick the best setup, not all of them
    if already_traded_today():
        return False, "already_traded_today"

    # No trades on Friday for day trades
    if today.weekday() == 4 and symbol in DAY_TRADE_TICKERS:
        return False, "no_day_friday"

    if symbol in ("SPY", "QQQ"):
        if counts["day"] >= MAX_DAY_TRADES:
            return False, "day_limit"
        return True, "day"

    if symbol == "IWM":
        # Use as day trade first, then swing if day limit reached
        if counts["day"] < MAX_DAY_TRADES:
            return True, "day"
        if counts["iwm_swing"] < MAX_IWM_SWINGS:
            return True, "iwm_swing"
        return False, "iwm_limit"

    # Individual stocks — swing only
    if counts["stock_swing"] >= MAX_STOCK_SWINGS:
        return False, "stock_swing_limit"
    return True, "stock_swing"


def get_scan_tickers():
    tickers = list(BASE_TICKERS)
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        for t in data.get("jr_watchlist", []):
            if t not in tickers:
                tickers.append(t)
    except:
        pass
    return tickers


def get_open_tickers():
    try:
        positions = tc.get_all_positions()
        return {"".join(c for c in p.symbol if c.isalpha()) for p in positions}
    except:
        return set()


# ── Indicators ────────────────────────────────────────────────────────────────
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    return 100 - (100 / (1 + gain / loss))


# ── Real-time bar fetch ───────────────────────────────────────────────────────
def get_bars(symbol):
    """
    Fetch last 3 days of 5-min bars from Alpaca.
    Returns a DataFrame with columns: open, high, low, close, volume.
    Returns None if not enough data.
    """
    try:
        start = now_ct() - datetime.timedelta(days=3)
        req = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=TimeFrame(5, TimeFrameUnit.Minute),
            start=start,
            feed="iex",
        )
        bars = data_client.get_stock_bars(req)
        df = bars.df

        # Flatten MultiIndex if needed
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(symbol, level="symbol")

        df = df.sort_index()
        return df if len(df) >= 30 else None

    except Exception as e:
        print(f"  {symbol} bars error: {e}")
        return None


# ── Signal detection ─────────────────────────────────────────────────────────
def analyze(symbol):
    """
    Returns dict with technicals + signal, or None if no data.
    Signal: 'PUT' at tops, 'CALL' at bottoms.
    """
    df = get_bars(symbol)
    if df is None:
        return None

    close = df["close"]
    high  = df["high"]
    low   = df["low"]
    open_ = df["open"]

    e8  = ema(close, 8).iloc[-1]
    e20 = ema(close, 20).iloc[-1]
    e50 = ema(close, 50).iloc[-1]
    r   = rsi(close).iloc[-1]
    p   = close.iloc[-1]

    pct_from_ema20 = (p - e20) / e20 * 100

    # Last candle rejection patterns
    c, o, h, l = p, open_.iloc[-1], high.iloc[-1], low.iloc[-1]
    body    = abs(c - o)
    candle  = h - l
    upper_w = h - max(c, o)
    lower_w = min(c, o) - l
    bearish = candle > 0 and upper_w > body * 1.5 and upper_w > candle * 0.4
    bullish = candle > 0 and lower_w > body * 1.5 and lower_w > candle * 0.4

    signal = reason = None

    # ── PUT: price at a top ───────────────────────────────────────────────────
    # RSI overbought + price above EMAs + bearish rejection candle
    if r >= RSI_TOP_THRESHOLD and p > e8 and p > e20 and 0 <= pct_from_ema20 <= MAX_ABOVE_EMA20:
        if bearish:
            signal = "PUT"
            reason = f"RSI {r:.0f} + bearish rejection — top signal"
        elif r >= 72:
            signal = "PUT"
            reason = f"RSI {r:.0f} — very overbought above all EMAs"

    # ── CALL: price at a bottom ───────────────────────────────────────────────
    # RSI oversold + price below EMAs + bullish rejection candle
    if signal is None:
        if r <= RSI_BOT_THRESHOLD and p < e8 and p < e20 and -MAX_BELOW_EMA20 <= pct_from_ema20 <= 0:
            if bullish:
                signal = "CALL"
                reason = f"RSI {r:.0f} + bullish rejection — bottom signal"
            elif r <= 32:
                signal = "CALL"
                reason = f"RSI {r:.0f} — very oversold below all EMAs"

    return {
        "symbol":   symbol,
        "price":    p,
        "ema8":     e8,
        "ema20":    e20,
        "ema50":    e50,
        "rsi":      r,
        "pct":      pct_from_ema20,
        "bearish":  bearish,
        "bullish":  bullish,
        "signal":   signal,
        "reason":   reason,
    }


# ── Execute trade ─────────────────────────────────────────────────────────────
def execute(t, trade_type):
    symbol    = t["symbol"]
    direction = t["signal"]
    price     = t["price"]
    cfg       = TRADE_CONFIG.get(symbol, {"contracts": 5, "dte_days": 7, "otm": 3})

    # IWM uses different DTE/OTM depending on day trade vs swing
    if symbol == "IWM" and trade_type in ("day",):
        dte = cfg.get("dte_day_days", 1)
        otm = cfg.get("otm_day", 1)
    elif symbol == "IWM":
        dte = cfg.get("dte_swing_days", 7)
        otm = cfg.get("otm_swing", 3)
    else:
        dte = cfg.get("dte_days", 7)
        otm = cfg.get("otm", 3)

    strike_target = price - otm if direction == "PUT" else price + otm
    exp_to        = (datetime.datetime.now() + datetime.timedelta(days=dte)).date()

    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[symbol],
            contract_type=ContractType.PUT if direction == "PUT" else ContractType.CALL,
            strike_price_gte=str(round(strike_target - 3, 0)),
            strike_price_lte=str(round(strike_target + 3, 0)),
            expiration_date_gte=datetime.date.today(),
            expiration_date_lte=exp_to,
        )
        contracts = tc.get_option_contracts(req)
        if not contracts.option_contracts:
            print(f"  {symbol}: no contract found near ${strike_target:.0f}")
            send_email(f"No Contract — {symbol} {direction}",
                       f"Signal fired but no contract found.\n{t['reason']}\nStock: ${price:.2f}")
            return

        # Filter to the correct contract type (PUT vs CALL) by checking symbol char
        type_char = "P" if direction == "PUT" else "C"
        matching = [c for c in contracts.option_contracts
                    if len(c.symbol) > len(symbol) + 6
                    and c.symbol[len(symbol) + 6] == type_char]
        pool = matching if matching else contracts.option_contracts

        contract = min(pool, key=lambda c: abs(float(c.strike_price) - strike_target))

        contract_price = float(contract.close_price or 0)
        if contract_price <= 0:
            print(f"  {symbol}: contract has no price")
            return

        limit = round(contract_price * 1.05, 2)
        total_cost = round(contract_price * 100, 2)

        # Cost limits per trade type
        max_cost = MAX_DAY_TRADE_COST if trade_type == "day" else MAX_SWING_TRADE_COST
        if total_cost > max_cost:
            print(f"  {symbol}: contract ${total_cost:.0f} over ${max_cost} max for {trade_type} — skipping")
            return

        # Stock swing qty tiers
        if trade_type == "stock_swing":
            if total_cost > 550:
                print(f"  {symbol}: contract ${total_cost:.0f} over $550 max — skipping")
                return
            elif total_cost <= 150:
                qty = 3
            elif total_cost <= 250:
                qty = 3
            elif total_cost <= 399:
                qty = 2
            else:
                qty = 1
        else:
            qty = cfg["contracts"]

        cost  = round(limit * qty * 100, 0)

        order = tc.submit_order(LimitOrderRequest(
            symbol=contract.symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=limit,
        ))

        # Wait up to 2 minutes — if still not filled, cancel and retry at market
        for _ in range(24):   # 24 × 5s = 120s
            time.sleep(5)
            refreshed = tc.get_order_by_id(order.id)
            if refreshed.status.value in ("filled", "partially_filled"):
                break
        else:
            try:
                tc.cancel_order_by_id(order.id)
                print(f"  {symbol}: limit not filled in 2 min — cancelling, no retry")
                send_email(f"Trade Not Filled — {symbol}", f"{symbol} {direction} limit order did not fill in 2 minutes. Cancelled. No market order placed.")
            except Exception as retry_err:
                print(f"  {symbol}: cancel error — {retry_err}")

        week_count = log_trade(trade_type)

        body = (
            f"AUTO-TRADE FIRED\n"
            f"{'='*45}\n"
            f"  {symbol} {direction} — {t['reason']}\n\n"
            f"  Stock:    ${price:.2f}\n"
            f"  RSI:      {t['rsi']:.1f}\n"
            f"  EMA20:    {t['pct']:+.1f}% from EMA20\n"
            f"  Pattern:  {'Bearish rejection' if t['bearish'] else 'Bullish rejection' if t['bullish'] else 'Momentum only'}\n\n"
            f"  Contract: {contract.symbol}\n"
            f"  Strike:   ${contract.strike_price} | Exp: {contract.expiration_date}\n"
            f"  Qty:      {qty} contracts\n"
            f"  Limit:    ${limit} | Est. cost: ${cost:.0f}\n"
            f"  Order ID: {order.id}\n"
            f"  Time:     {now_ct().strftime('%I:%M %p CT')}\n\n"
            f"  Trades this week: {week_count}/{MAX_TRADES_PER_WEEK}"
        )
        print(body)
        send_email(f"AUTO-TRADE: {direction} {symbol} ${contract.strike_price}", body)

    except Exception as e:
        err = f"{symbol} order error: {e}"
        print(f"  {err}")
        send_email(f"Auto-Trade Error — {symbol}", err)


# ── Scan loop ─────────────────────────────────────────────────────────────────
def scan():
    counts = get_week_counts()
    print(f"\n{'-'*50}")
    print(f"[{now_ct().strftime('%I:%M %p CT')}]  "
          f"Day: {counts['day']}/{MAX_DAY_TRADES}  "
          f"IWM swing: {counts['iwm_swing']}/{MAX_IWM_SWINGS}  "
          f"Stock swings: {counts['stock_swing']}/{MAX_STOCK_SWINGS}")

    open_tickers  = get_open_tickers()
    tickers       = get_scan_tickers()
    signals_found = 0

    for symbol in tickers:
        if symbol in open_tickers:
            continue

        allowed, trade_type = can_trade(symbol)
        if not allowed:
            continue

        t = analyze(symbol)
        if t is None:
            continue

        arrow = "^" if t["price"] > t["ema20"] else "v"
        flag  = f"  ** SIGNAL: {t['signal']} [{trade_type}] -- {t['reason']}" if t["signal"] else ""
        print(f"  {symbol:<6} ${t['price']:>8.2f}  RSI {t['rsi']:>5.1f}  "
              f"{arrow}EMA20 {t['pct']:>+5.1f}%  "
              f"{'[BEARISH] ' if t['bearish'] else ''}"
              f"{'[BULLISH] ' if t['bullish'] else ''}"
              f"{flag}")

        if t["signal"]:
            signals_found += 1
            execute(t, trade_type)
            time.sleep(2)

    if signals_found == 0:
        print("  No signals this scan.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("-" * 50)
    print("  BlockaBeatz Real-Time Auto Trader")
    print("  5-min Alpaca bars | Paper mode")
    print(f"  Tickers: {', '.join(BASE_TICKERS)}")
    print("  Hours: 8:45 AM - 2:30 PM CT | Scan every 5 min")
    print("=" * 50 + "\n")

    send_email("Auto-Trader Online",
               f"Real-time auto-trader is live.\n"
               f"Scanning {len(BASE_TICKERS)} tickers every 5 min on 5-min bars.\n"
               f"Will fire trades automatically when tops/bottoms detected.\n"
               f"Trading hours: 8:45 AM – 2:30 PM CT")

    while True:
        try:
            if is_trading_hours():
                scan()
            else:
                print(f"  [{now_ct().strftime('%I:%M %p CT')}] Outside trading hours — waiting...")
        except Exception as e:
            print(f"  Loop error: {e}")
            send_email("Auto-Trader Error", str(e))

        time.sleep(300)
