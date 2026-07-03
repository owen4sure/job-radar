"""
Job Radar — upload your resume, get AI-scored job matches.
======================================================================
A tiny self-hosted tool. Upload your resume (PDF or paste text, any language);
an LLM builds your matching profile, scrapes public job boards (Yourator +
LinkedIn guest, no login), and ranks every posting 0-100 against YOUR resume —
with a one-line reason. Open a full JD (optionally translated), shortlist the
good ones, and dislike the bad ones so future scans steer away from them.

Bring your own LLM — any OpenAI-compatible endpoint. See .env.example.

Quick start:
    cp .env.example .env   # add your OPENAI_API_KEY
    ./run.sh               # or: pip install -r requirements.txt && uvicorn app:app --port 8080
Open http://127.0.0.1:8080
"""
import json
import os
import re
import urllib.parse
import urllib.request

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(APP_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)
PROFILE_PATH = os.path.join(DATA_DIR, "profile.json")
DISLIKES_PATH = os.path.join(DATA_DIR, "dislikes.json")
SAVED_PATH = os.path.join(DATA_DIR, "saved.json")
JD_CACHE_PATH = os.path.join(DATA_DIR, "jd_cache.json")

# --- config (all overridable via env; see .env.example) ---
LLM_BASE = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
LLM_KEY = os.environ.get("OPENAI_API_KEY", "")
LLM_MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
DEFAULT_LOCATION = os.environ.get("JOB_LOCATION", "Taiwan")   # LinkedIn location query
# If set (e.g. "Traditional Chinese"), a "translate" button appears on each JD.
TRANSLATE_TO = os.environ.get("TRANSLATE_JD_TO", "").strip()
PORT = int(os.environ.get("PORT", "8080"))
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36")

app = FastAPI(title="Job Radar")
templates = Jinja2Templates(directory=os.path.join(APP_DIR, "templates"))


# ---------- storage (atomic) ----------
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


def _key(j):
    return f"{j.get('company','')}|{j.get('title','')}".lower()


