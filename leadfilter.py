"""
Buyer-intent filter for Craigslist computer-gig posts.

The cpg section is mostly other agencies advertising (free website offers,
AI receptionist pitches, SEO packages) and employers hiring testers, writers
or assistants. Neither is a COAI customer. A post only counts as a lead when
it reads like someone who needs work done for their own business.

classify(title, body="") -> {"tier": "hot" | "possible" | None, "reason": str}

  hot       buyer voice in the title ("Website Needed", "need a web developer")
  possible  neutral title, buyer voice in the body
  None      seller ad, job/gig-worker post, off-topic, or no buyer signal

Order matters: seller and job checks run first, so a pitch that happens to
contain "need" ("Need a website? We build them") is still dropped.
"""
import re

# The post has to be about something COAI sells.
TOPIC = re.compile(
    r"\b(web\s?site|web|site|landing page|app|wordpress|shopify|wix|squarespace|"
    r"seo|google|ads?|domain|host(ing)?|chat\s?bot|ai|receptionist|booking|crm|"
    r"e-?commerce|online store|developer|designer|programmer)\b"
)

# Seller voice: the poster is offering a service.
SELLER = [
    r"\bwe (build|rebuild|design|develop|create|make|offer|fix|do|help|specialize)\b",
    r"\bi (build|design|develop|create|make|offer|fix|do|specialize)\b",
    r"\bi'?ll\b", r"\bi will\b", r"\bhire me\b", r"\bmy services\b", r"\bour services\b",
    r"\byour (business|website|site|company|customers|clients|shop|brand)\b",
    r"\bfor (your|local|small) business", r"\bfor businesses\b", r"\bbusinesses\b",
    r"\bowners\s*:", r"\bget (a|more|your)\b",
    r"\bfree\b", r"\bcomplimentary\b", r"\bno obligation\b", r"\bno monthly\b",
    r"\bfee waived\b", r"\baffordable\b", r"\bstarting at\b", r"\bdiscount\b", r"\bsale\b",
    r"\baward[- ]winning\b", r"\b\d+\+? ?(yrs|years)\b", r"\bexperienced?\b",
    r"\bstop (paying|answering)\b", r"\bbreak free\b", r"\blet clients\b",
    r"\bservices?\b", r"\bsolutions?\b", r"\bagency\b", r"\bdevelopment for\b",
    r"\bbuilds\b", r"\badd-?on\b", r"\bapplications open\b", r"\blink in footer\b",
    r"\bcan meet\b", r"\bcall (now|today)\b", r"\bbuy\b.*\bsell\b",
    r"\b[a-z0-9-]+\.(com|net|io|co|ai)\b",
    r"\bai (voice|chatbot|agents?)\b.*\bfor\b",
    # Questions aimed at the reader are ad hooks ("Need a website? Call us").
    r"\b(do|are) you\b", r"\btired of\b", r"\bwe can\b", r"\b(call|text|contact) us\b",
    r"\b(need|want)(s)? (a|an|more) [\w\s-]{0,25}\?",
]

# Job / gig-worker posts: they want to hire a worker, not buy a build.
JOB = [
    r"\btest(er|ers|ing)?\b", r"\breview(er)?s?\b.*\b(feedback|gig)\b", r"\bfeedback\b",
    r"\brater\b", r"\bstudy\b", r"\bsurvey\b", r"\bparticipant\b", r"\bfocus group\b",
    r"\bwriters?\b", r"\bwriting\b", r"\bcontent creators?\b",
    r"\bjoin (our|the|fast)\b", r"\bteam\b", r"\bappointment setter\b",
    r"\bvirtual assistant\b", r"\bassistants?\b", r"\breferral\b", r"\blead generator\b",
    r"\bbroker\b", r"\bcommission\b", r"\bearn\b", r"\$\s?\d+\s?(/|per)\s?h(ou)?r",
    r"\bremote work\b", r"\bpart[- ]time\b", r"\bfull[- ]time\b", r"\bpaid\b",
    r"\battorney\b", r"\bpromote\b", r"\bresearch\b",
]

# Buyer voice: the poster needs something built or fixed.
BUYER = [
    r"\b(i|we) need\b", r"\bneed(ed|s)?\s+(a|an|my|our|someone|somebody|help|website|web|"
    r"site|developer|designer|programmer|freelancer)\b",
    r"\b(web\s?site|website|web|site|app|developer|designer|programmer|wordpress|shopify|"
    r"wix|squarespace|seo|host|expert|store|logo)\b[\w\s/&-]{0,30}\b(needed|wanted)\b",
    r"\bneed\b[\w\s]{0,15}\bhelp\b", r"\bneeds? (fixing|fixed|work|help|updating|redesign)\b",
    r"\bwant(ed)? (someone|somebody|a|an)\b",
    r"\blooking for (a |an )?(web|website|developer|designer|programmer|freelancer|someone|"
    r"somebody|help)\b",
    r"\bseeking (a |an )?(web|website|developer|designer|programmer|freelancer)\b",
    r"\bhelp (with|building|fixing|setting up)\b", r"^website help$", r"\bwebsite help\b$",
    r"\b(build|fix|redo|redesign|update) (my|our)\b", r"\bmy\b[\w\s]{0,20}\b(website|site|store|"
    r"shop|app|business|company)\b", r"\bcan (someone|anyone)\b", r"\bwho can\b", r"\bhire (a|an)\b",
]

SELLER_RE = [re.compile(p) for p in SELLER]
JOB_RE = [re.compile(p) for p in JOB]
BUYER_RE = [re.compile(p) for p in BUYER]

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿]")


def _norm(text):
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _first(patterns, text):
    for p in patterns:
        if p.search(text):
            return p.pattern
    return None


def _looks_like_ad(raw_title):
    """Shouting or emoji titles are almost always ads."""
    if EMOJI.search(raw_title or ""):
        return "emoji in title"
    letters = [c for c in (raw_title or "") if c.isalpha()]
    if len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.6:
        return "all-caps title"
    if (raw_title or "").count("!") >= 2 or "**" in (raw_title or ""):
        return "hype punctuation"
    return None


def classify(title, body=""):
    t = _norm(title)
    b = _norm(body)
    if not t:
        return {"tier": None, "reason": "empty title"}

    ad = _looks_like_ad(title)
    if ad:
        return {"tier": None, "reason": f"seller ad: {ad}"}

    hit = _first(SELLER_RE, t)
    if hit:
        return {"tier": None, "reason": f"seller ad: /{hit}/"}
    # A pitch question in the title ("Old or broken website?") is an ad hook.
    if "?" in t and not _first(BUYER_RE, t):
        return {"tier": None, "reason": "seller ad: hook question"}

    hit = _first(JOB_RE, t)
    if hit:
        return {"tier": None, "reason": f"job post: /{hit}/"}

    if not TOPIC.search(t) and not TOPIC.search(b):
        return {"tier": None, "reason": "off-topic"}

    if _first(BUYER_RE, t):
        return {"tier": "hot", "reason": "buyer voice in title"}

    if b:
        if _first(SELLER_RE, b) and not _first(BUYER_RE, b):
            return {"tier": None, "reason": "seller ad: body pitch"}
        if _first(BUYER_RE, b):
            return {"tier": "possible", "reason": "buyer voice in body"}

    return {"tier": None, "reason": "no buyer signal"}


def is_lead(title, body=""):
    return classify(title, body)["tier"] is not None


def dedupe_key(title):
    """Same ad cross-posted to many cities collapses to one entry."""
    return re.sub(r"[^a-z0-9]+", " ", _norm(title)).strip()
