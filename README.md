# Discord Options Trading Alerts Bot

Monitors Discord channels for options trading alerts and automatically places OTOCO orders through Tastytrade — entry order triggers a linked OCO pair (stop-loss + take-profit) so one filling cancels the other.

## Features

- **Flexible alert parsing** — handles varying alert formats (e.g. `$RKLB - weekly $71 calls for $1.20`, `BTO AAPL 150C 3/15 @ 2.50`)
- **OTOCO orders** — entry triggers linked stop-loss + take-profit via Tastytrade's OCO support
- **Automatic position sizing** — risks only 1% of account value per trade (adjustable)
- **Discord commands** — adjust risk, stop-loss, take-profit, and paper mode right from Discord
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
   - Invite the bot to your server with "Read Messages" + "Send Messages" permissions

3. **Set up Tastytrade OAuth:**
   - Log in to tastytrade.com
   - Go to **My Profile → OAuth Applications → New Application**
   - Check all scopes, add `http://localhost:8000` as callback, save the **client secret**
   - Go to **Manage → Create Grant** to generate a **refresh token**
   - Refresh tokens don't expire, so this is a one-time setup

4. **Configure environment:**
   ```bash
   cp .env.example .env
   # Edit .env with your credentials
   ```

5. **Run:**
   ```bash
   python3 main.py
   ```

## Discord Commands

All settings can be adjusted live from Discord — no need to edit files or restart:

| Command | Description |
|---|---|
| `!settings` | View current settings |
| `!set risk <pct>` | Set risk per trade % (default: 1) |
| `!set stoploss <pct>` | Set stop-loss % (default: 25) |
| `!set takeprofit <pct>` | Set take-profit % (default: 30) |
| `!set paper on\|off` | Toggle paper/live trading |
| `!help` | Show available commands |

## Configuration (.env)

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token |
| `DISCORD_CHANNEL_IDS` | Comma-separated channel IDs to monitor |
| `TT_CLIENT_SECRET` | Tastytrade OAuth client secret |
| `TT_REFRESH_TOKEN` | Tastytrade OAuth refresh token |
| `TT_ACCOUNT_NUMBER` | Your Tastytrade account number |
| `RISK_PER_TRADE_PCT` | Max % of account to risk per trade (default: 1.0) |
| `STOP_LOSS_PCT` | Stop-loss trigger % (default: 25.0) |
| `TAKE_PROFIT_PCT` | Take-profit trigger % (default: 30.0) |
| `PAPER_TRADE` | Set to `false` for live trading (default: `true`) |

## How It Works

1. Bot monitors specified Discord channels for messages
2. Each message is parsed to extract: ticker, strike, call/put, expiration, entry price
3. Position size is calculated so max loss stays within the risk budget
4. An OTOCO order is placed through Tastytrade:
   - **Trigger**: limit buy-to-open at the alert price
   - **OCO leg 1**: take-profit limit sell at +30% (configurable)
   - **OCO leg 2**: stop-loss sell at -25% (configurable)
5. When one OCO leg fills, the other is automatically cancelled
