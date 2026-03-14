"""
Flexible parser for Discord options trading alerts.

Handles two types of alerts:

1. **Entry alerts** — buy signals with ticker, strike, type, expiration, price:
   "$RKLB - Lotto size - weekly $71 calls for $1.20  @everyone $alert"
   "BTO AAPL 150C 3/15 @ 2.50"
   "TSLA 800 puts 1/19 for $3.40"
   "Buying SPY $450 calls expiring Friday at $1.05"

2. **Trim/profit alerts** — signals to sell an open position:
   "Trim RKLB calls"
   "Take profit on AAPL"
   "Close TSLA position"
   "Selling half SPY calls"
   "Lock in gains on RKLB"
"""

import re
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass
class ParsedAlert:
    ticker: str
    strike: float
    option_type: str  # "call" or "put"
    expiration: str  # ISO date string YYYY-MM-DD
    entry_price: float
    raw_text: str


@dataclass
class TrimAlert:
    """A signal to sell/trim an open position."""
    ticker: str
    sell_all: bool  # True = close entire position, False = trim half
    raw_text: str


# Common tickers that might be confused with words
TICKER_BLACKLIST = {"FOR", "ALL", "AT", "THE", "BIG", "NEW", "ONE", "NOW", "IT", "UP"}


def parse_alert(text: str) -> ParsedAlert | None:
    """Parse a free-form trading alert into structured data.

    Returns None if the message doesn't look like a trading alert.
    """
    raw = text
    cleaned = text.upper()

    ticker = _extract_ticker(cleaned)
    if not ticker:
        return None

    option_type = _extract_option_type(cleaned)
    if not option_type:
        option_type = "call"  # Default to call if not specified

    strike = _extract_strike(cleaned, ticker)
    if strike is None:
        return None

    entry_price = _extract_entry_price(cleaned)
    if entry_price is None:
        return None

    expiration = _extract_expiration(text)

    return ParsedAlert(
        ticker=ticker,
        strike=strike,
        option_type=option_type,
        expiration=expiration,
        entry_price=entry_price,
        raw_text=raw,
    )


def _extract_ticker(text: str) -> str | None:
    """Extract ticker symbol. Looks for $TICKER or standalone 1-5 letter symbols."""
    # Try $TICKER first (most explicit)
    match = re.search(r"\$([A-Z]{1,5})\b", text)
    if match and match.group(1) not in TICKER_BLACKLIST and match.group(1) != "ALERT":
        return match.group(1)

    # Try "BTO TICKER" or "BUY TICKER" pattern
    match = re.search(r"\b(?:BTO|BUY|BUYING)\s+([A-Z]{1,5})\b", text)
    if match and match.group(1) not in TICKER_BLACKLIST:
        return match.group(1)

    # Try first standalone 1-5 letter word that looks like a ticker
    # (at start of message or after common prefixes)
    match = re.search(r"(?:^|\n)\s*\$?([A-Z]{1,5})\b", text)
    if match and match.group(1) not in TICKER_BLACKLIST:
        candidate = match.group(1)
        # Avoid matching action words
        if candidate not in {"BTO", "BUY", "BUYING", "STO", "SELL", "LOTTO", "ALERT"}:
            return candidate

    return None


def _extract_option_type(text: str) -> str | None:
    """Determine if this is a call or put."""
    call_patterns = [r"\bCALLS?\b", r"\d+C\b", r"\bC\s+\d"]
    put_patterns = [r"\bPUTS?\b", r"\d+P\b", r"\bP\s+\d"]

    is_call = any(re.search(p, text) for p in call_patterns)
    is_put = any(re.search(p, text) for p in put_patterns)

    if is_call and not is_put:
        return "call"
    if is_put and not is_call:
        return "put"
    if is_call and is_put:
        # Ambiguous — pick whichever appears first
        call_pos = min(
            (m.start() for p in call_patterns if (m := re.search(p, text))),
            default=9999,
        )
        put_pos = min(
            (m.start() for p in put_patterns if (m := re.search(p, text))),
            default=9999,
        )
        return "call" if call_pos < put_pos else "put"

    return None


def _extract_strike(text: str, ticker: str) -> float | None:
    """Extract the strike price."""
    # Try "$STRIKE calls/puts" pattern
    match = re.search(r"\$(\d+(?:\.\d+)?)\s*(?:CALLS?|PUTS?|C\b|P\b)", text)
    if match:
        return float(match.group(1))

    # Try "STRIKE calls/puts" (no dollar sign)
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:CALLS?|PUTS?)\b", text)
    if match:
        return float(match.group(1))

    # Try "STRIKEc" or "STRIKEp" compact format
    match = re.search(r"\b(\d+(?:\.\d+)?)\s*[CP]\b", text)
    if match:
        return float(match.group(1))

    # Try number after ticker: "AAPL 150" or "$AAPL $150"
    pattern = rf"\$?{re.escape(ticker)}\s+\$?(\d+(?:\.\d+)?)"
    match = re.search(pattern, text)
    if match:
        return float(match.group(1))

    return None


