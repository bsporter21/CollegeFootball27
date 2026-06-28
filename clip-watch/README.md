# Clip Watch — College Football 27 Xbox → Discord

A tiny bot that watches your league's Xbox gamertags and posts every new
**College Football 27** game clip and screenshot into a Discord channel.
It runs entirely on **free GitHub Actions** — no server, no monthly bill.

---

## What you need (one-time, ~10 minutes)

1. **A free OpenXBL API key** — sign in at <https://xbl.io> with a Microsoft
   account and copy your key. The free tier (150 requests/hour) is plenty;
   this bot uses far less.
2. **A Discord webhook URL** — in your Discord server: Channel → Edit →
   Integrations → Webhooks → New Webhook → Copy URL. Pick the channel you want
   clips posted into.
3. **A GitHub account** (free).

> ⚠️ **Rotate your old webhook.** The previous version of this bot had a
> Discord webhook hard-coded in the source, so that one should be considered
> compromised. Delete it in Discord and make a fresh one for the steps below.

---

## Setup

### 1. Put these files in a new GitHub repo
Create a new repository (private is fine) and upload everything in this folder,
keeping the structure intact:

```
scraper.py
requirements.txt
state.json
.github/workflows/clip-watch.yml
```

### 2. Add your two secrets
In the repo: **Settings → Secrets and variables → Actions → New repository
secret**. Add both:

| Name | Value |
|------|-------|
| `XBL_API_KEY` | your OpenXBL key |
| `DISCORD_WEBHOOK_URL` | your fresh Discord webhook URL |

(Secrets are encrypted and never visible in logs — this is why the webhook is
no longer in the code.)

### 3. Turn it on
Go to the **Actions** tab and enable workflows if prompted. The bot will start
running on its own every ~10 minutes. To test immediately, open the
**Clip Watch** workflow and click **Run workflow**.

That's it. New clips will start showing up in your Discord channel.

---

## How it works

- GitHub fires the workflow on a cron schedule (every ~10 min).
- `scraper.py` runs **once**: for each gamertag it pulls recent clips +
  screenshots from OpenXBL, keeps only College Football 27 items, and posts any
  it hasn't seen before to Discord.
- It records what it posted in `state.json` and commits that back to the repo,
  so the next run doesn't repeat anything.
- Gamertag → Xbox ID lookups are cached in `state.json` after the first run, so
  ongoing usage is just ~10 API requests per run — comfortably free.

## Tweaks

- **Change who's tracked:** edit the `GAMERTAGS` list at the top of
  `scraper.py`.
- **Change how often it checks:** edit the `cron:` line in
  `.github/workflows/clip-watch.yml`. `*/10 * * * *` = every 10 min;
  `*/15 * * * *` = every 15.
- **Track a different game:** edit `GAME_FILTER` in `scraper.py`.

## Cost

- OpenXBL API: **free**
- Discord webhook: **free**
- GitHub Actions: **free** for this volume (a public repo has unlimited
  minutes; a private repo gets 2,000 free minutes/month and each run uses well
  under one).

## Notes & limits

- GitHub sometimes delays scheduled runs when it's busy, so "every 10 minutes"
  is really "every 10-ish minutes." Fine for clip alerts.
- This only sees media the player has saved to their Xbox DVR and that OpenXBL
  can read. PlayStation accounts aren't covered (no equivalent open API).
- This version forwards clips/screenshots as-is. Turning the box-score
  screenshots into stats for the tracker (OCR) is a separate, later step.
