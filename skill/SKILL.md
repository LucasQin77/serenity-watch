---
name: serenity-watch
description: "Serenity Watch tracks @aleabitoreddit's public X posts about stocks. Generates interactive HTML dashboards (daily/weekly/monthly/quarterly) and answers stock questions — thesis narrative evolution, latest stance, bull/bear arguments, risks, mention frequency, price context, with links to original posts. Use for stock reports, opinion dashboards, stance history, bearish or bullish calls, mention tracking, thesis deep-dives, or anything about @aleabitoreddit, Serenity, or stock opinions."
---

# Serenity Watch

---

## ⚠️ COMPLIANCE — READ BEFORE ANYTHING ELSE

This SKILL is a **account-tracking tool**, NOT a stock-analysis or investment-advice tool.

**Mandatory rules for every response:**

1. Every output (HTML dashboard or text answer) MUST include this disclaimer, or a faithful translation in the user's language:
   > This is an aggregation of @aleabitoreddit's public posts, summarized automatically by AI. It may contain errors or omissions and is not guaranteed accurate — always refer to the original posts and verify independently. This does not constitute investment advice of any kind.

2. NEVER say "you should buy/sell", "this stock will go up/down", or anything that constitutes investment advice. Frame everything as: "the account expressed…", "the account mentioned…", "the account's stance is…".

3. NEVER use analyst/trader role labels for the account. Use "stance" (bullish/bearish/neutral), not "recommendation" or "rating".

4. The account owner's gender is unknown. In Chinese, use the gender-neutral pronoun (qi) instead of gendered pronouns. In English use "the account" or "they".

5. Metaphors, quotes of third-party views, and hypothetical discussions do NOT count as stance. If `mention_type` is `background_mention`, `comparison`, or `quote`, do not report it as the account's own opinion.

6. Preserve the original language of the account's tweets, reasons, company names, industry labels, dates, numbers, and currency symbols — never translate these.

---

## Environment Variables

| Variable | Required | Purpose |
|---|---|---|
| `SERENITY_REPO` | Yes | GitHub repo path, e.g. `your-username/serenity-watch` |
| `GITHUB_TOKEN` | No | Only needed if your data repo is private. Public repos work without a token. |

**Script location**: All scripts (serenity_render.py, analyze_stock.py) are in the `scripts/` subdirectory of this SKILL's installation path. Determine this path at the start of execution:

```python
SKILL_SCRIPTS_DIR = os.path.join(os.path.dirname(SKILL_MD_PATH), "scripts")
```

Do NOT guess this path — list the SKILL directory to confirm `scripts/serenity_render.py` exists before calling it.

---

## Data Access (Shared by Both Modes)

Every request begins by checking whether local data is fresh. If not, download fresh data. Both dashboard and Q&A modes read from the same local copy.

### Step 1 — Check freshness and decide whether to download

```python
import requests, json, os, tempfile

TOKEN = os.environ.get("GITHUB_TOKEN", "")
REPO = os.environ.get("SERENITY_REPO", "your-username/serenity-watch")
RAW = f"https://raw.githubusercontent.com/{REPO}/main"
HEADERS = {"Authorization": f"token {TOKEN}"} if TOKEN else {}

WORK = os.path.join(tempfile.gettempdir(), "serenity-work")
DB = os.path.join(WORK, "db")
LOCAL_MANIFEST = os.path.join(DB, "manifest.json")

# Fetch remote manifest (tiny request, always do this)
manifest = json.loads(requests.get(f"{RAW}/data/db/manifest.json", headers=HEADERS).text)
remote_date = manifest["date_range"][1]

# Compare with local cache
need_download = True
if os.path.exists(LOCAL_MANIFEST):
    local = json.loads(open(LOCAL_MANIFEST, encoding="utf-8").read())
    if local.get("date_range", [None, None])[1] == remote_date:
        need_download = False   # Local data is current
```

Tell the user: data covers `manifest["tickers"]` stocks (this is an integer, e.g. 941), latest data: `manifest["date_range"][1]` (a date string). Other manifest fields: `total_mentions` (int), `priced_tickers` (int), `size_mb` (float).

