import yfinance as yf, smtplib, json
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

def load_trades():
    with open(TRADES_FILE) as f:
        return json.load(f)

def save_trades(data):
    with open(TRADES_FILE, "w") as f:
        json.dump(data, f, indent=2)

data = load_trades()
changed = False

for t in data["trades"]:
    if t["status"] != "open":
        continue

    stock = yf.Ticker(t["ticker"]).fast_info.get("lastPrice")
    opt   = yf.Ticker(t["symbol"]).fast_info.get("lastPrice")
    if not stock or not opt:
        continue

    val  = round(opt * t["contracts"] * 100, 2)
    pnl  = round(val - t["total_cost"], 2)
    pct  = round((pnl / t["total_cost"]) * 100, 1)

    hit_target = False

    # IWM — take profit at $380+ gain
    if t["ticker"] == "IWM" and pnl >= 380:
        hit_target = True

    # CAR — take profit when stock hits $175 (down $17)
    if t["ticker"] == "CAR" and stock <= t.get("target_stock_price", 0):
        hit_target = True

    # F — take profit after 45 cent move
    if t["ticker"] == "F":
        entry_stock = t.get("entry_stock_price", 0)
        if t["type"] == "PUT"  and stock <= entry_stock - 0.45: hit_target = True
        if t["type"] == "CALL" and stock >= entry_stock + 0.45: hit_target = True

    # Stop loss warning at -50%
    if pnl <= -(t["total_cost"] * 0.5):
        send_email(
            f"STOP LOSS WARNING: {t['ticker']} {t['type']} P&L ${pnl}",
            f"{t['ticker']} {t['type']} ${t['strike']}\nStock: ${stock}\nOption: ${opt}\nP&L: ${pnl} ({pct}%)\nConsider closing."
        )

    if hit_target:
        t["status"] = "take_profit"
        t["exit_price"] = opt
        t["pnl"] = pnl
        changed = True
        send_email(
            f"TAKE PROFIT HIT: {t['ticker']} {t['type']} +${pnl}",
            f"TAKE PROFIT HIT\n{t['ticker']} {t['type']} ${t['strike']} exp {t['expiration']}\n"
            f"Stock: ${stock}\nOption exit: ${opt}\nP&L: +${pnl} ({pct}%)\n"
            f"Total received: ${val}\nTime: {datetime.now().strftime('%I:%M %p CT')}"
        )

if changed:
    save_trades(data)
