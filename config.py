import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # Discord
    DISCORD_BOT_TOKEN: str = os.getenv("DISCORD_BOT_TOKEN", "")
    DISCORD_CHANNEL_IDS: list[int] = [
        int(cid.strip())
        for cid in os.getenv("DISCORD_CHANNEL_IDS", "").split(",")
        if cid.strip()
    ]

    # Tastytrade OAuth
    TT_CLIENT_SECRET: str = os.getenv("TT_CLIENT_SECRET", "")
    TT_REFRESH_TOKEN: str = os.getenv("TT_REFRESH_TOKEN", "")
    TT_ACCOUNT_NUMBER: str = os.getenv("TT_ACCOUNT_NUMBER", "")

    # Risk management (adjustable at runtime via Discord commands)
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "4.0"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "25.0"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "30.0"))

    # Exit mode: "auto" = OTOCO with fixed TP, "manual" = stop-loss only, TP via trim alert
    EXIT_MODE: str = os.getenv("EXIT_MODE", "manual")

    # Optional separate channel(s) for trim/profit alerts (comma-separated IDs)
    # If empty, trim alerts are detected in the same alert channels
    PROFIT_CHANNEL_IDS: list[int] = [
        int(cid.strip())
        for cid in os.getenv("PROFIT_CHANNEL_IDS", "").split(",")
        if cid.strip()
    ]

    # Paper trading mode
    PAPER_TRADE: bool = os.getenv("PAPER_TRADE", "false").lower() == "true"

    # Command prefix for Discord commands
    COMMAND_PREFIX: str = "!"
