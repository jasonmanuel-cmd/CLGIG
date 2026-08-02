# Craigslist Computer Gigs Monitor

Hourly watcher across 247 US Craigslist markets. New posts trigger an
email + text alert.

## How it works

1. GitHub Actions runs `monitor.py` every hour.
2. The script pulls each city's `cpg` (computer gigs) RSS feed.
3. It diffs the post IDs against `data/seen.json` (committed to the
   repo — this is the memory between runs).
4. Anything new gets emailed (full list) and texted (top 3, via your
   carrier's email-to-SMS gateway) through Resend.

## Setup (10 minutes)

1. **Create a new GitHub repo** and push this folder to it.

2. **Add repo secrets** — Settings → Secrets and variables → Actions →
   New repository secret:

   | Secret | Value |
   |---|---|
   | `RESEND_API_KEY` | Your Resend API key |
   | `FROM_EMAIL` | A verified sender on your Resend domain, e.g. `alerts@coaibakersfield.com` |
   | `ALERT_EMAIL` | Where the full digest goes |
   | `ALERT_SMS_GATEWAY` | Your number @ your carrier's SMS gateway (see below) |

3. **Find your carrier's SMS gateway address** — this turns an email
   into a text message, no Twilio account needed:

   | Carrier | Format |
   |---|---|
   | Verizon | `number@vtext.com` |
   | AT&T | `number@txt.att.net` |
   | T-Mobile | `number@tmomail.net` |
   | Google Fi | `number@msg.fi.google.com` |

   Example: `6615551234@vtext.com`

4. **Enable the workflow** — it runs automatically on the hourly cron
   once it's on the default branch. You can also trigger it manually
   from the Actions tab (`Run workflow`) to test it immediately.

5. **First run will look noisy** — every post currently live counts
   as "new" the first time, since `seen.json` starts empty. That's
   expected; every run after that only reports genuinely new posts.

## Known limitations — read before relying on this

- **Craigslist's ToS prohibits automated access.** RSS is the
  lightest-weight, most sanctioned way to poll, but this isn't an
  official API — Craigslist can rate-limit or block a source IP at
  any time without notice. GitHub Actions runners use shared/rotating
  IPs, which cuts both ways: less likely to build up a block history
  tied to you personally, but you don't control the reputation of the
  IP you land on either.
- **Not every city's `cpg` feed will return data on every run** — a
  timeout or non-200 is treated as "skip this city this cycle," not a
  hard failure, so a bad run for one city won't break the others.
- **If Craigslist changes their RSS format or blocks it outright**,
  this breaks silently until you notice the alerts stopped — worth
  spot-checking the Actions run log every so often.
- Add/remove cities anytime by editing `data/cities.json`.

## Files

- `monitor.py` — the actual check-and-alert logic
- `data/cities.json` — the 247 markets it watches
- `data/seen.json` — state file (auto-created/updated by the workflow)
- `.github/workflows/monitor.yml` — the hourly schedule
