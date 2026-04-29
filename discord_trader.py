import discord
import re
import io
import json
import smtplib
import httpx
import pytesseract
from PIL import Image
from datetime import datetime, date, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOptionContractsRequest, LimitOrderRequest
from alpaca.trading.enums import ContractType, OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
import anthropic

import os, platform
if platform.system() == "Windows":
    pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

# ── Credentials (set these as environment variables in Railway) ────────────────
USER_TOKEN    = os.environ["DISCORD_TOKEN"]
ALPACA_KEY    = os.environ["ALPACA_KEY"]
ALPACA_SECRET = os.environ["ALPACA_SECRET"]
EMAIL         = os.environ.get("EMAIL", "Blocka9od@gmail.com")
EMAIL_PASS    = os.environ["EMAIL_PASS"]

# ── Config ────────────────────────────────────────────────────────────────────
WATCH_SERVER       = "stock levels university"
WATCH_CHANNEL      = "free-watchlist-alerts"
WATCH_USER         = "jrgreatness"
CONTRACT_MIN       = 10      # minimum contract total cost ($10)
CONTRACT_MAX       = 150     # maximum contract total cost ($150)
MIN_STOCK_PRICE    = 10.0    # skip stocks that don't move in dollars
QTY_TIER1          = 12      # qty when contract costs $10–$25
QTY_TIER2          = 6       # qty when contract costs $26–$75
QTY_TIER3          = 3       # qty when contract costs $76–$150
TAKE_PROFIT_MIN    = 3.50    # 350% = 3.5x entry
TAKE_PROFIT_MAX    = 14.00   # 1300% = 14x entry
PNL_MIN            = -6.0    # min P&L % for normal entry
PNL_MAX            = 38.0    # max P&L % for normal entry
LATE_PNL_MIN       = 50.0    # min P&L % for late entry
LATE_PNL_MAX       = 133.0   # max P&L % for late entry
LATE_CONTRACT_MIN  = 350     # late entry contract min cost ($)
LATE_CONTRACT_MAX  = 750     # late entry contract max cost ($)
LATE_QTY           = 1       # late entry qty
LATE_TAKE_PROFIT   = 1.80    # late entry take profit (180%)
DAY_TRADE_LIMIT    = 2       # max day trades per week
DAY_TRADES_FILE    = r"C:\Users\ajblo\trading_bot\day_trades.json"

tc             = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
data_client    = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)
claude_client  = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_KEY", ""))

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

def get_day_trades_used():
    week = date.today().strftime("%Y-W%W")
    try:
        with open(DAY_TRADES_FILE, "r") as f:
            data = json.load(f)
        if data.get("week") != week:
            return 0
        return data.get("count", 0)
    except:
        return 0

def increment_day_trades():
    week = date.today().strftime("%Y-W%W")
    count = get_day_trades_used() + 1
    with open(DAY_TRADES_FILE, "w") as f:
        json.dump({"week": week, "count": count}, f)
    return count

def parse_trade(text):
    text = text.upper()
    ticker = None
    m = re.search(r'\$([A-Z]{1,5})\b|^([A-Z]{1,5})\s+\$?\d', text)
    if m:
        ticker = m.group(1) or m.group(2)

    strike = None
    m = re.search(r'\$?(\d{1,4}(?:\.\d{1,2})?)\s*(?:C\b|P\b|CALL|PUT)', text)
    if not m:
        m = re.search(r'(\d{2,4}(?:\.\d{1,2})?)', text)
    if m:
        strike = float(m.group(1))

    direction = None
    if re.search(r'\bCALL|\bCALLS|\d+C\b', text):
        direction = "CALL"
    elif re.search(r'\bPUT|\bPUTS|\d+P\b', text):
        direction = "PUT"

    expiry = None
    m = re.search(r'(\d{1,2})[/\-](\d{1,2})', text)
    if m:
        try:
            expiry = date(2026, int(m.group(1)), int(m.group(2)))
        except:
            pass

    if ticker and strike and direction:
        return {"ticker": ticker, "strike": strike, "direction": direction, "expiry": expiry}
    return None

def extract_pnl_from_screenshot(image_url):
    """Download JR's screenshot and OCR it to find the P&L percentage. Returns (pnl, ocr_text)."""
    try:
        resp     = httpx.get(image_url, timeout=10)
        img      = Image.open(io.BytesIO(resp.content))
        ocr_text = pytesseract.image_to_string(img)
        print(f"  OCR text: {ocr_text[:200]}")
        matches  = re.findall(r'([+-]?\d{1,3}(?:\.\d{1,2})?)\s*%', ocr_text)
        pnls     = [float(x) for x in matches if -100 <= float(x) <= 2000]
        if pnls:
            return pnls[0], ocr_text
        return None, ocr_text
    except Exception as e:
        print(f"  OCR failed: {e}")
    return None, None

