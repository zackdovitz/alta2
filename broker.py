"""
Robinhood broker integration for placing options orders.

Uses robin_stocks to:
  - Authenticate with Robinhood
  - Get account value for position sizing
  - Place options buy orders
  - Place stop-loss orders
"""

import logging
from dataclasses import dataclass

import robin_stocks.robinhood as rh

from alert_parser import ParsedAlert
from config import Config

logger = logging.getLogger(__name__)


@dataclass
class OrderResult:
    success: bool
    order_id: str | None
    contracts: int
    total_cost: float
    stop_loss_price: float
    message: str


def login() -> bool:
    """Authenticate with Robinhood. Returns True on success."""
    try:
        result = rh.login(
            username=Config.RH_USERNAME,
            password=Config.RH_PASSWORD,
            mfa_code=Config.RH_MFA_CODE or None,
            store_session=True,
        )
        if result:
            logger.info("Robinhood login successful")
            return True
        logger.error("Robinhood login failed — no result returned")
        return False
    except Exception as e:
        logger.error("Robinhood login error: %s", e)
        return False


def get_account_value() -> float:
    """Get total portfolio value (cash + positions)."""
    profile = rh.profiles.load_portfolio_profile()
    equity = float(profile.get("equity", 0) or 0)
    if equity == 0:
        # Fallback to cash balance
        account = rh.profiles.load_account_profile()
        equity = float(account.get("portfolio_cash", 0) or 0)
    return equity


def calculate_position(
    account_value: float, entry_price: float, risk_pct: float, stop_loss_pct: float
) -> tuple[int, float]:
    """Calculate number of contracts and stop-loss price.

    Args:
        account_value: Total account value in dollars.
        entry_price: Price per contract (premium).
        risk_pct: Max percentage of account to risk (e.g. 1.0 = 1%).
        stop_loss_pct: Stop-loss as percentage loss (e.g. 25.0 = sell at 25% loss).

    Returns:
        (num_contracts, stop_loss_price)
    """
    max_risk_dollars = account_value * (risk_pct / 100.0)

    # Cost per contract = premium * 100 shares
    cost_per_contract = entry_price * 100

    # The amount we'd lose per contract if stop loss hits
    loss_per_contract = cost_per_contract * (stop_loss_pct / 100.0)

    # Number of contracts we can buy while keeping risk within budget
    if loss_per_contract <= 0:
        return 0, 0.0

    num_contracts = int(max_risk_dollars / loss_per_contract)
    num_contracts = max(num_contracts, 0)

    # Stop loss price = entry minus the allowed loss (per share, not per contract)
    stop_loss_price = round(entry_price * (1 - stop_loss_pct / 100.0), 2)

    return num_contracts, stop_loss_price


def place_order(alert: ParsedAlert) -> OrderResult:
    """Place an options buy order with a stop loss based on the parsed alert."""
    account_value = get_account_value()
    logger.info("Account value: $%.2f", account_value)

    num_contracts, stop_loss_price = calculate_position(
        account_value=account_value,
        entry_price=alert.entry_price,
        risk_pct=Config.RISK_PER_TRADE_PCT,
        stop_loss_pct=Config.STOP_LOSS_PCT,
    )

    if num_contracts == 0:
        return OrderResult(
            success=False,
            order_id=None,
            contracts=0,
            total_cost=0,
            stop_loss_price=0,
            message=(
                f"Cannot place order: account value ${account_value:.2f} with "
                f"1% risk (${account_value * 0.01:.2f}) is too small for "
                f"{alert.ticker} at ${alert.entry_price} per contract"
            ),
        )

    total_cost = num_contracts * alert.entry_price * 100

    logger.info(
        "Placing order: %d contracts of %s $%.2f %s exp %s @ $%.2f (total $%.2f)",
        num_contracts,
        alert.ticker,
        alert.strike,
        alert.option_type,
        alert.expiration,
        alert.entry_price,
        total_cost,
    )

    if Config.PAPER_TRADE:
        logger.info("[PAPER TRADE] Order not sent to broker")
        return OrderResult(
            success=True,
            order_id="PAPER-TRADE",
            contracts=num_contracts,
            total_cost=total_cost,
            stop_loss_price=stop_loss_price,
            message=(
                f"[PAPER] Would buy {num_contracts}x {alert.ticker} "
                f"${alert.strike} {alert.option_type} exp {alert.expiration} "
                f"@ ${alert.entry_price} | Stop loss @ ${stop_loss_price}"
            ),
        )

    # --- Live order ---
    try:
        order = rh.orders.order_buy_option_limit(
            positionEffect="open",
            creditOrDebit="debit",
            price=alert.entry_price,
            symbol=alert.ticker,
            quantity=num_contracts,
            expirationDate=alert.expiration,
            strike=alert.strike,
            optionType=alert.option_type,
            timeInForce="gfd",
        )

        order_id = order.get("id", "unknown")
        logger.info("Buy order placed: %s", order_id)

        # Place stop-loss order
        _place_stop_loss(alert, num_contracts, stop_loss_price)

        return OrderResult(
            success=True,
            order_id=order_id,
            contracts=num_contracts,
            total_cost=total_cost,
            stop_loss_price=stop_loss_price,
            message=(
                f"Bought {num_contracts}x {alert.ticker} ${alert.strike} "
                f"{alert.option_type} exp {alert.expiration} @ ${alert.entry_price} "
                f"| Stop loss set @ ${stop_loss_price}"
            ),
        )
    except Exception as e:
        logger.error("Order failed: %s", e)
        return OrderResult(
            success=False,
            order_id=None,
            contracts=num_contracts,
            total_cost=total_cost,
            stop_loss_price=stop_loss_price,
            message=f"Order failed: {e}",
        )


def _place_stop_loss(
    alert: ParsedAlert, num_contracts: int, stop_loss_price: float
) -> None:
    """Place a stop-loss sell order for an existing position."""
    try:
        rh.orders.order_sell_option_limit(
            positionEffect="close",
            creditOrDebit="credit",
            price=stop_loss_price,
            symbol=alert.ticker,
            quantity=num_contracts,
            expirationDate=alert.expiration,
            strike=alert.strike,
            optionType=alert.option_type,
            timeInForce="gtc",
        )
        logger.info(
            "Stop-loss order placed at $%.2f for %d contracts", stop_loss_price, num_contracts
        )
    except Exception as e:
        logger.error("Stop-loss order failed: %s", e)
