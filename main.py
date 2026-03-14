"""
Discord Options Trading Alerts Bot

Monitors specified Discord channels for options trading alerts,
parses them, and automatically places orders through Robinhood
with a 25% stop loss, risking no more than 1% of account value per trade.
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

    # Login to Robinhood on startup
    if not Config.PAPER_TRADE:
        if not login():
            logger.error("Robinhood login failed — bot will run but cannot place orders")
    else:
        logger.info("Skipping Robinhood login in paper-trade mode")


@client.event
async def on_message(message: discord.Message):
    # Ignore our own messages
    if message.author == client.user:
        return

    # Only process messages from monitored channels
    if message.channel.id not in Config.DISCORD_CHANNEL_IDS:
        return

    text = message.content.strip()
    if not text:
        return

    logger.info("Alert received in #%s: %s", message.channel.name, text)

    # Parse the alert
    alert = parse_alert(text)
    if alert is None:
        logger.info("Message did not parse as a trading alert, skipping")
        return

    logger.info(
        "Parsed alert: %s $%.2f %s exp %s @ $%.2f",
        alert.ticker,
        alert.strike,
        alert.option_type,
        alert.expiration,
        alert.entry_price,
    )

    # Place the order
    result = place_order(alert)

    # Log and reply in channel
    if result.success:
        logger.info("Order placed: %s", result.message)
        reply = (
            f"**Order Placed**\n"
            f"{result.message}\n"
            f"Contracts: {result.contracts} | "
            f"Total cost: ${result.total_cost:.2f} | "
            f"Stop loss: ${result.stop_loss_price:.2f}"
        )
    else:
        logger.warning("Order failed: %s", result.message)
        reply = f"**Order Failed**\n{result.message}"

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
        "Risk settings: %.1f%% per trade, %.1f%% stop loss",
        Config.RISK_PER_TRADE_PCT,
        Config.STOP_LOSS_PCT,
    )

    client.run(Config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
