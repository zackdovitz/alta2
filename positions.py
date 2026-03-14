"""
In-memory tracker for open positions.

Tracks positions opened by the bot so it knows what to sell when a
trim/profit alert arrives. Keyed by ticker symbol (uppercase).
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class Position:
    ticker: str
    strike: float
    option_type: str  # "call" or "put"
    expiration: str  # YYYY-MM-DD
    contracts: int
    entry_price: float
    total_cost: float
    stop_loss_price: float
    entry_order_id: str | None
    stop_order_id: str | None  # standalone stop-loss order (manual TP mode)
    option_symbol: str | None  # Tastytrade option symbol for selling
    opened_at: datetime = field(default_factory=datetime.now)


# Ticker -> list of positions (could have multiple entries on the same ticker)
_positions: dict[str, list[Position]] = {}


def add_position(position: Position) -> None:
    ticker = position.ticker.upper()
    _positions.setdefault(ticker, []).append(position)
    logger.info(
        "Tracking position: %s %dx $%.2f %s exp %s (stop order: %s)",
        ticker, position.contracts, position.strike, position.option_type,
        position.expiration, position.stop_order_id,
    )


def get_positions(ticker: str) -> list[Position]:
    return _positions.get(ticker.upper(), [])


def get_all_positions() -> dict[str, list[Position]]:
    return dict(_positions)


def remove_position(ticker: str, position: Position) -> None:
    ticker = ticker.upper()
    if ticker in _positions:
        try:
            _positions[ticker].remove(position)
            if not _positions[ticker]:
                del _positions[ticker]
            logger.info("Removed position: %s", ticker)
        except ValueError:
            logger.warning("Position not found for removal: %s", ticker)


def remove_all_positions(ticker: str) -> list[Position]:
    """Remove and return all positions for a ticker."""
    ticker = ticker.upper()
    removed = _positions.pop(ticker, [])
    if removed:
        logger.info("Removed all %d position(s) for %s", len(removed), ticker)
    return removed