def get_claude_opinion(trade, pnl, stock_price, contract_price, image_url=None):
    """Ask Claude if this trade is worth taking."""
    ticker    = trade["ticker"]
    strike    = trade["strike"]
    direction = trade["direction"]
    expiry    = trade.get("expiry")
    total     = round(contract_price * 100, 2)

    prompt = (
        f"You are a options trading assistant helping a trader decide whether to copy a trade from a trader named JR.\n\n"
        f"JR's trade details:\n"
        f"- Ticker: {ticker}\n"
        f"- Stock price: ${stock_price:.2f}\n"
        f"- Option: ${strike} {direction} exp {expiry}\n"
        f"- Contract price: ${contract_price:.2f}/share (${total} per contract)\n"
        f"- JR's current P&L on this position: {pnl}%\n\n"
        f"Entry rules:\n"
        f"- Normal entry: JR P&L between -6% and 38%, contract $10–$150\n"
        f"- Late entry: JR P&L between 50% and 133%, contract $350–$750, 1 qty only\n"
        f"- Stock must move $5–$15/day (no penny stocks)\n\n"
        f"Give a SHORT opinion (3-4 sentences max): Should the trader get in YES or NO, and the main reason why. "
        f"Consider JR's P&L timing, the stock's momentum, and whether the contract price makes sense."
    )

    messages = [{"role": "user", "content": prompt}]

    if image_url:
        try:
            img_data = httpx.get(image_url, timeout=10).content
            img_b64  = __import__('base64').b64encode(img_data).decode()
            messages = [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": img_b64}},
                {"type": "text", "text": prompt}
            ]}]
        except:
            pass

    try:
        resp = claude_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=200,
            messages=messages
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"Claude opinion unavailable: {e}"

async def execute_late_entry(trade, source_text, pnl, report=""):
    """Late entry: JR up 50-133%, 1 qty, $350-$750/contract, TP at 180%."""
    ticker    = trade["ticker"]
    strike    = trade["strike"]
    direction = trade["direction"]
    expiry    = trade.get("expiry")

    exp_from = date.today()
    exp_to   = (datetime.now() + timedelta(days=21)).date()
    if expiry:
        exp_from = expiry
        exp_to   = expiry

    try:
        req = GetOptionContractsRequest(
            underlying_symbols=[ticker],
            contract_type=ContractType.CALL if direction == "CALL" else ContractType.PUT,
            strike_price_gte=str(strike - 1),
            strike_price_lte=str(strike + 1),
            expiration_date_gte=exp_from,
            expiration_date_lte=exp_to,
        )
        contracts = tc.get_option_contracts(req)
        if not contracts.option_contracts:
            send_email("Late Entry — No Contract", f"JR P&L {pnl}%\n{source_text}\n\nNo contract found.")
            return

        contract   = contracts.option_contracts[0]
        price      = float(contract.close_price) if contract.close_price else None
        if price is None:
            send_email("Late Entry — No Price", f"JR P&L {pnl}%\n{source_text}\n\nCould not get contract price.")
            return

        total_cost = price * 100
        print(f"  Late entry contract: ${price:.2f}/share = ${total_cost:.0f}/contract")

        if not (LATE_CONTRACT_MIN <= total_cost <= LATE_CONTRACT_MAX):
            msg = f"SKIPPED late entry: contract ${total_cost:.0f} outside ${LATE_CONTRACT_MIN}–${LATE_CONTRACT_MAX} range"
            print(msg)
            send_email("Late Entry Skipped — Cost Out of Range", f"{source_text}\n\n{msg}")
            return

        tp_price = round(price * LATE_TAKE_PROFIT, 2)
        order = tc.submit_order(LimitOrderRequest(
            symbol=contract.symbol,
            qty=LATE_QTY,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(price * 1.05, 2),
        ))
        increment_day_trades()

        body = (
            f"LATE ENTRY — JR UP {pnl}%\n"
            f"Contract: {contract.symbol}\n"
            f"Strike: ${contract.strike_price} | Exp: {contract.expiration_date}\n"
            f"Direction: {direction} | Qty: {LATE_QTY}\n"
            f"Limit: ${round(price*1.05,2)} | Est. cost: ${round(price*1.05*100,2)}\n"
            f"*** TAKE PROFIT AT 180% = ${tp_price}/share ***\n"
            f"Order ID: {order.id}\n"
            f"Time: {datetime.now().strftime('%I:%M %p')}\n\n"
            f"── WHAT BOT SAW ──\n{report}"
        )
        print(body)
        send_email(f"LATE ENTRY: {ticker} {direction} ${contract.strike_price} — TP@180%", body)

    except Exception as e:
        err = f"Late entry error for {ticker}: {e}"
        print(err)
        send_email("Late Entry Error", err)

