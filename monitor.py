#!/usr/bin/env python3
"""
Craigslist Computer Gigs Monitor — Patched for GitHub IP throttling
- Only emails posts that read like a buyer (see leadfilter.py); other
  agencies' ads and job/gig-worker posts are dropped and logged
- Slower delay (4s) + every 3 hours so Craigslist doesn't block datacenter IPs
- Verbose logging so you can see why seen.json was staying {}
- Reads Craigslist's search JSON (the RSS feed stopped answering)
- Still uses data/cities.json → data/seen.json → Resend email/SMS
- seen.json keeps {post id: first seen} per city for 60 days, plus the text
  keys of posts already emailed, so old and cross-posted ads never repeat
"""
import html
import json
import os
import re
import sys
import time
from pathlib import Path

import requests

from leadfilter import classify, dedupe_key

ROOT = Path(__file__).parent
CITIES_FILE = ROOT / "data" / "cities.json"
SEEN_FILE = ROOT / "data" / "seen.json"

RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
# GitHub passes a missing secret as an empty string, so fall back on empty too.
# onboarding@resend.dev only delivers to the Resend account's own address.
DEFAULT_FROM = "COAI Gig Monitor <onboarding@resend.dev>"
_SENDER = re.compile(r"^(?:[^<>@]+<)?[^\s<>@\"']+@[^\s<>@\"']+\.[^\s<>@\"']+>?$")


def pick_sender(raw):
    """Resend wants email@example.com or Name <email@example.com>. A blank or
    malformed FROM_EMAIL secret would make every send fail, so use the default."""
    val = (raw or "").strip().strip('"').strip("'").strip()
    if val and _SENDER.match(val) and (val.endswith(">") == ("<" in val)):
        return val
    if val:
        print("WARN: FROM_EMAIL secret is not a valid sender address; using the default sender")
    return DEFAULT_FROM


FROM_EMAIL = pick_sender(os.environ.get("FROM_EMAIL"))
ALERT_EMAIL = os.environ.get("ALERT_EMAIL")
ALERT_SMS_GATEWAY = os.environ.get("ALERT_SMS_GATEWAY")

REQUEST_TIMEOUT = 15
DELAY_BETWEEN_REQUESTS = 4.0  # bumped from 1.5 to avoid Craigslist throttling GH Actions IPs
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; COAI-GigMonitor/1.0)"}
SEEN_DAYS = 60  # forget a post this many days after we first saw it (gig posts expire sooner)
KEYS = "_alerted"  # dedupe keys already emailed, so cross-posts never repeat
PENDING = "_pending"  # leads whose email failed, retried next run


def _now():
    return int(time.time())


def normalize_seen(seen):
    """Old seen.json stored a list of ids per city; now it is {id: first_seen}."""
    out = {}
    for slug, val in (seen or {}).items():
        if isinstance(val, dict):
            out[slug] = {k: int(v) for k, v in val.items()}
        elif isinstance(val, list):
            out[slug] = {k: _now() for k in val}
    return out


def prune_seen(seen, now=None):
    cutoff = (now or _now()) - SEEN_DAYS * 86400
    for slug in list(seen):
        seen[slug] = {k: t for k, t in seen[slug].items() if t >= cutoff}
    return seen

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

SEARCH_URL = ("https://sapi.craigslist.org/web/v8/postings/search/full"
              "?batch={area}-0-360-0-0&cc=US&lang=en&searchPath=cpg")
AREAS_URL = "https://reference.craigslist.org/Areas"
_AREA_IDS = None


