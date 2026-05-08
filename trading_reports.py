import os
import json
import time
import smtplib
import schedule
import datetime
import pytz
import pandas as pd
import yfinance as yf
import feedparser
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import OrderStatus

# ── Credentials ──────────────────────────────────────────────────────────────
ALPACA_API_KEY    = os.environ.get("ALPACA_API_KEY",    "PK2G5C5BQQ7AP5WNWBEUSKXOTI")
ALPACA_SECRET_KEY = os.environ.get("ALPACA_SECRET_KEY", "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP")
PAPER_TRADING     = os.environ.get("PAPER_TRADING", "true").lower() == "true"

EMAIL_FROM     = os.environ.get("EMAIL_FROM",         "Blocka9od@gmail.com")
EMAIL_TO       = os.environ.get("EMAIL_TO",           "Blocka9od@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_APP_PASSWORD", "dnlw dleb ryxs cljg")

WEEKLY_PROFIT_TARGET = float(os.environ.get("WEEKLY_PROFIT_TARGET", "5000"))
CT = pytz.timezone("America/Chicago")

BASE_TICKERS   = ['SPY', 'IWM', 'QQQ', 'AMD', 'TSLA', 'META', 'NVDA', 'INTC', 'TSM', 'CAR', 'F']
WATCHLIST_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "watchlist.json")


def get_scan_tickers():
    """BASE_TICKERS + any tickers JR has traded (from watchlist.json)."""
    tickers = list(BASE_TICKERS)
    try:
        with open(WATCHLIST_FILE) as f:
            data = json.load(f)
        for t in data.get("jr_watchlist", []):
            if t not in tickers:
                tickers.append(t)
    except Exception:
        pass
    return tickers


TICKERS = BASE_TICKERS  # kept for any legacy references

trading_client = TradingClient(ALPACA_API_KEY, ALPACA_SECRET_KEY, paper=PAPER_TRADING)


# ── Helpers ───────────────────────────────────────────────────────────────────
def is_weekday():
    return datetime.datetime.now(CT).weekday() < 5


def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"]    = EMAIL_FROM
    msg["To"]      = EMAIL_TO
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL_FROM, EMAIL_PASSWORD)
        s.send_message(msg)
    print(f"  Email sent: {subject}")


def calc_ema(series, period):
    return series.ewm(span=period, adjust=False).mean()


def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss
    return 100 - (100 / (1 + rs))


def get_technicals(symbol):
    df = yf.download(symbol, period="60d", interval="1d", auto_adjust=True, progress=False)
    close = df["Close"].squeeze()
    high  = df["High"].squeeze()
    low   = df["Low"].squeeze()
    open_ = df["Open"].squeeze()

    ema8  = calc_ema(close, 8)
    ema20 = calc_ema(close, 20)
    ema50 = calc_ema(close, 50)
    rsi   = calc_rsi(close, 14)

    p      = close.iloc[-1]
    e8     = ema8.iloc[-1]
    e20    = ema20.iloc[-1]
    e50    = ema50.iloc[-1]
    r      = rsi.iloc[-1]
    low20  = close.tail(20).min()

    rules = {
        "below_ema8":       p < e8,
        "below_ema20":      p < e20,
        "below_ema50":      p < e50,
        "rsi_in_range":     38 <= r <= 60,
        "not_overextended": p >= e20 * 0.98,
        "not_at_20d_low":   p > low20 * 1.005,
    }
    score = sum(rules.values())

    # ── Pattern Detection ──────────────────────────────────────────────────────
    c = close.values
    h = high.values
    l = low.values
    o = open_.values
    patterns = []

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

    recent_h = h[-20:]
    sorted_h = sorted(enumerate(recent_h), key=lambda x: -x[1])
    if len(sorted_h) >= 2:
        h1_idx, h1 = sorted_h[0]; h2_idx, h2 = sorted_h[1]
        if abs(h1_idx - h2_idx) >= 5 and abs(h1 - h2) / h1 < 0.015:
            patterns.append("DOUBLE TOP")

    recent_l = l[-20:]
    sorted_l = sorted(enumerate(recent_l), key=lambda x: x[1])
    if len(sorted_l) >= 2:
        l1_idx, l1 = sorted_l[0]; l2_idx, l2 = sorted_l[1]
        if abs(l1_idx - l2_idx) >= 5 and abs(l1 - l2) / l1 < 0.015:
            patterns.append("DOUBLE BOTTOM")

    if len(h) >= 30:
        lh, hh, rh = max(h[-30:-20]), max(h[-20:-10]), max(h[-10:])
        ll, hl, rl = min(l[-30:-20]), min(l[-20:-10]), min(l[-10:])
        if hh > lh * 1.01 and hh > rh * 1.01 and abs(lh - rh) / lh < 0.04:
            patterns.append("HEAD & SHOULDERS")
        if hl < ll * 0.99 and hl < rl * 0.99 and abs(ll - rl) / ll < 0.04:
            patterns.append("INVERSE H&S")

    return dict(symbol=symbol, price=p, ema8=e8, ema20=e20, ema50=e50,
                rsi=r, low20=low20, rules=rules, score=score,
                patterns=list(set(patterns)))


