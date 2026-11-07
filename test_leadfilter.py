"""
Regression test for leadfilter.py.

REAL_NON_LEADS are titles copied from the live "COAI Lead Digest" emails
(Oct 31 - Nov 7, 2026). Every one was flagged as a lead and every one is
another seller's ad or a job/gig-worker post. None may pass.

REAL_BUYERS are titles from the same digests that read like a business
needing work done. They must still pass.

SYNTHETIC_BUYERS are hand-written, not real posts, to cover common phrasing.

Run: python3 test_leadfilter.py
"""
import sys

from leadfilter import classify, dedupe_key

REAL_NON_LEADS = [
    # Nov 7 digest (identical to Oct 31)
    "Free website rebuild for established local businesses (link in footer)",
    "Website Fixes, Google Sheets & Business Automation — Fri & Sun",
    "Complimentary Website or Web App Development — Applications Open",
    "Free Website Mockup for Weirton Area Small Businesses - No Obligation",
    "AI Voice Receptionist for Los Angeles Businesses",
    "Tech Services: 24/7 Tech Support, Servers, Networking, WiFi, Cat6 runs",
    # Nov 2 - Nov 7 digests
    "Website and Game Tester - Join Fast",
    "BREAK FREE From Marketing Fees - Stop Paying For Emails, Texts & CRM",
    "Retired attorney needed for review",
    "**Join Our Team as a Bilingual Commission-Only Appointment Setter!**",
    "WEBSITE QA TESTING FOR SMALL BUSINESSES & PERSONAL WEBSITES",
    "Go2DomainSales.com - CHOOSE a NAME THAT FITS! OR BUY / SELL A WEBSITE!",
    "Short gig to review website",
    "Free website build!",
    "🛑 Pigeon Forge, Knoxville Website/Marketing - Local (Can Meet) 🛑",
    "Houston Roofing SEO | Get More Leads From Google + AI Search!",
    "Website Review and Feedback (Local Business)",
    "A free business website. You pay hosting. | ALCO",
    "Computer Services, Networking, Security Cameras Systems and Website",
    "Test our website (Virtual, need to hire for tonight & throughout week)",
    "Urgently seeking AI research assistants promote Hydrogrameen",
    "Old or broken business website? We rebuild it and keep it running",
    "Virtual Assistant - Remote (Orange County / L.A) (ORANGE COUNTY / L.A.",
    "Freelance Content Writers Wanted for Blog & Website Projects",
    "Remote Writing Work Available - Articles, Blogs & Web Content",
    "Referral Partner/Lead Generator Wanted for Landscaping/Tree Company",
    "Get a Website That Wins Customers | Award-Winning Design & SEO",
    "Systems integration, website hosting, domain and cloud services",
    "New Business Website - Setup Fee Waived with SALE",
    "Virtual Assistant",
    "Looking for Website & Web App Testers – Share Your Feedback!",
    "HVAC Website Builds - Marin & Bay Area",
    "💻 LOCAL WEBSITE HELP – BRADENTON, LWR & SARASOTA 💻",
    "iPhone Duo App Redesign, Foldable-Ready iOS Apps (15 yrs exp)",
    "AI Voice Agents & Virtual Receptionists for Businesses",
    "Code Loopy - websites, e-commerce, AI voice & office bots",
    "TV Mounting & Network Wiring | Super Easy Online Booking",
    "Houston Salon, Spa & Barber Owners: Let Clients Book Online",
    "Stop Answering the Phone to Book Appointments | Online Booking",
    "San Antonio Salon, Spa & Barber Owners: Online Booking Add-On",
    "Home Based Referral Broker - High Commissions, No Selling",
    "AI Chatbot Development for Websites & Businesses",
    "Review help",
    "Get a FREE AI Chatbot Sales Assistant No Obligation | Web Services",
]

REAL_BUYERS = [
    "Website Needed",
    "Website help",
    "Help with Facebook Ads for my creative website",
    "Website Host for an online sales site Wanted",
]

SYNTHETIC_BUYERS = [
    "Need a website built for my plumbing company",
    "Looking for a web developer - WordPress site rebuild",
    "Need someone to fix my Shopify store",
    "Web designer needed for small restaurant",
    "I need a landing page for my food truck",
    "Can someone set up online booking for my salon",
    "Seeking web developer for church website",
    "Need SEO help for my roofing business",
    "Wix site needs fixing",
]

# Hand-written seller hooks (not real posts) that borrow buyer words.
SYNTHETIC_SELLERS = [
    "Do you need a website? We can help",
    "Need a new website? Call us",
    "Need more customers?",
    "Website for your business - need one?",
]


def main():
    failures = []
    for t in REAL_NON_LEADS + SYNTHETIC_SELLERS:
        r = classify(t)
        if r["tier"]:
            failures.append(f"seller/job passed: {t!r} ({r['reason']})")
    for t in REAL_BUYERS + SYNTHETIC_BUYERS:
        r = classify(t)
        if not r["tier"]:
            failures.append(f"buyer dropped: {t!r} ({r['reason']})")

    # Neutral title + buyer body -> possible; neutral title + pitch body -> dropped.
    r = classify("Web site development", "I need a simple site for my bakery, budget $800")
    if r["tier"] != "possible":
        failures.append(f"body buyer not caught: {r}")
    r = classify("Web site development", "We build fast sites for your business. Call today.")
    if r["tier"]:
        failures.append(f"body pitch passed: {r}")

    # Body pitch or job wording beats buyer words anywhere (PR 1 review).
    body_rejects = [
        ("Web site development", "Need a website? We build sites for your business. Call us today."),
        ("Web site development", "We need website testers for a paid study."),
        ("Website Needed", "We design custom websites for your business, 10 years experience."),
        ("Need help with my website", "Join our team! $25/hr part-time, remote."),
    ]
    for title, body in body_rejects:
        r = classify(title, body)
        if r["tier"]:
            failures.append(f"body exclusion missed: {title!r} / {body!r} ({r['reason']})")
    # Real buyers whose bodies use words the broad seller list would catch.
    body_keeps = [
        ("Web site needed", "We build custom homes and need a website for the business. Affordable please."),
        ("Website help", "Our team needs someone experienced to fix our WordPress site."),
        ("Web site development", "I need a simple site for my bakery. Are you available this week?"),
    ]
    for title, body in body_keeps:
        r = classify(title, body)
        if not r["tier"]:
            failures.append(f"buyer body dropped: {title!r} / {body!r} ({r['reason']})")

    if dedupe_key("Free website build!", "Same text.") != dedupe_key("FREE Website Build", "Same text."):
        failures.append("dedupe_key does not collapse cross-posts")
    if dedupe_key("Website Needed", "Need a site for my bakery in Reno.") == dedupe_key(
            "Website Needed", "Looking for someone to redo our church website."):
        failures.append("dedupe_key merges different buyers with the same title")

    for f in failures:
        print("FAIL", f)
    total = (len(REAL_NON_LEADS) + len(SYNTHETIC_SELLERS) + len(REAL_BUYERS) + len(SYNTHETIC_BUYERS)
             + 3 + len(body_rejects) + len(body_keeps) + 1)
    print(f"{total - len(failures)}/{total} checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
