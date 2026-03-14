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

    # Robinhood
    RH_USERNAME: str = os.getenv("RH_USERNAME", "")
    RH_PASSWORD: str = os.getenv("RH_PASSWORD", "")
    RH_MFA_CODE: str = os.getenv("RH_MFA_CODE", "")

    # Risk management (adjustable — these are defaults)
    RISK_PER_TRADE_PCT: float = float(os.getenv("RISK_PER_TRADE_PCT", "1.0"))
    STOP_LOSS_PCT: float = float(os.getenv("STOP_LOSS_PCT", "25.0"))
    TAKE_PROFIT_PCT: float = float(os.getenv("TAKE_PROFIT_PCT", "30.0"))

    # Paper trading mode
    PAPER_TRADE: bool = os.getenv("PAPER_TRADE", "true").lower() == "true"
