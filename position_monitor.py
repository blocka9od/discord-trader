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
PROFIT_MIN      = 1000.0   # minimum take profit near close
PROFIT_MID      = 2000.0   # target range start — let it ride here
PROFIT_MAX      = 4000.0   # hard take profit — always close at $4,000
IWM_TP_MULTIPLE = 3.4      # IWM straddle: close when profit = 3.4x cost
CHECK_INTERVAL  = 60       # check every 60 seconds

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

def send_email(subject, body):
    try:
        msg = MIMEMultipart()
        msg["From"]    = EMAIL
        msg["To"]      = EMAIL
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
            s.login(EMAIL, EMAIL_PASS)
            s.send_message(msg)
    except Exception as e:
        print(f"Email error: {e}")

def check_profit_targets():
    try:
        now = datetime.now()
        near_close = now.hour == 14 and now.minute >= 30  # after 2:30 PM CT
        positions = tc.get_all_positions()

        # IWM straddle — group call + put, close both when combined profit = 3.4x cost
        iwm_positions = [p for p in positions if 'IWM' in p.symbol and p.symbol != 'IWM']
        if iwm_positions:
            total_cost = sum(float(p.avg_entry_price) * float(p.qty) * 100 for p in iwm_positions)
            total_pnl  = sum(float(p.unrealized_pl) for p in iwm_positions)
            tp_target  = round(total_cost * IWM_TP_MULTIPLE, 2)
            print(f"  IWM straddle P&L: ${total_pnl:.2f} | target: ${tp_target:.2f} (3.4x ${total_cost:.2f})")
            if total_pnl >= tp_target:
                for p in iwm_positions:
                    key = f"{p.symbol}_tp_{now.strftime('%Y%m%d')}"
                    if key not in alerted:
                        try:
                            tc.close_position(p.symbol)
                            alerted.add(key)
                        except Exception as e:
                            print(f"  Close error {p.symbol}: {e}")
                send_email(
                    f"IWM STRADDLE CLOSED — +${total_pnl:.2f} (3.4x)",
                    f"IWM straddle hit 3.4x take profit\nTotal invested: ${total_cost:.2f}\nProfit: +${total_pnl:.2f}\nTarget was: ${tp_target:.2f}"
                )
                print(f"  IWM straddle closed at 3.4x")

        for p in positions:
            pnl = float(p.unrealized_pl)
            key = f"{p.symbol}_tp_{now.strftime('%Y%m%d')}"

            should_close = False
            reason = ""

            if pnl >= PROFIT_MAX:
                should_close = True
                reason = f"hit max target ${pnl:.2f}"
            elif pnl >= PROFIT_MID:
                should_close = True
                reason = f"in target range ${pnl:.2f}"
            elif pnl >= PROFIT_MIN and near_close:
                should_close = True
                reason = f"at ${pnl:.2f} near market close — taking $1k+"

            if should_close and key not in alerted:
                try:
                    tc.close_position(p.symbol)
                    send_email(
                        f"TAKE PROFIT — {p.symbol} +${pnl:.2f}",
                        f"Auto-closed {p.symbol}\nReason: {reason}\nP&L: +${pnl:.2f}\nEntry: ${p.avg_entry_price}\nCurrent: ${p.current_price}\nQty: {p.qty}"
                    )
                    alerted.add(key)
                    print(f"  CLOSED {p.symbol} — {reason}")
                except Exception as e:
                    print(f"  Close error {p.symbol}: {e}")
            elif pnl >= PROFIT_MIN and not should_close:
                print(f"  {p.symbol} at +${pnl:.2f} — letting ride toward $2,000-$3,000")
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