# ---------- LLM (any OpenAI-compatible endpoint) ----------
def _llm(prompt, timeout=90, temperature=0.2):
    if not LLM_KEY:
        raise RuntimeError("OPENAI_API_KEY not set")
    req = urllib.request.Request(
        f"{LLM_BASE}/chat/completions",
        data=json.dumps({"model": LLM_MODEL,
                         "messages": [{"role": "user", "content": prompt}],
                         "temperature": temperature}).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {LLM_KEY}"})
    r = json.loads(urllib.request.urlopen(req, timeout=timeout).read())
    return r["choices"][0]["message"]["content"]


def _extract_pdf(raw_bytes, path):
    try:
        import pypdf
        return "\n".join((p.extract_text() or "") for p in pypdf.PdfReader(path).pages).strip()
    except Exception:
        return ""


# ---------- scrapers (public endpoints, no login) ----------
def fetch_104(terms):
    """104 blocks API scraping (Cloudflare) — optional headless-browser fetch.
    Only runs if `playwright` is installed (pip install playwright && playwright
    install chromium). Returns [] otherwise, so the app stays lightweight by default."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return []
    out, seen = [], set()
    js = r"""() => { const res=[];
      document.querySelectorAll(".info-container, [class*='vue-recycle']").forEach(card => {
        const a = card.querySelector("a[href*='/job/']"); if(!a) return;
        const title=(a.innerText||'').trim(); if(!title||title.length>60) return;
        const comp=card.querySelector("a[href*='/company/']:not([href*='/company/search'])");
        const loc=card.querySelector("a[href*='area=']");
        let sal=''; card.querySelectorAll("a[href*='joblist_tag']").forEach(x=>{
          const t=(x.innerText||'').trim(); if(/月薪|年薪|待遇|面議|時薪/.test(t)&&!sal) sal=t; });
        res.push({title, company:(comp?comp.innerText.trim():''),
                  loc:(loc?loc.innerText.trim():''), salary:sal, url:a.href.split('?')[0]}); });
      return res; }"""
    try:
        with sync_playwright() as pw:
            b = pw.chromium.launch(headless=True, args=["--disable-blink-features=AutomationControlled"])
            try:
                pg = b.new_page(user_agent=UA)
                for term in terms[:2]:
                    try:
                        pg.goto(f"https://www.104.com.tw/jobs/search/?keyword={urllib.parse.quote(term)}&order=16",
                                timeout=40000)
                        try:                              # wait for cards (cap 8s) instead of blind sleep
                            pg.wait_for_selector("a[href*='/job/']", timeout=8000)
                        except Exception:
                            pass
                        for j in pg.evaluate(js):
                            u = j.get("url", "")
                            if u and u not in seen and j.get("title"):
                                seen.add(u)
                                out.append({"source": "104", "title": j["title"], "company": j.get("company", ""),
                                            "loc": j.get("loc", ""), "salary": j.get("salary", ""), "url": u})
                    except Exception:
                        pass
            finally:
                b.close()                                 # always close, even on error (no zombie Chromium)
    except Exception:
        pass
    return out


def fetch_yourator(terms):
    out = []
    for term in terms:
        try:
            q = urllib.parse.quote(term)
            for page in (1, 2):
                url = f"https://www.yourator.co/api/v4/jobs?term%5B%5D={q}&page={page}"
                d = json.loads(urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": UA}), timeout=15).read())
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


def fetch_linkedin(terms, location):
    out = []
    for kw in terms:
        try:
            q = urllib.parse.quote(kw)
            loc = urllib.parse.quote(location)
            url = ("https://www.linkedin.com/jobs-guest/jobs/api/"
                   f"seeMoreJobPostings/search?keywords={q}&location={loc}&start=0")
            raw = urllib.request.urlopen(
                urllib.request.Request(url, headers={"User-Agent": UA}), timeout=20
            ).read().decode("utf-8", "ignore")
            # split per card so fields never misalign when one card lacks a company link
            cards = re.split(r'<li>|<div class="base-card', raw)
            for c in cards:
                m = re.search(r'base-search-card__title["\s>]+([^<]+)', c)
                if not m:
                    continue
                comp = re.search(r'base-search-card__subtitle[^>]*>\s*(?:<a[^>]*>)?\s*([^<]+)', c)
                lc = re.search(r'job-search-card__location[^>]*>\s*([^<]+)', c)
                u = re.search(r'href="(https://[a-z]+\.linkedin\.com/jobs/view/[^"?]+)', c)
                jid = re.search(r'jobPosting:(\d+)', c)
                out.append({
                    "source": "LinkedIn",
                    "title": m.group(1).strip().replace("&amp;", "&"),
                    "company": (comp.group(1).strip() if comp else ""),
                    "loc": (lc.group(1).strip() if lc else ""),
                    "salary": "",
                    "url": (u.group(1) if u
                            else "https://www.linkedin.com/jobs/search/?keywords=" + q),
                    "jd_id": (jid.group(1) if jid else ""),
                })
        except Exception:
            pass
    return out


def fetch_jd(job):
    """Full job description text for one posting. LinkedIn guest jobPosting API,
    or the posting page's JSON-LD description for others."""
    try:
        if job.get("source") == "LinkedIn" and job.get("jd_id"):
            raw = urllib.request.urlopen(urllib.request.Request(
                f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job['jd_id']}",
                headers={"User-Agent": UA}), timeout=18).read().decode("utf-8", "ignore")
            m = re.search(r'show-more-less-html__markup[^>]*>([\s\S]*?)</div>', raw)
            if m:
                return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', m.group(1))).strip()[:4000]
        if job.get("url", "").startswith("http"):
            raw = urllib.request.urlopen(urllib.request.Request(
                job["url"], headers={"User-Agent": UA}), timeout=18).read().decode("utf-8", "ignore")
            for block in re.findall(r'<script type="application/ld\+json">([\s\S]*?)</script>', raw):
                try:
                    obj = json.loads(block)
                except Exception:
                    continue
                for cand in (obj if isinstance(obj, list) else [obj]):
                    desc = isinstance(cand, dict) and cand.get("description")
                    if desc:
                        t = re.sub(r'<[^>]+>', ' ', desc).replace("&nbsp;", " ").replace("&amp;", "&")
                        return re.sub(r'\s+', ' ', t).strip()[:4000]
    except Exception:
        pass
    return ""


