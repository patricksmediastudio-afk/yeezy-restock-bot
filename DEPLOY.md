# Deploying

The bot needs to run when your computer does not. The chosen path is GitHub
Actions, which is free. Railway instructions are kept at the bottom as an
alternative.

## GitHub Actions (current setup)

`.github/workflows/restock.yml` starts one job an hour that watches for 50
minutes, checking every 5. See "Why hourly and not every 5 minutes" below,
which is the least obvious decision in this repo.

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

### Why hourly and not every 5 minutes

The workflow used to run `--once` on a `*/5` cron. Measured over 36 hours of
real runs on this repo, that delivered:

| | |
|---|---|
| Runs a perfect `*/5` would give | 415 |
| Runs that actually happened | 68 |
| Median gap between checks | 30 min |
| p90 gap | 48 min |
| Worst gap | 91 min |

66 of 67 gaps were over 15 minutes. GitHub's scheduler is best effort, it
deprioritises high frequency schedules, and it was delivering about 16% of
what the cron asked for. The old note here said "expect 5 minutes typically
and occasionally 15 to 30", which was a guess and was wrong.

The fix is not to ask harder. `*/5` is already GitHub's minimum. The fix is to
stop depending on the scheduler: ask it for one job an hour, and let that job
stay alive and do the polling itself.

GitHub can delay *starting* a job, and clearly does. It cannot interrupt one
that is already running. So the 5 minute sleep inside `restock_bot.py` runs on
the runner's own clock and nothing can skip it. That turns 288 scheduler
requests a day into 24, and everything in between is guaranteed.

Net effect: roughly 11 checks an hour instead of 2, and the gaps that remain
are at the predictable handoff at the top of each hour rather than scattered
randomly.

The job watches for 50 minutes, not 55, on purpose. If GitHub starts it late,
it still finishes before the next hour's trigger, so the two never queue up
behind the concurrency group.

### What this costs

The repo now uses about 20 hours a day of runner time instead of 5 minutes.
That is free and within terms for a public repo, but it is a real step up in
how much GitHub infrastructure a personal project is using. Worth knowing.

### The failure mode

Alerts email the moment they are found, mid-run. The state commit only happens
when the job ends. So a job that crashes at minute 30 loses its dedup record
and may re-send an alert next hour. The cost of a crash is a duplicate email,
never a missed one, which is the right way round.

A genuine crash fails the workflow loudly. Exit code 124 is `timeout` doing its
job and is treated as success; anything else non-zero fails the run, so a dead
watcher does not sit there looking healthy.

### Proving the sender works

The scheduled run never exercises email. State commits happen either way, and
alerts only fire on a match. A dead `SMTP_PASSWORD` would look exactly like a
healthy bot right up until the drop.

So: Actions tab > "Yeezy restock watch" > Run workflow > mode `test-email`.
That sends a test email using the real production secrets. Do it after any
change to the secrets.

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
