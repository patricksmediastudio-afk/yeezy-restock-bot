#!/usr/bin/env python3
"""
restock_bot.py -- 24/7 watcher for the JD Sports Yeezy YS-01 Slide restock.

Watches sneaker news sources that permit automated access and emails you the
moment a JD Sports Yeezy Slide restock is announced.

Why news sources and not jdsports.com directly:
    JD Finish Line fronts jdsports.com with bot protection that returns
    HTTP 403 to every non-browser client. Getting past that means
    impersonating a real browser, which breaks their terms and gets the
    IP banned. The sources below serve scripts a clean 200 and, in practice,
    publish restock news before the product page flips to in-stock.

Usage:
    python restock_bot.py --test     # send a test email, verify plumbing
    python restock_bot.py --once     # run one check and exit (for cron)
    python restock_bot.py            # run forever, checking every INTERVAL
"""

import os
import re
import sys
import json
import time
import html
import argparse
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

# --------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------
SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", SENDER_EMAIL)
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "300"))
STATE_DIR = Path(os.getenv("STATE_DIR", Path(__file__).parent))
STATE_FILE = STATE_DIR / "seen.json"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
TIMEOUT = 25

# The JD product page, included in the alert email for convenience.
JD_PRODUCT_URL = (
    "https://www.jdsports.com/pdp/yeezy-ys-01-slide-sandals/prod30231081/YS015/400"
)

# RSS feeds. Verified to return 200 to scripts.
RSS_SOURCES = [
    ("SneakerNews", "https://sneakernews.com/feed/"),
    ("Sneaker Bar Detroit", "https://sneakerbardetroit.com/feed/"),
]

# Soleretriever. Their homepage sits behind Cloudflare and 403s scripts, but
# their sitemap is published in robots.txt and serves a clean 200. It is also
# a better source: every entry carries a <lastmod> timestamp, so we can tell a
# freshly updated restock post from a two year old article.
SOLERETRIEVER_SITEMAP = "https://www.soleretriever.com/sitemap.xml"

# Only consider Soleretriever articles touched in the last N days.
SOLERETRIEVER_MAX_AGE_DAYS = 21

# --------------------------------------------------------------------
# MATCHING
# --------------------------------------------------------------------
# An item must mention Yeezy AND slides AND stock language to alert.
RE_YEEZY = re.compile(r"\byeezy\b|\bys-?01\b", re.I)
RE_SLIDE = re.compile(r"\bslides?\b|\bys-?01\b", re.I)
RE_STOCK = re.compile(r"restock|back in stock|drop|release|available|launch", re.I)
RE_JD = re.compile(r"\bjd\b|jd sports|finish ?line", re.I)


def score_item(title, summary=""):
    """Return (should_alert, reason). Requires Yeezy + slide + stock language."""
    text = "{} {}".format(title, summary)
    if not RE_YEEZY.search(text):
        return False, ""
    if not RE_SLIDE.search(text):
        return False, ""
    if not RE_STOCK.search(text):
        return False, ""

    reason = "Yeezy slide stock news"
    if RE_JD.search(text):
        reason = "JD SPORTS Yeezy slide stock news"
    if re.search(r"restock|back in stock", text, re.I):
        reason = reason.replace("stock news", "RESTOCK")
    return True, reason


# --------------------------------------------------------------------
# STATE
# --------------------------------------------------------------------
def load_seen():
    if STATE_FILE.exists():
        try:
            return set(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return set()
    return set()


def save_seen(seen):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the file from growing without bound.
    trimmed = list(seen)[-500:]
    STATE_FILE.write_text(json.dumps(trimmed, indent=2), encoding="utf-8")


# --------------------------------------------------------------------
# FETCHING
# --------------------------------------------------------------------
def fetch(url):
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT)
        if r.status_code != 200:
            log("  {} returned {}, skipping this round".format(url, r.status_code))
            return None
        return r.text
    except requests.RequestException as e:
        log("  {} failed: {}".format(url, e))
        return None


