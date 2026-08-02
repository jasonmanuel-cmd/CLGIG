#!/usr/bin/env python3
"""
Craigslist Computer Gigs Monitor
---------------------------------
Polls the RSS feed for the "computer gigs" category (cpg) across every
city in data/cities.json, diffs against previously-seen post IDs
(data/seen.json), and emails/texts a digest of new listings via Resend.

Designed to run hourly from GitHub Actions. State (seen.json) is
committed back to the repo by the workflow after each run.
"""

import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

ROOT = Path(__file__).parent
CITIES_FILE = ROOT / "data" / "cities.json"
SEEN_FILE = ROOT / "data" / "seen.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "alerts@coaibakersfield.com")
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")
ALERT_SMS_GATEWAY = os.environ.get("ALERT_SMS_GATEWAY")  # e.g. 6615551234@vtext.com

REQUEST_TIMEOUT = 12
DELAY_BETWEEN_REQUESTS = 1.5  # seconds, be polite / avoid rate-limit flags
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; COAI-GigMonitor/1.0)"
}


def load_json(path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except json.JSONDecodeError:
            return default
    return default


def save_json(path, data):
    path.write_text(json.dumps(data, indent=2))


def fetch_feed(slug):
    """Fetch and parse the cpg RSS feed for one city. Returns list of (guid, title, link)."""
    url = f"https://{slug}.craigslist.org/search/cpg?format=rss"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            return None  # city may not have this category, or blocked
        root = ET.fromstring(resp.content)
        items = []
        for item in root.iter("item"):
            guid_el = item.find("guid")
            title_el = item.find("title")
            link_el = item.find("link")
            guid = guid_el.text.strip() if guid_el is not None and guid_el.text else None
            title = title_el.text.strip() if title_el is not None and title_el.text else "(no title)"
            link = link_el.text.strip() if link_el is not None and link_el.text else url
            if guid:
                items.append((guid, title, link))
        return items
    except (requests.RequestException, ET.ParseError):
        return None


def send_email(subject, html_body):
    if not (RESEND_API_KEY and ALERT_EMAIL):
        print("Email skipped: RESEND_API_KEY or ALERT_EMAIL not set")
        return
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": FROM_EMAIL,
            "to": [ALERT_EMAIL],
            "subject": subject,
            "html": html_body,
        },
        timeout=REQUEST_TIMEOUT,
    )
    print(f"Email send status: {resp.status_code}")


def send_sms(text_body):
    if not (RESEND_API_KEY and ALERT_SMS_GATEWAY):
        print("SMS skipped: RESEND_API_KEY or ALERT_SMS_GATEWAY not set")
        return
    # Carrier email-to-SMS gateways render the email body as the text.
    # Keep it short — most gateways truncate around 140-160 chars.
    resp = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
        json={
            "from": FROM_EMAIL,
            "to": [ALERT_SMS_GATEWAY],
            "subject": "",
            "text": text_body[:150],
        },
        timeout=REQUEST_TIMEOUT,
    )
    print(f"SMS send status: {resp.status_code}")


def main():
    cities = load_json(CITIES_FILE, [])
    seen = load_json(SEEN_FILE, {})

    new_posts = []  # list of dicts: city, title, link

    for city in cities:
        slug = city["slug"]
        name = city["name"]
        items = fetch_feed(slug)
        time.sleep(DELAY_BETWEEN_REQUESTS)

        if items is None:
            continue  # skip cities with no feed / blocked / no cpg category

        seen_ids = set(seen.get(slug, []))
        current_ids = set()

        for guid, title, link in items:
            current_ids.add(guid)
            if guid not in seen_ids:
                new_posts.append({"city": name, "title": title, "link": link})

        # Update seen list (cap at last 300 per city to keep file size sane)
        seen[slug] = list(current_ids)[-300:]

    save_json(SEEN_FILE, seen)

    if not new_posts:
        print("No new posts this run.")
        return

    print(f"{len(new_posts)} new post(s) found.")

    # Build email digest
    rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #333;">{p["city"]}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #333;">'
        f'<a href="{p["link"]}">{p["title"]}</a></td></tr>'
        for p in new_posts
    )
    html_body = f"""
    <h2>{len(new_posts)} new Craigslist computer gig post(s)</h2>
    <table style="border-collapse:collapse;width:100%;font-family:sans-serif;">
      <tr><th style="text-align:left;padding:6px 10px;">City</th><th style="text-align:left;padding:6px 10px;">Post</th></tr>
      {rows}
    </table>
    """
    send_email(f"COAI Gig Monitor: {len(new_posts)} new post(s)", html_body)

    # Build short SMS digest
    top = new_posts[:3]
    sms_lines = [f"{len(new_posts)} new gig post(s)."]
    for p in top:
        sms_lines.append(f"{p['city']}: {p['title'][:40]}")
    send_sms(" | ".join(sms_lines))


if __name__ == "__main__":
    sys.exit(main())