async def execute_trade(trade, source_text, report=""):
    ticker    = trade["ticker"]
    strike    = trade["strike"]
    direction = trade["direction"]
    expiry    = trade.get("expiry")

    # 1. Skip penny stocks / stocks that don't move in dollars
    try:
        latest      = data_client.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=ticker))
        stock_price = latest[ticker].price
        if stock_price < MIN_STOCK_PRICE:
            msg = f"SKIPPED {ticker}: stock price ${stock_price:.2f} — moves in cents, not dollars"
            print(msg)
            send_email("Trade Skipped — Penny Stock", f"{source_text}\n\n{msg}")
            return
    except Exception as e:
        print(f"  Price check failed for {ticker}: {e}")

    exp_from = date.today()
    exp_to   = (datetime.now() + timedelta(days=21)).date()
    if expiry:
        exp_from = expiry
        exp_to   = expiry

    # 2. Try JR's exact strike first, then 1-2 strikes OTM if needed
    if direction == "CALL":
        candidates = [strike, strike + 1, strike + 2]
    else:
        candidates = [strike, strike - 1, strike - 2]

    chosen_contract = None
    chosen_price    = None

    for s in candidates:
        try:
            req = GetOptionContractsRequest(
                underlying_symbols=[ticker],
                contract_type=ContractType.CALL if direction == "CALL" else ContractType.PUT,
                strike_price_gte=str(s - 0.5),
                strike_price_lte=str(s + 0.5),
                expiration_date_gte=exp_from,
                expiration_date_lte=exp_to,
            )
            contracts = tc.get_option_contracts(req)
            if not contracts.option_contracts:
                continue
            contract   = contracts.option_contracts[0]
            price      = float(contract.close_price) if contract.close_price else None
            if price is None:
                continue
            total_cost = price * 100
            label      = "JR's strike" if s == strike else f"${s} strike (OTM)"
            print(f"  {label}: ${price:.2f}/share = ${total_cost:.0f}/contract")
            if CONTRACT_MIN <= total_cost <= CONTRACT_MAX:
                chosen_contract = contract
                chosen_price    = price
                break
        except Exception as e:
            print(f"  Error checking strike {s}: {e}")

    if not chosen_contract:
        msg = (f"NO TRADE: {ticker} {direction} — no contract in ${CONTRACT_MIN}–${CONTRACT_MAX} range "
               f"(tried strikes {candidates})")
        print(msg)
        send_email("Trade Skipped — Out of Price Range", f"{source_text}\n\n{msg}")
        return

    # 3. Qty tiers based on contract cost
    total_cost = chosen_price * 100
    if total_cost <= 25:
        qty = QTY_TIER1
    elif total_cost <= 75:
        qty = QTY_TIER2
    else:
        qty = QTY_TIER3

    # Get Claude's opinion before placing order
    opinion = get_claude_opinion(trade, pnl if 'pnl' in dir() else None, stock_price, chosen_price)
    print(f"  Claude: {opinion}")

    try:
        order = tc.submit_order(LimitOrderRequest(
            symbol=chosen_contract.symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            limit_price=round(chosen_price * 1.05, 2),
        ))

        body = (
            f"TRADE COPIED FROM JR\n"
            f"Contract: {chosen_contract.symbol}\n"
            f"Strike: ${chosen_contract.strike_price} | Exp: {chosen_contract.expiration_date}\n"
            f"Direction: {direction} | Qty: {qty}\n"
            f"Limit: ${round(chosen_price*1.05,2)} | Est. cost: ${round(chosen_price*1.05*qty*100,2)}\n"
            f"Order ID: {order.id}\n"
            f"Time: {datetime.now().strftime('%I:%M %p')}\n\n"
            f"── CLAUDE'S OPINION ──\n{opinion}\n\n"
            f"── WHAT BOT SAW ──\n{report}"
        )
        increment_day_trades()
        print(body)
        send_email(f"TRADE COPIED: {ticker} {direction} ${chosen_contract.strike_price}", body)

    except Exception as e:
        err = f"Error placing order for {ticker}: {e}"
        print(err)
        send_email("Discord Trade Error", err)