def swing_direction(t):
    """Determine 3-day swing direction and entry quality."""
    p, e8, e20, e50, r = t["price"], t["ema8"], t["ema20"], t["ema50"], t["rsi"]
    score = t["score"]

    if score == 6:
        direction = "CALLS (Strong Pullback — Full Setup)"
        quality   = "A+ Entry | All 6/6 rules met"
    elif score >= 4 and t["rules"]["not_overextended"]:
        direction = "CALLS (Pullback — Good Discount)"
        quality   = f"B Entry | {score}/6 rules met"
    elif p > e20 and e8 > e20 > e50:
        direction = "CALLS (Trend Continuation)"
        quality   = "B Entry | Bullish EMA alignment"
    elif p < e50 and r < 38:
        direction = "PUTS (Downtrend — Oversold)"
        quality   = "C Entry | Price below all EMAs, RSI low"
    elif p < e50 and not t["rules"]["rsi_in_range"]:
        direction = "PUTS (Bearish Momentum)"
        quality   = f"C Entry | {score}/6 rules met"
    else:
        direction = "WAIT / NEUTRAL"
        quality   = "No clean setup — do not force a trade"

    return direction, quality


# ── News ──────────────────────────────────────────────────────────────────────
OIL_KEYWORDS = ["oil", "crude", "opec", "wti", "brent", "energy", "petroleum"]

MACRO_EVENTS = {
    "FOMC / Fed Decision":   ["fomc", "federal reserve", "fed meeting", "rate decision",
                               "rate hike", "rate cut", "powell", "fed decision"],
    "CPI / Inflation Data":  ["cpi", "consumer price index", "inflation data", "inflation report",
                               "core inflation"],
    "PPI Data":              ["ppi", "producer price index", "producer price"],
    "Jobs Report / NFP":     ["jobs report", "nonfarm payroll", "nfp", "unemployment report",
                               "jobless claims", "adp employment", "adp report"],
    "GDP Report":            ["gdp report", "gross domestic product", "gdp growth"],
    "Retail Sales":          ["retail sales report", "retail sales data"],
    "ISM / PMI":             ["ism manufacturing", "ism services", "pmi data",
                               "purchasing managers"],
    "Earnings Season":       ["earnings season", "earnings week", "big tech earnings"],
}

RSS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=SPY,IWM,QQQ&region=US&lang=en-US",
    "https://www.cnbc.com/id/100003114/device/rss/rss.html",
    "https://feeds.marketwatch.com/marketwatch/topstories/",
]

def get_all_headlines():
    headlines, oil_news = [], []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:8]:
                title = entry.get("title", "")
                if title and title not in headlines:
                    headlines.append(title)
                    if any(k in title.lower() for k in OIL_KEYWORDS):
                        oil_news.append(title)
        except Exception:
            pass
    return headlines[:12], oil_news


def get_headlines():
    return get_all_headlines()


def get_macro_events():
    """Return dict of {event_name: headline} for any macro events detected today."""
    all_headlines, _ = get_all_headlines()
    found = {}
    for title in all_headlines:
        title_lower = title.lower()
        for event_name, keywords in MACRO_EVENTS.items():
            if event_name not in found:
                if any(kw in title_lower for kw in keywords):
                    found[event_name] = title
    return found


