#!/usr/bin/env python3
"""
Craigslist Computer Gigs Monitor — Patched for GitHub IP throttling
- Only emails posts that read like a buyer (see leadfilter.py); other
  agencies' ads and job/gig-worker posts are dropped and logged
- Slower delay (4s) + every 3 hours so Craigslist doesn't block datacenter IPs
- Verbose logging so you can see why seen.json was staying {}
- Still uses data/cities.json → data/seen.json → Resend email/SMS
"""
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

from leadfilter import classify, dedupe_key

ROOT = Path(__file__).parent
CITIES_FILE = ROOT / "data" / "cities.json"
SEEN_FILE = ROOT / "data" / "seen.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "alerts@coaibakersfield.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")
ALERT_SMS_GATEWAY = os.environ.get("ALERT_SMS_GATEWAY")

REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 4.0  # bumped from 1.5 to avoid Craigslist throttling GH Actions IPs
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; COAI-GigMonitor/1.0)"}

def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError as e:
            print(f"WARN: {path} JSON error: {e}, using default")
            return default
    print(f"WARN: {path} not found, using default")
    return default

def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))
    print(f"Saved {path} with {len(data)} city entries")

def fetch_feed(slug):
    url = f"https://{slug}.craigslist.org/search/cpg?format=rss"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            print(f"  {slug}: HTTP {resp.status_code}")
            return None
        if not resp.content:
            print(f"  {slug}: empty response")
            return None
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            guid_el = item.find("guid")
            title_el = item.find("title")
            link_el = item.find("link")
            desc_el = item.find("description")
            guid = guid_el.text.strip() if guid_el is not None and guid_el.text else None
            title = title_el.text.strip() if title_el is not None and title_el.text else "(no title)"
            link = link_el.text.strip() if link_el is not None and link_el.text else url
            desc = desc_el.text.strip() if desc_el is not None and desc_el.text else ""
            if guid:
                items.append((guid, title, link, desc))
        print(f"  {slug}: {len(items)} posts")
        return items
    except (requests.RequestException, ET.ParseError) as e:
        print(f"  {slug}: error {e}")
        return None

def send_email(subject, html_body):
    if not (RESEND_API_KEY and ALERT_EMAIL):
        print("Email skipped: RESEND_API_KEY or ALERT_EMAIL not set")
        return
    resp = requests.post("https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={"from": FROM_EMAIL, "to": [ALERT_EMAIL], "subject": subject, "html": html_body},
        timeout=REQUEST_TIMEOUT)
    print(f"Email send status: {resp.status_code} {resp.text[:200]}")

def send_sms(text_body):
    if not (RESEND_API_KEY and ALERT_SMS_GATEWAY):
        print("SMS skipped: RESEND_API_KEY or ALERT_SMS_GATEWAY not set")
        return
    resp = requests.post("https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={"from": FROM_EMAIL, "to": [ALERT_SMS_GATEWAY], "subject": "", "text": text_body[:150]},
        timeout=REQUEST_TIMEOUT)
    print(f"SMS send status: {resp.status_code}")

def main():
    cities = load_json(CITIES_FILE, [])
    seen = load_json(SEEN_FILE, {})
    print(f"Loaded {len(cities)} cities from {CITIES_FILE}, {len(seen)} cities in seen.json")

    if not cities:
        print("ERROR: No cities loaded — check data/cities.json exists and is valid JSON")
        # still write empty seen to make job succeed
        save_json(SEEN_FILE, seen)
        return 0

    new_posts = []
    blocked = 0
    empty = 0

    for idx, city in enumerate(cities, 1):
        slug = city.get("slug") if isinstance(city, dict) else None
        name = city.get("name", slug) if isinstance(city, dict) else str(city)
        if not slug:
            print(f"[{idx}/{len(cities)}] SKIP bad entry {city}")
            continue
        print(f"[{idx}/{len(cities)}] {name} ({slug})", flush=True)
        items = fetch_feed(slug)
        time.sleep(DELAY_BETWEEN_REQUESTS)
        if items is None:
            blocked += 1
            continue
        if len(items) == 0:
            empty += 1
            # still mark as seen with empty list so we don't refetch infinitely
            seen[slug] = []
            continue
        seen_ids = set(seen.get(slug, []))
        current_ids = set()
        for guid, title, link, desc in items:
            current_ids.add(guid)
            if guid not in seen_ids:
                new_posts.append({"city": name, "slug": slug, "title": title, "link": link, "desc": desc})
        seen[slug] = list(current_ids)[-300:]
        if idx % 20 == 0:
            print(f"  ... progress {idx}/{len(cities)}, new so far: {len(new_posts)}, blocked: {blocked}")

    print(f"Done: checked {len(cities)}, blocked/no-feed: {blocked}, empty feeds: {empty}, total seen cities now: {len(seen)}")
    save_json(SEEN_FILE, seen)

    if not new_posts:
        print("No new posts this run.")
        return 0

    print(f"{len(new_posts)} new post(s) found.")

    # Keep buyers only, and collapse the same ad cross-posted to many cities.
    leads, dropped, dupes = [], {}, set()
    for p in new_posts:
        key = dedupe_key(p["title"], p.get("desc", ""))
        if key in dupes:
            continue
        dupes.add(key)
        verdict = classify(p["title"], p.get("desc", ""))
        if verdict["tier"]:
            p["tier"] = verdict["tier"]
            leads.append(p)
        else:
            kind = verdict["reason"].split(":")[0]
            dropped[kind] = dropped.get(kind, 0) + 1
    print(f"Leads after filter: {len(leads)}; dropped: {dropped}")
    if not leads:
        print("No buyer posts this run, no email sent.")
        return 0
    leads.sort(key=lambda p: p["tier"] != "hot")
    new_posts = leads
    rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #333;">{p["city"]}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #333;">{p["tier"].upper()}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #333;"><a href="{p["link"]}">{p["title"]}</a></td></tr>'
        for p in new_posts)
    html_body = f"<h2>{len(new_posts)} buyer post(s) on Craigslist computer gigs</h2><table style=\"border-collapse:collapse;width:100%;font-family:sans-serif;\"><tr><th style=\"text-align:left;padding:6px 10px;\">City</th><th style=\"text-align:left;padding:6px 10px;\">Tier</th><th style=\"text-align:left;padding:6px 10px;\">Post</th></tr>{rows}</table>"
    send_email(f"COAI Gig Monitor: {len(new_posts)} buyer post(s)", html_body)
    top = new_posts[:3]
    sms_lines = [f"{len(new_posts)} buyer post(s)."] + [f"{p['city']}: {p['title'][:40]}" for p in top]
    send_sms(" | ".join(sms_lines))
    return 0

if __name__ == "__main__":
    sys.exit(main())
