"""
In-memory tracker for open positions and pending orders.

Tracks positions opened by the bot so it knows what to sell when a
trim/profit alert arrives. Keyed by ticker symbol (uppercase).

Also tracks pending orders (placed but not yet filled) so the user
can view and cancel them.
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


@dataclass
class PendingOrder:
    """An order that has been placed but not yet filled."""
    order_id: str
    ticker: str
    strike: float
    option_type: str  # "call" or "put"
    expiration: str  # YYYY-MM-DD
    contracts: int
    entry_price: float
    total_cost: float
    stop_loss_price: float
    take_profit_price: float
    option_symbol: str | None
    stop_order_id: str | None
    placed_at: datetime = field(default_factory=datetime.now)


# Ticker -> list of positions (could have multiple entries on the same ticker)
_positions: dict[str, list[Position]] = {}

# order_id -> PendingOrder
_pending_orders: dict[str, PendingOrder] = {}


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


# --- Pending order tracking ---

def add_pending_order(order: PendingOrder) -> None:
    _pending_orders[order.order_id] = order
    logger.info("Tracking pending order %s: %s %dx $%.2f %s",
                order.order_id, order.ticker, order.contracts,
                order.strike, order.option_type)


def get_pending_order(order_id: str) -> PendingOrder | None:
    return _pending_orders.get(order_id)


def get_all_pending_orders() -> dict[str, PendingOrder]:
    return dict(_pending_orders)


def remove_pending_order(order_id: str) -> PendingOrder | None:
    order = _pending_orders.pop(order_id, None)
    if order:
        logger.info("Removed pending order %s for %s", order_id, order.ticker)
    return order