# ---------- endpoints ----------
@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    prof = _load(PROFILE_PATH, {})
    return templates.TemplateResponse("index.html", {
        "request": request,
        "has_llm": bool(LLM_KEY),
        "profile": prof.get("summary", ""),
        "location": prof.get("location", DEFAULT_LOCATION),
        "custom": prof.get("custom", ""),
        "translate_to": TRANSLATE_TO,
    })


def _build_profile(resume, custom, location):
    prompt = (
        "Read this resume and produce a JSON object for a job-matching engine:\n"
        '{"summary":"<=120 words: their level, domain, hard skills, and what roles fit>",'
        '"terms":["3-6 job-search keywords to query boards with"]}\n'
        + (f"\nThe user also stated hard preferences (respect them): {custom}\n" if custom else "")
        + f"\nRESUME:\n{resume[:6000]}\n\nReturn ONLY the JSON object.")
    text = _llm(prompt)
    obj = json.loads(re.search(r"\{[\s\S]*\}", text).group(0))
    prof = {"summary": str(obj.get("summary", ""))[:900],
            "terms": [str(t)[:40] for t in (obj.get("terms") or [])][:6] or ["Product Manager"],
            "custom": custom, "location": location or DEFAULT_LOCATION}
    _save(PROFILE_PATH, prof)
    return prof


@app.post("/api/resume")
async def upload_resume(file: UploadFile = File(...)):
    """Upload a PDF/txt resume → extract text → LLM builds the matching profile."""
    raw = await file.read()
    name = (file.filename or "resume").lower()
    if name.endswith(".pdf"):
        path = os.path.join(DATA_DIR, "resume.pdf")
        with open(path, "wb") as f:
            f.write(raw)
        text = _extract_pdf(raw, path)
    else:
        text = raw.decode("utf-8", "ignore")
    if len(text) < 40:
        return JSONResponse({"ok": False,
                             "error": "Couldn't read text (scanned PDF? paste text instead)"}, status_code=400)
    prof = _load(PROFILE_PATH, {})
    try:
        prof = _build_profile(text, prof.get("custom", ""), prof.get("location", DEFAULT_LOCATION))
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"LLM failed: {str(e)[:120]}"}, status_code=502)
    return JSONResponse({"ok": True, "filename": file.filename, **prof})


@app.post("/api/analyze")
async def analyze(req: Request):
    """Paste resume text (+ optional custom preferences / location) → build profile."""
    body = await req.json()
    resume = str((body or {}).get("resume", "")).strip()
    custom = str((body or {}).get("custom", "")).strip()
    location = str((body or {}).get("location", "")).strip()
    # allow saving prefs alone (no resume text) once a profile already exists
    prof = _load(PROFILE_PATH, {})
    if len(resume) < 40:
        if prof.get("summary"):
            prof["custom"] = custom
            prof["location"] = location or prof.get("location", DEFAULT_LOCATION)
            _save(PROFILE_PATH, prof)
            return JSONResponse({"ok": True, **prof})
        return JSONResponse({"ok": False, "error": "Paste a resume (or upload a PDF) first"}, status_code=400)
    try:
        prof = _build_profile(resume, custom, location)
    except Exception as e:
        return JSONResponse({"ok": False, "error": f"LLM failed: {str(e)[:120]}"}, status_code=502)
    return JSONResponse({"ok": True, **prof})


