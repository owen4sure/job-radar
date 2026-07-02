"""
Job Radar — upload your resume, get AI-scored job matches.
======================================================================
A tiny self-hosted tool: paste/upload your resume (any language), it uses an
LLM to build your profile, scrapes public job boards (Yourator + LinkedIn guest,
no login), and ranks every job 0-100 against YOUR resume — with a one-line
reason and a Chinese translation for English postings. Dislike ones you don't
want; the tool remembers and steers future matches away from them.

Bring your own LLM (any OpenAI-compatible endpoint). Set:
    OPENAI_BASE_URL   e.g. https://api.openai.com/v1   (or a local proxy)
    OPENAI_API_KEY
    OPENAI_MODEL      e.g. gpt-4o-mini  (default)

Run:  pip install -r requirements.txt  &&  uvicorn app:app --port 8080
Open: http://127.0.0.1:8080
"""
import json
import os
import re
import urllib.parse
import urllib.request

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
PROFILE_PATH = os.path.join(DATA_DIR, "profile.json")
DISLIKES_PATH = os.path.join(DATA_DIR, "dislikes.json")

LLM_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

app = FastAPI(title="Job Radar")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))


# ---------- storage ----------
def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _save(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# ---------- LLM ----------
def _llm(prompt, timeout=60):
    if not LLM_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    req = urllib.request.Request(
        f"{LLM_BASE}/chat/completions",
        data=json.dumps({"model": LLM_MODEL,
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": 0.2}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LLM_KEY}"})
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return r["choices"][0]["message"]["content"]


# ---------- scrapers (public, no login) ----------
def fetch_yourator(terms):
    out = []
    for term in terms:
        try:
            q = urllib.parse.quote(term)
            for page in (1, 2):
                url = f"https://www.yourator.co/api/v4/jobs?term%5B%5D={q}&page={page}"
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                d = json.loads(urllib.request.urlopen(req, timeout=15).read())
                for j in d.get("payload", {}).get("jobs", []):
                    out.append({
                        "source": "Yourator",
                        "title": (j.get("name") or "").strip(),
                        "company": (j.get("company", {}) or {}).get("brand")
                                   or j.get("companyName") or "",
                        "loc": j.get("location") or "",
                        "salary": j.get("salary") or "",
                        "url": "https://www.yourator.co" + (j.get("path") or ""),
                    })
        except Exception:
            pass
    return out


def fetch_linkedin(terms, location="Taiwan"):
    out = []
    for kw in terms:
        try:
            q = urllib.parse.quote(kw)
            loc = urllib.parse.quote(location)
            url = ("https://www.linkedin.com/jobs-guest/jobs/api/"
                   f"seeMoreJobPostings/search?keywords={q}&location={loc}&start=0")
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            raw = urllib.request.urlopen(req, timeout=20).read().decode("utf-8", "ignore")
            titles = re.findall(r'base-search-card__title["\s>]+([^<]+)', raw)
            comps = re.findall(r'base-search-card__subtitle[^>]*>\s*<a[^>]*>\s*([^<]+)', raw)
            locs = re.findall(r'job-search-card__location[^>]*>\s*([^<]+)', raw)
            urls = re.findall(r'base-card__full-link"[^>]*href="([^"]+)"', raw)
            for i, t in enumerate(titles):
                out.append({
                    "source": "LinkedIn",
                    "title": t.strip().replace("&amp;", "&"),
                    "company": (comps[i].strip() if i < len(comps) else ""),
                    "loc": (locs[i].strip() if i < len(locs) else ""),
                    "salary": "",
                    "url": (urls[i].split("?")[0] if i < len(urls)
                            else "https://www.linkedin.com/jobs/search/?keywords=" + q),
                })
        except Exception:
            pass
    return out


# ---------- endpoints ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    prof = _load(PROFILE_PATH, {})
    return templates.TemplateResponse("index.html", {
        "request": request,
        "has_llm": bool(LLM_KEY),
        "profile": prof.get("summary", ""),
        "search_terms": ", ".join(prof.get("terms", [])),
        "location": prof.get("location", "Taiwan"),
        "custom": prof.get("custom", ""),
    })


@app.post("/api/analyze")
async def analyze(req: Request):
    """Take the raw resume text → LLM builds a search profile
    (summary + search keywords + location + fit criteria)."""
    body = await req.json()
    resume = str((body or {}).get("resume", "")).strip()
    custom = str((body or {}).get("custom", "")).strip()
    location = str((body or {}).get("location", "Taiwan")).strip() or "Taiwan"
    if len(resume) < 40:
        return JSONResponse({"ok": False, "error": "resume too short"}, status_code=400)
    prompt = (
        "Read this resume and produce a JSON object for a job-matching engine:\n"
        '{"summary": "<=120 words describing their level, domain, hard skills, and '
        'what roles fit>", "terms": ["3-6 job-search keywords to query boards with"], '
        '"seniority": "intern|junior|mid|senior|lead"}\n'
        + (f"\nThe user ALSO stated hard preferences (respect them): {custom}\n" if custom else "")
        + f"\nRESUME:\n{resume[:6000]}\n\nReturn ONLY the JSON object.")
    try:
        text = _llm(prompt)
        m = re.search(r"\{[\s\S]*\}", text)
        prof = json.loads(m.group(0)) if m else {}
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"LLM failed: {e}"}, status_code=502)
    prof = {"summary": str(prof.get("summary", ""))[:900],
            "terms": [str(t)[:40] for t in (prof.get("terms") or [])][:6] or ["Product Manager"],
            "seniority": prof.get("seniority", ""),
            "custom": custom, "location": location}
    _save(PROFILE_PATH, prof)
    return JSONResponse({"ok": True, **prof})


