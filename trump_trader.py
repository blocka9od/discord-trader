"""
Trump Stock Monitor — watches news every 60 seconds during market hours.
When Trump mentions a stock positively -> buy calls.
When Trump mentions a stock negatively -> buy puts.
Target: ~$450 total cost, 2-3 qty, 7-day DTE.
Take profit next day if $600-$1,500 in profit.
"""

import os, json, time, re, smtplib, datetime, pytz
import feedparser
import anthropic
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest
from alpaca.trading.enums import ContractType, OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

ALPACA_KEY    = os.environ.get("ALPACA_KEY",    "PK2G5C5BQQ7AP5WNWBEUSKXOTI")
ALPACA_SECRET = os.environ.get("ALPACA_SECRET", "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP")
EMAIL         = os.environ.get("EMAIL",         "Blocka9od@gmail.com")
EMAIL_PASS    = os.environ.get("EMAIL_PASS",    "dnlw dleb ryxs cljg")
ANTHROPIC_KEY = os.environ.get("ANTHROPIC_KEY", "")

tc          = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
data_client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
claude      = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
CT          = pytz.timezone("America/Chicago")

BOT_DIR         = os.path.dirname(os.path.abspath(__file__))
SEEN_FILE       = os.path.join(BOT_DIR, "trump_seen.json")
POSITIONS_FILE  = os.path.join(BOT_DIR, "trump_positions.json")

NEWS_FEEDS = [
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s=DJIA&region=US&lang=en-US",
    "https://feeds.skynews.com/feeds/rss/us.xml",
    "https://rss.cnn.com/rss/money_news_international.rss",
    "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
]

COMPANY_TO_TICKER = {
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL", "alphabet": "GOOGL",
    "amazon": "AMZN", "meta": "META", "facebook": "META", "nvidia": "NVDA",
    "tesla": "TSLA", "dell": "DELL", "intel": "INTC", "amd": "AMD",
    "ford": "F", "gm": "GM", "general motors": "GM", "boeing": "BA",
    "exxon": "XOM", "chevron": "CVX", "jpmorgan": "JPM", "goldman": "GS",
    "walmart": "WMT", "target": "TGT", "home depot": "HD", "disney": "DIS",
    "netflix": "NFLX", "uber": "UBER", "airbnb": "ABNB", "palantir": "PLTR",
    "us steel": "X", "steel": "X", "arm": "ARM", "tsmc": "TSM",
}

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL
        msg["To"]   = EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL, EMAIL_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f"Email error: {e}")

def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def load_positions():
    try:
        with open(POSITIONS_FILE) as f:
            return json.load(f)
    except:
        return []

def save_positions(positions):
    with open(POSITIONS_FILE, "w") as f:
        json.dump(positions, f, indent=2)

def is_market_hours():
    now = datetime.datetime.now(CT)
    if now.weekday() >= 5:
        return False
    market_open  = now.replace(hour=8, minute=30, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=59, second=0, microsecond=0)
    return market_open <= now <= market_close

def analyze_with_claude(title, description):
    """Ask Claude if this is Trump mentioning a stock and if it's positive or negative."""
    text = f"{title}\n{description}"
    if "trump" not in text.lower():
        return None
    try:
        resp = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=150,
            messages=[{"role": "user", "content": (
                f"You are a skilled trading AI. TRADING RULES: MAX 1 trade per day. "
                f"EXCEPTIONS that bypass this: (1) Trump stock mentions — trade immediately, "
                f"(2) Friday IWM straddle at 2:35 PM CT, (3) 5x opportunity setups. "
                f"Trump trades use $1 OTM, 7-day DTE, 2-3 qty targeting ~$450 cost. "
                f"TP: $600-$1,500 next day, hold 1 week max.\n\n"
                f"Does this news article mention Trump talking about a specific stock or company "
                f"in a way that could move its price?\n\n"
                f"{text}\n\n"
                f"Reply in this exact format only:\n"
                f"TICKER: [stock ticker or NONE]\n"
                f"SENTIMENT: [POSITIVE, NEGATIVE, or NEUTRAL]\n"
                f"REASON: [one sentence]"
            )}]
        )
        result = resp.content[0].text.strip()
        ticker_match = re.search(r'TICKER:\s*([A-Z]{1,5}|NONE)', result)
        sentiment_match = re.search(r'SENTIMENT:\s*(POSITIVE|NEGATIVE|NEUTRAL)', result)
        if ticker_match and sentiment_match:
            ticker    = ticker_match.group(1)
            sentiment = sentiment_match.group(1)
            if ticker == "NONE" or sentiment == "NEUTRAL":
                return None
            return {"ticker": ticker, "sentiment": sentiment, "text": text}
    except Exception as e:
        print(f"Claude error: {e}")

    # Fallback: keyword matching
    for company, ticker in COMPANY_TO_TICKER.items():
        if company in text.lower():
            positive_words = ["buy", "great", "good", "love", "deal", "invest", "amazing", "tremendous", "beautiful"]
            negative_words = ["bad", "tariff", "sanction", "investigate", "fine", "sue", "ban", "enemy"]
            if any(w in text.lower() for w in positive_words):
                return {"ticker": ticker, "sentiment": "POSITIVE", "text": text}
            if any(w in text.lower() for w in negative_words):
                return {"ticker": ticker, "sentiment": "NEGATIVE", "text": text}
    return None

