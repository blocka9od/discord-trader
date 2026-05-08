#!/bin/bash
echo "Starting auto_trader..."
python auto_trader.py &

echo "Starting discord_trader..."
python discord_trader.py
