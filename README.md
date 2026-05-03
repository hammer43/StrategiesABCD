# Gold Signals Trading System

Automated XAUUSD signal follower for AJD Trades V2 Telegram channel.
Paper trading system with multiple strategies, ML scoring, and confluence detection.

## Architecture## Strategies

- **Strategy A** — Direct signal follower, 40% ML threshold, 100% close at TP1
- **Strategy B** — Tiered ML entry (55%+ threshold, L1/L2 levels)
- **Strategy C** — Hybrid ML + FVG confluence engine
- **Strategy D** — Hybrid ML + FVG + Order Block + ADM (Bayesian approach)

## Setup

1. Copy credentials: `cp credentials.py.example credentials.py`
2. Fill in credentials.py with your API keys
3. Install requirements: `pip install -r requirements.txt`
4. Authenticate Telegram session: `python3 pipeline/telegram_listener.py`
5. Start system: `bash scripts/restart.sh`

## Requirements

See requirements.txt

## Performance (Paper Trading)

- Strategy A: 88.6% win rate
- Strategy D: Confluence-filtered, all TPs hit on first trade

## Notes

- credentials.py is gitignored — never commit it
- Telegram session files (.session) are gitignored
- All JSON data files are gitignored
