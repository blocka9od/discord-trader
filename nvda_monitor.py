"""
NVDA Price Monitor — closes NVDA260515C00222500 when NVDA stock hits $228.60
"""
import time, smtplib, requests
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from alpaca.trading.client import TradingClient

ALPACA_KEY    = "PK2G5C5BQQ7AP5WNWBEUSKXOTI"
ALPACA_SECRET = "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP"
EMAIL         = "Blocka9od@gmail.com"
EMAIL_PASS    = "dnlw dleb ryxs cljg"
TARGET_PRICE  = 228.60
OPTION_SYMBOL = "NVDA260515C00222500"

tc = TradingClient(ALPACA_KEY, ALPACA_SECRET, paper=True)

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

def get_nvda_price():
    try:
        r = requests.get(
            "https://data.alpaca.markets/v2/stocks/NVDA/trades/latest",
            headers={"APCA-API-KEY-ID": ALPACA_KEY, "APCA-API-SECRET-KEY": ALPACA_SECRET}
        )
        return float(r.json()["trade"]["p"])
    except:
        return None

print(f"NVDA Monitor started — will sell {OPTION_SYMBOL} when NVDA >= ${TARGET_PRICE}")

while True:
    try:
        price = get_nvda_price()
        if price:
            print(f"[{datetime.now().strftime('%H:%M')}] NVDA: ${price:.2f} | Target: ${TARGET_PRICE}")
            if price >= TARGET_PRICE:
                print(f"TARGET HIT — closing {OPTION_SYMBOL}")
                try:
                    tc.close_position(OPTION_SYMBOL)
                    send_email(
                        f"NVDA SOLD — Target Hit ${price:.2f}",
                        f"NVDA hit ${price:.2f} — target was ${TARGET_PRICE}\n"
                        f"Closed {OPTION_SYMBOL} x5\n"
                        f"Check account for final P&L."
                    )
                    print("Position closed — monitor stopping")
                    break
                except Exception as e:
                    print(f"Close error: {e}")
        time.sleep(60)
    except KeyboardInterrupt:
        break
    except Exception as e:
        print(f"Error: {e}")
        time.sleep(60)