@app.post("/api/scan")
async def scan(req: Request):
    """Scrape → LLM ranks each job vs the saved profile → return sorted cards."""
    prof = _load(PROFILE_PATH, {})
    if not prof.get("summary"):
        return JSONResponse({"ok": False, "error": "analyze a resume first"}, status_code=400)
    terms = prof.get("terms") or ["Product Manager"]
    jobs = fetch_yourator(terms) + fetch_linkedin(terms, prof.get("location", "Taiwan"))
    # dedup + drop disliked
    dislikes = _load(DISLIKES_PATH, {})
    uniq = {}
    for j in jobs:
        k = f"{j['company']}|{j['title']}".lower()
        if k not in dislikes:
            uniq.setdefault(k, {**j, "key": k})
    pool = list(uniq.values())[:24]
    if not pool:
        return JSONResponse({"ok": True, "jobs": []})
    items = "\n".join(
        f"{i}. {j['title']} | {j['company']} | {j['loc']} | {j.get('salary','')}"
        for i, j in enumerate(pool))
    disliked = [v.get("title", k) for k, v in dislikes.items()][-15:]
    prompt = (
        f"Candidate profile:\n{prof['summary']}\n"
        + (f"\nHard preferences (highest priority): {prof.get('custom')}\n" if prof.get("custom") else "")
        + (f"\nThey disliked these before (score similar ones low):\n- " + "\n- ".join(disliked) + "\n" if disliked else "")
        + "\nScore each job's fit 0-100 (80+=apply now, 60-79=worth a look, <60=skip), "
        "give a <=15-word reason, and if the title is not English add title_zh (a Chinese translation).\n"
        f"{items}\n\n"
        'Return ONLY a JSON array: [{"i":0,"fit":85,"reason":"...","title_zh":"..."}]')
    try:
        text = _llm(prompt, timeout=90)
        arr = json.loads(re.search(r"\[[\s\S]*\]", text).group(0))
        for it in arr:
            i = int(it.get("i", -1))
            if 0 <= i < len(pool):
                pool[i]["fit"] = max(0, min(100, int(it.get("fit", 0))))
                pool[i]["reason"] = str(it.get("reason", ""))[:80]
                tz = str(it.get("title_zh", "") or "")
                if tz and tz != pool[i]["title"]:
                    pool[i]["title_zh"] = tz[:80]
    except Exception:
        for j in pool:
            j.setdefault("fit", 50)
    ranked = sorted([j for j in pool if j.get("fit", 0) >= 55],
                    key=lambda x: -x.get("fit", 0))
    return JSONResponse({"ok": True, "jobs": ranked})


@app.post("/api/dislike")
async def dislike(req: Request):
    body = await req.json()
    key = str((body or {}).get("key", "")).strip().lower()
    if not key:
        return JSONResponse({"ok": False}, status_code=400)
    d = _load(DISLIKES_PATH, {})
    d[key] = {"title": (body or {}).get("title", "")}
    _save(DISLIKES_PATH, d)
    return JSONResponse({"ok": True, "count": len(d)})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