def area_ids():
    """Hostname -> Craigslist area id, e.g. bakersfield -> 63. Fetched once per run."""
    global _AREA_IDS
    if _AREA_IDS is None:
        _AREA_IDS = {}
        try:
            resp = requests.get(AREAS_URL, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            for a in resp.json():
                if a.get("Hostname") and a.get("AreaID") is not None:
                    _AREA_IDS[a["Hostname"]] = int(a["AreaID"])
            print(f"Loaded {len(_AREA_IDS)} Craigslist area ids")
        except (requests.RequestException, ValueError) as e:
            print(f"ERROR: could not load Craigslist area ids: {e}")
    return _AREA_IDS


def parse_search(payload):
    """Turn Craigslist's search JSON into (guid, title, link, desc) tuples.

    The old ?format=rss feed no longer answers, so this reads the JSON the
    craigslist.org search page itself loads. Each item is an array: [0] is the
    posting id minus data.decode.minPostingId, tagged arrays [6, slug] and
    [13, token] build the post link, [7, text] is the pay, and the last
    element is the title. The search results carry no description.
    """
    data = (payload or {}).get("data") or {}
    base_id = (data.get("decode") or {}).get("minPostingId") or 0
    items = []
    for it in data.get("items") or []:
        if not isinstance(it, list) or not it:
            continue
        tags = {x[0]: x[1] for x in it if isinstance(x, list) and len(x) == 2}
        title = it[-1] if isinstance(it[-1], str) else None
        slug, token = tags.get(6), tags.get(13)
        if not (title and slug and token) or not isinstance(it[0], int):
            continue
        guid = str(base_id + it[0])
        link = f"https://www.craigslist.org/view/d/{slug}/{token}"
        items.append((guid, title.strip(), link, ""))
    return items


def fetch_feed(slug):
    area = area_ids().get(slug)
    if area is None:
        print(f"  {slug}: no Craigslist area id")
        return None
    url = SEARCH_URL.format(area=area)
    try:
        resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
        if resp.status_code != 200:
            print(f"  {slug}: HTTP {resp.status_code}")
            return None
        items = parse_search(resp.json())
        print(f"  {slug}: {len(items)} posts")
        return items
    except (requests.RequestException, ValueError) as e:
        print(f"  {slug}: error {e}")
        return None


def send_email(subject, html_body):
    if not (RESEND_API_KEY and ALERT_EMAIL):
        print("Email skipped: RESEND_API_KEY or ALERT_EMAIL not set")
        return False
    try:
        resp = requests.post("https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            json={"from": FROM_EMAIL, "to": [ALERT_EMAIL], "subject": subject, "html": html_body},
            timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        print(f"Email send failed: {e}")
        return False
    print(f"Email send status: {resp.status_code} {resp.text[:200]}")
    return 200 <= resp.status_code < 300

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
    raw = load_json(SEEN_FILE, {})
    if not isinstance(raw, dict):
        raw = {}
    pending = raw.pop(PENDING, [])  # a list of lead dicts, not a city
    if not isinstance(pending, list):
        pending = []
    pending = [p for p in pending if isinstance(p, dict) and p.get("key") and p.get("tier")]
    seen = prune_seen(normalize_seen(raw))
    alerted = seen.pop(KEYS, {})
    print(f"Loaded {len(cities)} cities from {CITIES_FILE}, {len(seen)} cities in seen.json")

    if not cities:
        print("ERROR: No cities loaded — check data/cities.json exists and is valid JSON")
        seen[KEYS] = alerted
        seen[PENDING] = pending
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
            seen.setdefault(slug, {})
            continue
        first_visit = slug not in seen
        city_seen = seen.setdefault(slug, {})
        for guid, title, link, desc in items:
            if guid in city_seen:
                continue
            city_seen[guid] = _now()
            # First visit to a city only records what is already up, so a fresh
            # or reset seen.json never floods the inbox with old posts.
            if not first_visit:
                new_posts.append({"city": name, "slug": slug, "title": title, "link": link, "desc": desc})
        if idx % 20 == 0:
            print(f"  ... progress {idx}/{len(cities)}, new so far: {len(new_posts)}, blocked: {blocked}")

    print(f"Done: checked {len(cities)}, blocked/no-feed: {blocked}, empty feeds: {empty}, total seen cities now: {len(seen)}")

    if new_posts:
        print(f"{len(new_posts)} new post(s) found.")
    else:
        print("No new posts this run.")
    leads = pick_leads(new_posts, alerted)
    queued = {p["key"] for p in pending}
    leads = [p for p in pending if p.get("key") not in alerted] + \
            [p for p in leads if p["key"] not in queued]
    leads.sort(key=lambda p: p["tier"] != "hot")
    for p in leads:
        print(f"  lead [{p['tier']}] {p['city']}: {p['title']}")

    ok = True
    if leads:
        ok = bool(send_digest(leads))
        if ok:
            for p in leads:
                alerted[p["key"]] = _now()
            pending = []
        else:
            cutoff = _now() - SEEN_DAYS * 86400
            pending = [p for p in leads if p.get("found", _now()) >= cutoff]
            print(f"ERROR: lead email failed; {len(pending)} lead(s) kept to retry next run")
    else:
        print("No buyer posts this run, no email sent.")
        pending = []
    seen[KEYS] = alerted
    seen[PENDING] = pending
    save_json(SEEN_FILE, seen)
    return 0 if ok else 1


def pick_leads(new_posts, alerted):
    """Keep buyers only. A post whose text was already emailed (same ad
    cross-posted to another city, now or in an earlier run) is skipped."""
    leads, dropped, batch = [], {}, set()
    for p in new_posts:
        key = dedupe_key(p["title"], p.get("desc", ""))
        if key in alerted or key in batch:
            dropped["already sent"] = dropped.get("already sent", 0) + 1
            continue
        verdict = classify(p["title"], p.get("desc", ""))
        if verdict["tier"]:
            # Marked as sent only after the email goes out (see main).
            batch.add(key)
            p["tier"] = verdict["tier"]
            p["key"] = key
            p["found"] = _now()
            leads.append(p)
        else:
            kind = verdict["reason"].split(":")[0]
            dropped[kind] = dropped.get(kind, 0) + 1
    cutoff = _now() - SEEN_DAYS * 86400
    for k in [k for k, t in alerted.items() if t < cutoff]:
        del alerted[k]
    print(f"Leads after filter: {len(leads)}; dropped: {dropped}")
    return leads


def _safe_link(link):
    return link if isinstance(link, str) and link.startswith("https://") else "#"


def send_digest(new_posts):
    rows = "".join(
        f'<tr><td style="padding:6px 10px;border-bottom:1px solid #333;">{html.escape(p["city"])}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #333;">{p["tier"].upper()}</td>'
        f'<td style="padding:6px 10px;border-bottom:1px solid #333;"><a href="{html.escape(_safe_link(p["link"]))}">{html.escape(p["title"])}</a></td></tr>'
        for p in new_posts)
    html_body = f"<h2>{len(new_posts)} buyer post(s) on Craigslist computer gigs</h2><table style=\"border-collapse:collapse;width:100%;font-family:sans-serif;\"><tr><th style=\"text-align:left;padding:6px 10px;\">City</th><th style=\"text-align:left;padding:6px 10px;\">Tier</th><th style=\"text-align:left;padding:6px 10px;\">Post</th></tr>{rows}</table>"
    ok = send_email(f"COAI Gig Monitor: {len(new_posts)} buyer post(s)", html_body)
    top = new_posts[:3]
    sms_lines = [f"{len(new_posts)} buyer post(s)."] + [f"{p['city']}: {p['title'][:40]}" for p in top]
    send_sms(" | ".join(sms_lines))
    return ok

if __name__ == "__main__":
    sys.exit(main())
