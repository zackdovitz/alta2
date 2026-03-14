# Discord Options Trading Alerts Bot

Monitors Discord channels for options trading alerts and automatically places orders through Tastytrade with built-in risk management.

## Features

- **Flexible alert parsing** — handles varying alert formats (e.g. `$RKLB - weekly $71 calls for $1.20`, `BTO AAPL 150C 3/15 @ 2.50`)
- **Two exit modes:**
  - **Auto** — OTOCO order: entry triggers linked stop-loss + take-profit (one fills, other cancels)
  - **Manual** — Entry + stop-loss only; bot sells when a trim/profit alert appears in Discord
- **Trim alert detection** — parses messages like "Trim RKLB calls", "Take profit on AAPL", "STC SPY"
- **Separate profit channel support** — trim alerts can come from the same channel or a dedicated profit channel
- **Automatic position sizing** — risks only 1% of account value per trade (adjustable)
- **Discord commands** — adjust all settings live without restarting
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

## Exit Modes

### Auto Mode (`EXIT_MODE=auto`)
Places an OTOCO order through Tastytrade:
1. **Trigger**: limit buy-to-open at the alert price
2. **OCO leg 1**: take-profit limit sell at +30% (configurable)
3. **OCO leg 2**: stop-loss sell at -25% (configurable)

When one OCO leg fills, the other is automatically cancelled.

### Manual Mode (`EXIT_MODE=manual`) — default
Places entry + standalone stop-loss only. The bot then watches for **trim/profit alerts** from Discord to sell:
- The trim alert can come from the **same alert channel** or a **separate profit channel** (`PROFIT_CHANNEL_IDS`)
- Recognizes messages like: "Trim RKLB", "Take profit on AAPL", "STC SPY calls", "Close TSLA position"
- "Trim" = sell half the position; "Close/STC/Sell" = sell all

This mode is ideal when you follow an alerts service that posts separate entry and exit signals.

## Discord Commands

| Command | Description |
|---|---|
| `!settings` | View current settings |
| `!positions` | View open positions (manual mode) |
| `!set risk <pct>` | Set risk per trade % (default: 1) |
| `!set stoploss <pct>` | Set stop-loss % (default: 25) |
| `!set takeprofit <pct>` | Set take-profit % (default: 30, auto mode only) |
| `!set exit auto\|manual` | Switch exit mode |
| `!set paper on\|off` | Toggle paper/live trading |
| `!help` | Show available commands |

## Configuration (.env)

| Variable | Description |
|---|---|
| `DISCORD_BOT_TOKEN` | Your Discord bot token |
| `DISCORD_CHANNEL_IDS` | Comma-separated channel IDs for entry alerts |
| `PROFIT_CHANNEL_IDS` | Optional: separate channel IDs for trim/profit alerts |
| `TT_CLIENT_SECRET` | Tastytrade OAuth client secret |
| `TT_REFRESH_TOKEN` | Tastytrade OAuth refresh token |
| `TT_ACCOUNT_NUMBER` | Your Tastytrade account number |
| `RISK_PER_TRADE_PCT` | Max % of account to risk per trade (default: 1.0) |
| `STOP_LOSS_PCT` | Stop-loss trigger % (default: 25.0) |
| `TAKE_PROFIT_PCT` | Take-profit trigger % in auto mode (default: 30.0) |
| `EXIT_MODE` | `auto` (OTOCO) or `manual` (trim alerts) — default: `manual` |
| `PAPER_TRADE` | `true` or `false` (default: `true`) |
