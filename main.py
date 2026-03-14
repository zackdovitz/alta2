"""
Discord Options Trading Alerts Bot

Monitors specified Discord channels for options trading alerts,
parses them, and automatically places OTOCO orders through Tastytrade
(entry + OCO stop-loss/take-profit).

Settings are adjustable via Discord commands:
  !settings              — view current settings
  !set risk <pct>        — set risk per trade %
  !set stoploss <pct>    — set stop-loss %
  !set takeprofit <pct>  — set take-profit %
  !set paper on|off      — toggle paper trading mode
"""

import logging
import discord

from config import Config
from alert_parser import parse_alert
from broker import login, place_order

# --- Logging setup ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("trading_bot.log"),
    ],
)
logger = logging.getLogger("bot")

# --- Discord bot ---
intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)

PREFIX = Config.COMMAND_PREFIX


@client.event
async def on_ready():
    logger.info("Bot connected as %s", client.user)
    channel_names = []
    for cid in Config.DISCORD_CHANNEL_IDS:
        ch = client.get_channel(cid)
        if ch:
            channel_names.append(f"#{ch.name}")
    logger.info("Monitoring channels: %s", ", ".join(channel_names) or "(none found)")

    if Config.PAPER_TRADE:
        logger.info("*** PAPER TRADING MODE — no real orders will be placed ***")

    if not Config.PAPER_TRADE:
        if not login():
            logger.error("Tastytrade login failed — bot will run but cannot place orders")
    else:
        logger.info("Skipping Tastytrade login in paper-trade mode")


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    # Only respond in monitored channels
    if message.channel.id not in Config.DISCORD_CHANNEL_IDS:
        return

    text = message.content.strip()
    if not text:
        return

    # --- Handle commands ---
    if text.startswith(PREFIX):
        await _handle_command(message, text[len(PREFIX):].strip())
        return

    # --- Handle trading alerts ---
    logger.info("Alert received in #%s: %s", message.channel.name, text)

    alert = parse_alert(text)
    if alert is None:
        logger.info("Message did not parse as a trading alert, skipping")
        return

    logger.info(
        "Parsed alert: %s $%.2f %s exp %s @ $%.2f",
        alert.ticker, alert.strike, alert.option_type,
        alert.expiration, alert.entry_price,
    )

    result = place_order(alert)

    if result.success:
        logger.info("Order placed: %s", result.message)
        reply = (
            f"**Order Placed**\n"
            f"{result.message}\n"
            f"Contracts: {result.contracts} | "
            f"Total cost: ${result.total_cost:.2f} | "
            f"SL: ${result.stop_loss_price:.2f} | "
            f"TP: ${result.take_profit_price:.2f}"
        )
    else:
        logger.warning("Order failed: %s", result.message)
        reply = f"**Order Failed**\n{result.message}"

    await message.channel.send(reply)


async def _handle_command(message: discord.Message, command: str):
    """Handle bot commands for viewing/adjusting settings."""
    parts = command.lower().split()
    if not parts:
        return

    cmd = parts[0]

    if cmd == "settings":
        await _show_settings(message)
    elif cmd == "set" and len(parts) >= 3:
        await _set_setting(message, parts[1], parts[2])
    elif cmd == "help":
        await _show_help(message)
    else:
        await message.channel.send(
            f"Unknown command. Type `{PREFIX}help` for available commands."
        )


async def _show_settings(message: discord.Message):
    mode = "PAPER" if Config.PAPER_TRADE else "LIVE"
    reply = (
        f"**Current Settings**\n"
        f"```\n"
        f"Mode:         {mode}\n"
        f"Risk/trade:   {Config.RISK_PER_TRADE_PCT}%\n"
        f"Stop loss:    {Config.STOP_LOSS_PCT}%\n"
        f"Take profit:  {Config.TAKE_PROFIT_PCT}%\n"
        f"```"
    )
    await message.channel.send(reply)


async def _set_setting(message: discord.Message, key: str, value: str):
    key = key.lower()

    if key == "risk":
        try:
            pct = float(value)
            if not (0.1 <= pct <= 100):
                raise ValueError
            Config.RISK_PER_TRADE_PCT = pct
            await message.channel.send(f"Risk per trade set to **{pct}%**")
        except ValueError:
            await message.channel.send("Invalid value. Use a number between 0.1 and 100.")

    elif key == "stoploss":
        try:
            pct = float(value)
            if not (1 <= pct <= 100):
                raise ValueError
            Config.STOP_LOSS_PCT = pct
            await message.channel.send(f"Stop loss set to **{pct}%**")
        except ValueError:
            await message.channel.send("Invalid value. Use a number between 1 and 100.")

    elif key == "takeprofit":
        try:
            pct = float(value)
            if not (1 <= pct <= 10000):
                raise ValueError
            Config.TAKE_PROFIT_PCT = pct
            await message.channel.send(f"Take profit set to **{pct}%**")
        except ValueError:
            await message.channel.send("Invalid value. Use a number between 1 and 10000.")

    elif key == "paper":
        if value in ("on", "true", "yes"):
            Config.PAPER_TRADE = True
            await message.channel.send("Paper trading **enabled** — no real orders will be placed")
        elif value in ("off", "false", "no"):
            Config.PAPER_TRADE = False
            if not login():
                await message.channel.send(
                    "Paper trading disabled but **Tastytrade login failed**. "
                    "Fix credentials and try again."
                )
            else:
                await message.channel.send(
                    "Paper trading **disabled** — LIVE orders will be placed!"
                )
        else:
            await message.channel.send("Use `on` or `off`.")

    else:
        await message.channel.send(
            f"Unknown setting `{key}`. Options: `risk`, `stoploss`, `takeprofit`, `paper`"
        )


async def _show_help(message: discord.Message):
    reply = (
        f"**Trading Bot Commands**\n"
        f"```\n"
        f"{PREFIX}settings              View current settings\n"
        f"{PREFIX}set risk <pct>        Set risk per trade (default: 1%)\n"
        f"{PREFIX}set stoploss <pct>    Set stop-loss % (default: 25%)\n"
        f"{PREFIX}set takeprofit <pct>  Set take-profit % (default: 30%)\n"
        f"{PREFIX}set paper on|off      Toggle paper/live trading\n"
        f"{PREFIX}help                  Show this message\n"
        f"```"
    )
    await message.channel.send(reply)


def main():
    if not Config.DISCORD_BOT_TOKEN:
        logger.error("DISCORD_BOT_TOKEN not set in .env — exiting")
        return

    if not Config.DISCORD_CHANNEL_IDS:
        logger.error("DISCORD_CHANNEL_IDS not set in .env — exiting")
        return

    logger.info("Starting Discord Trading Alerts Bot...")
    logger.info(
        "Risk settings: %.1f%% per trade, %.1f%% stop loss, %.1f%% take profit",
        Config.RISK_PER_TRADE_PCT, Config.STOP_LOSS_PCT, Config.TAKE_PROFIT_PCT,
    )

    client.run(Config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
