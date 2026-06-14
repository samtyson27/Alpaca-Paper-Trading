"""
Sends HTML email summaries via Gmail SMTP.
Configure EMAIL_FROM, EMAIL_TO, SMTP_PASSWORD in .env.
Use a Gmail App Password: https://myaccount.google.com/apppasswords
"""

import os
import smtplib
import logging
from datetime import date
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def send_summary(
    top_politician: dict,
    rank_table: list[dict],
    mirror_result: dict,
    new_disclosures: list[dict],
    run_date: date | None = None,
) -> bool:
    """
    Send the daily summary email.

    Parameters
    ----------
    top_politician : dict with keys name, party, chamber, return_12m_pct
    rank_table     : list of top-N dicts from ranker output
    mirror_result  : dict from trader.mirror_positions
    new_disclosures: list of trade dicts filed since last run
    run_date       : date of this run (defaults to today)
    """
    run_date = run_date or date.today()

    from_addr = os.environ.get("EMAIL_FROM", "")
    to_addr = os.environ.get("EMAIL_TO", "")
    password = os.environ.get("SMTP_PASSWORD", "")
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", 587))

    if not all([from_addr, to_addr, password]):
        logger.warning("Email credentials not fully configured — skipping send")
        return False

    subject = f"[Congress Trader] Daily Summary – {run_date.isoformat()}"

    # --- Build HTML ---
    top_name = top_politician.get("name", "N/A")
    top_ret = top_politician.get("return_12m_pct", 0)
    top_party = top_politician.get("party", "")
    top_chamber = top_politician.get("chamber", "")

    rank_rows = "".join(
        f"<tr>"
        f"<td>{i+1}</td>"
        f"<td>{r['name']}</td>"
        f"<td>{r['party']}</td>"
        f"<td>{r['chamber']}</td>"
        f"<td style='color:{'green' if r['return_12m_pct']>=0 else 'red'}'>"
        f"{r['return_12m_pct']:+.2f}%</td>"
        f"<td>{r['trade_count']}</td>"
        f"</tr>"
        for i, r in enumerate(rank_table)
    )

    orders_placed = mirror_result.get("orders_placed", [])
    orders_rows = "".join(
        f"<tr><td>{o['ticker']}</td><td>${o['dollars']:,.2f}</td></tr>"
        for o in orders_placed
    )

    disclosure_rows = "".join(
        f"<tr>"
        f"<td>{d.get('politician','')}</td>"
        f"<td>{d.get('ticker','')}</td>"
        f"<td>{d.get('type','')}</td>"
        f"<td>{d.get('amount','')}</td>"
        f"<td>{d.get('transactionDate','')}</td>"
        f"</tr>"
        for d in new_disclosures[:50]  # cap at 50 rows
    )

    html = f"""
<html><body style="font-family:Arial,sans-serif;max-width:800px;margin:auto">
<h2 style="color:#1a1a2e">📊 Congress Trader – {run_date.isoformat()}</h2>

<h3>🏆 Top Performer (12-Month Return)</h3>
<p style="font-size:1.2em">
  <strong>{top_name}</strong> ({top_party} · {top_chamber})<br>
  12-month return: <span style="color:{'green' if top_ret>=0 else 'red'};font-size:1.3em">
    {top_ret:+.2f}%</span>
</p>

<h3>📋 Full Rankings</h3>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
  <tr style="background:#1a1a2e;color:white">
    <th>#</th><th>Name</th><th>Party</th><th>Chamber</th>
    <th>12M Return</th><th>Trades</th>
  </tr>
  {rank_rows}
</table>

<h3>🔄 Today's Mirrored Positions</h3>
<p>Account equity: <strong>${mirror_result.get('account_equity',0):,.2f}</strong> &nbsp;|&nbsp;
   Budget deployed: <strong>${mirror_result.get('budget_deployed',0):,.2f}</strong> &nbsp;|&nbsp;
   Positions closed: <strong>{len(mirror_result.get('positions_closed',[]))}</strong></p>
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse">
  <tr style="background:#1a1a2e;color:white"><th>Ticker</th><th>Allocation</th></tr>
  {orders_rows if orders_rows else '<tr><td colspan="2">No new orders placed</td></tr>'}
</table>

<h3>🆕 New Disclosures Since Yesterday</h3>
{'<p>No new disclosures.</p>' if not new_disclosures else f'''
<table border="1" cellpadding="6" cellspacing="0" style="border-collapse:collapse;width:100%">
  <tr style="background:#1a1a2e;color:white">
    <th>Member</th><th>Ticker</th><th>Type</th><th>Amount</th><th>Tx Date</th>
  </tr>
  {disclosure_rows}
</table>'''}

<p style="color:#888;font-size:0.85em;margin-top:40px">
  This is an automated paper-trading summary. Not financial advice.
</p>
</body></html>
"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.attach(MIMEText(html, "html"))

    try:
        with smtplib.SMTP(host, port) as server:
            server.ehlo()
            server.starttls()
            server.login(from_addr, password)
            server.sendmail(from_addr, to_addr, msg.as_string())
        logger.info("Email sent to %s", to_addr)
        return True
    except Exception as exc:
        logger.error("Failed to send email: %s", exc)
        return False
