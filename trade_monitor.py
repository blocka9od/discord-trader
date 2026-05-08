import json, smtplib, time, schedule
import yfinance as yf
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

EMAIL      = "Blocka9od@gmail.com"
EMAIL_PASS = "dnlw dleb ryxs cljg"
TRADES_FILE = r"C:\Users\ajblo\trading_bot\paper_trades.json"

def send_email(subject, body):
    msg = MIMEMultipart()
    msg["From"] = EMAIL
    msg["To"]   = EMAIL
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
        s.login(EMAIL, EMAIL_PASS)
        s.send_message(msg)
    print(f"  Email sent: {subject}")

def load_trades():
    with open(TRADES_FILE) as f:
        return json.load(f)

def save_trades(data):
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_price(ticker):
    try:
        return yf.Ticker(ticker).fast_info.get("lastPrice")
    except:
        return None

def get_option_price(symbol):
    try:
        return yf.Ticker(symbol).fast_info.get("lastPrice")
    except:
        return None

def check_trades():
    print(f"\n[{datetime.now().strftime('%I:%M %p')}] Checking trades...")
    data = load_trades()
    changed = False

    for trade in data["trades"]:
        if trade["status"] != "open":
            continue

        ticker = trade["ticker"]
        stock_price = get_price(ticker)
        if not stock_price:
            print(f"  {ticker}: no price data")
            continue

        entry = trade["entry_price"]
        contracts = trade["contracts"]
        cost = trade["total_cost"]
        target_stock = trade.get("target_stock_price")
        trade_type = trade["type"]

        # Get option price from Yahoo
        opt_price = get_option_price(trade["symbol"])
        current_value = round(opt_price * contracts * 100, 2) if opt_price else None
        pnl = round(current_value - cost, 2) if current_value else None
        pnl_pct = round((pnl / cost) * 100, 1) if pnl else None

        print(f"  {ticker} {trade_type} ${trade['strike']} | Stock: ${stock_price} | Option: ${opt_price} | P&L: ${pnl} ({pnl_pct}%)")

        # Check take profit conditions
        hit_target = False

        # IWM — take profit at $380-$630 gain (use $500 as midpoint)
        if ticker == "IWM" and pnl and pnl >= 380:
            hit_target = True

        # CAR — take profit when stock drops $17 from ~$192 entry
        if ticker == "CAR" and target_stock and stock_price <= target_stock:
            hit_target = True

        # Ford — take profit after 45 cent move
        if ticker == "F":
            if trade_type == "CALL" and stock_price >= trade.get("entry_stock_price", 0) + 0.45:
                hit_target = True
            if trade_type == "PUT" and stock_price <= trade.get("entry_stock_price", 999) - 0.45:
                hit_target = True

        if hit_target:
            trade["status"] = "take_profit"
            trade["exit_price"] = opt_price
            trade["pnl"] = pnl
            changed = True
            body = (
                f"TAKE PROFIT HIT: {trade_type} on {ticker}\n"
                f"Symbol: {trade['symbol']}\n"
                f"Stock price: ${stock_price}\n"
                f"Option exit price: ${opt_price}\n"
                f"P&L: +${pnl} ({pnl_pct}%)\n"
                f"Total received: ${current_value}\n"
                f"Time: {datetime.now().strftime('%I:%M %p CT')}"
            )
            send_email(f"TAKE PROFIT: {ticker} {trade_type} +${pnl}", body)
            print(f"  *** TAKE PROFIT HIT on {ticker}! P&L: +${pnl} ***")

        # Alert if big move against us (stop loss warning at -50%)
        if pnl and pnl <= -(cost * 0.5):
            body = (
                f"STOP LOSS WARNING: {trade_type} on {ticker}\n"
                f"Stock price: ${stock_price}\n"
                f"Option price: ${opt_price}\n"
                f"P&L: ${pnl} ({pnl_pct}%)\n"
                f"Time: {datetime.now().strftime('%I:%M %p CT')}\n"
                f"Consider closing this position."
            )
            send_email(f"STOP LOSS WARNING: {ticker} {trade_type} ${pnl}", body)

    if changed:
        save_trades(data)

    # Print summary
    print(f"\n  Open trades summary:")
    for t in data["trades"]:
        if t["status"] == "open":
            print(f"    {t['ticker']} {t['type']} ${t['strike']} exp {t['expiration']} | {t['contracts']} contracts | Entry: ${t['entry_price']}")

def run():
    print("Trade Monitor Running — checking every 5 minutes")
    check_trades()
    schedule.every(5).minutes.do(check_trades)
    while True:
        schedule.run_pending()
        time.sleep(30)

if __name__ == "__main__":
    run()
