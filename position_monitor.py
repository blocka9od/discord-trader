import time
import smtplib
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from alpaca.trading.client import TradingClient

ALPACA_KEY        = "PK2G5C5BQQ7AP5WNWBEUSKXOTI"
ALPACA_SECRET     = "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP"
EMAIL             = "Blocka9od@gmail.com"
EMAIL_PASS        = "dnlw dleb ryxs cljg"
PHONE_SMS         = "9012708979@sms.cricketwireless.net"
IWM_PROFIT_TARGET = 3400.0
CHECK_INTERVAL    = 180  # 3 minutes

tc         = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)
sent_today = None
alerted    = set()

def send_text(message):
    try:
        msg = MIMEMultipart()
        msg["From"]    = EMAIL
        msg["To"]      = PHONE_SMS
        msg["Subject"] = ""
        msg.attach(MIMEText(message, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL, EMAIL_PASS)
            s.send_message(msg)
        print(f"  TEXT SENT: {message}")
    except Exception as e:
        print(f"  Text error: {e}")

def check_profit_targets():
    try:
        positions = tc.get_all_positions()
        for p in positions:
            pnl = float(p.unrealized_pl)
            if "IWM" in p.symbol and "P" in p.symbol and pnl >= IWM_PROFIT_TARGET:
                key = f"IWM_3400_{datetime.now().strftime('%Y%m%d')}"
                if key not in alerted:
                    send_text(f"IWM PUT HIT $3,400 PROFIT — P&L ${pnl:.2f} TAKE IT NOW")
                    alerted.add(key)
    except Exception as e:
        print(f"Error checking profit targets: {e}")

def send_daily_pnl():
    try:
        positions = tc.get_all_positions()
        total_pnl = sum(float(p.unrealized_pl) for p in positions)
        lines     = [f"Daily P&L — {datetime.now().strftime('%m/%d %I:%M %p')}"]
        for p in positions:
            pnl     = float(p.unrealized_pl)
            pnl_pct = float(p.unrealized_plpc) * 100
            lines.append(f"{p.symbol}: ${pnl:.2f} ({pnl_pct:.1f}%)")
        lines.append(f"TOTAL: ${total_pnl:.2f}")
        send_text("\n".join(lines))
    except Exception as e:
        print(f"Error getting positions: {e}")

print("Position monitor started — IWM TP $3,400 | daily text at 2:30 PM")

while True:
    now = datetime.now()
    check_profit_targets()
    if now.hour == 14 and now.minute == 30 and now.strftime("%Y-%m-%d") != sent_today:
        send_daily_pnl()
        sent_today = now.strftime("%Y-%m-%d")
    time.sleep(CHECK_INTERVAL)
