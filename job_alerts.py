import requests
import json
import time
import schedule
import hashlib
from datetime import datetime
from bs4 import BeautifulSoup
import feedparser

# ── Config ────────────────────────────────────────────────────────────
PUSHOVER_USER  = "uqi5dnudf87ff3uajbja1hih4xy9ky"
PUSHOVER_TOKEN = "aqtk29hfn37j1py82m3qak8txmwfpo"
SEEN_FILE      = "seen_jobs.json"

KEYWORDS = [
    "software engineer", "backend engineer", "sde", "swe",
    "member of technical staff", "software developer",
    "backend developer", "platform engineer", "full stack engineer",
    "fullstack engineer", "systems engineer", "application engineer",
]

# ── Block these — senior / irrelevant roles ───────────────────────────
BLOCK_TITLE = [
    # seniority
    "senior", "sr.", "sr ", "staff engineer", "principal", "lead", "manager",
    "director", "vp ", "head of", "architect", "distinguished", "fellow",
    # roman / numeric levels (Engineer II, SDE-2, SWE 3 etc)
    " ii", " iii", " iv", "-2", "-3", "-4", " 2", " 3", " 4",
    "level 2", "level 3", "level 4", "l2", "l3", "l4", "l5",
    # wrong domains
    "data engineer", "data scientist", "ml engineer", "devops engineer",
    "site reliability", "sre", "security engineer", "qa engineer",
    "test engineer", "embedded engineer", "firmware", "hardware",
    "product manager", "program manager", "scrum", "intern",
]

# Block experience requirements > 3 years
BLOCK_YOE = [
    "4+ years", "5+ years", "6+ years", "7+ years", "8+ years",
    "4-6 years", "5-7 years", "6-8 years", "4 to 6", "5 to 7",
    "minimum 4 years", "minimum 5 years", "at least 4 years", "at least 5 years",
    "4 years of experience", "5 years of experience", "6 years of experience",
]

# ── India / remote only ───────────────────────────────────────────────
INDIA_KEYWORDS = [
    "india", "bangalore", "bengaluru", "hyderabad", "mumbai",
    "pune", "delhi", "gurgaon", "gurugram", "noida", "chennai",
    "remote", "work from home", "wfh", "anywhere",
]

# ── Block locations that are clearly not India/remote ─────────────────
BLOCK_LOCATION = [
    "united states", "usa", "new york", "san francisco", "seattle",
    "london", "uk ", "united kingdom", "canada", "toronto",
    "australia", "sydney", "germany", "berlin", "singapore",
    "dubai", "uae", "japan", "tokyo",
]

# ── All target companies (Greenhouse slugs) ───────────────────────────
GREENHOUSE_SLUGS = {
    "Postman":            "postman",
    "Glean":              "glean",
    "Eightfold AI":       "eightfold",
    "Databricks":         "databricks",
    "Confluent":          "confluent",
    "Abnormal Security":  "abnormalsecurity",
    "Rubrik":             "rubrik",
    "Druva":              "druva",
    "Nutanix":            "nutanix",
    "Freshworks":         "freshworks",
    "Atlassian":          "atlassian",
    "Cloudflare":         "cloudflare",
    "Datadog":            "datadoghq",
    "Twilio":             "twilio",
    "Palo Alto Networks": "paloaltonetworks",
    "CrowdStrike":        "crowdstrike",
    "Cohesity":           "cohesity",
    "Sprinklr":           "sprinklr",
    "Chargebee":          "chargebee",
    "BrowserStack":       "browserstack",
    "Setu":               "setu",
    "Groww":              "groww",
}

# ── All target companies (Lever slugs) ────────────────────────────────
LEVER_SLUGS = {
    "CRED":         "cred",
    "Razorpay":     "razorpay",
    "BrowserStack": "browserstack",
    "Hasura":       "hasura",
    "Meesho":       "meesho",
    "Juspay":       "juspay",
    "Zepto":        "zepto",
    "PhonePe":      "phonepe",
    "Smallcase":    "smallcase",
    "Sarvam AI":    "sarvam-ai",
    "Krutrim":      "krutrim",
}

