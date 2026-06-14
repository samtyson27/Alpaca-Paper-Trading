"""
Mirrors a congressional member's open positions into the Alpaca paper account.

Strategy:
  - Liquidate everything not in the target portfolio.
  - Allocate remaining buying power proportionally across target tickers.
  - Uses market orders at open.
"""

import logging
import os
from typing import Optional

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, GetAssetsRequest
from alpaca.trading.enums import OrderSide, TimeInForce, AssetClass, AssetStatus
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest

logger = logging.getLogger(__name__)


def _client() -> TradingClient:
    return TradingClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
        paper=True,
    )


def _data_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=os.environ["ALPACA_API_KEY"],
        secret_key=os.environ["ALPACA_SECRET_KEY"],
    )


def get_account_info() -> dict:
    client = _client()
    acct = client.get_account()
    return {
        "equity": float(acct.equity),
        "buying_power": float(acct.buying_power),
        "cash": float(acct.cash),
    }


def get_current_positions() -> dict[str, float]:
    """Return {ticker: market_value} for current open positions."""
    client = _client()
    positions = client.get_all_positions()
    return {p.symbol: float(p.market_value) for p in positions}


def _is_tradeable(ticker: str) -> bool:
    """Check whether the ticker is a tradeable US equity on Alpaca."""
    client = _client()
    try:
        asset = client.get_asset(ticker)
        return asset.tradable and asset.status == AssetStatus.ACTIVE
    except Exception:
        return False


def _latest_price(ticker: str) -> Optional[float]:
    dc = _data_client()
    try:
        resp = dc.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=[ticker]))
        return float(resp[ticker].price)
    except Exception:
        return None


def liquidate_position(ticker: str) -> bool:
    """Close the entire position in ticker. Returns True on success."""
    client = _client()
    try:
        client.close_position(ticker)
        logger.info("Liquidated %s", ticker)
        return True
    except Exception as exc:
        logger.warning("Could not liquidate %s: %s", ticker, exc)
        return False


def liquidate_all_not_in(target_tickers: set[str]) -> list[str]:
    """Liquidate every position not in target_tickers. Returns list of closed tickers."""
    current = get_current_positions()
    closed = []
    for ticker in current:
        if ticker not in target_tickers:
            if liquidate_position(ticker):
                closed.append(ticker)
    return closed


def place_buy(ticker: str, dollars: float) -> bool:
    """Buy `dollars` worth of `ticker` using a notional market order."""
    if dollars < 1:
        return False
    client = _client()
    try:
        req = MarketOrderRequest(
            symbol=ticker,
            notional=round(dollars, 2),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )
        client.submit_order(req)
        logger.info("Placed buy $%.2f of %s", dollars, ticker)
        return True
    except Exception as exc:
        logger.warning("Buy order failed for %s ($%.2f): %s", ticker, dollars, exc)
        return False


def mirror_positions(
    target_positions: list[dict],
    allocation_pct: float = 0.95,
) -> dict:
    """
    Mirror `target_positions` (list of {ticker, amount}) into the Alpaca account.

    Steps:
      1. Filter to tradeable tickers.
      2. Close positions not in the target.
      3. Allocate buying_power * allocation_pct proportionally across targets.
      4. Place notional buys.

    Returns a summary dict.
    """
    # Filter to tradeable tickers only
    tradeable = [p for p in target_positions if _is_tradeable(p["ticker"])]
    if not tradeable:
        logger.warning("No tradeable tickers in target portfolio")
        return {"error": "No tradeable tickers"}

    target_set = {p["ticker"] for p in tradeable}
    closed = liquidate_all_not_in(target_set)

    acct = get_account_info()
    budget = acct["buying_power"] * allocation_pct

    total_weight = sum(p["amount"] for p in tradeable)
    orders_placed = []
    orders_failed = []

    for pos in tradeable:
        alloc = budget * (pos["amount"] / total_weight)
        if place_buy(pos["ticker"], alloc):
            orders_placed.append({"ticker": pos["ticker"], "dollars": round(alloc, 2)})
        else:
            orders_failed.append(pos["ticker"])

    return {
        "account_equity": acct["equity"],
        "budget_deployed": budget,
        "positions_closed": closed,
        "orders_placed": orders_placed,
        "orders_failed": orders_failed,
    }