# ── 2:40 PM Report ────────────────────────────────────────────────────────────
def send_240_report():
    if not is_weekday():
        return
    now      = datetime.datetime.now(CT)
    date_str = now.strftime("%A, %B %d, %Y")

    iwm = get_technicals("IWM")
    spy = get_technicals("SPY")
    headlines, oil_news = get_headlines()

    def fmt(t):
        direction, quality = swing_direction(t)
        rules = t["rules"]
        checks = "\n".join([
            f"  {'✅' if rules['below_ema8']       else '❌'} Price below EMA 8       ({t['price']:.2f} vs {t['ema8']:.2f})",
            f"  {'✅' if rules['below_ema20']      else '❌'} Price below EMA 20      ({t['price']:.2f} vs {t['ema20']:.2f})",
            f"  {'✅' if rules['below_ema50']      else '❌'} Price below EMA 50      ({t['price']:.2f} vs {t['ema50']:.2f})",
            f"  {'✅' if rules['rsi_in_range']     else '❌'} RSI 38–60               (RSI = {t['rsi']:.1f})",
            f"  {'✅' if rules['not_overextended'] else '❌'} Not overextended        (EMA20 -2% floor = {t['ema20']*0.98:.2f})",
            f"  {'✅' if rules['not_at_20d_low']   else '❌'} Not at 20-day low       (Low = {t['low20']:.2f})",
        ])
        return (
            f"{t['symbol']} — 3-Day Swing Direction\n"
            f"  Price:     ${t['price']:.2f}\n"
            f"  EMA 8/20/50: {t['ema8']:.2f} / {t['ema20']:.2f} / {t['ema50']:.2f}\n"
            f"  RSI (14):  {t['rsi']:.1f}\n"
            f"  Score:     {t['score']}/6\n\n"
            f"  6/6 Checklist:\n{checks}\n\n"
            f"  ➡ Direction: {direction}\n"
            f"  ➡ Quality:   {quality}"
        )

    oil_section = ""
    if oil_news:
        oil_section = "\n🛢 OIL HEADLINES (impacts GLD, energy tickers):\n" + \
                      "\n".join(f"  • {h}" for h in oil_news[:3])

    news_section = "\n📰 TOP MARKET HEADLINES:\n" + \
                   "\n".join(f"  • {h}" for h in headlines[:6])

    body = f"""
SWING DIRECTION REPORT — {date_str}
3-Day Outlook | Sent at 2:40 PM CT
{'='*55}

{fmt(iwm)}

{'─'*55}

{fmt(spy)}

{'='*55}
{oil_section}
{news_section}

{'='*55}
Trade Rules Reminder:
  • NEVER buy calls at the top without a confirmed trend
  • NEVER sell at the bottom without a confirmed trend
  • Get the discount — reversal entries only
  • 5 trades/week | Day: $1,500–$2,000 | Swing: $2,500–$3,000
  • Weekly target: $5,000

Trade like: JRGreatness | HoneyDripNet | School of Trade | Callme100k
    """.strip()

    send_email(f"📊 Swing Report ({date_str})", body)


# ── 3:00 PM Report ────────────────────────────────────────────────────────────
def send_300_report():
    if not is_weekday():
        return
    now      = datetime.datetime.now(CT)
    date_str = now.strftime("%A, %B %d, %Y")

    try:
        account = trading_client.get_account()
        portfolio_value = float(account.portfolio_value)
        last_equity     = float(account.last_equity)
        daily_pnl       = portfolio_value - last_equity

        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        req = GetOrdersRequest(status=OrderStatus.FILLED, after=today_start, limit=50)
        orders = trading_client.get_orders(filter=req)

        order_lines = ""
        if orders:
            for o in orders:
                filled_price = float(o.filled_avg_price) if o.filled_avg_price else 0
                order_lines += f"\n  {o.symbol:<6} | {o.side.value.upper():<4} | qty {o.qty} | filled @ ${filled_price:.2f}"
        else:
            order_lines = "\n  No trades executed today"

        pnl_icon = "🟢" if daily_pnl >= 0 else "🔴"
        body = f"""
DAILY P&L REPORT — {date_str}
End of Day Summary | 3:00 PM CT
{'='*55}

Portfolio Value:     ${portfolio_value:>12,.2f}
Daily P&L:          ${daily_pnl:>+12,.2f}  {pnl_icon}

Weekly Target:       ${WEEKLY_PROFIT_TARGET:>12,.2f}

Today's Filled Orders:{order_lines}

{'='*55}
Paper Trading: {'YES ✅' if PAPER_TRADING else 'LIVE 🔴'}
        """.strip()

        send_email(f"{pnl_icon} Daily P&L ({date_str}) ${daily_pnl:+,.2f}", body)

    except Exception as e:
        send_email(f"⚠️ P&L Report Error — {date_str}", f"Could not fetch P&L:\n{e}")


