#!/usr/bin/env python3
"""
COAI Bakersfield Auto-Finder — runs every 15 min
Checks:
  1) Craigslist 661 region (Bakersfield + 7 neighbors) — RSS
  2) Reddit r/Bakersfield + r/KernCounty — JSON (no auth)

Sends to Discord webhook and Telegram bot, saves seen.json so it only alerts on NEW.
Designed for GitHub Actions (free), Bakersfield-only = 15 min is safe (32 sec runtime).
"""
import json, os, sys, time, re
import xml.etree.ElementTree as ET
from pathlib import Path
import requests

ROOT = Path(__file__).parent
SEEN_FILE = ROOT / "data" / "seen_bakersfield.json"  # separate from seen.json so it doesn't clash

# --- Config: Bakersfield 661 region ---
CRAIGSLIST_SLUGS = [
    ("Bakersfield", "bakersfield"),
    ("Fresno / Madera", "fresno"),
    ("SLO", "slo"),
    ("Santa Barbara", "santabarbara"),
    ("Ventura", "ventura"),
    ("Stockton", "stockton"),
    ("Modesto", "modesto"),
    ("Visalia-Tulare", "visalia"),
]

REDDIT_SUBS = ["Bakersfield", "KernCounty", "BakersfieldJobs"]
REDDIT_KEYWORDS = re.compile(r"computer|pc\b|laptop|repair|virus|tech support|it help|need.*computer|geek squad", re.I)

# Secrets (set in GitHub → Settings → Secrets → Actions)
DISCORD_WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT = os.environ.get("TELEGRAM_CHAT_ID", "").strip()

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; COAI-BakersfieldBot/1.0) Bakersfield lead monitor",
    "Accept": "application/rss+xml, application/xml, text/xml, */*"
}
REQUEST_TIMEOUT = 12
DELAY = 3  # seconds between Craigslist cities — polite

def load_seen():
    if SEEN_FILE.exists():
        try:
            return json.loads(SEEN_FILE.read_text())
        except: return {"craigslist": {}, "reddit": []}
    return {"craigslist": {}, "reddit": []}

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(seen, indent=2))
    print(f"Saved {SEEN_FILE} — {sum(len(v) for v in seen['craigslist'].values())} CL guids, {len(seen['reddit'])} reddit ids")

def fetch_craigslist(slug):
    url = f"https://{slug}.craigslist.org/search/cpg?format=rss"
    try:
        r = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            print(f"  {slug}: HTTP {r.status_code}")
            return []
        root = ET.fromstring(r.content)
        out=[]
        for it in root.iter("item"):
            guid = (it.findtext("guid") or it.findtext("link") or "").strip()
            title = (it.findtext("title") or "").strip()
            link = (it.findtext("link") or "").strip()
            pub = it.findtext("pubDate") or ""
            if guid and guid not in out:
                out.append({"guid": guid, "title": title, "link": link, "pubDate": pub})
        print(f"  {slug}: {len(out)} posts")
        return out
    except Exception as e:
        print(f"  {slug}: error {e}")
        return []

def fetch_reddit(sub):
    url = f"https://www.reddit.com/r/{sub}/new.json?limit=25"
    try:
        r = requests.get(url, headers={"User-Agent": "COAI-BakersfieldBot/1.0"}, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            print(f"  r/{sub}: HTTP {r.status_code}")
            return []
        data = r.json()
        posts = data.get("data", {}).get("children", [])
        out=[]
        for p in posts:
            d = p.get("data", {})
            title = d.get("title","")
            if not REDDIT_KEYWORDS.search(title):
                continue
            out.append({
                "id": d.get("id"),
                "title": title,
                "link": "https://reddit.com" + d.get("permalink",""),
                "sub": sub,
                "author": d.get("author","")
            })
        print(f"  r/{sub}: {len(out)} matching / {len(posts)} total")
        return out
    except Exception as e:
        print(f"  r/{sub}: error {e}")
        return []

def send_discord(blocks):
    if not DISCORD_WEBHOOK:
        print("Discord skipped: DISCORD_WEBHOOK_URL not set")
        return
    # Discord embeds max 10 per message, 6000 chars
    embeds=[]
    for b in blocks[:10]:
        embeds.append({
            "title": b["title"][:256],
            "url": b["link"],
            "description": f"{b['source']} • {b['city'] if 'city' in b else 'r/'+b.get('sub','')}\n{b['link']}",
            "color": 0xFF6A00 if b["source"]=="craigslist" else 0x22C55E
        })
    payload = {
        "username": "COAI Lead Bot",
        "content": f"🔔 **{len(blocks)} new Bakersfield lead(s)** — {time.strftime('%I:%M %p %Z')}",
        "embeds": embeds
    }
    r = requests.post(DISCORD_WEBHOOK, json=payload, timeout=10)
    print(f"Discord status: {r.status_code} {r.text[:200]}")

def send_telegram(blocks):
    if not (TELEGRAM_TOKEN and TELEGRAM_CHAT):
        print("Telegram skipped: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set")
        return
    # Telegram 4096 char limit, send 1 message with top 8
    lines = [f"🔔 *{len(blocks)} new Bakersfield lead(s)*"]
    for b in blocks[:8]:
        # escape markdown minimally
        title = b["title"].replace("*","").replace("_","")[:70]
        lines.append(f"• *{b['city'] if 'city' in b else 'r/'+b.get('sub','')}*: [{title}]({b['link']})")
    text = "\n".join(lines)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    r = requests.post(url, json={"chat_id": TELEGRAM_CHAT, "text": text, "parse_mode": "Markdown", "disable_web_page_preview": True}, timeout=10)
    print(f"Telegram status: {r.status_code} {r.text[:200]}")

def main():
    seen = load_seen()
    seen_cl = seen.get("craigslist", {})
    seen_rd = set(seen.get("reddit", []))
    new_blocks=[]

    print(f"Checking Craigslist 661 region ({len(CRAIGSLIST_SLUGS)} cities)...")
    for name, slug in CRAIGSLIST_SLUGS:
        items = fetch_craigslist(slug)
        time.sleep(DELAY)
        known = set(seen_cl.get(slug, []))
        current = set()
        for it in items:
            current.add(it["guid"])
            if it["guid"] not in known:
                new_blocks.append({"source":"craigslist", "city": name, "slug": slug, "title": it["title"], "link": it["link"]})
        seen_cl[slug] = list(current)[-150:]  # keep last 150

    print(f"Checking Reddit ({len(REDDIT_SUBS)} subs)...")
    for sub in REDDIT_SUBS:
        posts = fetch_reddit(sub)
        time.sleep(1)
        for p in posts:
            if p["id"] not in seen_rd:
                new_blocks.append({"source":"reddit", "sub": sub, "title": p["title"], "link": p["link"]})
                seen_rd.add(p["id"])
        # cap seen reddit to last 300
        if len(seen_rd) > 400:
            seen_rd = set(list(seen_rd)[-300:])

    seen["craigslist"] = seen_cl
    seen["reddit"] = list(seen_rd)[-300:]
    save_seen(seen)

    if not new_blocks:
        print("No new Bakersfield leads this run.")
        return 0

    print(f"Found {len(new_blocks)} new lead(s):")
    for b in new_blocks:
        print(f"  - [{b['source']}] {b.get('city', 'r/'+b.get('sub',''))}: {b['title'][:80]}")

    # Send both channels (if configured)
    try: send_discord(new_blocks)
    except Exception as e: print(f"Discord error: {e}")
    try: send_telegram(new_blocks)
    except Exception as e: print(f"Telegram error: {e}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
