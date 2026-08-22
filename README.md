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

An item alerts if it mentions **Yeezy** *and* **slides**. That is the whole
gate. "Nike Dunk Restock" and "Yeezy 700 Release Date" are both correctly
ignored, because neither is a Yeezy slide.

The rule used to also require stock language, and that was a mistake. This
watcher exists for a one-shot event: a false negative costs the shoes, a
false positive costs one email. Tested against three weeks of live source
data, the Yeezy + slide test on its own let through exactly one article that
was not about availability, while the stock gate silently dropped headlines
like "Yeezy Slides Releasing Online At JD Sports" and "Where To Buy The Yeezy
Slide YS-01". `"release"` does not match `"releasing"`, which is how half the
plausible phrasings were being lost.

Stock language still does work, as a triage label rather than a filter. The
subject line leads with one of three tiers:

| Subject prefix | Meaning |
|---|---|
| `JD RESTOCK ALERT` | Restock language *and* it names JD or Finish Line. Open this first. |
| `RESTOCK ALERT` | Says restock, back in stock, returning. Some other retailer. |
| `JD Yeezy slide availability` | Release, drop, launch, raffle, where to buy, goes live, sold out, at JD. |
| `Yeezy slide availability` | Same, elsewhere. |
| `Yeezy slide mention` | A Yeezy slide article with no availability language at all. |

The prefix is the whole triage surface, because it is what shows on a phone
lock screen without unlocking it.

## Tests

```bash
python test_matching.py
```

Stdlib only, no pytest. It checks a corpus of 15 real-world drop phrasings
that must alert and 8 noise headlines that must not, plus the tier labels,
the subject lines and the seen-state trimming. Run it after touching the
regexes. CI runs it on every push that is not a state commit.

## Setup

```bash
pip install -r requirements.txt
```

Copy `.env.example` to `.env` and fill it in.

The default sender is `EMAIL_BACKEND=smtp`, which needs a Google **app
password** in `SMTP_PASSWORD`. Create one at
https://myaccount.google.com/apppasswords (2FA must be on). It is a 16
character string, and the spaces Google shows are presentational.

App passwords are used deliberately instead of OAuth. They do not expire,
so an unattended bot keeps running. Gmail OAuth refresh tokens get revoked,
and expire after 7 days while the consent screen is in Testing mode, which
kills a 24/7 watcher silently. `EMAIL_BACKEND=gmail` and `=sendgrid` are
still available if you want them.

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

It is already deployed. **GitHub Actions** runs
`restock_bot.py --once` on a `*/5` cron, and commits `state/seen.json` back to
the repo so state survives between runs. Full detail, including why the repo
has to be public and why that state commit is load bearing, is in
[DEPLOY.md](DEPLOY.md).

Railway is kept as a fallback (`railway.json`, `Procfile`, `.railwayignore`
are all still configured) but the trial expired, so it now needs a paid plan.

## Also worth doing, and it takes 30 seconds

Set the back in stock notification on the JD product page itself:

https://www.jdsports.com/pdp/yeezy-ys-01-slide-sandals/prod30231081/YS015/400

That comes straight from JD's own inventory system, so for the specific
size and colorway you want it can beat the news cycle. This bot is the wider
net that catches the announcement, the drop time, and the store list. Run both.

## Status as of 2026-08-22

Running on GitHub Actions, checking every 5 minutes, state committing back
cleanly. Nothing has matched yet.

A JD restock is confirmed but **in store only**, in limited quantities, with
no announced date for an online drop. Soleretriever's wording is that an
online drop is expected "in the coming weeks". That online drop is what this
bot is waiting for.