def parse_rss(xml_text, source):
    """Parse an RSS 2.0 feed into item dicts."""
    items = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log("  could not parse {} feed: {}".format(source, e))
        return items

    for item in root.iter("item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        summary = (item.findtext("description") or "").strip()
        summary = re.sub(r"<[^>]+>", " ", summary)
        if title and link:
            items.append({
                "title": html.unescape(title),
                "link": link,
                "summary": html.unescape(summary)[:400],
                "source": source,
            })
    return items


def _parse_lastmod(value):
    """Parse a sitemap lastmod timestamp into an aware datetime, or None."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def gather_soleretriever():
    """Read the newest Soleretriever news sitemap and return recent Yeezy items.

    The sitemap index lists sitemap-news/0 .. sitemap-news/N oldest to newest,
    so the highest numbered one holds the current articles.
    """
    index_xml = fetch(SOLERETRIEVER_SITEMAP)
    if not index_xml:
        return []

    news_maps = re.findall(r"<loc>([^<]*sitemap-news/(\d+))</loc>", index_xml)
    if not news_maps:
        log("  no news sitemaps found in Soleretriever index")
        return []

    newest_url = max(news_maps, key=lambda pair: int(pair[1]))[0]
    news_xml = fetch(newest_url)
    if not news_xml:
        return []

    entries = re.findall(
        r"<url>\s*<loc>([^<]+)</loc>\s*(?:<lastmod>([^<]*)</lastmod>)?",
        news_xml,
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=SOLERETRIEVER_MAX_AGE_DAYS)
    items = []
    for loc, lastmod in entries:
        if "/news/articles/" not in loc or not RE_YEEZY.search(loc):
            continue

        stamp = _parse_lastmod(lastmod)
        if stamp and stamp < cutoff:
            continue

        slug = loc.rstrip("/").rsplit("/", 1)[-1]
        items.append({
            "title": slug.replace("-", " ").title(),
            "link": loc,
            "summary": "",
            "source": "Soleretriever",
            # Include lastmod in the dedup key: restock articles get edited
            # when the drop actually goes live, and that edit is the signal.
            "key": "{}#{}".format(loc, lastmod or ""),
        })
    return items


def gather_items():
    items = []
    for source, url in RSS_SOURCES:
        xml_text = fetch(url)
        if xml_text:
            items.extend(parse_rss(xml_text, source))

    items.extend(gather_soleretriever())
    return items


# --------------------------------------------------------------------
# EMAIL
# --------------------------------------------------------------------
def send_email(subject, html_body):
    if not SENDGRID_API_KEY or not SENDER_EMAIL:
        log("ERROR: SENDGRID_API_KEY and SENDER_EMAIL must be set in .env")
        return False

    import sendgrid
    from sendgrid.helpers.mail import Mail

    message = Mail(
        from_email=SENDER_EMAIL,
        to_emails=ALERT_EMAIL,
        subject=subject,
        html_content=html_body,
    )
    try:
        sg = sendgrid.SendGridAPIClient(api_key=SENDGRID_API_KEY)
        resp = sg.send(message)
        log("  email sent to {} (status {})".format(ALERT_EMAIL, resp.status_code))
        return True
    except Exception as e:
        log("  email FAILED: {}".format(e))
        return False


def alert(item, reason):
    subject = "RESTOCK ALERT: {}".format(item["title"][:80])
    parts = [
        "<h2>{}</h2>".format(html.escape(item["title"])),
        "<p><b>Why you are getting this:</b> {}<br>".format(html.escape(reason)),
        "<b>Source:</b> {}</p>".format(html.escape(item["source"])),
        '<p><a href="{}">Read the article</a></p>'.format(html.escape(item["link"])),
        '<p><a href="{}">Go straight to the JD product page</a></p>'.format(JD_PRODUCT_URL),
    ]
    if item["summary"]:
        parts.append("<p>{}</p>".format(html.escape(item["summary"])))
    parts.append(
        '<hr><p style="color:#888;font-size:12px">Detected {} by restock_bot.</p>'.format(
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        )
    )
    return send_email(subject, "".join(parts))


# --------------------------------------------------------------------
# MAIN LOOP
# --------------------------------------------------------------------
def log(msg):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    print("[{}] {}".format(stamp, msg), flush=True)


def check_once():
    log("Checking sources...")
    seen = load_seen()
    items = gather_items()
    log("  {} items gathered, {} already seen".format(len(items), len(seen)))

    hits = 0
    for item in items:
        if item.get("key", item["link"]) in seen:
            continue
        should, reason = score_item(item["title"], item["summary"])
        if not should:
            continue

        log("  MATCH [{}] {}".format(reason, item["title"]))
        if alert(item, reason):
            seen.add(item.get("key", item["link"]))
            hits += 1

    save_seen(seen)
    log("Check complete. {} new alert(s) sent.".format(hits))
    return hits


def prime():
    """Mark everything currently published as seen, so the first real run only
    alerts on genuinely new posts instead of the existing backlog."""
    seen = load_seen()
    if seen:
        return
    log("First run: priming state so you do not get blasted with old articles.")
    for item in gather_items():
        seen.add(item.get("key", item["link"]))
    save_seen(seen)
    log("Primed with {} existing items. Watching for new ones from here.".format(len(seen)))


def main():
    ap = argparse.ArgumentParser(description="JD Yeezy slide restock watcher")
    ap.add_argument("--once", action="store_true", help="run one check and exit")
    ap.add_argument("--test", action="store_true", help="send a test email and exit")
    ap.add_argument("--no-prime", action="store_true",
                    help="skip priming, alert on existing articles too")
    args = ap.parse_args()

    if args.test:
        ok = send_email(
            "restock_bot test email",
            "<p>If you are reading this, the bot can email you. "
            "Alerts will go to {}.</p>".format(ALERT_EMAIL),
        )
        sys.exit(0 if ok else 1)

    if not args.no_prime:
        prime()

    if args.once:
        check_once()
        return

    log("Watching every {}s. Alerts go to {}. Ctrl+C to stop.".format(
        INTERVAL_SECONDS, ALERT_EMAIL))
    while True:
        try:
            check_once()
        except Exception as e:
            log("Unexpected error, continuing: {}".format(e))
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