@app.post("/api/scan")
async def scan(req: Request):
    """Scrape → LLM ranks each job vs the saved profile → sorted cards."""
    prof = _load(PROFILE_PATH, {})
    if not prof.get("summary"):
        return JSONResponse({"ok": False, "error": "Add a resume first"}, status_code=400)
    terms = prof.get("terms") or ["Product Manager"]
    jobs = fetch_yourator(terms) + fetch_linkedin(terms, prof.get("location", DEFAULT_LOCATION)) + fetch_104(terms)
    if not jobs:
        return JSONResponse({"ok": True, "jobs": [], "note": "No jobs returned (try broader keywords/location)"})
    dislikes = _load(DISLIKES_PATH, {})
    uniq = {}
    for j in jobs:
        k = _key(j)
        if k not in dislikes:
            uniq.setdefault(k, {**j, "key": k})
    pool = list(uniq.values())[:24]
    items = "\n".join(f"{i}. {j['title']} | {j['company']} | {j['loc']} | {j.get('salary','')}"
                      for i, j in enumerate(pool))
    disliked = [v.get("title", k) for k, v in dislikes.items()][-15:]
    prompt = (
        f"Candidate profile:\n{prof['summary']}\n"
        + (f"\nHard preferences (highest priority): {prof.get('custom')}\n" if prof.get("custom") else "")
        + (f"\nThey disliked these (score similar ones low):\n- " + "\n- ".join(disliked) + "\n" if disliked else "")
        + "\nScore each job 0-100 (80+=apply now, 60-79=worth a look, <60=skip), give a "
        "<=15-word reason. If the title is NOT English, add title_zh (its translation).\n"
        f"{items}\n\nReturn ONLY a JSON array: "
        '[{"i":0,"fit":85,"reason":"...","title_zh":"..."}]')
    try:
        arr = json.loads(re.search(r"\[[\s\S]*\]", _llm(prompt)).group(0))
        for it in arr:
            i = int(it.get("i", -1))
            if 0 <= i < len(pool):
                pool[i]["fit"] = max(0, min(100, int(it.get("fit", 0))))
                pool[i]["reason"] = str(it.get("reason", ""))[:80]
                tz = str(it.get("title_zh", "") or "")
                if tz and tz != pool[i]["title"]:
                    pool[i]["title_zh"] = tz[:80]
    except Exception:
        for j in pool:            # LLM down → still show keyword results, unscored
            j.setdefault("fit", None)
    ranked = sorted(pool, key=lambda x: -(x.get("fit") or 0))
    ranked = [j for j in ranked if j.get("fit") is None or j["fit"] >= 55]
    return JSONResponse({"ok": True, "jobs": ranked})


@app.post("/api/jd")
async def get_jd(req: Request):
    """Fetch a posting's full JD (cached). If ?translate and TRANSLATE_JD_TO is set,
    also return a translation."""
    body = await req.json()
    job = (body or {}).get("job") or {}
    key = (str((body or {}).get("key", "")).strip().lower() or _key(job))
    do_translate = bool((body or {}).get("translate")) and bool(TRANSLATE_TO)
    cache = _load(JD_CACHE_PATH, {})
    entry = cache.get(key)
    if not entry:
        if not job:   # fall back to the shortlist if only a key was sent
            job = next((j for j in (_load(SAVED_PATH, {}).values()) if j.get("key") == key), None)
        if not job:
            return JSONResponse({"ok": False, "error": "Open this from a card"}, status_code=404)
        entry = {"jd": fetch_jd(job)}
        cache[key] = entry
        _save(JD_CACHE_PATH, cache)
    jd = entry.get("jd", "")
    if not jd:
        return JSONResponse({"ok": True, "jd": "", "note": "No description found — open the posting link"})
    jd_t = entry.get("jd_translated", "")
    if do_translate and not jd_t:
        try:
            jd_t = _llm(f"Translate this job description into {TRANSLATE_TO}, keep the "
                        f"bullet structure, no commentary:\n\n{jd}", temperature=0.1)
            entry["jd_translated"] = jd_t
            cache[key] = entry
            _save(JD_CACHE_PATH, cache)
        except Exception:
            jd_t = ""
    return JSONResponse({"ok": True, "jd": jd, "jd_translated": jd_t})


@app.post("/api/save")
async def save_job(req: Request):
    """Toggle a job in the shortlist (fetches + stores its JD when saving)."""
    body = await req.json()
    job = (body or {}).get("job") or {}
    key = _key(job)
    if not key.strip("|"):
        return JSONResponse({"ok": False}, status_code=400)
    saved = _load(SAVED_PATH, {})
    if key in saved:
        saved.pop(key, None)
        _save(SAVED_PATH, saved)
        return JSONResponse({"ok": True, "saved": False})
    saved[key] = {**job, "key": key}
    _save(SAVED_PATH, saved)
    return JSONResponse({"ok": True, "saved": True, "count": len(saved)})


@app.get("/api/saved")
def list_saved():
    return JSONResponse({"ok": True, "jobs": list(_load(SAVED_PATH, {}).values())})


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
    uvicorn.run(app, host="0.0.0.0", port=PORT)