### Step 2 — Download data (only if needed)

Skip this step if `need_download` is False (local cache is current).

```python
if need_download:
    import zipfile, io, shutil

    if os.path.exists(WORK):
        shutil.rmtree(WORK)
    os.makedirs(os.path.join(DB, "stocks"), exist_ok=True)

    # Single HTTP request — download repo zip
    r = requests.get(
        f"https://api.github.com/repos/{REPO}/zipball/main",
        headers={"Authorization": f"token {TOKEN}"},
    )
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))

    # Extract ONLY data/db/ (skip raw_tweets, extracted, scripts, etc.)
    prefix = z.namelist()[0].split("/")[0]
    for name in z.namelist():
        rel = name[len(prefix) + 1:]
        if not rel.startswith("data/db/"):
            continue
        target = os.path.join(DB, rel[len("data/db/"):])
        if name.endswith("/"):
            os.makedirs(target, exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, "wb") as f:
                f.write(z.read(name))
```

After this step, stock data is at `{WORK}/db/stocks/*.json` (where WORK is the temp directory created above).

**Note**: The LLM does NOT need to read these JSON files into context. The render script and analysis script read them directly via Python — the data flows through their process, not through the LLM's context window.

---

## Intent Detection

**→ Dashboard mode** (default): user asks for a report, dashboard, overview, or "run the tracker".
Examples: "show me the dashboard", "run serenity tracker", "latest report", "what's new today".

**→ Q&A mode**: user asks about a specific stock or company.
Examples: "what does Serenity think about NVDA?", "SIVE opinion history", "any bearish calls?".

**→ General analysis mode**: user asks broad questions about the account's overall focus, cross-stock comparisons, industry breakdown, or any question not about a single specific stock.
Examples: "which sectors does the account cover most?", "compare stance on NVDA vs AMD", "what are the most mentioned stocks this month?", "any new stocks flagged recently?".
For these questions, write and execute Python code to analyze the local stock data at `{DB}/stocks/*.json` directly. The Stock JSON Schema section below documents the data structure. Always include the compliance disclaimer in your response.

If ambiguous, default to Q&A if a stock name/ticker is mentioned, General analysis if a cross-stock or aggregate question is asked, otherwise Dashboard.

---

## Mode A: Dashboard Generation

Generates a self-contained interactive HTML file (Daily / Weekly / Monthly / Quarterly views + per-stock detail pages).

### A1 — Determine language

- Detect user's language from conversation.
- Only `en` and `zh` are supported. English is the default and fallback for any inconsistency.
- If user explicitly requests another language, inform them only en/zh are available and fall back to en.

### A2 — Run render

Set the `SERENITY_DB` environment variable (do NOT use the `--db` CLI flag — the script's positional argument parser has a known conflict with `--db` values that don't start with `-`):

```python
import subprocess
os.environ["SERENITY_DB"] = DB   # DB = the path set in Step 2
result = subprocess.run(
    ["python", os.path.join(SKILL_SCRIPTS_DIR, "serenity_render.py"), DATE, "--lang", LANG],
    cwd=WORK, capture_output=True, text=True
)
# Output HTML is in WORK directory
```

- `DATE`: target date `YYYY-MM-DD`. Omit to use latest available date in data.
- `LANG`: `en` or `zh`.
- Output: `serenity-tracker-{DATE}[-{LANG}].html` in the WORK directory.

If the user requests a specific date, compare against `manifest["date_range"][1]`. If their date is newer than available, inform them and use the latest.

### A3 — Deliver

Return the HTML file. Brief note:
- What date the report covers
- How many stocks are tracked
- Data is synced hourly (on the hour, UTC). The current report reflects data as of `manifest["generated_at"]`. To check for newer data, just ask again.
- Users can click any stock for a detail view (price chart + stance history + original tweet links)
- **Include compliance disclaimer**

---

## Mode B: Stock Q&A

