# Deploying

The bot needs to run when your computer does not. The chosen path is GitHub
Actions, which is free. Railway instructions are kept at the bottom as an
alternative.

## GitHub Actions (current setup)

`.github/workflows/restock.yml` runs `restock_bot.py --once` on a `*/5` cron.

### 1. The repo must be public

This is the part that decides whether it is free. Public repos get unlimited
Actions minutes. Private repos get 2000 minutes a month, and this bot runs
288 times a day, so a private repo would burn through that in about a week
and then stop.

Nothing sensitive is in the repo. Credentials live in GitHub Secrets, and
`.env`, `token.json` and `credentials.json` are all gitignored.

### 2. Create the repo and push

```
gh repo create yeezy-restock-bot --public --source . --push
```

Or create it in the GitHub web UI and push manually.

### 3. Add the secrets

Repo Settings > Secrets and variables > Actions > New repository secret.
Four of them:

| Secret | Value |
|---|---|
| `SMTP_USER` | patricksmediastudio@gmail.com |
| `SMTP_PASSWORD` | your 16 character Google app password |
| `SENDER_EMAIL` | patricksmediastudio@gmail.com |
| `ALERT_EMAIL` | patricksmediastudio@gmail.com |

Or from the CLI:

```
gh secret set SMTP_PASSWORD
```

### 4. Trigger a run to confirm it works

Actions tab > "Yeezy restock watch" > Run workflow. Do not wait for the cron
to prove it works.

The first run primes `state/seen.json` with everything already published and
sends nothing. That is correct behaviour, not a failure. From then on it only
alerts on new items.

### How state survives

Each Actions run is a fresh machine, so anything not committed is lost. The
workflow sets `STATE_DIR=state` and commits `state/seen.json` back to the repo
after each run. Without that the bot would re-prime every single run, treat
everything current as already seen, and never alert. It would look perfectly
healthy in the logs while catching nothing.

That commit also keeps the repo active. GitHub disables scheduled workflows on
public repos after 60 days of inactivity, and these commits reset that clock.

### The real tradeoff

GitHub's scheduler is best effort. Scheduled runs are frequently delayed, and
under heavy platform load they can be skipped entirely. Expect 5 minutes
typically and occasionally 15 to 30.

For this bot that is acceptable, because the latency is dominated by how long
it takes a sneaker site to publish the news, not by how often we poll. It
would be a bad tradeoff for a bot watching a live stock endpoint. It is a
reasonable one for a bot watching news.

If a restock is announced and you want minute-accurate alerting, run the bot
locally at the same time. Both can run at once, and they keep separate state,
so the only cost is a duplicate email.

## Railway (alternative, needs a paid plan)

The trial on workspace `patricksmediastudio-afk` expired on 2026-08-21, so
this needs the Hobby plan (~$5/mo). `railway.json`, `Procfile` and
`.railwayignore` are all still configured.

```
railway init
railway up
railway variables --set "EMAIL_BACKEND=smtp" --set "SMTP_USER=..." --set "SMTP_PASSWORD=..." --set "SENDER_EMAIL=..." --set "ALERT_EMAIL=..." --set "INTERVAL_SECONDS=300" --set "STATE_DIR=/data"
```

Then mount a volume at `/data` in the dashboard, and `railway up` again.
The volume is what makes `STATE_DIR=/data` persist across redeploys.

Railway gives exact 5 minute timing and no scheduler drift. If you ever pay
for it to bring the Zeffirelli chatbot back online, moving this bot there too
costs nothing extra.
