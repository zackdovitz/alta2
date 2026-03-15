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
   "Manage your TSLA position here"
   "Really loving these SPY gains, lets lock in 75% here"

LLM fallback:
   When regex fails or the message looks complex/ambiguous, an LLM is used
   to extract structured fields. Set OPENAI_API_KEY (and optionally
   OPENAI_BASE_URL / PARSER_MODEL) in your .env to enable this.
   If no API key is set, the parser silently falls back to regex-only.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class ParsedAlert:
    ticker: str
    strike: float
    option_type: str  # "call" or "put"
    expiration: str   # ISO date string YYYY-MM-DD
    entry_price: float
    raw_text: str


@dataclass
class TrimAlert:
    """A signal to sell/trim an open position."""
    ticker: str
    sell_all: bool        # True = close entire position, False = partial
    sell_fraction: float  # 0.0–1.0; 1.0 means sell everything
    raw_text: str


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def is_llm_available() -> bool:
    """Return True if an LLM API key is configured."""
    return bool(os.getenv("OPENAI_API_KEY", "").strip())


def _llm_client():
    """Return an openai.OpenAI client configured from env vars."""
    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError:
        return None

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None

    base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").strip()
    return OpenAI(api_key=api_key, base_url=base_url)


_LLM_ENTRY_PROMPT = """You are a parser for options trading alert messages posted in Discord.
Extract the following fields from the message and return ONLY valid JSON (no markdown, no explanation):

{{
  "ticker": "<1-5 letter stock symbol, uppercase>",
  "strike": <strike price as a number, e.g. 150.0>,
  "option_type": "<call or put>",
  "expiration": "<YYYY-MM-DD; use next Friday if weekly/unspecified>",
  "entry_price": <price per contract as a number, e.g. 2.50>
}}

If you cannot determine a field with confidence, set it to null.
Today's date is {today}.

Message: {message}"""

_LLM_TRIM_PROMPT = """You are a parser for options trading exit/trim alert messages posted in Discord.
Extract the following fields and return ONLY valid JSON (no markdown, no explanation):

{{
  "ticker": "<1-5 letter stock symbol, uppercase>",
  "sell_fraction": <fraction of position to sell, 0.0 to 1.0>,
  "reasoning": "<one sentence>"
}}

Rules for sell_fraction:
- "close", "STC", "sell all", "exit all", "full exit" → 1.0
- "trim", "take some off", "scale out", "manage your position" → 0.5
- "sell 75%", "take 75% off", very exuberant language → 0.75
- "sell half", "half off", "partial" → 0.5
- explicit percentage mentioned → use that value (e.g. "60%" → 0.6)
- ambiguous → 0.5

If you cannot identify a ticker, set it to null.

Message: {message}"""


def _llm_parse_entry(text: str) -> ParsedAlert | None:
    """Ask the LLM to parse an entry alert. Returns None on failure."""
    client = _llm_client()
    if client is None:
        return None

    model = os.getenv("PARSER_MODEL", "gpt-4o-mini")
    today = datetime.now().strftime("%Y-%m-%d")
    prompt = _LLM_ENTRY_PROMPT.format(today=today, message=text)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=200,
        )
        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
    except Exception as e:
        logger.debug("LLM entry parse failed: %s", e)
        return None

    try:
        ticker = data.get("ticker")
        strike = data.get("strike")
        option_type = data.get("option_type", "").lower()
        expiration = data.get("expiration")
        entry_price = data.get("entry_price")

        if not all([ticker, strike is not None, option_type in ("call", "put"),
                    expiration, entry_price is not None]):
            return None

        return ParsedAlert(
            ticker=str(ticker).upper(),
            strike=float(strike),
            option_type=option_type,
            expiration=str(expiration),
            entry_price=float(entry_price),
            raw_text=text,
        )
    except Exception as e:
        logger.debug("LLM entry result invalid: %s", e)
        return None


def _llm_parse_trim(text: str) -> TrimAlert | None:
    """Ask the LLM to parse a trim/exit alert. Returns None on failure."""
    client = _llm_client()
    if client is None:
        return None

    model = os.getenv("PARSER_MODEL", "gpt-4o-mini")
    prompt = _LLM_TRIM_PROMPT.format(message=text)

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=150,
        )
        raw = resp.choices[0].message.content.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
        data = json.loads(raw)
    except Exception as e:
        logger.debug("LLM trim parse failed: %s", e)
        return None

    try:
        ticker = data.get("ticker")
        sell_fraction = float(data.get("sell_fraction", 0.5))
        sell_fraction = max(0.0, min(1.0, sell_fraction))

        if not ticker:
            return None

        return TrimAlert(
            ticker=str(ticker).upper(),
            sell_all=(sell_fraction >= 1.0),
            sell_fraction=sell_fraction,
            raw_text=text,
        )
    except Exception as e:
        logger.debug("LLM trim result invalid: %s", e)
        return None


# ---------------------------------------------------------------------------
# Complexity heuristic — decides when to prefer LLM over regex
# ---------------------------------------------------------------------------

