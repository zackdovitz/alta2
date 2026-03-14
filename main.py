"""
Discord Options Trading Alerts Bot

Monitors specified Discord channels for options trading alerts,
parses them, and automatically places orders through Tastytrade.

Two exit modes:
  - "auto":   OTOCO order with fixed stop-loss + take-profit percentages
  - "manual": Entry + stop-loss only; sells when a trim/profit alert
              appears (from the same channel or a separate profit channel)

Settings are adjustable via Discord commands:
  !settings              — view current settings
  !positions             — view open positions (manual mode)
  !set risk <pct>        — set risk per trade %
  !set stoploss <pct>    — set stop-loss %
  !set takeprofit <pct>  — set take-profit %
  !set paper on|off      — toggle paper trading mode
  !set exit auto|manual  — switch exit mode
"""

import logging
import discord

from config import Config
from alert_parser import parse_alert, parse_trim_alert
from broker import login, place_order, sell_position
from positions import Position, add_position, get_positions, get_all_positions, remove_all_positions

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


def _all_monitored_channels() -> set[int]:
    """Return all channel IDs the bot should listen to."""
    return set(Config.DISCORD_CHANNEL_IDS) | set(Config.PROFIT_CHANNEL_IDS)


@client.event
async def on_ready():
    logger.info("Bot connected as %s", client.user)
    channel_names = []
    for cid in _all_monitored_channels():
        ch = client.get_channel(cid)
        if ch:
            channel_names.append(f"#{ch.name}")
    logger.info("Monitoring channels: %s", ", ".join(channel_names) or "(none found)")
    logger.info("Exit mode: %s", Config.EXIT_MODE)

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

    if message.channel.id not in _all_monitored_channels():
        return

    text = message.content.strip()
    if not text:
        return

    # --- Handle commands ---
    if text.startswith(PREFIX):
        await _handle_command(message, text[len(PREFIX):].strip())
        return

    # --- Check for trim/profit alerts first ---
    is_profit_channel = message.channel.id in Config.PROFIT_CHANNEL_IDS
    is_alert_channel = message.channel.id in Config.DISCORD_CHANNEL_IDS

    if Config.EXIT_MODE == "manual":
        trim = parse_trim_alert(text)
        if trim:
            await _handle_trim_alert(message, trim)
            return

    # --- Handle entry alerts (only from alert channels) ---
    if not is_alert_channel:
        return

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

        # Track position for manual TP mode
        if Config.EXIT_MODE == "manual":
            pos = Position(
                ticker=alert.ticker,
                strike=alert.strike,
                option_type=alert.option_type,
                expiration=alert.expiration,
                contracts=result.contracts,
                entry_price=alert.entry_price,
                total_cost=result.total_cost,
                stop_loss_price=result.stop_loss_price,
                entry_order_id=result.order_id,
                stop_order_id=result.stop_order_id,
                option_symbol=result.option_symbol,
            )
            add_position(pos)

        tp_line = (
            f"TP: ${result.take_profit_price:.2f}"
            if result.take_profit_price > 0
            else "TP: awaiting trim alert"
        )
        reply = (
            f"**Order Placed**\n"
            f"{result.message}\n"
            f"Contracts: {result.contracts} | "
            f"Total cost: ${result.total_cost:.2f} | "
            f"SL: ${result.stop_loss_price:.2f} | "
            f"{tp_line}"
        )
    else:
        logger.warning("Order failed: %s", result.message)
        reply = f"**Order Failed**\n{result.message}"

    await message.channel.send(reply)


