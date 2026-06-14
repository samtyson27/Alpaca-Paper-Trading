"""
Ranks Congress members by their 12-month portfolio return.

Method:
  - Collect all buy/sell trades in the trailing 365 days.
  - For each buy: fetch the stock price on the trade date and today.
  - Compute weighted return = sum(size * pct_gain) / sum(size).
  - Sells that closed a position are counted at the exit price.
  - Only equities with a valid ticker are included.
"""

import logging
from datetime import datetime, timedelta, date
from typing import Optional

import pandas as pd
import yfinance as yf

from scraper import fetch_all_politicians, fetch_trades_for_politician, amount_midpoint

logger = logging.getLogger(__name__)


def _price_on(ticker: str, target_date: date) -> Optional[float]:
    """Return closing price for ticker on or just before target_date."""
    start = target_date - timedelta(days=7)
    end = target_date + timedelta(days=1)
    try:
        hist = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                           progress=False, auto_adjust=True)
        if hist.empty:
            return None
        return float(hist["Close"].iloc[-1])
    except Exception:
        return None


def _current_price(ticker: str) -> Optional[float]:
    """Return most recent closing price for ticker."""
    return _price_on(ticker, datetime.utcnow().date())


def compute_politician_return(trades: list[dict]) -> Optional[float]:
    """
    Given a list of trade dicts, compute the dollar-weighted 12-month return.
    Returns None if there are no computable trades.
    """
    buys: dict[str, list[dict]] = {}   # ticker -> list of open buy lots
    weighted_returns = []

    today = datetime.utcnow().date()
    current_prices: dict[str, Optional[float]] = {}

    for trade in sorted(trades, key=lambda t: t.get("transactionDate", "")):
        ticker = (trade.get("ticker") or "").upper().strip()
        if not ticker or ticker in ("N/A", ""):
            continue

        tx_type = (trade.get("type") or "").lower()
        amount = amount_midpoint(trade.get("amount") or "")
        tx_date_str = trade.get("transactionDate") or trade.get("reportedDate") or ""
        try:
            tx_date = date.fromisoformat(tx_date_str[:10])
        except ValueError:
            continue

        if tx_type in ("buy", "purchase"):
            buy_price = _price_on(ticker, tx_date)
            if buy_price is None:
                continue
            buys.setdefault(ticker, []).append({
                "amount": amount,
                "buy_price": buy_price,
                "buy_date": tx_date,
            })

        elif tx_type in ("sell", "sale"):
            sell_price = _price_on(ticker, tx_date)
            if sell_price is None:
                continue
            lots = buys.get(ticker, [])
            remaining = amount
            while lots and remaining > 0:
                lot = lots[0]
                used = min(lot["amount"], remaining)
                pct = (sell_price - lot["buy_price"]) / lot["buy_price"]
                weighted_returns.append((used, pct))
                lot["amount"] -= used
                remaining -= used
                if lot["amount"] <= 0:
                    lots.pop(0)

    # Mark-to-market any still-open buys
    for ticker, lots in buys.items():
        if ticker not in current_prices:
            current_prices[ticker] = _current_price(ticker)
        cur = current_prices[ticker]
        if cur is None:
            continue
        for lot in lots:
            pct = (cur - lot["buy_price"]) / lot["buy_price"]
            weighted_returns.append((lot["amount"], pct))

    if not weighted_returns:
        return None

    total_weight = sum(w for w, _ in weighted_returns)
    if total_weight == 0:
        return None

    return sum(w * r for w, r in weighted_returns) / total_weight


def rank_politicians(top_n: int = 10) -> pd.DataFrame:
    """
    Fetch all politicians, compute 12-month returns, return sorted DataFrame.
    """
    politicians = fetch_all_politicians()
    results = []

    for pol in politicians:
        pid = pol.get("id") or pol.get("slug") or ""
        name = pol.get("name") or pol.get("fullName") or pid
        party = pol.get("party") or ""
        chamber = pol.get("chamber") or ""

        logger.info("Calculating return for %s ...", name)
        try:
            trades = fetch_trades_for_politician(pid, days=365)
        except Exception as exc:
            logger.warning("Could not fetch trades for %s: %s", name, exc)
            continue

        if not trades:
            continue

        ret = compute_politician_return(trades)
        if ret is None:
            continue

        results.append({
            "id": pid,
            "name": name,
            "party": party,
            "chamber": chamber,
            "return_12m": ret,
            "trade_count": len(trades),
        })

    df = pd.DataFrame(results)
    if df.empty:
        return df

    df = df.sort_values("return_12m", ascending=False).reset_index(drop=True)
    df["return_12m_pct"] = (df["return_12m"] * 100).round(2)
    return df.head(top_n)


def get_current_positions(politician_id: str) -> list[dict]:
    """
    Derive the politician's current open stock positions from their trade history.
    Returns list of {ticker, net_amount} dicts (estimated dollar exposure).
    """
    trades = fetch_trades_for_politician(politician_id, days=365)
    holdings: dict[str, float] = {}

    for trade in sorted(trades, key=lambda t: t.get("transactionDate", "")):
        ticker = (trade.get("ticker") or "").upper().strip()
        if not ticker or ticker in ("N/A", ""):
            continue
        tx_type = (trade.get("type") or "").lower()
        amount = amount_midpoint(trade.get("amount") or "")

        if tx_type in ("buy", "purchase"):
            holdings[ticker] = holdings.get(ticker, 0) + amount
        elif tx_type in ("sell", "sale"):
            holdings[ticker] = holdings.get(ticker, 0) - amount

    return [
        {"ticker": t, "amount": a}
        for t, a in holdings.items()
        if a > 0
    ]