The user asks about a specific stock. Run the analysis script, then synthesize a narrative from its output.

### B1 — Identify the ticker

- If user gives a ticker symbol (e.g., "NVDA"), use it directly.
- If user gives a company name (e.g., "Nvidia"), check `index.json`:

```python
index = json.loads(open(os.path.join(DB, "index.json"), encoding="utf-8").read())
# Search for matching company name
```

- If not found: "This stock is not tracked by @aleabitoreddit. The tracker covers {N} stocks, primarily in photonics, CPO, and AI semiconductors."
- If multiple matches: ask user to clarify.

### B2 — Run analysis script

```python
import subprocess
result = subprocess.run(
    ["python", os.path.join(SKILL_SCRIPTS_DIR, "analyze_stock.py"),
     os.path.join(DB, "stocks", f"{TICKER}.json")],
    capture_output=True, text=True
)
analysis = json.loads(result.stdout)
```

The script outputs structured JSON to stdout (~2-3 KB). Read this output — it is the ONLY data the LLM needs in context. Do NOT read the raw stock JSON directly.

### B3 — LLM narrative synthesis

Read the analysis script output and generate a response with **three modules**:

---

#### MODULE 1: Latest Opinion (1 item)

Use `latest_stance` from the script output (the most recent `explicit_stance` mention — NOT the most recent mention of any type). Present:
- Date + stance
- Original tweet text (in original language, do NOT translate)
- Link to original tweet

Format:
```
━━ Latest Opinion ━━
[{date}] {stance}: "{text}" → [Original post](url)
```

---

#### MODULE 2: Narrative Arc (core value — LLM synthesis)

This is the key module. Write a **3–5 sentence macro-level narrative** that explains HOW the account is constructing their thesis about this stock and how that thesis has evolved over time.

Read `arc_phases`, `transitions`, `bull_reasons`, `bear_reasons`, `frequency_trend`, and `conviction_trend` from the script output.

**Synthesis instructions:**

1. Identify the thesis phases:
   - Discovery: first noticed the stock, initial logic
   - Conviction building: catalysts confirmed, theory → evidence
   - Expansion: new dimensions added (e.g., valuation arguments on top of tech thesis)
   - Wavering: if stance weakened or reversed

2. Spot narrative pivot points: compare `top_reasons` across phases.
   - New reason appearing = thesis expansion
   - Reason disappearing = narrative narrowing
   - Reason shifting category (technical → commercial → valuation) = thesis maturation

3. Assess current thesis state:
   - Early exploration: few mentions, single reason
   - Conviction rising: frequency increasing, reasons diversifying
   - Mature/stable: high frequency but no new reasons
   - Wavering: risk mentions increasing, or bearish stances appearing

4. Write as a coherent paragraph, NOT a bullet list. Embed original `top_reasons` text as inline quotes where they illustrate the narrative. State the tracking period and total stance count for context.

**Example output:**
> The account first flagged SIVE in Dec 2025 based on a structural thesis — "chokepoint investment theory" — arguing CPO is an unavoidable bottleneck in AI infrastructure. Over the next three months, the narrative shifted from theoretical to evidence-based, citing specific design wins as confirmation. From May 2026 onward, a valuation dimension emerged ("still undervalued vs TAM"), signaling thesis expansion from pure technology conviction to price-opportunity argument. Across 7 months and 142 stances, conviction has remained consistently high with rising mention frequency. (Tracked: 2025-12 to 2026-06, 142 explicit stances, 128 bullish / 8 bearish / 6 neutral)

---

#### MODULE 3: Key Opinions (3 items)

Select 3 from `key_candidates` in the script output. Selection criteria — choose the 3 that best represent the thesis evolution, NOT the 3 most frequent:

| Priority | What to pick | Why |
|---|---|---|
| 1st | Thesis origin or biggest transition | Defines the starting point or inflection |
| 2nd | Thesis expansion / catalyst confirmation | Shows evolution |
| 3rd | High conviction or most recent significant | Shows current state |