# ── Morning Alert — Earnings + Macro (8:15 AM CT) ────────────────────────────
def get_earnings_today():
    today = datetime.datetime.now(CT).date()
    hits = []
    for symbol in get_scan_tickers():
        try:
            cal = yf.Ticker(symbol).calendar
            if cal is None:
                continue
            if isinstance(cal, dict):
                dates = cal.get("Earnings Date", [])
                if not hasattr(dates, "__iter__") or isinstance(dates, str):
                    dates = [dates]
            else:
                continue
            for d in dates:
                if hasattr(d, "date"):
                    d = d.date()
                if str(d) == str(today):
                    hits.append(symbol)
                    break
        except Exception:
            pass
    return hits


def send_morning_alert():
    if not is_weekday():
        return
    today_str = datetime.datetime.now(CT).strftime("%A, %B %d, %Y")

    earnings    = get_earnings_today()
    macro_today = get_macro_events()

    if not earnings and not macro_today:
        return

    sections = []

    # ── Macro Events ──────────────────────────────────────────────────────────
    if macro_today:
        macro_lines = [f"  🔔 {event}: \"{headline}\"" for event, headline in macro_today.items()]
        impact = []
        if any("FOMC" in e or "Fed" in e for e in macro_today):
            impact.append("FOMC/Fed day: expect whipsaw + trend reversal after announcement")
        if any("CPI" in e or "PPI" in e for e in macro_today):
            impact.append("Inflation data: gap open likely — wait for first 15 min to settle")
        if any("Jobs" in e or "NFP" in e for e in macro_today):
            impact.append("Jobs data: IWM/SPY will react hard at open — wait for direction")
        if any("GDP" in e for e in macro_today):
            impact.append("GDP release: watch QQQ reaction — tech leads direction")
        impact_str = "\n".join(f"  ⚡ {i}" for i in impact) if impact else "  ⚡ Trade carefully — reduce size on macro event days"
        sections.append(
            f"📅 MACRO EVENTS TODAY:\n{'─'*50}\n"
            + "\n".join(macro_lines)
            + f"\n\n{impact_str}"
        )

    # ── Earnings ──────────────────────────────────────────────────────────────
    if earnings:
        earn_lines = []
        for symbol in earnings:
            try:
                t = get_technicals(symbol)
                direction, quality = swing_direction(t)
                bias = "PUTS" if "PUTS" in direction else "CALLS" if "CALLS" in direction else "NEUTRAL"
                earn_lines.append(
                    f"  {symbol} — ${t['price']:.2f} | RSI: {t['rsi']:.1f} | Score: {t['score']}/6\n"
                    f"  Bias: {bias} | {quality}\n"
                    f"  → Get in BEFORE the move, not after"
                )
            except Exception:
                earn_lines.append(f"  {symbol} — earnings today (no technicals available)")
        sections.append(
            f"⚠️  EARNINGS TODAY: {', '.join(earnings)}\n{'─'*50}\n"
            + "\n\n".join(earn_lines)
            + "\n\n  STRATEGY:\n"
            + "  • RSI 65+ & above EMAs → TOP → PUTS before the drop\n"
            + "  • RSI 38 or below & below EMAs → BOTTOM → CALLS\n"
            + "  • Size DOWN on earnings — binary event"
        )

    body = f"""
🌅 MORNING ALERT — {today_str}
{'='*55}

{f"{chr(10)*2}{'='*55}{chr(10)*2}".join(sections)}

{'='*55}
Watch for setups at 8:45 AM CT — get in before the move.
    """.strip()

    subj_parts = []
    if macro_today: subj_parts.append(", ".join(macro_today.keys()))
    if earnings:    subj_parts.append(f"Earnings: {', '.join(earnings)}")
    send_email(f"🌅 Morning Alert — {' | '.join(subj_parts)} — {today_str}", body)