async def _handle_trim_alert(message: discord.Message, trim):
    """Sell positions matching a trim/profit alert."""
    logger.info("Trim alert for %s (sell_all=%s): %s", trim.ticker, trim.sell_all, trim.raw_text)

    positions = get_positions(trim.ticker)
    if not positions:
        logger.info("No open positions for %s, ignoring trim alert", trim.ticker)
        return

    total_sold = 0
    messages = []

    for pos in positions:
        contracts_to_sell = pos.contracts if trim.sell_all else max(1, pos.contracts // 2)

        result = sell_position(
            option_symbol=pos.option_symbol,
            contracts=contracts_to_sell,
            stop_order_id=pos.stop_order_id,
        )

        if result.success:
            total_sold += result.contracts_sold
            messages.append(result.message)
        else:
            messages.append(f"Failed to sell {pos.ticker}: {result.message}")

    # Remove positions if fully sold
    if trim.sell_all:
        remove_all_positions(trim.ticker)

    action = "Closed" if trim.sell_all else "Trimmed"
    reply = (
        f"**{action} {trim.ticker}**\n"
        + "\n".join(messages)
        + f"\nTotal contracts sold: {total_sold}"
    )
    await message.channel.send(reply)


async def _handle_command(message: discord.Message, command: str):
    """Handle bot commands for viewing/adjusting settings."""
    parts = command.lower().split()
    if not parts:
        return

    cmd = parts[0]

    if cmd == "settings":
        await _show_settings(message)
    elif cmd == "positions":
        await _show_positions(message)
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
    exit_desc = "Auto (OTOCO)" if Config.EXIT_MODE == "auto" else "Manual (trim alerts)"
    reply = (
        f"**Current Settings**\n"
        f"```\n"
        f"Mode:         {mode}\n"
        f"Exit mode:    {exit_desc}\n"
        f"Risk/trade:   {Config.RISK_PER_TRADE_PCT}%\n"
        f"Stop loss:    {Config.STOP_LOSS_PCT}%\n"
        f"Take profit:  {Config.TAKE_PROFIT_PCT}% (auto mode only)\n"
        f"```"
    )
    await message.channel.send(reply)


async def _show_positions(message: discord.Message):
    all_pos = get_all_positions()
    if not all_pos:
        await message.channel.send("No open positions.")
        return

    lines = ["**Open Positions**\n```"]
    for ticker, positions in all_pos.items():
        for p in positions:
            lines.append(
                f"{ticker}: {p.contracts}x ${p.strike} {p.option_type} "
                f"exp {p.expiration} @ ${p.entry_price:.2f} "
                f"(SL ${p.stop_loss_price:.2f})"
            )
    lines.append("```")
    await message.channel.send("\n".join(lines))


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

    elif key == "exit":
        if value in ("auto", "manual"):
            Config.EXIT_MODE = value
            desc = "Auto (OTOCO stop+TP)" if value == "auto" else "Manual (stop only, sell on trim alert)"
            await message.channel.send(f"Exit mode set to **{desc}**")
        else:
            await message.channel.send("Use `auto` or `manual`.")

    else:
        await message.channel.send(
            f"Unknown setting `{key}`. Options: `risk`, `stoploss`, `takeprofit`, `paper`, `exit`"
        )


async def _show_help(message: discord.Message):
    reply = (
        f"**Trading Bot Commands**\n"
        f"```\n"
        f"{PREFIX}settings              View current settings\n"
        f"{PREFIX}positions             View open positions\n"
        f"{PREFIX}set risk <pct>        Set risk per trade (default: 1%)\n"
        f"{PREFIX}set stoploss <pct>    Set stop-loss % (default: 25%)\n"
        f"{PREFIX}set takeprofit <pct>  Set take-profit % (default: 30%)\n"
        f"{PREFIX}set paper on|off      Toggle paper/live trading\n"
        f"{PREFIX}set exit auto|manual  Switch exit mode\n"
        f"{PREFIX}help                  Show this message\n"
        f"```\n"
        f"**Exit Modes:**\n"
        f"- `auto` — OTOCO: entry + fixed stop-loss + fixed take-profit\n"
        f"- `manual` — Entry + stop-loss only; sells when a trim alert appears"
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
    logger.info("Exit mode: %s", Config.EXIT_MODE)

    if Config.PROFIT_CHANNEL_IDS:
        logger.info("Profit alert channels: %s", Config.PROFIT_CHANNEL_IDS)

    client.run(Config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    main()
