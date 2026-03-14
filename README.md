# Discord Options Trading Alerts Bot

Monitors Discord channels for options trading alerts and automatically places orders through Robinhood with risk management.

## Features

- **Flexible alert parsing** — handles varying alert formats (e.g. `$RKLB - weekly $71 calls for $1.20`, `BTO AAPL 150C 3/15 @ 2.50`)
- **Automatic position sizing** — risks only 1% of account value per trade
- **Automatic stop loss** — places a 25% stop-loss order immediately after entry
- **Paper trading mode** — test safely before going live

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Create a Discord bot:**
   - Go to https://discord.com/developers/applications
   - Create a new application and add a bot
   - Enable the **Message Content Intent** under Bot settings
   - Copy the bot token
   - Invite the bot to your server with "Read Messages" permission

3. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

4. **Run:**
   ```bash
   python3 main.py
   ```

## Configuration (.env)

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token |
| `DISCORD_CHANNEL_IDS` | Comma-separated channel IDs to monitor |
| `RH_USERNAME` | Robinhood email |
| `RH_PASSWORD` | Robinhood password |
| `RISK_PER_TRADE_PCT` | Max % of account to risk per trade (default: 1.0) |
| `STOP_LOSS_PCT` | Stop-loss trigger % (default: 25.0) |
| `PAPER_TRADE` | Set to `false` for live trading (default: `true`) |

## How It Works

1. Bot monitors specified Discord channels for messages
2. Each message is parsed to extract: ticker, strike, call/put, expiration, entry price
3. Position size is calculated: `max_risk = account_value * 1%`, then `contracts = max_risk / (loss_per_contract)`
4. A limit buy order is placed through Robinhood
5. A stop-loss sell order is immediately placed at 25% below entry price
