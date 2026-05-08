from alpaca.trading.client import TradingClient

tc = TradingClient("PK2G5C5BQQ7AP5WNWBEUSKXOTI", "5NDwBjMCdn1ytRNHPqLTxTukeX32GPNmCnRtyiXxSifP", paper=True)

positions = tc.get_all_positions()
if not positions:
    print("No open positions")
else:
    for p in positions:
        print(f"Symbol:  {p.symbol}")
        print(f"Qty:     {p.qty}")
        print(f"Avg Entry: ${float(p.avg_entry_price):.2f}")
        print(f"Current:   ${float(p.current_price):.2f}")
        print(f"P&L:    ${float(p.unrealized_pl):.2f} ({float(p.unrealized_plpc)*100:.1f}%)")
        print("---")
