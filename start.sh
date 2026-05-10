#!/bin/bash
echo "Starting auto_trader..."
python auto_trader.py &

echo "Starting trump_trader..."
python trump_trader.py &

echo "Starting discord_trader..."
python discord_trader.py