_COMPLEX_PATTERNS = [
    r"\bmanage\b",
    r"\bnote\b",
    r"\bupdate\b",
    r"\bswing\b",
    r"\bday trade\b",
    r"\bsize accordingly\b",
    r"\badding to\b",
    r"\bposition\b",
    r"[.!?].*[.!?]",   # multiple sentences
]

def _looks_complex(text: str) -> bool:
    """Return True if the message has language that trips up regex."""
    lower = text.lower()
    return any(re.search(p, lower) for p in _COMPLEX_PATTERNS)


# ---------------------------------------------------------------------------
# Regex helpers (unchanged from original)
# ---------------------------------------------------------------------------

TICKER_BLACKLIST = {
    "FOR", "ALL", "AT", "THE", "BIG", "NEW", "ONE", "NOW", "IT", "UP",
    # Action words that appear before tickers in trim alerts
    "TRIM", "TAKE", "CLOSE", "SELL", "STC", "BTO", "BUY", "BUYING",
    "SCALE", "EXIT", "LOCK", "MANAGE", "RING", "CASH",
}


def _extract_ticker(text: str) -> str | None:
    # $TICKER is most explicit
    match = re.search(r"\$([A-Z]{1,5})\b", text)
    if match and match.group(1) not in TICKER_BLACKLIST and match.group(1) != "ALERT":
        return match.group(1)

    # "BTO/BUY/TRIM/STC/CLOSE TICKER" pattern
    match = re.search(
        r"\b(?:BTO|BUY|BUYING|TRIM|STC|CLOSE|SELL(?:ING)?|SCALE\s+OUT\s+OF|EXIT|MANAGE(?:\s+YOUR)?)\s+([A-Z]{1,5})\b",
        text,
    )
    if match and match.group(1) not in TICKER_BLACKLIST:
        return match.group(1)

    # "TAKE PROFIT ON TICKER" / "LOCK IN ... ON TICKER"
    match = re.search(r"\bON\s+([A-Z]{1,5})\b", text)
    if match and match.group(1) not in TICKER_BLACKLIST:
        return match.group(1)

    # First standalone 1-5 letter word at start of message
    match = re.search(r"(?:^|\n)\s*\$?([A-Z]{1,5})\b", text)
    if match and match.group(1) not in TICKER_BLACKLIST:
        candidate = match.group(1)
        if candidate not in {"BTO", "BUY", "BUYING", "STO", "SELL", "LOTTO", "ALERT"}:
            return candidate

    return None


def _extract_option_type(text: str) -> str | None:
    call_patterns = [r"\bCALLS?\b", r"\d+C\b", r"\bC\s+\d"]
    put_patterns = [r"\bPUTS?\b", r"\d+P\b", r"\bP\s+\d"]

    is_call = any(re.search(p, text) for p in call_patterns)
    is_put = any(re.search(p, text) for p in put_patterns)

    if is_call and not is_put:
        return "call"
    if is_put and not is_call:
        return "put"
    if is_call and is_put:
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
    match = re.search(r"\$(\d+(?:\.\d+)?)\s*(?:CALLS?|PUTS?|C\b|P\b)", text)
    if match:
        return float(match.group(1))

    match = re.search(r"\b(\d+(?:\.\d+)?)\s*(?:CALLS?|PUTS?)\b", text)
    if match:
        return float(match.group(1))

    match = re.search(r"\b(\d+(?:\.\d+)?)\s*[CP]\b", text)
    if match:
        return float(match.group(1))

    pattern = rf"\$?{re.escape(ticker)}\s+\$?(\d+(?:\.\d+)?)"
    match = re.search(pattern, text)
    if match:
        return float(match.group(1))

    return None


def _extract_entry_price(text: str) -> float | None:
    match = re.search(r"(?:FOR|@|AT)\s*\$?(\d+(?:\.\d+)?)", text, re.IGNORECASE)
    if match:
        return float(match.group(1))

    match = re.search(r"\$?(\d+\.\d+)\s*(?:DEBIT|PREMIUM|CREDIT)", text)
    if match:
        return float(match.group(1))

    return None


def _extract_expiration(text: str) -> str:
    upper = text.upper()
    today = datetime.now()

    if re.search(r"\bWEEKL(?:Y|IES)\b", upper):
        return _next_friday(today)

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

    return _next_friday(today)


