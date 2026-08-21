# Deploying to Railway

Everything in this folder is ready. What's left needs your Railway account,
so it's yours to run. Should take about five minutes.

## Before you start

Run the email test locally if you haven't. If email is broken, a deployed bot
is a bot that silently does nothing:

```
C:\Users\patri\AppData\Local\Python\pythoncore-3.14-64\python.exe restock_bot.py --test
```

## 1. Install the Railway CLI

It isn't on this machine yet.

```
npm i -g @railway/cli
```

## 2. Log in and create the project

```
cd C:\Users\patri\Documents\restock-bot
railway login
railway init
```

Name it something like `yeezy-restock-bot`.

## 3. Set the environment variables

Do NOT upload `.env`. It is gitignored and should stay local. Set the values
in Railway instead:

```
railway variables --set SENDGRID_API_KEY=your_mail_send_only_key
railway variables --set SENDER_EMAIL=your_verified_sender
railway variables --set ALERT_EMAIL=patricksmediastudio@gmail.com
railway variables --set INTERVAL_SECONDS=300
railway variables --set STATE_DIR=/data
```

Use a **Mail Send only** SendGrid key here, not the one your faq-bot outreach
uses. If this bot ever misbehaves or the key leaks, your business sending
should not go down with it.

## 4. Add the volume, this is the step people skip

In the Railway dashboard, open the service, go to **Variables and Volumes**,
and mount a volume at `/data`.

This is what `STATE_DIR=/data` points at. Without it, `seen.json` lives on
ephemeral disk and is wiped on every redeploy and restart. The bot would then
re-prime from scratch, treat the current articles as already seen, and you
would miss anything posted during that window. The volume is the difference
between a bot that works and a bot that looks like it works.

## 5. Deploy

```
railway up
```

## 6. Confirm it's alive

```
railway logs
```

You want to see, roughly every five minutes:

```
[timestamp] Checking sources...
[timestamp]   39 items gathered, 39 already seen
[timestamp] Check complete. 0 new alert(s) sent.
```

`0 new alerts` is the healthy state. It means the bot is running and there is
simply no restock news yet. You will know it fired when the email arrives.

## Alternative: deploy from GitHub

If you would rather not use the CLI, push this folder to its own private
GitHub repo and connect it in the Railway dashboard. It already has its own
git repo, separate from your home directory. Steps 3 and 4 still apply.

## A warning about your home directory

`C:\Users\patri` is itself a git repository. That is almost certainly not
intentional. If anything ever runs `git add -A && git push` in your home
folder, it would publish `.claude.json`, `credentials.json`, `token.json`,
and every `.env` on the machine, including your SendGrid and Anthropic keys.

This folder now has its own repo so deploying it cannot drag the rest along.
Worth looking at the home directory repo separately when you have a moment.