For each, present:
- Date + original tweet text (original language, do NOT translate)
- Link to original tweet
- One sentence explaining WHY this moment matters for the thesis arc

Format:
```
━━ Key Opinions (3) ━━
1⃣ [{date}] "{text}" → [Original post](url)
   Why: {one sentence — e.g., "Thesis origin: first articulation of the chokepoint argument"}

2⃣ [{date}] "{text}" → [Original post](url)
   Why: {e.g., "Catalyst confirmation: first evidence-based mention citing design wins"}

3⃣ [{date}] "{text}" → [Original post](url)
   Why: {e.g., "Thesis expansion: first time adding valuation argument"}
```

---

#### RISKS (if any)

If `risk_reasons` or `bear_reasons` are non-empty, append a brief risk section:
```
━━ Risks Mentioned by Account ━━
⚠️ {reason} ({count} mentions, latest {date}) → [Original post](sample_url)
```

If no risks: omit this section entirely (do NOT say "no risks" — that could sound like investment assurance).

---

#### DISCLAIMER (mandatory, always last)

```
━━━━━━━━━
⚠️ The above is an aggregation of @aleabitoreddit's public posts,
summarized automatically by AI. It may contain errors or omissions.
Always refer to the original posts and verify independently.
This does not constitute investment advice of any kind.
```

---

### B4 — Follow-up handling

After presenting the analysis, the user may ask follow-ups in the same session:

- "Show me more posts about this stock" → Extract additional mentions from the local data, present chronologically with links
- "Compare with another stock" → Run analyze_stock.py on the second stock, present side by side
- "Generate the full dashboard" → Switch to Mode A using already-downloaded data
- "What about {another stock}?" → Run analyze_stock.py on the new stock

Since data is already downloaded, all follow-ups are instant (no re-download).

---

## Stock JSON Schema Reference

Each file in `data/db/stocks/{TICKER}.json`:

```
ticker, company, industry, exchange, currency
first_mention, last_mention, total_mentions
price_series[]     — {date, close}
price_status       — "ok" | "not_covered" | "error" | null
mentions[]         — each has:
    .date           — YYYY-MM-DD (US Eastern)
    .stance         — "bullish" | "bearish" | "neutral" | null
    .mention_type   — "explicit_stance" | "background_mention" | "comparison" | "quote" | "industry_discussion" | "risk_warning"
    .reasons[]      — stated reasons (original language)
    .is_risk        — boolean
    .text           — tweet text (original language, do NOT translate)
    .url            — link to original tweet
    .engagement     — {likes, retweets, replies}
```

Only `explicit_stance` counts as the account expressing their own opinion. All other mention_types are informational context only.

---

## Language Rules

- **Supported**: `en` (default), `zh` — both built into render script and analysis output
- **Fallback**: English. If anything is missing or inconsistent, use English.
- **Never translate**: tweet text, reasons, company names, industry labels, dates, numbers, currency symbols.
- **Q&A responses**: respond in user's language, but keep the above items in original language. When responding in Chinese, keep the account's original English tweet text and reasons as-is, with a parenthetical note that the original is preserved.
- **count_unit**: in Chinese the count unit is "ci" (times); in English it is an empty string — do not add a unit word.

---

## Error Handling

- **GitHub API fails**: tell user data source is temporarily unavailable, suggest trying later. Do not expose token or repo URL.
- **Stock not found**: "This stock is not currently tracked. The tracker focuses on stocks discussed by @aleabitoreddit, primarily in photonics, CPO, and AI semiconductors."
- **Render script fails**: verify data was downloaded correctly (stocks/ should contain JSON files). If issue persists, offer Q&A mode as fallback.
- **Stale data**: if `manifest["generated_at"]` is >48 hours old, warn user data may not reflect the account's most recent posts.
- **Few mentions**: if a stock has <3 explicit stances, the analysis script returns `status: "no_explicit_stance"` or minimal data. Tell user: "This stock has only brief or background mentions — not enough data for a thesis analysis. Here's what's available: [present raw latest_mention]."