def _extract_entry_price(text: str) -> float | None:
    """Extract the price to pay per contract."""
    # Try "for $PRICE" or "@ $PRICE" or "at $PRICE" (handles $.58, $1.20, $2, etc.)
    match = re.search(
        r"(?:FOR|@|AT)\s*\$?(\.?\d+(?:\.\d+)?)", text, re.IGNORECASE
    )
    if match:
        return float(match.group(1))

    # Try "PRICE debit" or "PRICE premium"
    match = re.search(r"\$?(\.?\d+(?:\.\d+)?)\s*(?:DEBIT|PREMIUM|CREDIT)", text)
    if match:
        return float(match.group(1))

    return None


def _extract_expiration(text: str) -> str:
    """Extract expiration date from alert text.

    Priority:
      1. "0DTE" / "0 DTE" → today
      2. Explicit date (3/15, 03/15/25, 2025-03-15) → that date
      3. "weekly" / "weeklies" → this Friday
      4. Day name ("Friday", "Mon") → the upcoming occurrence
      5. No date info → this Friday (most common options expiry)
    """
    upper = text.upper()
    today = datetime.now()

    # 0DTE = expiring today
    if re.search(r"\b0\s*DTE\b", upper):
        return today.strftime("%Y-%m-%d")

    # Try explicit date formats: 3/15, 03/15, 3/15/25, 03/15/2025, 2025-03-15
    match = re.search(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b", text)
    if match:
        month, day = int(match.group(1)), int(match.group(2))
        year = today.year
        if match.group(3):
            y = int(match.group(3))
            year = y if y > 100 else 2000 + y
        try:
            exp = datetime(year, month, day)
            if exp < today:
                exp = exp.replace(year=year + 1)
            return exp.strftime("%Y-%m-%d")
        except ValueError:
            pass

    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if match:
        return match.group(0)

    # Check for "weekly" or "weeklies" — this Friday
    if re.search(r"\bWEEKL(?:Y|IES)\b", upper):
        return _this_friday(today)

    # Check for day names
    day_map = {
        "MONDAY": 0, "TUESDAY": 1, "WEDNESDAY": 2,
        "THURSDAY": 3, "FRIDAY": 4, "SATURDAY": 5, "SUNDAY": 6,
        "MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6,
    }
    for name, dow in day_map.items():
        if re.search(rf"\b{name}\b", upper):
            days_ahead = (dow - today.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = today + timedelta(days=days_ahead)
            return target.strftime("%Y-%m-%d")

    # Default: this Friday (most common options expiry)
    return _this_friday(today)


def _this_friday(from_date: datetime) -> str:
    """Return Friday of the current week. If today is Friday, return today.
    If today is Saturday/Sunday, return next Friday."""
    weekday = from_date.weekday()
    if weekday <= 4:  # Mon-Fri
        days_ahead = 4 - weekday  # 0 if already Friday
    else:  # Sat=5, Sun=6
        days_ahead = 4 + (7 - weekday)
    friday = from_date + timedelta(days=days_ahead)
    return friday.strftime("%Y-%m-%d")


def partial_parse(text: str) -> list[str] | None:
    """Check if text looks like a trading alert but is missing fields.

    Returns a list of missing field names if it looks like a partial alert,
    or None if it doesn't look like a trading alert at all.
    """
    cleaned = text.upper()
    ticker = _extract_ticker(cleaned)
    if not ticker:
        # Doesn't look like a trading alert at all
        return None

    missing = []
    strike = _extract_strike(cleaned, ticker)
    if strike is None:
        missing.append("strike price")

    entry_price = _extract_entry_price(cleaned)
    if entry_price is None:
        missing.append("entry price")

    if not missing:
        return None  # Has all required fields, shouldn't reach here

    return missing


# --- Trim/profit alert parsing ---

# Patterns that indicate "sell/trim this position"
_TRIM_PATTERNS = [
    r"\bTRIM\b",
    r"\bTAKE\s+PROFIT\b",
    r"\bLOCK\s+IN\b",
    r"\bCLOSE\b.*\bPOSITION\b",
    r"\bSELL(?:ING)?\b",
    r"\bSTC\b",          # Sell To Close
    r"\bCLOSING\b",
    r"\bEXIT\b",
    r"\bCASH\s*(?:OUT|IN)\b",
    r"\bSCALE\s+OUT\b",
    r"\bRING\s+.*REGISTER\b",
]

# Patterns that indicate "sell only half / partial"
_PARTIAL_PATTERNS = [
    r"\bHALF\b",
    r"\bPARTIAL\b",
    r"\bSOME\b",
    r"\bTRIM\b",       # "trim" typically implies partial, not full close
    r"\bSCALE\s+OUT\b",
]


def parse_trim_alert(text: str) -> TrimAlert | None:
    """Parse a message as a trim/profit-taking alert.

    Returns None if the message doesn't look like a trim signal.
    """
    upper = text.upper()

    # Must match at least one trim pattern
    is_trim = any(re.search(p, upper) for p in _TRIM_PATTERNS)
    if not is_trim:
        return None

    # Extract ticker
    ticker = _extract_ticker(upper)
    if not ticker:
        return None

    # Determine if it's a partial trim or full close
    is_partial = any(re.search(p, upper) for p in _PARTIAL_PATTERNS)
    sell_all = not is_partial

    return TrimAlert(ticker=ticker, sell_all=sell_all, raw_text=text)
