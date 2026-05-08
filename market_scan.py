from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestQuoteRequest, StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from datetime import datetime, timedelta

ALPACA_KEY    = "PK2G5C5BQQ7AP5WNWBEUSKXOTI"
ALPACA_SECRET = "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP"

WATCHLIST = ["IWM", "SPY", "QQQ", "TSLA", "NVDA", "AAPL", "AMZN", "META", "MSFT", "AMD", "TSM", "WMT"]

client = StockHistoricalDataClient(ALPACA_KEY, ALPACA_SECRET)

def get_market_data():
    quotes = client.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=WATCHLIST))

    end   = datetime.now()
    start = end - timedelta(days=2)
    bars  = client.get_stock_bars(StockBarsRequest(
        symbol_or_symbols=WATCHLIST,
        timeframe=TimeFrame.Day,
        start=start,
        end=end
    ))

    print(f"\n{'='*55}")
    print(f"  MARKET SCAN — {datetime.now().strftime('%A %m/%d/%Y %I:%M %p')}")
    print(f"{'='*55}")

    for symbol in WATCHLIST:
        try:
            quote = quotes[symbol]
            price = (quote.ask_price + quote.bid_price) / 2

            bar_list = bars[symbol]
            if len(bar_list) >= 2:
                prev_close = bar_list[-2].close
                change     = price - prev_close
                pct        = (change / prev_close) * 100
                high       = bar_list[-1].high
                low        = bar_list[-1].low
                rng        = high - low
                arrow      = "▲" if change >= 0 else "▼"
                print(f"  {symbol:<6} ${price:<8.2f} {arrow} {change:+.2f} ({pct:+.2f}%)  Range: ${low:.2f}–${high:.2f} (${rng:.2f})")
            else:
                print(f"  {symbol:<6} ${price:.2f}")
        except Exception as e:
            print(f"  {symbol}: error — {e}")

    print(f"{'='*55}\n")

get_market_data()
