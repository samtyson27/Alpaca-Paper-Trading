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

# Persistent state: remember which politician we're mirroring and last run date
_state = {
    "top_politician_id": None,
    "top_politician": None,
    "last_rank_date": None,
}


def run_pipeline():
    """Full daily pipeline: rank → mirror → email."""
    from ranker import rank_politicians, get_current_positions
    from trader import mirror_positions, get_account_info
    from scraper import fetch_recent_trades
    from emailer import send_summary

    today = date.today()
    logger.info("=== Pipeline starting for %s ===", today)

    # Re-rank every Monday (or if we've never ranked)
    monday = today.weekday() == 0
    if monday or _state["last_rank_date"] is None:
        logger.info("Ranking politicians (this may take several minutes)...")
        rank_df = rank_politicians(top_n=10)
        if rank_df.empty:
            logger.error("Ranking returned no results — aborting pipeline")
            return
        top = rank_df.iloc[0].to_dict()
        _state["top_politician_id"] = top["id"]
        _state["top_politician"] = top
        _state["last_rank_date"] = today
        _state["rank_table"] = rank_df.to_dict("records")
        logger.info("Top performer: %s (%.2f%%)", top["name"], top["return_12m_pct"])
    else:
        top = _state["top_politician"]

    # Get their current open positions
    logger.info("Fetching positions for %s ...", top["name"])
    target_positions = get_current_positions(_state["top_politician_id"])
    logger.info("Found %d open positions to mirror", len(target_positions))

    # Mirror into Alpaca
    logger.info("Mirroring positions into Alpaca paper account...")
    mirror_result = mirror_positions(target_positions)
    logger.info("Mirror result: %s orders placed, %s closed",
                len(mirror_result.get("orders_placed", [])),
                len(mirror_result.get("positions_closed", [])))

    # Fetch new disclosures since yesterday
    new_disclosures = fetch_recent_trades(days=2)

    # Send email
    logger.info("Sending summary email...")
    sent = send_summary(
        top_politician=top,
        rank_table=_state.get("rank_table", [top]),
        mirror_result=mirror_result,
        new_disclosures=new_disclosures,
        run_date=today,
    )
    if sent:
        logger.info("Email sent successfully")
    else:
        logger.warning("Email not sent — check EMAIL_FROM/EMAIL_TO/SMTP_PASSWORD in .env")

    logger.info("=== Pipeline complete ===")


def is_market_day() -> bool:
    """Return True if today is Mon–Fri (ignores US holidays for simplicity)."""
    return datetime.now(ET).weekday() < 5


def scheduled_job():
    if is_market_day():
        run_pipeline()
    else:
        logger.info("Weekend — skipping pipeline")


def main():
    parser = argparse.ArgumentParser(description="Congress Trader")
    parser.add_argument(
        "--now", action="store_true",
        help="Run the pipeline immediately and exit"
    )
    args = parser.parse_args()

    if args.now:
        run_pipeline()
        return

    # Schedule daily at 9:30 AM ET
    schedule.every().day.at("09:30").do(scheduled_job)
    logger.info("Scheduler started — will run weekdays at 09:30 ET. Press Ctrl+C to stop.")

    # Run immediately on first start so you don't have to wait until morning
    logger.info("Running initial pipeline now...")
    run_pipeline()

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
