"""
Tastytrade broker integration for placing options orders.

Supports two exit modes:
  - "auto":   OTOCO order (entry + OCO stop-loss/take-profit)
  - "manual": Entry + standalone stop-loss only; take-profit triggered
              by a Discord trim alert

Uses the tastyware/tastytrade SDK to:
  - Authenticate via OAuth
  - Get account value for position sizing
  - Look up option contracts
  - Place OTOCO or entry+stop orders
  - Sell positions on trim alerts
"""

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from tastytrade import Session, Account
from tastytrade.instruments import Option, get_option_chain
from tastytrade.order import (
    Leg,
    NewComplexOrder,
    NewOrder,
    OrderAction,
    OrderTimeInForce,
    OrderType,
)

from alert_parser import ParsedAlert
from config import Config

logger = logging.getLogger(__name__)

# Module-level state
_session: Session | None = None
_account: Account | None = None
_session_ctx = None  # holds the async context manager so it stays alive


@dataclass
class OrderResult:
    success: bool
    order_id: str | None
    stop_order_id: str | None  # standalone stop order ID (manual TP mode)
    option_symbol: str | None  # for selling later
    contracts: int
    total_cost: float
    stop_loss_price: float
    take_profit_price: float  # 0 in manual mode
    message: str


@dataclass
class SellResult:
    success: bool
    contracts_sold: int
    message: str


async def login() -> bool:
    """Authenticate with Tastytrade via OAuth. Returns True on success."""
    global _session, _account, _session_ctx
    try:
        session = Session(Config.TT_CLIENT_SECRET, Config.TT_REFRESH_TOKEN)
        # Session must be entered as an async context manager to initialize
        # the httpx AsyncClient — without this, all API calls fail.
        _session_ctx = session.__asynccontextmanager__()
        _session = await _session_ctx.__aenter__()
        accounts = await Account.get(_session)
        if isinstance(accounts, list):
            _account = next(
                (a for a in accounts if a.account_number == Config.TT_ACCOUNT_NUMBER),
                accounts[0] if accounts else None,
            )
        else:
            _account = accounts
        if not _account:
            logger.error("No Tastytrade account found for %s", Config.TT_ACCOUNT_NUMBER)
            return False
        logger.info("Tastytrade login successful for account %s", _account.account_number)
        return True
    except Exception as e:
        logger.error("Tastytrade login error: %s", e)
        return False


async def get_account_value() -> float:
    """Get net liquidating value of the account."""
    if not _session or not _account:
        raise RuntimeError("Not logged in to Tastytrade")
    balance = await _account.get_balances(_session)
    return float(balance.derivative_buying_power)


async def _find_option(alert: ParsedAlert) -> Option | None:
    """Look up the specific option contract from the chain."""
    if not _session:
        raise RuntimeError("Not logged in to Tastytrade")

    chain = await get_option_chain(_session, alert.ticker)
    exp_date = date.fromisoformat(alert.expiration)

    if exp_date not in chain:
        available = sorted(chain.keys())
        closest = min(available, key=lambda d: abs((d - exp_date).days), default=None)
        if closest is None:
            logger.error("No expirations found for %s", alert.ticker)
            return None
        logger.warning(
            "Exact expiration %s not found for %s, using closest: %s",
            exp_date, alert.ticker, closest,
        )
        exp_date = closest

    strike = Decimal(str(alert.strike))
    option_type = "C" if alert.option_type == "call" else "P"

    for option in chain[exp_date]:
        if option.strike_price == strike and option.option_type == option_type:
            return option

    logger.error(
        "Option not found: %s $%s %s exp %s",
        alert.ticker, alert.strike, alert.option_type, exp_date,
    )
    return None


def calculate_position(
    account_value: float,
    entry_price: float,
    risk_pct: float,
    stop_loss_pct: float,
    take_profit_pct: float,
) -> tuple[int, float, float]:
    """Calculate number of contracts, stop-loss price, and take-profit price.

    Returns:
        (num_contracts, stop_loss_price, take_profit_price)
    """
    max_risk_dollars = account_value * (risk_pct / 100.0)
    cost_per_contract = entry_price * 100
    loss_per_contract = cost_per_contract * (stop_loss_pct / 100.0)

    if loss_per_contract <= 0:
        return 0, 0.0, 0.0

    num_contracts = int(max_risk_dollars / loss_per_contract)
    # Always buy at least 1 contract if affordable
    num_contracts = max(num_contracts, 1)

    stop_loss_price = round(entry_price * (1 - stop_loss_pct / 100.0), 2)
    take_profit_price = round(entry_price * (1 + take_profit_pct / 100.0), 2)

    return num_contracts, stop_loss_price, take_profit_price


