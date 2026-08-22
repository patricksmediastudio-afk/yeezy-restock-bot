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
# "smtp" (default), "gmail", or "sendgrid".
#
# smtp is the default on purpose. A Google app password does not expire, so
# an unattended bot keeps working. Gmail OAuth refresh tokens get revoked
# (and expire after 7 days if the consent screen is still in Testing mode),
# which silently kills a 24/7 watcher exactly when you need it.
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "smtp").strip().lower()

# --- SMTP backend ---
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
# Google displays app passwords as "abcd efgh ijkl mnop". The spaces are
# presentational, so strip them rather than making a failed login look like
# a wrong password.
SMTP_PASSWORD = (os.getenv("SMTP_PASSWORD") or "").replace(" ", "").strip() or None

SENDGRID_API_KEY = os.getenv("SENDGRID_API_KEY")
SENDER_EMAIL = os.getenv("SENDER_EMAIL")
ALERT_EMAIL = os.getenv("ALERT_EMAIL", SENDER_EMAIL)

# Gmail auth. token.json already contains client_id, client_secret and the
# refresh token, so credentials.json is not needed and the interactive OAuth
# flow is never run. A server has no browser to complete it with.
GMAIL_TOKEN_PATH = Path(
    os.getenv("GMAIL_TOKEN_PATH", Path(__file__).parent / "token.json")
)
# For hosting: paste the whole token.json contents into this variable instead
# of shipping the file.
GMAIL_TOKEN_JSON = os.getenv("GMAIL_TOKEN_JSON")
# Recorded for reference only. Not passed when loading credentials, see
# _gmail_credentials() for why.
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
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
# An item alerts if it mentions Yeezy AND slides. That is the whole gate.
#
# It used to also require stock language, and that was wrong. The drop is a
# one-shot event, so a false negative costs the shoes while a false positive
# costs one email. Measured against three weeks of live source data, the
# Yeezy + slide test on its own let through exactly one article that was not
# about availability (a YS-01 comparison piece), while the stock gate would
# have silently dropped headlines like "Yeezy Slides Releasing Online At JD
# Sports" and "Where To Buy The Yeezy Slide YS-01". Precision was not worth
# buying at that price.
#
# Stock language still matters, but as a priority label in the subject line
# rather than as a filter. See test_matching.py for the worked corpus.
RE_YEEZY = re.compile(r"\byeezy\b|\bys-?01\b", re.I)
RE_SLIDE = re.compile(r"\bslides?\b|\bys-?01\b", re.I)
RE_JD = re.compile(r"\bjd\b|jd sports|finish ?line", re.I)

# Highest confidence: the item says stock is coming back.
RE_RESTOCK = re.compile(
    r"restock\w*|back in stock|in stock again|returns?\b|returning\b",
    re.I,
)
# Still availability news, just less certain it is a restock as opposed to a
# first release. "releas\w*" is deliberate: "release" does not match
# "releasing", which is how the old gate lost half the plausible headlines.
RE_AVAILABILITY = re.compile(
    r"releas\w*|drop\w*|launch\w*|avail\w*|restock\w*|back in stock"
    r"|where to buy|how to (?:buy|cop|get)|raffle|now live|goes? live"
    r"|on sale|sells? out|sold out|sell-out|buy now|shop now|coming soon"
    r"|hits? (?:shelves|stores|online)|online now|in stock",
    re.I,
)


def score_item(title, summary=""):
    """Return (should_alert, reason).

    Alerts on any Yeezy slide item. The reason string carries the triage
    signal: RESTOCK is the one to open first, JD SPORTS says it names the
    retailer Patrick is actually buying from.
    """
    text = "{} {}".format(title, summary)
    if not RE_YEEZY.search(text):
        return False, ""
    if not RE_SLIDE.search(text):
        return False, ""

    if RE_RESTOCK.search(text):
        tier = "RESTOCK"
    elif RE_AVAILABILITY.search(text):
        tier = "availability news"
    else:
        tier = "mention"

    who = "JD SPORTS " if RE_JD.search(text) else ""
    return True, "{}Yeezy slide {}".format(who, tier)


