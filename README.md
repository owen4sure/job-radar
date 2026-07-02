# 📡 Job Radar

Upload your resume → an LLM builds your profile → it scrapes public job boards
and **ranks every job 0-100 against *your* resume**, with a one-line reason and
a Chinese translation for English postings. Dislike ones you don't want and it
steers future matches away from them.

No account, no database, no tracking. One Python file + one HTML page. Bring
your own LLM key.

![screenshot](docs/screenshot.png)

## Why

Job boards rank by *their* relevance, not yours, and drown you in noise. Job
Radar reads your actual resume and scores each posting for **you** — "85% · AI
Agent PM, matches your side projects" beats scrolling 200 listings.

## Features

- **Resume → profile**: paste any resume (any language); the LLM extracts your
  level, domain, skills, and the search keywords to query with.
- **Per-job AI fit score** 0-100 with a specific reason — not keyword matching.
- **English → Chinese** title translation on every foreign posting.
- **Custom hard preferences**: "remote only, 70k+, no finance" — treated as the
  top-priority matching standard.
- **Dislike & learn**: ✕ a job and similar ones score lower next time.
- **Public sources, no login**: Yourator API + LinkedIn guest search.

## Quick start

```bash
pip install -r requirements.txt

export OPENAI_API_KEY=sk-...            # required
export OPENAI_BASE_URL=https://api.openai.com/v1   # optional (any compatible endpoint)
export OPENAI_MODEL=gpt-4o-mini         # optional

uvicorn app:app --port 8080
```

Open http://127.0.0.1:8080 → paste your resume → **讓 AI 讀懂我的履歷** →
**開始搜尋 + 評分**.

### Using a local / alternative LLM

Any OpenAI-compatible endpoint works — point `OPENAI_BASE_URL` at Ollama,
LM Studio, Together, Groq, a Gemini OpenAI-compat proxy, etc.

## How it scores

1. `POST /api/analyze` — resume text → `{summary, terms, seniority}` (saved to `data/profile.json`).
2. `POST /api/scan` — scrape by `terms` → dedup, drop disliked → LLM scores the
   top ~24 against your profile + hard preferences → sorted cards.
3. `POST /api/dislike` — remembers a rejection (`data/dislikes.json`) so it
   becomes a negative signal in the next scan.

## Adding job sources

`fetch_yourator()` / `fetch_linkedin()` in `app.py` each return a list of
`{source, title, company, loc, salary, url}` dicts. Add a `fetch_yoursite()`
in the same shape and include it in `scan()` — the scoring/UI need no changes.

## Notes

- Data lives in `./data/*.json` (gitignored). Delete it to reset.
- Scrapers hit public endpoints politely; respect each site's terms of use.
- 104 is intentionally not scraped (Cloudflare + SPA); it breaks constantly.

## License

MIT