# ── Seen jobs tracker ─────────────────────────────────────────────────
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)

def job_id(title, company, url=""):
    return hashlib.md5(f"{title}{company}{url}".lower().encode()).hexdigest()

# ── Pushover notification ─────────────────────────────────────────────
def notify(title, message, url=""):
    payload = {
        "token":    PUSHOVER_TOKEN,
        "user":     PUSHOVER_USER,
        "title":    f"🚀 {title}",
        "message":  message,
        "sound":    "cashregister",
        "priority": 0,
    }
    if url:
        payload["url"] = url
        payload["url_title"] = "View & Apply →"
    try:
        r = requests.post("https://api.pushover.net/1/messages.json",
                          data=payload, timeout=10)
        return r.status_code == 200
    except:
        return False

# ── Filters ───────────────────────────────────────────────────────────
def matches_keyword(text):
    t = text.lower()
    return any(k in t for k in KEYWORDS)

def is_junior_role(title, description=""):
    t = title.lower()
    d = description.lower()
    if any(b in t for b in BLOCK_TITLE):
        return False
    if any(y in d for y in BLOCK_YOE):
        return False
    return True

def is_india_or_remote(location_text):
    t = location_text.lower()
    if any(b in t for b in BLOCK_LOCATION):
        return False
    if any(k in t for k in INDIA_KEYWORDS):
        return True
    if not t.strip():
        return True
    return False

def is_relevant(title, location="", description=""):
    return (matches_keyword(title)
            and is_junior_role(title, description)
            and is_india_or_remote(location))

# ── Indeed RSS ────────────────────────────────────────────────────────
def scrape_indeed():
    jobs = []
    queries = [
        ("software+engineer", "Bangalore"),
        ("backend+engineer", "India"),
        ("SDE+product+company", "India"),
        ("software+engineer", "Hyderabad"),
        ("member+of+technical+staff", "India"),
        ("SWE+backend", "India"),
    ]
    for q, loc in queries:
        url = f"https://indeed.com/rss?q={q}&l={loc}&sort=date&fromage=1"
        try:
            feed = feedparser.parse(url)
            for entry in feed.entries[:20]:
                title   = entry.get("title", "")
                company = entry.get("author", "Unknown")
                link    = entry.get("link", "")
                summary = entry.get("summary", "")
                if is_relevant(title, loc, summary):
                    jobs.append({
                        "title":   title,
                        "company": company,
                        "url":     link,
                        "source":  "Indeed",
                        "summary": f"{loc} | {summary[:100]}"
                    })
        except Exception as e:
            print(f"  Indeed error ({q}): {e}")
    return jobs

# ── HN Who's Hiring ───────────────────────────────────────────────────
def scrape_hn():
    jobs = []
    try:
        r = requests.get(
            "https://hn.algolia.com/api/v1/search?query=Ask+HN+Who+is+hiring&tags=story&hitsPerPage=1",
            timeout=10)
        hits = r.json().get("hits", [])
        if not hits:
            return jobs
        story_id = hits[0]["objectID"]
        r2 = requests.get(f"https://hn.algolia.com/api/v1/items/{story_id}", timeout=10)
        story = r2.json()
        for comment in (story.get("children") or [])[:150]:
            text = comment.get("text") or ""
            soup = BeautifulSoup(text, "html.parser")
            clean = soup.get_text(" ")
            if is_relevant(clean[:80], clean) and is_india_or_remote(clean):
                jobs.append({
                    "title":   clean[:70].strip(),
                    "company": "HN Who's Hiring",
                    "url":     f"https://news.ycombinator.com/item?id={comment.get('id','')}",
                    "source":  "Hacker News",
                    "summary": clean[:130]
                })
    except Exception as e:
        print(f"  HN error: {e}")
    return jobs