# ── 2:00 PM Full Swing Scan ───────────────────────────────────────────────────
ETF_TICKERS   = {'SPY', 'IWM', 'QQQ'}
STOCK_TICKERS = {'AMD', 'TSLA', 'META', 'NVDA', 'INTC', 'TSM', 'CAR', 'F'}

def _fmt_scan_row(symbol, t, direction, quality):
    pats = t.get("patterns", [])
    bearish = [p for p in pats if p in ("BEARISH REJECTION", "DOUBLE TOP", "HEAD & SHOULDERS")]
    bullish = [p for p in pats if p in ("BULLISH REJECTION", "DOUBLE BOTTOM", "INVERSE H&S")]
    pat_str = ""
    if bearish: pat_str = f"\n     ⚠️  {', '.join(bearish)} → PUTS"
    elif bullish: pat_str = f"\n     ✨  {', '.join(bullish)} → CALLS"
    return (
        f"  {symbol:<6} ${t['price']:>8.2f} | RSI {t['rsi']:>5.1f} | Score {t['score']}/6\n"
        f"     ➡ {direction}\n"
        f"     ➡ {quality}{pat_str}"
    )


def _scan_quality(t, direction):
    if "A+" in direction or "A+" in _fmt_scan_row("", t, direction, ""):
        return 0
    q = ""
    _, q = swing_direction(t)
    if "A+" in q: return 0
    if "B Entry" in q: return 1
    if t.get("patterns"): return 2
    return 3


def send_swing_scan():
    if not is_weekday():
        return
    today_str = datetime.datetime.now(CT).strftime("%A, %B %d, %Y")

    all_tickers = get_scan_tickers()
    results = []
    for symbol in all_tickers:
        try:
            t = get_technicals(symbol)
            direction, quality = swing_direction(t)
            results.append((symbol, t, direction, quality))
        except Exception:
            pass

    def sort_key(x):
        _, t, _, q = x
        if "A+" in q: return 0
        if "B Entry" in q: return 1
        if t.get("patterns"): return 2
        return 3

    stocks = sorted([(s,t,d,q) for s,t,d,q in results if s in STOCK_TICKERS], key=sort_key)
    etfs   = sorted([(s,t,d,q) for s,t,d,q in results if s in ETF_TICKERS],   key=sort_key)

    call_stocks = [(s,t,d,q) for s,t,d,q in stocks if "CALLS" in d]
    put_stocks  = [(s,t,d,q) for s,t,d,q in stocks if "PUTS"  in d]
    pat_only    = [(s,t,d,q) for s,t,d,q in stocks if "NEUTRAL" in d and t.get("patterns")]

    stock_sections = []
    if call_stocks:
        stock_sections.append("🟢 CALL SETUPS — Buy the Dip:\n" +
                              "\n\n".join(_fmt_scan_row(*x) for x in call_stocks))
    if put_stocks:
        stock_sections.append("🔴 PUT SETUPS — Sell the Rip:\n" +
                              "\n\n".join(_fmt_scan_row(*x) for x in put_stocks))
    if pat_only:
        stock_sections.append("⚠️  PATTERN ALERTS (no clean trend, but watch):\n" +
                              "\n\n".join(_fmt_scan_row(*x) for x in pat_only))
    if not stock_sections:
        stock_sections.append("  No clean setups right now — do not force a trade.")

    etf_str = "\n\n".join(_fmt_scan_row(*x) for x in etfs) or "  No ETF data."

    body = f"""
📈 2:00 PM SWING SCAN — {today_str}
Enter by 2:30 PM CT | Holds overnight
{'='*55}

INDIVIDUAL STOCKS:
{'─'*55}
{f"{chr(10)*2}{'─'*55}{chr(10)*2}".join(stock_sections)}

{'='*55}
ETF MARKET DIRECTION:
{'─'*55}
{etf_str}

{'='*55}
SWING RULES:
  • Stocks:  5 contracts | $2,500–$3,000 | 3–5 strikes OTM
  • ETFs:    3–4 contracts | $1,500–$2,000 | ATM or $1 OTM
  • A+ and B entries only — never force a neutral setup
  • Cutoff: 2:30 PM CT
    """.strip()

    best = [s for s,t,d,q in stocks if "A+" in q or "B Entry" in q]
    subj = f"📈 2PM Scan: {', '.join(best[:4]) if best else 'No Clean Setups'} — {today_str}"
    send_email(subj, body)


