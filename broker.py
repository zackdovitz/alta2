"""
Tastytrade broker integration for placing options orders with OCO exit orders.

Uses the tastyware/tastytrade SDK to:
  - Authenticate via OAuth
  - Get account value for position sizing
  - Look up option contracts
  - Place OTOCO orders (entry + OCO stop-loss/take-profit)
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


@dataclass
class OrderResult:
    success: bool
    order_id: str | None
    contracts: int
    total_cost: float
    stop_loss_price: float
    take_profit_price: float
    message: str


def login() -> bool:
    """Authenticate with Tastytrade via OAuth. Returns True on success."""
    global _session, _account
    try:
        _session = Session(Config.TT_CLIENT_SECRET, Config.TT_REFRESH_TOKEN)
        _account = Account.get(_session, Config.TT_ACCOUNT_NUMBER)
        logger.info("Tastytrade login successful for account %s", Config.TT_ACCOUNT_NUMBER)
        return True
    except Exception as e:
        logger.error("Tastytrade login error: %s", e)
        return False


def get_account_value() -> float:
    """Get net liquidating value of the account."""
    if not _session or not _account:
        raise RuntimeError("Not logged in to Tastytrade")
    balance = _account.get_balances(_session)
    return float(balance.net_liquidating_value)


def _find_option(alert: ParsedAlert) -> Option | None:
    """Look up the specific option contract from the chain."""
    if not _session:
        raise RuntimeError("Not logged in to Tastytrade")

    chain = get_option_chain(_session, alert.ticker)
    exp_date = date.fromisoformat(alert.expiration)

    if exp_date not in chain:
        # Find the closest expiration
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

    Args:
        account_value: Total account value in dollars.
        entry_price: Price per contract (premium).
        risk_pct: Max percentage of account to risk (e.g. 1.0 = 1%).
        stop_loss_pct: Stop-loss as percentage loss (e.g. 25.0 = sell at 25% loss).
        take_profit_pct: Take-profit as percentage gain (e.g. 30.0 = sell at 30% gain).

    Returns:
        (num_contracts, stop_loss_price, take_profit_price)
    """
    max_risk_dollars = account_value * (risk_pct / 100.0)

    # Cost per contract = premium * 100 shares
    cost_per_contract = entry_price * 100

    # The amount we'd lose per contract if stop loss hits
    loss_per_contract = cost_per_contract * (stop_loss_pct / 100.0)

    if loss_per_contract <= 0:
        return 0, 0.0, 0.0

    num_contracts = int(max_risk_dollars / loss_per_contract)
    num_contracts = max(num_contracts, 0)

    stop_loss_price = round(entry_price * (1 - stop_loss_pct / 100.0), 2)
    take_profit_price = round(entry_price * (1 + take_profit_pct / 100.0), 2)

    return num_contracts, stop_loss_price, take_profit_price


def place_order(alert: ParsedAlert) -> OrderResult:
    """Place an OTOCO order: entry buy + OCO (stop-loss, take-profit)."""
    if not Config.PAPER_TRADE and (not _session or not _account):
        return OrderResult(
            success=False, order_id=None, contracts=0, total_cost=0,
            stop_loss_price=0, take_profit_price=0,
            message="Not logged in to Tastytrade",
        )

    # Calculate position size
    if Config.PAPER_TRADE:
        account_value = 25000.0  # Default paper account value
    else:
        account_value = get_account_value()

    logger.info("Account value: $%.2f", account_value)

    num_contracts, stop_loss_price, take_profit_price = calculate_position(
        account_value=account_value,
        entry_price=alert.entry_price,
        risk_pct=Config.RISK_PER_TRADE_PCT,
        stop_loss_pct=Config.STOP_LOSS_PCT,
        take_profit_pct=Config.TAKE_PROFIT_PCT,
    )

    if num_contracts == 0:
        return OrderResult(
            success=False, order_id=None, contracts=0, total_cost=0,
            stop_loss_price=0, take_profit_price=0,
            message=(
                f"Cannot place order: account value ${account_value:.2f} with "
                f"{Config.RISK_PER_TRADE_PCT}% risk "
                f"(${account_value * Config.RISK_PER_TRADE_PCT / 100:.2f}) is too small for "
                f"{alert.ticker} at ${alert.entry_price} per contract"
            ),
        )

    total_cost = num_contracts * alert.entry_price * 100

    logger.info(
        "Placing OTOCO: %d contracts of %s $%.2f %s exp %s @ $%.2f "
        "(SL $%.2f / TP $%.2f)",
        num_contracts, alert.ticker, alert.strike, alert.option_type,
        alert.expiration, alert.entry_price, stop_loss_price, take_profit_price,
    )

    if Config.PAPER_TRADE:
        logger.info("[PAPER TRADE] Order not sent to broker")
        return OrderResult(
            success=True, order_id="PAPER-TRADE",
            contracts=num_contracts, total_cost=total_cost,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            message=(
                f"[PAPER] Would buy {num_contracts}x {alert.ticker} "
                f"${alert.strike} {alert.option_type} exp {alert.expiration} "
                f"@ ${alert.entry_price} | SL @ ${stop_loss_price} | "
                f"TP @ ${take_profit_price}"
            ),
        )

    # --- Live OTOCO order via Tastytrade ---
    try:
        option = _find_option(alert)
        if option is None:
            return OrderResult(
                success=False, order_id=None,
                contracts=num_contracts, total_cost=total_cost,
                stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
                message=f"Could not find option contract for {alert.ticker} "
                        f"${alert.strike} {alert.option_type} exp {alert.expiration}",
            )

        opening_leg = option.build_leg(Decimal(num_contracts), OrderAction.BUY_TO_OPEN)
        closing_leg = option.build_leg(Decimal(num_contracts), OrderAction.SELL_TO_CLOSE)

        # OTOCO: entry triggers an OCO pair (take-profit limit + stop-loss)
        otoco = NewComplexOrder(
            trigger_order=NewOrder(
                time_in_force=OrderTimeInForce.DAY,
                order_type=OrderType.LIMIT,
                legs=[opening_leg],
                price=Decimal(str(-alert.entry_price)),  # negative = debit
            ),
            orders=[
                # Take-profit: limit sell at gain target
                NewOrder(
                    time_in_force=OrderTimeInForce.GTC,
                    order_type=OrderType.LIMIT,
                    legs=[closing_leg],
                    price=Decimal(str(take_profit_price)),  # positive = credit
                ),
                # Stop-loss: stop sell at loss target
                NewOrder(
                    time_in_force=OrderTimeInForce.GTC,
                    order_type=OrderType.STOP,
                    legs=[closing_leg],
                    stop_trigger=Decimal(str(stop_loss_price)),
                ),
            ],
        )

        response = _account.place_complex_order(_session, otoco, dry_run=False)
        order_id = str(getattr(response, "id", "unknown"))
        logger.info("OTOCO order placed: %s", order_id)

        return OrderResult(
            success=True, order_id=order_id,
            contracts=num_contracts, total_cost=total_cost,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            message=(
                f"Bought {num_contracts}x {alert.ticker} ${alert.strike} "
                f"{alert.option_type} exp {alert.expiration} @ ${alert.entry_price} "
                f"| SL @ ${stop_loss_price} | TP @ ${take_profit_price}"
            ),
        )
    except Exception as e:
        logger.error("Order failed: %s", e)
        return OrderResult(
            success=False, order_id=None,
            contracts=num_contracts, total_cost=total_cost,
            stop_loss_price=stop_loss_price, take_profit_price=take_profit_price,
            message=f"Order failed: {e}",
        )