# ── Remotive ──────────────────────────────────────────────────────────
def scrape_remotive():
    jobs = []
    try:
        r = requests.get(
            "https://remotive.com/api/remote-jobs?category=software-dev&limit=80",
            timeout=10)
        for job in r.json().get("jobs", []):
            title   = job.get("title", "")
            company = job.get("company_name", "")
            url     = job.get("url", "")
            geo     = job.get("candidate_required_location", "")
            if any(x in geo.lower() for x in ["worldwide", "anywhere", "india", "asia", ""]):
                if is_relevant(title, geo, job.get("description", "")):
                    jobs.append({
                        "title":   title,
                        "company": company,
                        "url":     url,
                        "source":  "Remotive",
                        "summary": f"Remote | {geo}"
                    })
    except Exception as e:
        print(f"  Remotive error: {e}")
    return jobs

# ── Greenhouse API ────────────────────────────────────────────────────
def scrape_greenhouse():
    jobs = []
    for company, slug in GREENHOUSE_SLUGS.items():
        try:
            r = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                timeout=10)
            if r.status_code != 200:
                continue
            for job in r.json().get("jobs", []):
                title    = job.get("title", "")
                location = job.get("location", {}).get("name", "")
                url      = job.get("absolute_url", "")
                desc     = job.get("description", "") + job.get("content", "") + job.get("text", "")
                if is_relevant(title, location, desc):
                    jobs.append({
                        "title":   title,
                        "company": company,
                        "url":     url,
                        "source":  "Greenhouse",
                        "summary": f"📍 {location or 'Location not specified'}"
                    })
        except Exception as e:
            print(f"  Greenhouse {company} error: {e}")
    return jobs

# ── Lever API ─────────────────────────────────────────────────────────
def scrape_lever():
    jobs = []
    for company, slug in LEVER_SLUGS.items():
        try:
            r = requests.get(
                f"https://api.lever.co/v0/postings/{slug}?mode=json",
                timeout=10)
            if r.status_code != 200:
                continue
            for job in r.json():
                title    = job.get("text", "")
                location = job.get("categories", {}).get("location", "")
                url      = job.get("hostedUrl", "")
                desc     = job.get("description", "") + job.get("content", "") + job.get("text", "")
                if is_relevant(title, location, desc):
                    jobs.append({
                        "title":   title,
                        "company": company,
                        "url":     url,
                        "source":  "Lever",
                        "summary": f"📍 {location or 'India / Remote'}"
                    })
        except Exception as e:
            print(f"  Lever {company} error: {e}")
    return jobs

# ── Main check ────────────────────────────────────────────────────────
def check_jobs():
    now = datetime.now().strftime('%d %b %H:%M')
    print(f"\n[{now}] Scanning for new jobs...")
    seen      = load_seen()
    new_count = 0

    all_jobs  = []
    all_jobs += scrape_indeed()
    all_jobs += scrape_hn()
    all_jobs += scrape_remotive()
    all_jobs += scrape_greenhouse()
    all_jobs += scrape_lever()

    print(f"  {len(all_jobs)} listings found — checking for new ones...")

    for job in all_jobs:
        jid = job_id(job["title"], job["company"], job["url"])
        if jid in seen:
            continue
        seen.add(jid)
        new_count += 1
        msg = f"{job['company']} [{job['source']}]\n{job.get('summary','')[:120]}"
        ok  = notify(job["title"], msg, job["url"])
        print(f"  {'✓' if ok else '✗'} {job['title']} @ {job['company']}")
        time.sleep(0.5)

    save_seen(seen)
    print(f"  → {new_count} new jobs notified" if new_count else "  → No new listings since last check")

# ── Entry point ───────────────────────────────────────────────────────
if __name__ == "__main__":
    print("=" * 55)
    print("  Piyush's Job Alert System")
    print("  Sources: Indeed · HN · Remotive · Greenhouse · Lever")
    print("  Filter:  Junior SWE roles · India + Remote only")
    print("  Checks:  Every 60 minutes")
    print("=" * 55)

    check_jobs()

    schedule.every(60).minutes.do(check_jobs)
    print("\nRunning... Press Ctrl+C to stop\n")
    while True:
        schedule.run_pending()
        time.sleep(30)