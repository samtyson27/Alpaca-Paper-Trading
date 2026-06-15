"""
Entry point. Runs the full pipeline once, then schedules it every weekday at 9:30 AM ET.

Usage:
  python main.py             # start scheduler (runs forever)
  python main.py --now       # run pipeline immediately, then exit
"""

import argparse
import logging
import os
import sys
import time
from datetime import datetime, date

import pytz
import schedule
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("congress_trader.log"),
    ],
)
logger = logging.getLogger(__name__)

ET = pytz.timezone("America/New_York")

_state = {
    "top_politician_id": None,
    "top_politician": None,
    "last_rank_date": None,
}


def run_pipeline():
    """Full daily pipeline: rank → mirror positions in Alpaca."""
    from ranker import rank_politicians, get_current_positions
    from trader import mirror_positions

    today = date.today()
    logger.info("=== Pipeline starting for %s ===", today)

    # Re-rank every Monday (or on first run)
    if today.weekday() == 0 or _state["last_rank_date"] is None:
        logger.info("Ranking politicians (this may take several minutes)...")
        rank_df = rank_politicians(top_n=10)
        if rank_df.empty:
            logger.error("Ranking returned no results — aborting")
            return
        top = rank_df.iloc[0].to_dict()
        _state["top_politician_id"] = top["id"]
        _state["top_politician"] = top
        _state["last_rank_date"] = today
        logger.info(
            "Top performer: %s (%s) — 12M return: %.2f%%",
            top["name"], top["party"], top["return_12m_pct"],
        )
    else:
        top = _state["top_politician"]

    logger.info("Fetching open positions for %s ...", top["name"])
    target_positions = get_current_positions(_state["top_politician_id"])
    logger.info("Found %d positions to mirror", len(target_positions))
    for p in target_positions:
        logger.info("  %s  ~$%,.0f", p["ticker"], p["amount"])

    logger.info("Mirroring into Alpaca paper account...")
    result = mirror_positions(target_positions)

    logger.info("--- Result ---")
    logger.info("Account equity : $%,.2f", result.get("account_equity", 0))
    logger.info("Budget deployed: $%,.2f", result.get("budget_deployed", 0))
    logger.info("Positions closed: %s", result.get("positions_closed", []))
    for o in result.get("orders_placed", []):
        logger.info("  BUY %-6s  $%,.2f", o["ticker"], o["dollars"])
    if result.get("orders_failed"):
        logger.warning("Failed orders: %s", result["orders_failed"])

    logger.info("=== Pipeline complete ===")


def is_market_day() -> bool:
    return datetime.now(ET).weekday() < 5


def scheduled_job():
    if is_market_day():
        run_pipeline()
    else:
        logger.info("Weekend — skipping")


def main():
    parser = argparse.ArgumentParser(description="Congress Trader")
    parser.add_argument("--now", action="store_true",
                        help="Run immediately and exit")
    args = parser.parse_args()

    if args.now:
        run_pipeline()
        return

    schedule.every().day.at("09:30").do(scheduled_job)
    logger.info("Scheduler started — weekdays at 09:30 ET. Ctrl+C to stop.")
    logger.info("Running initial pipeline now...")
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