# --------------------------------------------------------------------
# STATE
# --------------------------------------------------------------------
# Seen keys are held in a dict used as an ordered set. A plain set would work
# for the membership test, but set iteration order is arbitrary, so trimming
# it to the newest N below would actually keep a random N and could let an
# already-alerted article come back around and email twice. Dicts preserve
# insertion order, so oldest really is at the front.
def load_seen():
    if STATE_FILE.exists():
        try:
            return dict.fromkeys(json.loads(STATE_FILE.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def mark_seen(seen, key):
    seen[key] = None


def save_seen(seen):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the file from growing without bound. Oldest entries fall off first.
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
def _gmail_credentials():
    """Load Gmail credentials, refreshing if needed. Returns None on failure.

    Deliberately never falls back to the interactive OAuth flow: this runs
    headless, so a browser prompt would just hang forever.
    """
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request

    # Deliberately loaded WITHOUT an explicit scope list. This token file is
    # shared with faq-bot, which needs gmail.readonly as well as gmail.send.
    # Passing only our own scope would rewrite the file's scope list narrower
    # on write-back and break faq-bot's reply checking.
    from_file = False
    if GMAIL_TOKEN_JSON:
        creds = Credentials.from_authorized_user_info(json.loads(GMAIL_TOKEN_JSON))
    elif GMAIL_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(str(GMAIL_TOKEN_PATH))
        from_file = True
    else:
        log("  ERROR: no Gmail token. Copy token.json here, or set GMAIL_TOKEN_JSON.")
        return None

    if creds.valid:
        return creds

    if not (creds.expired and creds.refresh_token):
        log("  ERROR: Gmail token is invalid and cannot be refreshed. Re-auth needed.")
        return None

    try:
        creds.refresh(Request())
    except Exception as e:
        log("  ERROR: Gmail token refresh failed: {}".format(e))
        log("  Re-run the faq-bot OAuth flow to mint a fresh token.json.")
        return None

    # Persist the refreshed token so the next run starts valid.
    if from_file:
        try:
            GMAIL_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
        except OSError as e:
            log("  note: could not write refreshed token back: {}".format(e))

    return creds


def send_via_gmail(subject, html_body):
    import base64
    from email.mime.text import MIMEText
    from googleapiclient.discovery import build

    creds = _gmail_credentials()
    if not creds:
        return False

    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["To"] = ALERT_EMAIL
    if SENDER_EMAIL:
        msg["From"] = SENDER_EMAIL

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    try:
        service = build("gmail", "v1", credentials=creds)
        service.users().messages().send(userId="me", body={"raw": raw}).execute()
        log("  email sent to {} via Gmail".format(ALERT_EMAIL))
        return True
    except Exception as e:
        log("  email FAILED (gmail): {}".format(e))
        return False


def send_via_sendgrid(subject, html_body):
    if not SENDGRID_API_KEY or not SENDER_EMAIL:
        log("  ERROR: SENDGRID_API_KEY and SENDER_EMAIL must be set in .env")
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
        log("  email sent to {} via SendGrid (status {})".format(
            ALERT_EMAIL, resp.status_code))
        return True
    except Exception as e:
        log("  email FAILED (sendgrid): {}".format(e))
        return False


def send_via_smtp(subject, html_body):
    import smtplib
    from email.mime.text import MIMEText

    if not SMTP_USER or not SMTP_PASSWORD:
        log("  ERROR: set SMTP_USER and SMTP_PASSWORD in .env")
        log("  For Gmail, SMTP_PASSWORD is an app password, not your login password.")
        log("  Create one at https://myaccount.google.com/apppasswords")
        return False

    msg = MIMEText(html_body, "html")
    msg["Subject"] = subject
    msg["From"] = SENDER_EMAIL or SMTP_USER
    msg["To"] = ALERT_EMAIL

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=30) as server:
            server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        log("  email sent to {} via SMTP".format(ALERT_EMAIL))
        return True
    except smtplib.SMTPAuthenticationError:
        log("  email FAILED (smtp): authentication rejected.")
        log("  Gmail requires an app password here, and 2FA must be on.")
        return False
    except Exception as e:
        log("  email FAILED (smtp): {}".format(e))
        return False


BACKENDS = {
    "smtp": send_via_smtp,
    "gmail": send_via_gmail,
    "sendgrid": send_via_sendgrid,
}


def send_email(subject, html_body):
    if not ALERT_EMAIL:
        log("  ERROR: set ALERT_EMAIL (or SENDER_EMAIL) in .env")
        return False

    backend = BACKENDS.get(EMAIL_BACKEND)
    if not backend:
        log("  ERROR: unknown EMAIL_BACKEND '{}'. Use one of: {}".format(
            EMAIL_BACKEND, ", ".join(sorted(BACKENDS))))
        return False

    return backend(subject, html_body)


def alert(item, reason):
    # The subject line is the whole triage surface: it is what shows on a
    # phone lock screen. Lead with the tier so a genuine restock is
    # distinguishable from a Yeezy slide article at a glance.
    jd = "JD " if "JD SPORTS" in reason else ""
    if "RESTOCK" in reason:
        prefix = "{}RESTOCK ALERT".format(jd)
    elif "availability" in reason:
        prefix = "{}Yeezy slide availability".format(jd)
    else:
        prefix = "Yeezy slide mention"
    subject = "{}: {}".format(prefix, item["title"][:80])
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
            mark_seen(seen, item.get("key", item["link"]))
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
        mark_seen(seen, item.get("key", item["link"]))
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