async def place_order(alert: ParsedAlert) -> OrderResult:
    """Place an order based on the configured exit mode.

    - "auto" mode:   OTOCO (entry + OCO stop-loss/take-profit)
    - "manual" mode:  Entry buy + standalone stop-loss; TP via Discord trim alert
    """
    if not Config.PAPER_TRADE and (not _session or not _account):
        return OrderResult(
            success=False, order_id=None, stop_order_id=None, option_symbol=None,
            contracts=0, total_cost=0, stop_loss_price=0, take_profit_price=0,
            message="Not logged in to Tastytrade",
        )

    if Config.PAPER_TRADE:
        account_value = 25000.0
    else:
        account_value = await get_account_value()

    logger.info("Account value: $%.2f", account_value)

    is_lotto = "lotto" in alert.raw_text.lower()
    effective_risk_pct = Config.RISK_PER_TRADE_PCT * (0.5 if is_lotto else 1.0)
    if is_lotto:
        logger.info("Lotto alert detected — using half risk (%.1f%%)", effective_risk_pct)

    num_contracts, stop_loss_price, take_profit_price = calculate_position(
        account_value=account_value,
        entry_price=alert.entry_price,
        risk_pct=effective_risk_pct,
        stop_loss_pct=Config.STOP_LOSS_PCT,
        take_profit_pct=Config.TAKE_PROFIT_PCT,
    )

    total_cost = num_contracts * alert.entry_price * 100

    # Reject only if we can't afford even 1 contract
    if total_cost > account_value:
        return OrderResult(
            success=False, order_id=None, stop_order_id=None, option_symbol=None,
            contracts=0, total_cost=0, stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            message=(
                f"Cannot place order: 1 contract of {alert.ticker} costs "
                f"${alert.entry_price * 100:.2f} but buying power is ${account_value:.2f}"
            ),
        )
    is_manual = Config.EXIT_MODE == "manual"
    mode_label = "manual TP" if is_manual else "auto OTOCO"

    logger.info(
        "Placing %s: %d contracts of %s $%.2f %s exp %s @ $%.2f (SL $%.2f%s)",
        mode_label, num_contracts, alert.ticker, alert.strike, alert.option_type,
        alert.expiration, alert.entry_price, stop_loss_price,
        "" if is_manual else f" / TP ${take_profit_price}",
    )

    # --- Paper trade ---
    if Config.PAPER_TRADE:
        logger.info("[PAPER TRADE] Order not sent to broker")
        tp_info = f" | TP @ ${take_profit_price}" if not is_manual else " | TP: awaiting trim alert"
        return OrderResult(
            success=True, order_id="PAPER-TRADE",
            stop_order_id="PAPER-STOP" if is_manual else None,
            option_symbol=f"PAPER-{alert.ticker}",
            contracts=num_contracts, total_cost=total_cost,
            stop_loss_price=stop_loss_price,
            take_profit_price=0.0 if is_manual else take_profit_price,
            message=(
                f"[PAPER] Would buy {num_contracts}x {alert.ticker} "
                f"${alert.strike} {alert.option_type} exp {alert.expiration} "
                f"@ ${alert.entry_price} | SL @ ${stop_loss_price}{tp_info}"
            ),
        )

    # --- Live order ---
    try:
        option = await _find_option(alert)
        if option is None:
            return OrderResult(
                success=False, order_id=None, stop_order_id=None, option_symbol=None,
                contracts=num_contracts, total_cost=total_cost,
                stop_loss_price=stop_loss_price,
                take_profit_price=0.0 if is_manual else take_profit_price,
                message=f"Could not find option contract for {alert.ticker} "
                        f"${alert.strike} {alert.option_type} exp {alert.expiration}",
            )

        opening_leg = option.build_leg(Decimal(num_contracts), OrderAction.BUY_TO_OPEN)
        closing_leg = option.build_leg(Decimal(num_contracts), OrderAction.SELL_TO_CLOSE)
        option_symbol = option.symbol

        if is_manual:
            return await _place_entry_with_stop(
                alert, option, opening_leg, closing_leg, option_symbol,
                num_contracts, total_cost, stop_loss_price, take_profit_price,
            )
        else:
            return await _place_otoco(
                alert, option, opening_leg, closing_leg, option_symbol,
                num_contracts, total_cost, stop_loss_price, take_profit_price,
            )
    except Exception as e:
        logger.error("Order failed: %s", e)
        return OrderResult(
            success=False, order_id=None, stop_order_id=None, option_symbol=None,
            contracts=num_contracts, total_cost=total_cost,
            stop_loss_price=stop_loss_price,
            take_profit_price=0.0 if is_manual else take_profit_price,
            message=f"Order failed: {e}",
        )


async def _place_otoco(
    alert, option, opening_leg, closing_leg, option_symbol,
    num_contracts, total_cost, stop_loss_price, take_profit_price,
) -> OrderResult:
    """Place an OTOCO order: entry triggers OCO (take-profit + stop-loss)."""
    otoco = NewComplexOrder(
        trigger_order=NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[opening_leg],
            price=Decimal(str(-alert.entry_price)),
        ),
        orders=[
            NewOrder(
                time_in_force=OrderTimeInForce.GTC,
                order_type=OrderType.LIMIT,
                legs=[closing_leg],
                price=Decimal(str(take_profit_price)),
            ),
            NewOrder(
                time_in_force=OrderTimeInForce.GTC,
                order_type=OrderType.STOP,
                legs=[closing_leg],
                stop_trigger=Decimal(str(stop_loss_price)),
            ),
        ],
    )

    response = await _account.place_complex_order(_session, otoco, dry_run=False)
    order_id = str(getattr(response, "id", "unknown"))
    logger.info("OTOCO order placed: %s", order_id)

    return OrderResult(
        success=True, order_id=order_id, stop_order_id=None,
        option_symbol=option_symbol,
        contracts=num_contracts, total_cost=total_cost,
        stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
        message=(
            f"Bought {num_contracts}x {alert.ticker} ${alert.strike} "
            f"{alert.option_type} exp {alert.expiration} @ ${alert.entry_price} "
            f"| SL @ ${stop_loss_price} | TP @ ${take_profit_price}"
        ),
    )


