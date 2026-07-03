# 📡 Job Radar

[English](README.md) · **繁體中文**

上傳你的履歷 → LLM 讀懂後爬公開職缺板,**拿每個職缺跟「你的履歷」逐一評 0–100 分**,
每筆附一句理由。可展開完整 JD(能翻譯)、把好的加進追蹤清單、把爛的按掉——之後越推越準。

免帳號、免資料庫、不追蹤。一個 Python 檔 + 一頁 HTML。自帶你自己的 LLM 金鑰。

![截圖](docs/screenshot.png)

## 為什麼做這個

職缺板是照「它們」的相關度排序,把你淹沒在雜訊裡。Job Radar 讀你**真正的履歷**,
拿每個職缺對「你」評分——「90% · 完美對接你的 AI 產品經理背景」勝過滑 200 筆清單。

## 功能

- **履歷 → 輪廓** — 上傳 **PDF**(或貼文字,任何語言);LLM 抽出你的程度、領域、
  專長,以及要拿去搜職缺的關鍵字。
- **逐缺 AI 適配分** 0–100 + 具體理由——不是關鍵字比對。
- **完整職缺說明**隨點隨看,可**選配翻譯**(設 `TRANSLATE_JD_TO`,例:`Traditional Chinese`)。
- **自訂硬性條件** — 「只要遠端、月薪 7 萬以上、不要金融業」會變成最高優先的配對標準。
- **追蹤清單**(⭐)存起你喜歡的;**不喜歡**(✕)按掉爛的——之後類似職缺分數自動降低。
- **公開來源、免登入** — Yourator API + LinkedIn 訪客搜尋。
- 全部存進 `./data/*.json`,刪掉就重置。

## 快速開始

### 方式 A — Docker(什麼都不用裝)

```bash
cp .env.example .env          # 填你的 OPENAI_API_KEY
docker compose up
```

### 方式 B — Python

```bash
cp .env.example .env          # 填你的 OPENAI_API_KEY
./run.sh                      # 自動建 venv、裝依賴、起服務
```

然後開 **http://127.0.0.1:8080** → 上傳履歷 → **Scan + score**。

## 設定(`.env`)

| 變數 | 預設 | 說明 |
|---|---|---|
| `OPENAI_API_KEY` | — | **必填。** 你的金鑰。 |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | 任何 OpenAI 相容端點(Ollama、LM Studio、Groq、Together、proxy…)。 |
| `OPENAI_MODEL` | `gpt-4o-mini` | 模型名稱。 |
| `JOB_LOCATION` | `Taiwan` | 傳給 LinkedIn 搜尋的地區(例:`Remote`、`London`、`United States`)。 |
| `TRANSLATE_JD_TO` | *(關)* | 有設就在完整 JD 上加翻譯步驟(例:`Traditional Chinese`)。 |
| `PORT` | `8080` | 服務 port。 |

### 用本地 / 其他 LLM

把 `OPENAI_BASE_URL` 指到任何 OpenAI 相容伺服器——例如 Ollama
(`http://localhost:11434/v1`)、LM Studio、Groq、Together,或 Gemini 的 OpenAI
相容 proxy。`OPENAI_MODEL` 一起改。

## 運作原理

1. `POST /api/resume`(PDF)或 `/api/analyze`(文字)→ `{summary, terms}` 存進 `data/profile.json`。
2. `POST /api/scan` — 按 `terms` 爬取 → 去重、濾掉不喜歡的 → LLM 拿前 ~24 筆對你的
   輪廓 + 條件評分 → 排序後的卡片。
3. `POST /api/jd` — 抓某職缺的完整說明(LinkedIn 訪客 API 或職缺頁的 JSON-LD),快取,可翻譯。
4. `POST /api/save` / `/api/dislike` — 追蹤 / 負樣本,持久化。

## 加職缺來源

`app.py` 的 `fetch_yourator()` / `fetch_linkedin()` 各回傳一串
`{source, title, company, loc, salary, url}` dict。照同樣格式加一個 `fetch_yoursite()`
再納入 `scan()` 即可——評分與 UI 都不用改。

## 備註

- 爬蟲只打公開端點、有禮貌;請遵守各站服務條款。
- 104 刻意不爬(Cloudflare + SPA),一直壞。
- 這是獨立、自帶 LLM 的版本。它源自一個個人助理
  ([owen4sure/jarvis](https://github.com/owen4sure/jarvis)),在那裡同一套雷達還會做
  深度公司研究與 LinkedIn 一鍵投遞。

## License

MIT
