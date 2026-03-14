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
  !orders                — view pending (unfilled) orders
  !cancel <id>           — cancel a pending order
  !cancel all            — cancel all pending orders
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
from broker import login, place_order, sell_position, get_order_status, cancel_order
from positions import (
    Position, PendingOrder,
    add_position, get_positions, get_all_positions, remove_all_positions,
    add_pending_order, get_all_pending_orders, remove_pending_order,
)

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


def _extract_text(message: discord.Message) -> str:
    """Extract alert text from a message, including forwarded messages.

    Discord forwarded messages store the original content in message_snapshots
    or embeds rather than message.content.
    """
    # Regular message — use content directly
    text = message.content.strip()
    if text:
        return text

    # Forwarded messages — check message_snapshots (Discord 2024+ forwarding)
    snapshots = getattr(message, "message_snapshots", None)
    if snapshots:
        for snapshot in snapshots:
            snap_msg = getattr(snapshot, "message", snapshot)
            snap_content = getattr(snap_msg, "content", "")
            if snap_content and snap_content.strip():
                logger.info("Extracted text from forwarded message snapshot")
                return snap_content.strip()

    # Fallback — check embeds (some bots/forwards use embeds)
    for embed in message.embeds:
        if embed.description:
            logger.info("Extracted text from embed description")
            return embed.description.strip()
        if embed.title:
            return embed.title.strip()

    return ""


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return

    if message.channel.id not in _all_monitored_channels():
        return

    text = _extract_text(message)
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

    # --- Confirm we're placing the order ---
    mode_label = "PAPER" if Config.PAPER_TRADE else "LIVE"
    exit_label = "auto OTOCO" if Config.EXIT_MODE == "auto" else "manual"
    confirm_msg = (
        f"**Placing Order** [{mode_label} | {exit_label}]\n"
        f"{alert.ticker} ${alert.strike} {alert.option_type} exp {alert.expiration} "
        f"@ ${alert.entry_price:.2f}..."
    )
    await message.channel.send(confirm_msg)

    result = place_order(alert)

    if result.success:
        logger.info("Order placed: %s", result.message)

        # Track as pending order
        pending = PendingOrder(
            order_id=result.order_id or "unknown",
            ticker=alert.ticker,
            strike=alert.strike,
            option_type=alert.option_type,
            expiration=alert.expiration,
            contracts=result.contracts,
            entry_price=alert.entry_price,
            total_cost=result.total_cost,
            stop_loss_price=result.stop_loss_price,
            take_profit_price=result.take_profit_price,
            option_symbol=result.option_symbol,
            stop_order_id=result.stop_order_id,
        )
        add_pending_order(pending)

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
            f"**Order Placed Successfully**\n"
            f"{result.message}\n"
            f"Order ID: `{result.order_id}`\n"
            f"Contracts: {result.contracts} | "
            f"Total cost: ${result.total_cost:.2f} | "
            f"SL: ${result.stop_loss_price:.2f} | "
            f"{tp_line}"
        )

        # Check fill status (paper orders fill instantly)
        if Config.PAPER_TRADE:
            remove_pending_order(pending.order_id)
            reply += "\nStatus: **Filled** (paper)"
        else:
            status = get_order_status(result.order_id)
            if status and status.lower() in ("filled", "completed"):
                remove_pending_order(pending.order_id)
                reply += f"\nStatus: **Filled**"
            else:
                reply += f"\nStatus: **Pending** — use `{PREFIX}orders` to check"
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
    elif cmd == "orders":
        await _show_orders(message)
    elif cmd == "cancel" and len(parts) >= 2:
        await _cancel_order(message, parts[1])
    elif cmd == "cancel":
        await message.channel.send(f"Usage: `{PREFIX}cancel <order_id>` or `{PREFIX}cancel all`")
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


async def _show_orders(message: discord.Message):
    """Show all pending (unfilled) orders."""
    pending = get_all_pending_orders()
    if not pending:
        await message.channel.send("No pending orders.")
        return

    # Refresh statuses and remove filled ones
    filled = []
    lines = ["**Pending Orders**\n```"]
    for order_id, order in pending.items():
        status = get_order_status(order_id)
        if status and status.lower() in ("filled", "completed"):
            filled.append(order_id)
            continue
        if status and status.lower() in ("cancelled", "canceled", "rejected", "expired"):
            filled.append(order_id)
            continue
        status_str = status or "Unknown"
        lines.append(
            f"[{order_id}] {order.ticker} {order.contracts}x "
            f"${order.strike} {order.option_type} exp {order.expiration} "
            f"@ ${order.entry_price:.2f} — {status_str}"
        )

    # Clean up filled/cancelled orders
    for oid in filled:
        remove_pending_order(oid)

    if len(lines) == 1:
        await message.channel.send("No pending orders (all previously pending orders have filled or been cancelled).")
        return

    lines.append("```")
    lines.append(f"Cancel with `{PREFIX}cancel <order_id>` or `{PREFIX}cancel all`")
    await message.channel.send("\n".join(lines))


async def _cancel_order(message: discord.Message, order_id_or_all: str):
    """Cancel a pending order by ID, or all pending orders."""
    pending = get_all_pending_orders()

    if not pending:
        await message.channel.send("No pending orders to cancel.")
        return

    if order_id_or_all == "all":
        cancelled = []
        failed = []
        for oid, order in pending.items():
            if cancel_order(oid):
                remove_pending_order(oid)
                cancelled.append(f"{order.ticker} [{oid}]")
            else:
                failed.append(f"{order.ticker} [{oid}]")

        lines = []
        if cancelled:
            lines.append(f"**Cancelled {len(cancelled)} order(s):**\n" + ", ".join(cancelled))
        if failed:
            lines.append(f"**Failed to cancel {len(failed)}:**\n" + ", ".join(failed))
        await message.channel.send("\n".join(lines) if lines else "No orders to cancel.")
        return

    # Cancel specific order
    order = pending.get(order_id_or_all)
    if not order:
        await message.channel.send(
            f"Order `{order_id_or_all}` not found. Use `{PREFIX}orders` to see pending orders."
        )
        return

    if cancel_order(order_id_or_all):
        # Also cancel the associated stop order if any
        if order.stop_order_id:
            cancel_order(order.stop_order_id)
        remove_pending_order(order_id_or_all)
        await message.channel.send(
            f"**Order Cancelled**\n"
            f"{order.ticker} {order.contracts}x ${order.strike} {order.option_type} "
            f"exp {order.expiration} — Order `{order_id_or_all}`"
        )
    else:
        await message.channel.send(
            f"**Failed to cancel** order `{order_id_or_all}`. "
            f"It may have already filled or been cancelled."
        )


async def _show_help(message: discord.Message):
    reply = (
        f"**Trading Bot Commands**\n"
        f"```\n"
        f"{PREFIX}settings              View current settings\n"
        f"{PREFIX}positions             View open positions\n"
        f"{PREFIX}orders                View pending (unfilled) orders\n"
        f"{PREFIX}cancel <id>           Cancel a pending order by ID\n"
        f"{PREFIX}cancel all            Cancel all pending orders\n"
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