def place_trade(ticker, sentiment):
    direction = "CALL" if sentiment == "POSITIVE" else "PUT"
    try:
        latest = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=ticker))
        price  = latest[ticker].price
    except Exception as e:
        print(f"  Price fetch failed for {ticker}: {e}")
        return

    strike = round(price + 1) if direction == "CALL" else round(price - 1)
    exp_to = (datetime.datetime.now() + datetime.timedelta(days=7)).date()

    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[ticker],
            contract_type=ContractType.CALL if direction == "CALL" else ContractType.PUT,
            strike_price_gte=str(strike - 1),
            strike_price_lte=str(strike + 1),
            expiration_date_gte=datetime.date.today(),
            expiration_date_lte=exp_to,
        )
        contracts = tc.get_option_contracts(req)
        if not contracts.option_contracts:
            print(f"  No contract found for {ticker}")
            return

        type_char = "C" if direction == "CALL" else "P"
        matching  = [c for c in contracts.option_contracts if type_char in c.symbol]
        pool      = matching if matching else contracts.option_contracts
        contract  = min(pool, key=lambda c: abs(float(c.strike_price) - strike))

        contract_price = float(contract.close_price or 0)
        if contract_price <= 0:
            return

        cost_2 = round(contract_price * 2 * 100, 2)
        cost_3 = round(contract_price * 3 * 100, 2)
        qty    = 2 if abs(cost_2 - 450) < abs(cost_3 - 450) else 3

        limit = round(contract_price * 1.08, 2)
        order = tc.submit_order(LimitOrderRequest(
            symbol=contract.symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=limit,
        ))

        pos = {
            "ticker":        ticker,
            "symbol":        contract.symbol,
            "direction":     direction,
            "qty":           qty,
            "entry_price":   contract_price,
            "entered":       datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
            "check_date":    (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d"),
            "order_id":      str(order.id),
            "open":          True,
        }
        positions = load_positions()
        positions.append(pos)
        save_positions(positions)

        body = (
            f"TRUMP TRADE PLACED\n"
            f"Ticker: {ticker} | Direction: {direction}\n"
            f"Contract: {contract.symbol}\n"
            f"Strike: ${contract.strike_price} | Exp: {contract.expiration_date}\n"
            f"Qty: {qty} | Entry: ${contract_price} | Limit: ${limit}\n"
            f"Est. Cost: ${round(limit * qty * 100, 2)}\n"
            f"Order ID: {order.id}\n\n"
            f"Take profit tomorrow if $600-$1,500 in profit.\n"
            f"Hold up to 1 week if not.\n\n"
            f"Trigger: {sentiment} Trump mention"
        )
        print(body)
        send_email(f"TRUMP TRADE: {ticker} {direction}", body)

    except Exception as e:
        err = f"Trade error for {ticker}: {e}"
        print(err)
        send_email("Trump Trade Error", err)

def check_open_positions():
    """Check yesterday's Trump trades — close if $600-$1,500 in profit."""
    positions = load_positions()
    today     = datetime.date.today().strftime("%Y-%m-%d")
    updated   = False

    for pos in positions:
        if not pos.get("open") or pos.get("check_date") != today:
            continue
        try:
            p      = tc.get_open_position(pos["symbol"])
            pnl    = float(p.unrealized_pl)
            price  = float(p.current_price)
            print(f"  Trump position {pos['symbol']}: P&L ${pnl:.2f}")

            if 600 <= pnl <= 1500:
                tc.close_position(pos["symbol"])
                pos["open"] = False
                updated = True
                send_email(
                    f"TRUMP TRADE CLOSED — ${pnl:.2f} profit",
                    f"Closed {pos['symbol']} x{pos['qty']}\n"
                    f"Entry: ${pos['entry_price']} | Current: ${price}\n"
                    f"Profit: ${pnl:.2f}\n"
                    f"Trigger: $600-$1,500 TP hit"
                )
            elif pnl > 1500:
                tc.close_position(pos["symbol"])
                pos["open"] = False
                updated = True
                send_email(
                    f"TRUMP TRADE CLOSED — ${pnl:.2f} profit (max hit)",
                    f"Closed {pos['symbol']} x{pos['qty']}\nProfit: ${pnl:.2f}"
                )
            else:
                # Not in range — extend check to tomorrow
                next_check = (datetime.date.today() + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
                pos["check_date"] = next_check
                updated = True
                print(f"  Holding — P&L ${pnl:.2f}, checking again tomorrow")
        except Exception as e:
            print(f"  Position check error {pos['symbol']}: {e}")

    if updated:
        save_positions(positions)

def run():
    print("Trump Stock Monitor started — watching news every 60s during market hours")
    seen = load_seen()

    while True:
        try:
            if is_market_hours():
                check_open_positions()

                for feed_url in NEWS_FEEDS:
                    try:
                        feed = feedparser.parse(feed_url)
                        for entry in feed.entries[:10]:
                            uid = entry.get("id") or entry.get("link") or entry.get("title")
                            if uid in seen:
                                continue
                            seen.add(uid)
                            save_seen(seen)

                            title = entry.get("title", "")
                            desc  = entry.get("summary", "")
                            result = analyze_with_claude(title, desc)
                            if result:
                                print(f"\nTrump mention detected: {result['ticker']} {result['sentiment']}")
                                print(f"  Source: {title}")
                                place_trade(result["ticker"], result["sentiment"])
                    except Exception as e:
                        print(f"Feed error: {e}")

            time.sleep(60)
        except KeyboardInterrupt:
            print("Trump monitor stopped")
            break
        except Exception as e:
            print(f"Loop error: {e}")
            time.sleep(60)

if __name__ == "__main__":
    run()