# ── Selfbot ───────────────────────────────────────────────────────────────────
client = discord.Client()

@client.event
async def on_ready():
    print(f"Logged in as {client.user}")
    print(f"Watching #{WATCH_CHANNEL} in '{WATCH_SERVER}'")
    print("Monitoring JR signals...")
    send_email("Discord Selfbot Online", f"Watching #{WATCH_CHANNEL} for JR signals. Auto-trading via Alpaca.")

@client.event
async def on_message(message):
    if message.author == client.user:
        return

    guild_name = message.guild.name.lower() if message.guild else ""
    channel_name = message.channel.name.lower() if hasattr(message.channel, 'name') else ""

    if WATCH_SERVER not in guild_name:
        return
    if WATCH_CHANNEL not in channel_name:
        return
    if WATCH_USER not in message.author.name.lower() and WATCH_USER not in message.author.display_name.lower():
        return

    text = message.content
    print(f"\n[{datetime.now().strftime('%I:%M %p')}] JR: {text}")

    # Check if JR is exiting a trade
    exit_keywords = ["out", "sold", "closed", "took profit", "exit", "selling", "took gains", "done"]
    if any(word in text.lower() for word in exit_keywords):
        print(f"  JR EXIT signal detected")
        send_email("JR EXITING — Close Your Position", f"JR posted exit signal:\n\n{text}\n\nClose your matching position manually or via Alpaca.")
        return

    # Check P&L from screenshot attachments
    pnl        = None
    ocr_text   = None
    screenshot = None
    for attachment in message.attachments:
        if any(attachment.filename.lower().endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".webp"]):
            print(f"  Screenshot detected: {attachment.filename}")
            screenshot = attachment.filename
            pnl, ocr_text = extract_pnl_from_screenshot(attachment.url)
            if pnl is not None:
                print(f"  JR P&L: {pnl}%")
            break

    trade = parse_trade(text)
    if not trade:
        for embed in message.embeds:
            combined = f"{embed.title or ''} {embed.description or ''}"
            trade = parse_trade(combined)
            if trade:
                break

    # Build screenshot report header for all emails
    def screenshot_report():
        lines = [f"Time: {datetime.now().strftime('%I:%M %p')}"]
        if screenshot:
            lines.append(f"Screenshot: {screenshot}")
        if ocr_text:
            lines.append(f"\nWhat bot read from image:\n{ocr_text[:500]}")
        if pnl is not None:
            lines.append(f"\nP&L detected: {pnl}%")
        if trade:
            lines.append(f"Trade parsed: {trade['ticker']} ${trade['strike']} {trade['direction']} exp={trade.get('expiry')}")
        lines.append(f"\nJR's message: {text or '(no text)'}")
        return "\n".join(lines)

    if not trade:
        send_email("JR Posted — Check Manually", f"Bot could not parse a trade from this post.\n\n{screenshot_report()}")
        return

    # Route based on P&L
    if pnl is not None:
        if PNL_MIN <= pnl <= PNL_MAX:
            print(f"  P&L {pnl}% — normal entry")
            await execute_trade(trade, text, screenshot_report())

        elif LATE_PNL_MIN <= pnl <= LATE_PNL_MAX:
            used = get_day_trades_used()
            print(f"  P&L {pnl}% — late entry check (day trades used this week: {used})")
            if used == 1:
                print(f"  1 day trade used — qualifying for late entry")
                await execute_late_entry(trade, text, pnl, screenshot_report())
            else:
                msg = f"SKIPPED late entry: need exactly 1 day trade used this week, currently {used}"
                print(msg)
                send_email("Late Entry Skipped — Day Trade Count", f"{msg}\n\n{screenshot_report()}")

        else:
            msg = f"SKIPPED: JR P&L {pnl}% — not in normal range [{PNL_MIN}%–{PNL_MAX}%] or late range [{LATE_PNL_MIN}%–{LATE_PNL_MAX}%]"
            print(msg)
            send_email("Trade Skipped — P&L Out of Range", f"{msg}\n\n{screenshot_report()}")
    else:
        print(f"  No P&L detected — proceeding with normal entry")
        await execute_trade(trade, text, screenshot_report())

client.run(USER_TOKEN)