def _next_friday(from_date: datetime) -> str:
    days_ahead = (4 - from_date.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    friday = from_date + timedelta(days=days_ahead)
    return friday.strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def parse_alert(text: str) -> ParsedAlert | None:
    """Parse a free-form trading alert into structured data.

    Strategy:
    1. Try regex.
    2. If regex fails OR message looks complex, try LLM.
    3. Prefer LLM result when both succeed on a complex message.
    """
    raw = text
    cleaned = text.upper()

    # --- Regex attempt ---
    regex_result = None
    ticker = _extract_ticker(cleaned)
    if ticker:
        option_type = _extract_option_type(cleaned)
        strike = _extract_strike(cleaned, ticker)
        entry_price = _extract_entry_price(cleaned)
        if option_type and strike is not None and entry_price is not None:
            regex_result = ParsedAlert(
                ticker=ticker,
                strike=strike,
                option_type=option_type,
                expiration=_extract_expiration(text),
                entry_price=entry_price,
                raw_text=raw,
            )

    # Return regex result immediately if message is simple and parsed cleanly
    if regex_result and not _looks_complex(text):
        return regex_result

    # --- LLM attempt (complex message or regex failed) ---
    if is_llm_available():
        llm_result = _llm_parse_entry(text)
        if llm_result:
            logger.debug("LLM parsed entry alert for %s", llm_result.ticker)
            return llm_result

    # Fall back to regex result even if message was complex
    return regex_result


def parse_trim_alert(text: str) -> TrimAlert | None:
    """Parse a message as a trim/profit-taking alert.

    Returns None if the message doesn't look like a trim signal.
    sell_fraction indicates what portion of the position to sell (0.0–1.0).
    """
    upper = text.upper()

    # --- Regex attempt ---
    _TRIM_PATTERNS = [
        r"\bTRIM\b", r"\bTAKE\s+PROFIT\b", r"\bLOCK\s+IN\b",
        r"\bCLOSE\b.*\bPOSITION\b", r"\bSELL(?:ING)?\b", r"\bSTC\b",
        r"\bCLOSING\b", r"\bEXIT\b", r"\bCASH\s*(?:OUT|IN)\b",
        r"\bSCALE\s+OUT\b", r"\bRING\s+.*REGISTER\b", r"\bMANAGE\b",
    ]
    _PARTIAL_PATTERNS = [
        r"\bHALF\b", r"\bPARTIAL\b", r"\bSOME\b", r"\bTRIM\b",
        r"\bSCALE\s+OUT\b", r"\bMANAGE\b",
    ]

    is_trim = any(re.search(p, upper) for p in _TRIM_PATTERNS)

    if is_trim:
        ticker = _extract_ticker(upper)
        if ticker:
            # Check for explicit percentage
            pct_match = re.search(r"\b(\d{1,3})\s*%", text)
            if pct_match:
                sell_fraction = float(pct_match.group(1)) / 100.0
                sell_fraction = max(0.0, min(1.0, sell_fraction))
            else:
                is_partial = any(re.search(p, upper) for p in _PARTIAL_PATTERNS)
                sell_fraction = 0.5 if is_partial else 1.0

            regex_result = TrimAlert(
                ticker=ticker,
                sell_all=(sell_fraction >= 1.0),
                sell_fraction=sell_fraction,
                raw_text=text,
            )

            # Use LLM for complex/ambiguous messages
            if _looks_complex(text) and is_llm_available():
                llm_result = _llm_parse_trim(text)
                if llm_result:
                    logger.debug(
                        "LLM parsed trim alert: %s sell_fraction=%.2f",
                        llm_result.ticker, llm_result.sell_fraction
                    )
                    return llm_result

            return regex_result

    # Regex didn't match — try LLM anyway (catches creative exit language)
    if is_llm_available():
        llm_result = _llm_parse_trim(text)
        if llm_result:
            logger.debug(
                "LLM-only trim parse: %s sell_fraction=%.2f",
                llm_result.ticker, llm_result.sell_fraction
            )
            return llm_result

    return None


# ---------------------------------------------------------------------------
# Quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.DEBUG)

    entry_samples = [
        "$RKLB - Lotto size - weekly $71 calls for $1.20  @everyone $alert",
        "BTO AAPL 150C 3/15 @ 2.50",
        "TSLA 800 puts 1/19 for $3.40",
        "Buying SPY $450 calls expiring Friday at $1.05",
        "Adding to our NVDA position here, $680 calls for next week, entry around $4.50 - size accordingly",
        "MSFT calls, strike 420, exp 3/21, paying 3.20 -- note this is a swing not a day trade",
    ]

    trim_samples = [
        "Trim RKLB calls",
        "Take profit on AAPL",
        "Manage your TSLA position here",
        "Really loving these SPY gains, lets lock in 75% here, keep the rest running",
        "STC NVDA, close it all out",
        "Scale out of MSFT, take half off",
    ]

    llm_note = " (LLM enabled)" if is_llm_available() else " (regex only — no API key)"
    print(f"\n=== Entry Alerts{llm_note} ===")
    for msg in entry_samples:
        result = parse_alert(msg)
        if result:
            print(f"  ✅ {result.ticker} ${result.strike} {result.option_type} "
                  f"exp {result.expiration} @ ${result.entry_price}")
        else:
            print(f"  ❌ FAILED: {msg[:60]}")

    print(f"\n=== Trim Alerts{llm_note} ===")
    for msg in trim_samples:
        result = parse_trim_alert(msg)
        if result:
            action = "SELL ALL" if result.sell_all else f"SELL {int(result.sell_fraction*100)}%"
        print(f"  {'✅' if result else '❌'} {action if result else 'FAILED'}: {msg[:60]}")
        if result:
            print(f"       ticker={result.ticker} sell_fraction={result.sell_fraction}")
