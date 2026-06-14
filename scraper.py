"""
Scrapes Capitol Trades (capitoltrades.com) for congressional trade disclosures.
Uses their public JSON API endpoints discovered via network inspection.
"""

import time
import logging
from datetime import datetime, timedelta
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://capitoltrades.com"
API_BASE = "https://api.capitoltrades.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Referer": BASE_URL,
}

RANGE_MIDPOINTS = {
    "$1K - $15K":       8_000,
    "$15K - $50K":     32_500,
    "$50K - $100K":    75_000,
    "$100K - $250K":  175_000,
    "$250K - $500K":  375_000,
    "$500K - $1M":    750_000,
    "$1M - $5M":    3_000_000,
    "$5M - $25M":  15_000_000,
    "$25M+":        25_000_000,
}


def _get(path: str, params: Optional[dict] = None, retries: int = 3) -> dict:
    url = f"{API_BASE}{path}"
    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=HEADERS, params=params, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as exc:
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise RuntimeError(f"Failed to fetch {url}: {exc}") from exc
    return {}


def fetch_all_politicians() -> list[dict]:
    """Return list of all politicians with their metadata."""
    politicians = []
    page = 1
    while True:
        data = _get("/politicians", params={"page": page, "pageSize": 100})
        items = data.get("data", [])
        if not items:
            break
        politicians.extend(items)
        meta = data.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1
        time.sleep(0.3)
    logger.info("Fetched %d politicians", len(politicians))
    return politicians


def fetch_trades_for_politician(politician_id: str, days: int = 365) -> list[dict]:
    """Return all trades filed by a politician in the last `days` days."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    trades = []
    page = 1
    while True:
        data = _get(
            "/trades",
            params={
                "politician": politician_id,
                "dateFrom": cutoff,
                "page": page,
                "pageSize": 100,
            },
        )
        items = data.get("data", [])
        if not items:
            break
        trades.extend(items)
        meta = data.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1
        time.sleep(0.2)
    return trades


def fetch_recent_trades(days: int = 2) -> list[dict]:
    """Return trades disclosed in the last `days` days (for daily diff check)."""
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
    trades = []
    page = 1
    while True:
        data = _get(
            "/trades",
            params={"dateFrom": cutoff, "page": page, "pageSize": 100},
        )
        items = data.get("data", [])
        if not items:
            break
        trades.extend(items)
        meta = data.get("meta", {})
        if page >= meta.get("totalPages", 1):
            break
        page += 1
        time.sleep(0.2)
    logger.info("Fetched %d recent trades (last %d days)", len(trades), days)
    return trades


def amount_midpoint(amount_str: str) -> float:
    """Convert a disclosure amount range string to its midpoint dollar value."""
    for key, val in RANGE_MIDPOINTS.items():
        if key.lower() in amount_str.lower():
            return val
    # Fallback: try to parse a bare number
    cleaned = amount_str.replace("$", "").replace(",", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return 8_000  # default to lowest tier
