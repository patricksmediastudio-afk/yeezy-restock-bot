# JD Yeezy Slide Restock Bot

Watches for a JD Sports Yeezy YS-01 Slide restock and emails you when one is announced.

## Why it does not poll jdsports.com

The obvious design is to hit the product page every minute and look for the
"Add to Bag" button. That does not work here.

`jdsports.com` sits behind JD Finish Line's bot protection. Every non-browser
client gets an HTTP 403 "Your Access Has Been Denied" page, regardless of
headers:

```
plain curl            -> 403
curl with browser UA  -> 403
python-requests       -> 403
```

Getting past that means impersonating a real browser at the TLS and
fingerprint level. That breaks JD's terms, gets your IP and account banned,
and is exactly what they are watching for. This bot does not do it.

Instead it watches sources that serve scripts a clean 200 and that publish
restock news, in practice, before the product page flips to in stock.

## Sources

| Source | Endpoint | Notes |
|---|---|---|
| SneakerNews | `/feed/` | RSS, ~7 recent posts |
| Sneaker Bar Detroit | `/feed/` | RSS, ~25 recent posts |
| Soleretriever | `sitemap.xml` -> newest `sitemap-news/N` | Published in their robots.txt. Every entry carries a `lastmod`, so a re-edited restock post counts as a new signal. |

Soleretriever's homepage 403s scripts via Cloudflare, but their sitemap is
explicitly advertised in `robots.txt` and returns 200. Their `robots.txt`
disallows `/collections` and `/api`, which this bot never touches.

## Match rule

An item alerts only if it mentions **Yeezy** *and* **slides** *and* stock
language (restock, back in stock, drop, release, available, launch).

This is deliberately narrow. "Nike Dunk Restock" and "Yeezy 700 Release Date"
are both correctly ignored. Alerts mentioning JD or Finish Line are labelled
`JD SPORTS`, and anything saying restock or back in stock is labelled
`RESTOCK` so you can triage from the subject line.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in. `SENDGRID_API_KEY` and
`SENDER_EMAIL` are the same values as your faq-bot `.env`.

Verify email works before trusting it:

```bash
python restock_bot.py --test
```

## Running

```bash
python restock_bot.py
```

First run primes state with everything already published so you do not get a
burst of old articles. From then on it only alerts on new items, checking
every `INTERVAL_SECONDS` (default 300, which is five minutes and polite).

Other modes:

```bash
python restock_bot.py --once       # one check then exit, for cron
python restock_bot.py --no-prime   # alert on existing articles too
```

## Deploying 24/7

**Railway**, same as your other bots. The `Procfile` declares a `worker`
process. Set `SENDGRID_API_KEY`, `SENDER_EMAIL`, and `ALERT_EMAIL` as
variables. Mount a volume and set `STATE_DIR=/data` so `seen.json` survives
redeploys, otherwise the bot re-primes on every restart and you miss the
window between restart and the next post.

**Windows, no hosting.** Task Scheduler running
`python restock_bot.py --once` every 5 minutes also works, since `--once` is
stateless between runs and reads `seen.json` from disk.

## Also worth doing, and it takes 30 seconds

Set the back in stock notification on the JD product page itself:

https://www.jdsports.com/pdp/yeezy-ys-01-slide-sandals/prod30231081/YS015/400

That comes straight from JD's own inventory system, so for the specific
size and colorway you want it can beat the news cycle. This bot is the wider
net that catches the announcement, the drop time, and the store list. Run both.

## Status as of 2026-08-20

A JD restock is confirmed but **in store only**, in limited quantities, with
no announced date for an online drop. Soleretriever's wording is that an
online drop is expected "in the coming weeks". That online drop is what this
bot is waiting for.
