"""
Checks monitor.py's seen-state: no flood on first run, no repeats, no
cross-post repeats, old list-style seen.json still loads. No network.

Run: python3 test_monitor.py
"""
import json
import sys
import tempfile
from pathlib import Path

import monitor

BUYER = ("Website Needed", "Need a simple site for my bakery in Reno.")
SELLER = ("Free website build!", "We build websites for your business.")


def run(feeds, seen_path, cities):
    """One monitor run with fake feeds; returns the leads it would email."""
    sent = []
    monitor.fetch_feed = lambda slug: feeds.get(slug)
    monitor.send_digest = lambda leads: sent.extend(leads) or True
    monitor.time.sleep = lambda s: None
    monitor.main()
    return sent


def main():
    failures = []
    tmp = Path(tempfile.mkdtemp())
    monitor.SEEN_FILE = tmp / "seen.json"
    monitor.CITIES_FILE = tmp / "cities.json"
    cities = [{"name": "Reno", "slug": "reno"}, {"name": "Fresno", "slug": "fresno"}]
    monitor.CITIES_FILE.write_text(json.dumps(cities))

    def item(gid, post):
        return (gid, post[0], f"https://www.craigslist.org/view/d/x/{gid}", post[1])

    # Run 1: fresh seen.json, posts already up. Nothing is emailed.
    feeds = {"reno": [item("a1", BUYER)], "fresno": [item("b1", SELLER)]}
    if run(feeds, monitor.SEEN_FILE, cities):
        failures.append("first run emailed posts that were already up")

    # Run 2: same feed. Nothing new.
    if run(feeds, monitor.SEEN_FILE, cities):
        failures.append("second run re-sent the same posts")

    # Run 3: one new buyer in Reno, the same ad cross-posted to Fresno, one new seller.
    feeds = {"reno": [item("a1", BUYER), item("a2", ("Need a web developer", "Plumbing company site."))],
             "fresno": [item("b1", SELLER), item("b2", ("Need a web developer", "Plumbing company site.")),
                        item("b3", ("Get a website that wins customers", "Award-winning design."))]}
    sent = run(feeds, monitor.SEEN_FILE, cities)
    if [p["title"] for p in sent] != ["Need a web developer"]:
        failures.append(f"run 3 should send one buyer once, sent {[p['title'] for p in sent]}")

    # Run 4: the cross-post shows up again under a new id. Not re-sent.
    feeds["reno"].append(item("a3", ("Need a web developer", "Plumbing company site.")))
    if run(feeds, monitor.SEEN_FILE, cities):
        failures.append("cross-post re-sent in a later run")

    # A post that drops out of the feed and comes back is not re-sent.
    feeds = {"reno": [item("a2", ("Need a web developer", "Plumbing company site."))], "fresno": []}
    run(feeds, monitor.SEEN_FILE, cities)
    feeds["reno"].append(item("a1", BUYER))
    if run(feeds, monitor.SEEN_FILE, cities):
        failures.append("post that left and came back was re-sent")

    # Old list-style seen.json loads and counts as seen.
    monitor.SEEN_FILE.write_text(json.dumps({"reno": ["a1"], "fresno": []}))
    feeds = {"reno": [item("a1", BUYER)], "fresno": []}
    if run(feeds, monitor.SEEN_FILE, cities):
        failures.append("old list-style seen.json not honoured")

    # A failed email keeps the lead, fails the run, and sends it next run, once.
    monitor.SEEN_FILE.write_text(json.dumps({"reno": {}, "fresno": {}}))
    feeds = {"reno": [item("f1", ("Need a logo and site", "Bakery in Reno."))], "fresno": []}
    tries = []
    monitor.fetch_feed = lambda slug: feeds.get(slug)
    monitor.send_digest = lambda leads: tries.append([p["title"] for p in leads]) or False
    code = monitor.main()
    kept = json.loads(monitor.SEEN_FILE.read_text()).get("_pending", [])
    if code != 1 or [p["title"] for p in kept] != ["Need a logo and site"]:
        failures.append(f"failed email did not keep the lead or fail the run (exit {code}, kept {kept})")
    resent = run(feeds, monitor.SEEN_FILE, cities)
    again = run(feeds, monitor.SEEN_FILE, cities)
    if [p["title"] for p in resent] != ["Need a logo and site"] or again:
        failures.append(f"kept lead not sent exactly once after the fix (sent {resent}, then {again})")

    # A missing FROM_EMAIL secret arrives as "" and falls back to a valid sender.
    import os, importlib
    os.environ["FROM_EMAIL"] = ""
    if "@" not in importlib.reload(monitor).FROM_EMAIL:
        failures.append("empty FROM_EMAIL is not replaced with a valid sender")

    # Email body escapes titles.
    captured = {}
    monitor.send_email = lambda subj, body: captured.setdefault("body", body)
    monitor.send_sms = lambda text: None
    import importlib
    real = importlib.reload(monitor)
    real.send_email = lambda subj, body: captured.setdefault("body", body)
    real.send_sms = lambda text: None
    real.send_digest([{"city": "Reno", "tier": "hot", "title": "<script>x</script> site needed",
                       "link": "javascript:alert(1)"}])
    if "<script>" in captured.get("body", "") or "javascript:" in captured.get("body", ""):
        failures.append("email body does not escape titles or bad links")

    # Craigslist search JSON parses into posts with ids, titles and links.
    # The sample is shaped like a real sapi response for Bakersfield (Nov 9 2026),
    # abridged, with one title changed to a buyer post. The first item's id and
    # link were checked against its live post page (post id 7995269400).
    sample = json.loads((Path(__file__).parent / "tests" / "sapi_bakersfield_sample.json").read_text())
    posts = real.parse_search(sample)
    expect = [
        ("7995269400", "Paid Study | Test AI & Earn Up to $600 | Remote",
         "https://www.craigslist.org/view/d/bakersfield-paid-study-test-ai-earn-up/pFj49JDuWsS8HbrnrcMxj6", ""),
        ("7991554056", "Website Needed",
         "https://www.craigslist.org/view/d/bakersfield-website-needed/5L68WoyHJ9MLG4GdtNqvDH", ""),
    ]
    if posts != expect:
        failures.append(f"parse_search gave {posts}")
    if real.parse_search({}) != [] or real.parse_search({"data": {"items": [["junk"]]}}) != []:
        failures.append("parse_search does not tolerate empty or junk payloads")

    for f in failures:
        print("FAIL", f)
    total = 12
    print(f"{total - len(failures)}/{total} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
