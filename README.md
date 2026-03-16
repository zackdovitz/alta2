# Alta2 — Discord Options Trading Bot

Monitors Discord channels for options trading alerts and automatically places orders through Tastytrade with built-in risk management.

## Features

- **Smart alert parsing** — handles free-form alerts like `$RKLB weekly $71 calls for $1.20`, `BTO AAPL 150C 3/15 @ 2.50`, `Taking a few $IWM 247p 0dte`
- **LLM fallback parser** — when regex can't parse an alert, an AI model fills in the gaps
- **No price in alert?** — bot fetches the live ask price and places a limit order at that price
- **Lotto / small size detection** — alerts containing "lotto" or "taking a few" automatically use half the normal risk
- **Two exit modes** — Auto (OTOCO bracket) or Manual (trim alerts)
- **Trim alert detection** — parses "Trim RKLB calls", "Take profit on AAPL", "STC SPY", "Really loving these gains, lock in 75%"
- **Partial exit support** — trim alerts with percentages (e.g. "sell 75%") sell that fraction of the position
- **Unfilled order alerts** — if an order doesn't fill within 60 seconds, bot pings you with options to bump price, keep open, or cancel
- **Automatic position sizing** — risks a configurable % of account value per trade
- **Live Discord commands** — adjust all settings without restarting
- **24/7 uptime** — designed to run on a persistent server via pm2

---

## Quick Start

### 1. Clone the repo
```bash
git clone https://github.com/zackdovitz/alta2.git
cd alta2
pip install -r requirements.txt
```

