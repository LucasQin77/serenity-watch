# Serenity Watch

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-green.svg)](https://www.python.org/)

An open-source tracker for **[@aleabitoreddit](https://x.com/aleabitoreddit)** (Serenity) — one of the most influential voices in AI semiconductor investing on X.

If you follow stocks, semiconductors, or AI investing, you've almost certainly seen Serenity on your timeline. With **630,000+ followers** and **international media coverage**, Serenity has earned a reputation as a "supply-chain detective" — digging deep into the complex supply chains behind the AI revolution, spotlighting small, often-unknown companies building essential components for next-generation AI infrastructure, long before they hit the mainstream radar.

<img width="1600" height="900" alt="serenity-display-board-preview" src="https://github.com/user-attachments/assets/9b69ef19-2189-477d-8767-8c1cd4a5b53b" />

**We strongly recommend following [@aleabitoreddit](https://x.com/aleabitoreddit) directly. The original posts are where the real value is.** This project is simply a fan-built tool that reads Serenity's public posts, classifies each stock mention using AI, and organizes the results into a searchable, structured format.

> 💡 **Don't want to set up API keys and run code?** Use [Serenity Watch on Capafy](https://capafy.ai/agent/serenity-watch-public-x-mentions-tracker/2521387714) — it works online with no setup. The subscription covers the cost of building and maintaining the dataset (initial extraction of 6,200+ posts, plus ongoing hourly updates and API infrastructure) — it is not a commercial use of Serenity's content.

---

## Table of Contents

- [What You Get](#-what-you-get)
- [Quick Start](#-quick-start-fork--run)
- [Local Usage](#-local-usage)
- [Architecture](#-architecture)
- [Data Schema](#-data-schema)
- [Notes on LLM Choice](#-notes-on-llm-choice)
- [Notes on Tweet Data Source](#-notes-on-tweet-data-source)
- [Repository Structure](#-repository-structure)
- [Acknowledgments](#-acknowledgments)
- [Disclaimer](#%EF%B8%8F-disclaimer)

---

## 📊 What You Get

**A ready-to-use structured dataset built from Serenity's public posts:**

- 🏷️ **941 stocks tracked** across US, European, Japanese, Taiwanese, and Korean markets
- 📝 **~13,000 structured mentions** extracted from ~6,200 of Serenity's posts (July 2025 – June 2026), with stance labels, reasons, and links to original posts
- 💾 **All data stored as plain JSON files** — plug into your own analysis tools, dashboards, or research pipelines

<img width="1280" height="823" alt="stock-opinion-tracker-readme-demo-animated-light" src="https://github.com/user-attachments/assets/15f19aa4-acaf-447c-a1cf-9d175bca3956" />

**Plus a complete analysis toolchain:**

- 📈 **Interactive HTML dashboards** — daily / weekly / monthly / quarterly views, each stock clickable into a detail page with price chart, stance timeline, and links back to Serenity's original posts
- 🔍 **Stock Q&A script** — query any tracked ticker for a structured analysis (thesis arc, key opinions, bull/bear reasons)
- 🔄 **Hourly auto-updates** via GitHub Actions (requires your own API keys — see [Quick Start](#-quick-start-fork--run))

<img width="10000" height="5625" alt="serenity-case-showcase-preview" src="https://github.com/user-attachments/assets/d2e0de27-df5a-4700-b831-65a9b159c2a3" />

> 💡 If you'd rather skip the setup, [Serenity Watch on Capafy](https://capafy.ai/agent/serenity-watch-public-x-mentions-tracker/2521387714) gives you the same dashboards and Q&A — ready to use in your browser.

---

## 🚀 Quick Start (Fork & Run)

1. **Fork** this repository to your own GitHub account.

2. **Add two secrets** in your fork: Settings → Secrets and variables → Actions → New repository secret.

   | Secret name | Value |
   |---|---|
   | `X_BEARER_TOKEN` | Your [X API](https://developer.x.com) Bearer Token |
   | `ANTHROPIC_API_KEY` | Your [Anthropic](https://console.anthropic.com/) API key |

3. **Enable GitHub Actions** in your fork (Actions tab → "I understand my workflows, go ahead and enable them").

4. **Enable the schedules**: edit both workflow files in `.github/workflows/` and uncomment the `schedule` lines:

   In `hourly-sync.yml` (tweet fetching + LLM extraction, every hour):
   ```yaml
   schedule:
     - cron: "0 * * * *"
   ```

   In `daily-prices.yml` (stock price updates, weekdays after US market close):
   ```yaml
   schedule:
     - cron: "30 20 * * 1-5"
   ```

5. **Run the workflows** manually to verify: Actions → select each workflow → Run workflow.

That's it. Tweets and opinions sync hourly. Prices update once per trading day after market close.

### 🔑 Getting API Keys

**X API** — Sign up at [developer.x.com](https://developer.x.com). Create a Project and App, then generate a Bearer Token under "Keys and tokens". The Bearer Token (App-Only) is sufficient for reading public tweets.

**Anthropic** — Sign up at [console.anthropic.com](https://console.anthropic.com/). Create an API key under Settings → API Keys. The default model is `claude-opus-4-6`. You can switch to a smaller model with `--model` but extraction quality may degrade (see [Notes on LLM Choice](#-notes-on-llm-choice)).

---

## 💻 Local Usage

### Generate an HTML Dashboard

```bash
pip install -r requirements.txt
python scripts/serenity_render.py                    # latest date
python scripts/serenity_render.py 2026-06-01         # specific date
```

Output: `serenity-tracker-{date}.html` — open in any browser. Other languages available via `--lang`.

### Analyze a Single Stock

```bash
python scripts/analyze_stock.py data/db/stocks/NVDA.json
```

Outputs structured JSON: thesis arc phases, stance transitions, bull/bear reasons, price context, frequency trends. Designed to be piped into an LLM for narrative synthesis.

### Run the Full Pipeline Locally

```bash
export X_BEARER_TOKEN="your_token"
export ANTHROPIC_API_KEY="your_key"

python scripts/fetch_tweets.py --user aleabitoreddit   # 1. pull new posts
python scripts/extract.py --since 2026-05-28           # 2. LLM extraction (recent 7 days)
python scripts/build_db.py                             # 3. rebuild stock database
python scripts/prices.py                               # 4. update stock prices
python scripts/verify_data.py                          # 5. validate + write manifest
python scripts/serenity_render.py                      # 6. generate dashboard
```

---

## 🏗️ Architecture

```
fetch_tweets.py  ➜  raw_tweets.json       Pull new posts (X API v2)
                         ↓
extract.py       ➜  extracted.json        LLM stance extraction (Claude, last 7 days)
                         ↓
build_db.py      ➜  db/stocks/*.json      Aggregate per-stock files (zero API, seconds)
                         ↓
prices.py        ➜  prices_cache/         Backfill daily close prices (akshare)
                         ↓
verify_data.py   ➜  manifest.json         Validate data, write health stats
                         ↓
render.py        ➜  .html                 Generate interactive dashboard (zero API)
```

### Key Design Decisions

- **build_db does a full rebuild** every run. It's pure aggregation from extracted.json (takes seconds), and a full rebuild correctly handles stance corrections without incremental-state bugs.
- **extract.py only processes the last 7 days** (`--since`). Failed tweets get up to 7 retries before being dropped.
- **Prices use akshare** (no API key required). akshare covers US-listed stocks. Non-US tickers are attempted but will be marked `not_covered` if akshare cannot fetch them — the dashboard still shows mentions and stances, just without a price chart. Price data is cached in `data/prices_cache/` and committed to the repo for incremental fetching — the first run creates ~300 cache files.
- **render.py is deterministic** — zero LLM calls, zero network calls. Same input always produces the same HTML.
- **Dates are US Eastern** (UTC−4). Tweet syncs run hourly; price updates run once per trading day after US market close (20:30 UTC). The dashboard reflects the latest synced data at the time of generation.

---

## 📋 Data Schema

Each file in `data/db/stocks/{TICKER}.json`:

```
ticker              — stock symbol
company             — company name (from ticker_map)
industry            — sector label (from ticker_map)
exchange, currency  — listing info
first_mention       — earliest post date referencing this stock
last_mention        — most recent post date
total_mentions      — count of all mentions (all types)
price_series[]      — [{date, close}, ...] daily prices
price_status        — "ok" | "partial" | "not_covered"
mentions[]          — each mention contains:
    .date           — YYYY-MM-DD (US Eastern)
    .stance         — "bullish" | "bearish" | "neutral" | null
    .mention_type   — "explicit_stance" | "background_mention" | "comparison"
                      | "quote" | "industry_discussion" | "risk_warning"
    .reasons[]      — stated reasons (original language, never translated)
    .is_risk        — boolean
    .text           — original post text
    .url            — link to Serenity's original post on X
    .engagement     — {likes, retweets, replies}
```

Only `explicit_stance` mentions count as Serenity expressing their own opinion. Background mentions, quotes of others' views, and hypothetical discussions are tracked but excluded from stance statistics.

---

## 🤖 Notes on LLM Choice

The extraction prompts are optimized and validated on **Claude** (Anthropic). The default model is `claude-opus-4-6`, chosen because it handles metaphors, rhetorical questions, and third-party quotes more accurately than smaller models — important for correctly classifying stance in financial social media content where figurative language is common.

You can switch models with `python scripts/extract.py --model claude-sonnet-4-6`, at the risk of more misclassifications.

**Other LLM providers** (OpenAI, Google, etc.): to switch providers, modify the API calls and response parsing in `extract.py`. The current prompts use Claude-specific patterns (XML structured output); you will need to adapt the prompt format and validate extraction quality for your chosen model. Community contributions for alternative providers are welcome.

---

## 🐦 Notes on Tweet Data Source

This project uses the **official [X (Twitter) API v2](https://developer.x.com/en/docs/twitter-api)** to fetch Serenity's public posts. A Bearer Token (App-Only authentication) is sufficient for reading public tweets.

The X API user timeline endpoint returns up to ~3,200 of a user's most recent tweets. Since the repository ships with a pre-built dataset of ~6,200 of Serenity's posts (July 2025 – June 2026), fork users only need incremental fetches going forward.

---

## 📁 Repository Structure

```
serenity-watch/
├── README.md
├── LICENSE                          (MIT)
├── requirements.txt                 (requests, anthropic, akshare)
├── .env.example
├── .gitignore
├── scripts/
│   ├── fetch_tweets.py              — tweet fetcher (X API v2)
│   ├── extract.py                   — LLM stance extraction (Claude)
│   ├── build_db.py                  — aggregation (zero API)
│   ├── prices.py                    — price fetcher (akshare)
│   ├── verify_data.py               — data validation
│   ├── serenity_render.py           — HTML dashboard generator
│   └── analyze_stock.py             — single-stock analysis (for Q&A)
├── .github/workflows/
│   ├── hourly-sync.yml              — hourly tweet + extraction pipeline (2 secrets)
│   ├── daily-prices.yml             — weekday price updates (zero secrets)
│   └── smoke-test.yml               — manual validation (zero API)
├── data/
│   ├── raw_tweets.json              — Serenity's post corpus (~6,200 posts)
│   ├── extracted.json               — structured extractions
│   ├── ticker_map.json              — symbol resolution + metadata
│   └── db/
│       ├── index.json               — stock directory
│       ├── manifest.json            — data health stats
│       └── stocks/*.json            — 941 per-stock files
└── skill/                           — optional: SKILL.md for ClawHub
```

---

## 🙏 Acknowledgments

First and foremost: **thank you to [@aleabitoreddit](https://x.com/aleabitoreddit) (Serenity)**. This entire project exists because of the exceptional quality and depth of Serenity's public analysis. None of this data would exist without Serenity's work. We are deeply grateful and want to be clear: **this project is a tribute, not a substitute. Follow Serenity directly — the original posts are where the real insight is.**

- **[AKShare](https://github.com/akfamily/akshare)** — open-source financial data library (MIT License). Used for fetching US stock prices. **Users are responsible for ensuring their use of AKShare and its underlying data sources complies with applicable terms of service and local regulations. This project makes no representations about the commercial usability of data obtained through AKShare.**

- **[X API v2](https://developer.x.com)** — official Twitter/X API for fetching public tweets. Requires a Developer account and Bearer Token.

- **[Anthropic Claude](https://www.anthropic.com/)** — LLM used for post extraction. Requires a paid API key.

---

## ⚠️ Disclaimer

> **This is an independent, fan-built project — not affiliated with [@aleabitoreddit](https://x.com/aleabitoreddit) (Serenity). It is not investment advice, financial analysis, or a recommendation to buy or sell any security.**
>
> The data is extracted and summarized by AI. It may contain errors, omissions, or misinterpretations. Metaphors, quotes of third-party views, and hypothetical discussions may be incorrectly classified. Always refer to [Serenity's original posts](https://x.com/aleabitoreddit) and verify independently.
>
> Stock price data is provided by third-party sources and may be delayed, incomplete, or inaccurate. Non-US stocks may not have price data available.
>
> The authors and contributors of this project accept no liability for any decisions made based on this data.

---

> 💡 **Prefer a hosted version?** [Serenity Watch on Capafy](https://capafy.ai/agent/serenity-watch-public-x-mentions-tracker/2521387714) offers the same dashboards and stock Q&A online with zero setup. The subscription covers dataset building and ongoing API infrastructure costs — it is not a commercial use of Serenity's content.

---

## 📄 License

[MIT](LICENSE)