async def _place_entry_with_stop(
    alert, option, opening_leg, closing_leg, option_symbol,
    num_contracts, total_cost, stop_loss_price, take_profit_price,
) -> OrderResult:
    """Place OTOCO bracket: entry triggers OCO (stop-loss + take-profit). TP can be overridden by trim alert."""
    bracket = NewComplexOrder(
        trigger_order=NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.LIMIT,
            legs=[opening_leg],
            price=Decimal(str(-alert.entry_price)),
        ),
        orders=[
            NewOrder(
                time_in_force=OrderTimeInForce.GTC,
                order_type=OrderType.LIMIT,
                legs=[closing_leg],
                price=Decimal(str(take_profit_price)),
            ),
            NewOrder(
                time_in_force=OrderTimeInForce.GTC,
                order_type=OrderType.STOP,
                legs=[closing_leg],
                stop_trigger=Decimal(str(stop_loss_price)),
            ),
        ],
    )

    response = await _account.place_complex_order(_session, bracket, dry_run=False)
    order_id = str(getattr(response, "id", "unknown"))
    logger.info("Bracket order placed (entry + SL + TP): %s", order_id)

    return OrderResult(
        success=True, order_id=order_id, stop_order_id=order_id,
        option_symbol=option_symbol,
        contracts=num_contracts, total_cost=total_cost,
        stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
        message=(
            f"Bought {num_contracts}x {alert.ticker} ${alert.strike} "
            f"{alert.option_type} exp {alert.expiration} @ ${alert.entry_price} "
            f"| SL @ ${stop_loss_price} | TP @ ${take_profit_price} (or trim alert)"
        ),
    )


async def get_order_status(order_id: str) -> str | None:
    """Get the status of an order. Returns status string or None if not found."""
    if Config.PAPER_TRADE:
        return "Filled"
    if not _session or not _account:
        return None
    try:
        order = await _account.get_order(_session, order_id)
        return str(getattr(order, "status", "Unknown"))
    except Exception as e:
        logger.warning("Could not get status for order %s: %s", order_id, e)
        return None


async def cancel_order(order_id: str) -> bool:
    """Cancel an open order. Returns True on success."""
    if Config.PAPER_TRADE:
        logger.info("[PAPER] Would cancel order %s", order_id)
        return True
    if not _session or not _account:
        return False
    try:
        await _account.delete_order(_session, order_id)
        logger.info("Cancelled order %s", order_id)
        return True
    except Exception as e:
        logger.error("Failed to cancel order %s: %s", order_id, e)
        return False


async def sell_position(
    option_symbol: str,
    contracts: int,
    stop_order_id: str | None = None,
) -> SellResult:
    """Sell an open position (market order) and cancel its stop-loss if any.

    Args:
        option_symbol: The Tastytrade option symbol to sell.
        contracts: Number of contracts to sell.
        stop_order_id: If set, cancel this stop-loss order first.
    """
    if Config.PAPER_TRADE:
        logger.info("[PAPER] Would sell %d contracts of %s", contracts, option_symbol)
        return SellResult(
            success=True, contracts_sold=contracts,
            message=f"[PAPER] Sold {contracts}x {option_symbol}",
        )

    if not _session or not _account:
        return SellResult(
            success=False, contracts_sold=0,
            message="Not logged in to Tastytrade",
        )

    try:
        # Cancel the standing stop-loss order first
        if stop_order_id:
            try:
                await _account.delete_order(_session, stop_order_id)
                logger.info("Cancelled stop-loss order %s", stop_order_id)
            except Exception as e:
                logger.warning("Could not cancel stop order %s: %s", stop_order_id, e)

        # Look up the option to build a sell leg
        option = await Option.get(_session, option_symbol)
        closing_leg = option.build_leg(Decimal(contracts), OrderAction.SELL_TO_CLOSE)

        sell_order = NewOrder(
            time_in_force=OrderTimeInForce.DAY,
            order_type=OrderType.MARKET,
            legs=[closing_leg],
        )
        await _account.place_order(_session, sell_order, dry_run=False)
        logger.info("Sold %d contracts of %s", contracts, option_symbol)

        return SellResult(
            success=True, contracts_sold=contracts,
            message=f"Sold {contracts}x {option_symbol} at market",
        )
    except Exception as e:
        logger.error("Sell failed: %s", e)
        return SellResult(
            success=False, contracts_sold=0,
            message=f"Sell failed: {e}",
        )