### 2. Create a Discord bot
- Go to [discord.com/developers/applications](https://discord.com/developers/applications)
- Create a new application → add a Bot
- Under **Privileged Gateway Intents**, enable **Message Content Intent**
- Copy the **Bot Token**
- Under **OAuth2 → URL Generator**: check `bot` scope + **View Channels**, **Send Messages**, **Read Message History** permissions
- Use the generated URL to invite the bot to your server
- Right-click the channel you want it to monitor → **Copy Channel ID** (requires Developer Mode in Discord settings)

### 3. Set up Tastytrade OAuth
- Log in at tastytrade.com → **My Profile → OAuth Applications → New Application**
- Check all scopes, add `http://localhost:8000` as the callback URL
- Save the **Client Secret**
- Click **Manage → Create Grant** to generate a **Refresh Token**
- Your **Account Number** is shown on the main accounts page (e.g. `5WX12345`)

### 4. Configure your .env
```bash
cp .env.example .env
```

Edit `.env` with your credentials:

```env
DISCORD_BOT_TOKEN=your_bot_token
DISCORD_CHANNEL_IDS=your_channel_id

TT_CLIENT_SECRET=your_tastytrade_client_secret
TT_REFRESH_TOKEN=your_tastytrade_refresh_token
TT_ACCOUNT_NUMBER=your_account_number

RISK_PER_TRADE_PCT=3.0
STOP_LOSS_PCT=25.0
EXIT_MODE=manual
PAPER_TRADE=true        # set to false when ready to go live
```

### 5. Run the bot
```bash
python3 main.py
```

For 24/7 uptime using pm2:
```bash
pm2 start main.py --name trading-bot --interpreter python3
pm2 save
```

---

## LLM Parser (optional but recommended)

The bot includes an AI-powered alert parser that handles messy or unusual alert formats. To enable it, add to your `.env`:

```env
OPENAI_API_KEY=your_openai_key
OPENAI_BASE_URL=https://api.openai.com/v1   # or any OpenAI-compatible endpoint
PARSER_MODEL=gpt-4o-mini
```

Without this, the bot uses regex-only parsing which works for most standard formats.

---

## How Alert Parsing Works

The bot understands a wide variety of alert formats:

| Alert | Parsed as |
|---|---|
| `$RKLB - Lotto size - weekly $71 calls for $1.20` | RKLB $71 call, 0.5x risk (lotto) |
| `BTO AAPL 150C 3/15 @ 2.50` | AAPL $150 call exp 3/15 |
| `TSLA 800 puts 1/19 for $3.40` | TSLA $800 put exp 1/19 |
| `Taking a few $IWM 247p 0dte` | IWM $247 put exp today, 0.5x risk, limit @ ask |
| `we buying NVDA weeklies 180@.50` | NVDA $180 call exp next Friday |
| `Trim RKLB calls` | Sell 50% of RKLB position |
| `Really loving these SPY gains, lock in 75%` | Sell 75% of SPY position |
| `STC NVDA, close it out` | Sell 100% of NVDA position |

**Special cases:**
- **"lotto"** or **"taking a few"** in the alert → uses **half** the normal risk %
- **0dte** → sets expiration to today
- **No price given** → fetches live ask price and places limit order at that price

---

## Exit Modes

### Manual mode (default)
Places entry + stop-loss only. The bot watches for **trim/profit alerts** in your Discord channel(s) to exit:
- Same channel or a separate `PROFIT_CHANNEL_IDS` channel
- Recognizes "trim", "take profit", "close", "STC", "manage position", percentage mentions
- Sells the fraction specified (e.g. "75%" → sells 75% of position)

### Auto mode
Places a full OTOCO bracket order:
1. Limit buy-to-open at alert price
2. Take-profit limit sell at +`TAKE_PROFIT_PCT`%
3. Stop-loss sell at -`STOP_LOSS_PCT`%

When one leg fills, the other cancels automatically.

---

## Unfilled Order Handling

If an order hasn't filled within 60 seconds, the bot sends you a message:

```
⚠️ Unfilled Order — `12345`
1x AMD $190 put exp 2026-03-20 @ $2.70
Status: Working

What do you want to do?
• !bump 12345 — increase price by 5% (→ $2.85)
• !keep 12345 — leave it open, stop alerting
• !cancel 12345 — cancel the order
```

---

## Discord Commands

| Command | Description |
|---|---|
| `!settings` | View current settings |
| `!positions` | View open positions |
| `!orders` | View pending (unfilled) orders |
| `!bump <id>` | Cancel and resubmit order at +5% price |
| `!keep <id>` | Keep order open, silence alerts |
| `!cancel <id>` | Cancel an order |
| `!cancel all` | Cancel all pending orders |
| `!set risk <pct>` | Set risk per trade % |
| `!set stoploss <pct>` | Set stop-loss % |
| `!set takeprofit <pct>` | Set take-profit % |
| `!set exit auto\|manual` | Switch exit mode |
| `!set paper on\|off` | Toggle paper/live trading |
| `!buy TICKER STRIKE call/put EXPIRATION @ PRICE` | Manual trade entry |
| `!help` | Show all commands |

---

## Configuration Reference

| Variable | Default | Description |
|---|---|---|
| `DISCORD_BOT_TOKEN` | — | Your Discord bot token |
| `DISCORD_CHANNEL_IDS` | — | Comma-separated channel IDs for entry alerts |
| `PROFIT_CHANNEL_IDS` | — | Optional: separate channel IDs for trim/exit alerts |
| `TT_CLIENT_SECRET` | — | Tastytrade OAuth client secret |
| `TT_REFRESH_TOKEN` | — | Tastytrade OAuth refresh token |
| `TT_ACCOUNT_NUMBER` | — | Your Tastytrade account number |
| `RISK_PER_TRADE_PCT` | `1.0` | Max % of account to risk per trade |
| `STOP_LOSS_PCT` | `25.0` | Stop-loss trigger % below entry |
| `TAKE_PROFIT_PCT` | `30.0` | Take-profit trigger % above entry (auto mode) |
| `EXIT_MODE` | `manual` | `auto` (OTOCO) or `manual` (trim alerts) |
| `PAPER_TRADE` | `true` | `true` = no real orders placed |
| `OPENAI_API_KEY` | — | Optional: enables LLM parser fallback |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | LLM API endpoint |
| `PARSER_MODEL` | `gpt-4o-mini` | LLM model to use for parsing |

---

## Safety Notes

- Start with `PAPER_TRADE=true` to verify alerts are being parsed correctly before going live
- The bot risks `RISK_PER_TRADE_PCT`% of your account per trade — start conservative (1-3%)
- "Lotto" and "taking a few" alerts automatically use half the normal risk
- All orders include a stop-loss — the bot will never hold a position with no downside protection