# ── After-Hours Alert — Open Trades Only (3:30 PM + 4:30 PM CT) ──────────────
def get_open_trade_tickers():
    """Return set of underlying tickers with currently open trades."""
    tickers = set()
    # paper_trades.json
    try:
        json_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_trades.json")
        with open(json_path) as f:
            data = json.load(f)
        for trade in data.get("trades", []):
            if trade.get("status") == "open":
                tickers.add(trade["ticker"].upper())
    except Exception:
        pass
    # Alpaca open positions
    try:
        positions = trading_client.get_all_positions()
        for pos in positions:
            sym = pos.symbol
            # strip option suffix — underlying is the alpha prefix
            underlying = "".join(ch for ch in sym if ch.isalpha())
            if underlying:
                tickers.add(underlying.upper())
    except Exception:
        pass
    return tickers


def send_afterhours_alert():
    if not is_weekday():
        return
    today_str = datetime.datetime.now(CT).strftime("%A, %B %d, %Y")
    time_str  = datetime.datetime.now(CT).strftime("%I:%M %p CT")

    watch = get_open_trade_tickers()
    if not watch:
        return  # no open trades to watch

    movers = []
    for symbol in watch:
        try:
            info          = yf.Ticker(symbol).fast_info
            post_price    = getattr(info, "post_market_price",    None)
            regular_price = getattr(info, "last_price",           None) \
                          or getattr(info, "regular_market_price", None)
            if post_price and regular_price and regular_price > 0:
                pct = (post_price - regular_price) / regular_price * 100
                movers.append((symbol, regular_price, post_price, pct))
        except Exception:
            pass

    if not movers:
        return

    movers.sort(key=lambda x: abs(x[3]), reverse=True)
    lines = []
    for symbol, reg, post, pct in movers:
        icon = "🟢 ▲" if pct > 0 else "🔴 ▼"
        lines.append(f"  {icon} {symbol}: ${reg:.2f} → ${post:.2f} ({pct:+.1f}%)")

    body = f"""
🚨 AFTER-HOURS — YOUR OPEN TRADES — {today_str} @ {time_str}
{'='*55}

{chr(10).join(lines)}

{'='*55}
WHAT TO DO TOMORROW:
  • DOWN sharply → your PUTS are printing — plan exit at open
  • UP sharply   → your PUTS are bleeding — check stop loss
  • Don't chase — wait for 8:45 AM CT and confirm the move
    """.strip()

    send_email(f"🚨 After-Hours ({', '.join(m[0] for m in movers)}) — {today_str}", body)


# ── Scheduler ─────────────────────────────────────────────────────────────────
for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
    getattr(schedule.every(), day).at("08:15").do(send_morning_alert)
    getattr(schedule.every(), day).at("14:00").do(send_swing_scan)
    getattr(schedule.every(), day).at("14:40").do(send_240_report)
    getattr(schedule.every(), day).at("15:00").do(send_300_report)
    getattr(schedule.every(), day).at("15:30").do(send_afterhours_alert)
    getattr(schedule.every(), day).at("16:30").do(send_afterhours_alert)

if __name__ == "__main__":
    print("Trading report scheduler started.")
    print("  8:15 AM CT  — Morning alert (earnings + FOMC/CPI/jobs/macro events)")
    print("  2:00 PM CT  — Full swing scan (all stocks + ETFs, ranked by quality)")
    print("  2:40 PM CT  — IWM + SPY swing direction report")
    print("  3:00 PM CT  — Daily P&L report")
    print("  3:30 PM CT  — After-hours check (your open trades only)")
    print("  4:30 PM CT  — After-hours check (second pass)")
    print("Press Ctrl+C to stop.\n")
    while True:
        schedule.run_pending()
        time.sleep(30)
