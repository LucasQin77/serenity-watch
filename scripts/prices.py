#!/usr/bin/env python3
"""
prices.py — Serenity Watch price fetcher (NO LLM)

Reads the per-ticker files built by build_db.py and fills in the price fields
that build_db deliberately left empty:
    price_series  (full daily closes, first_mention -> today)  <- the chart data
    price_status  (ok / partial / not_covered)

NOTE: prices.py does NOT bake any return numbers. Per the locked architecture,
render owns ALL derived figures: it computes "gain since first mention"
(series[0].close -> latest close) and any deep-dive horizons straight from
price_series. prices.py is a pure fetch layer.

Design:
  - ALL tickers are attempted through akshare (no API key required).
    akshare covers US-listed stocks. Non-US symbols are
    attempted anyway — if akshare can fetch them, great; if not, the ticker
    is marked "not_covered" (no price chart, but mentions/stance still shown).
  - FULL-SERIES CACHE (data/prices_cache/{price_symbol}.json): first run
    back-fills the complete series from first_mention -> today; later daily
    runs fetch ONLY the gap (last cached day -> today) and append. The series
    written into the stock file is always the COMPLETE line (for the chart).
    The cache also makes us robust to build_db re-runs wiping price_series:
    we just refill from cache, no API calls.
  - Native currency kept as-fetched. NO FX normalization.
  - Raw (unadjusted) close, on purpose: it must line up with the prices
    quoted in the original tweets, which are raw quotes.
  - Scope: prices are fetched only for tickers we actually display =
    (mentioned in the last 90 days)  UNION  (total_mentions >= --min-mentions).

Secrets: NONE. akshare is free and requires no API key.

Run:
    python prices.py --provider-test          # check akshare connectivity
    python prices.py --ticker NVDA            # one ticker (test)
    python prices.py                          # all in-scope tickers (incremental)
    python prices.py --force                  # ignore cache, full re-fetch
    python prices.py --min-mentions 30        # widen/narrow the core set
    python prices.py --asof 2026-06-02        # pin "now" for the 90d window
"""

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
DB_DIR = DATA_DIR / "db"
STOCKS_DIR = DB_DIR / "stocks"
INDEX_PATH = DB_DIR / "index.json"
TMAP_PATH = DATA_DIR / "ticker_map.json"
CACHE_DIR = DATA_DIR / "prices_cache"

DEFAULT_MIN_MENTIONS = 50      # core set ~= the deep-divable tickers; tune with --min-mentions
RECENT_WINDOW_DAYS = 90        # "mentioned in the last 3 months"
RETRY = 3
PACING_SEC = 0.4               # polite pause between symbols


# --------------------------------------------------------------------------- io helpers
def log(m): print(m, flush=True)


def load_json(path, default=None):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"WARNING: {path.name} is corrupt.")
    return default


def save_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- pure logic
def merge_series(old, new):
    """Combine two [{date, close}] lists; newest fetch wins on date collision; sorted asc."""
    by_date = {p["date"]: {"date": p["date"], "close": p["close"]} for p in (old or [])}
    for p in (new or []):
        by_date[p["date"]] = {"date": p["date"], "close": p["close"]}
    return sorted(by_date.values(), key=lambda p: p["date"])


def in_scope(row, min_mentions, asof_date):
    """row = an index.json stock row. Scope = core OR recently-active."""
    if (row.get("total_mentions") or 0) >= min_mentions:
        return True
    lm = row.get("last_mention")
    if lm and lm >= (asof_date - timedelta(days=RECENT_WINDOW_DAYS)).isoformat():
        return True
    return False


# --------------------------------------------------------------------------- provider
class ProviderError(Exception):
    pass


class AkshareProvider:
    name = "akshare"

    def __init__(self):
        try:
            import akshare  # noqa
        except ImportError:
            raise ProviderError("akshare not installed (pip install akshare)")
        self._ak = __import__("akshare")

    @staticmethod
    def _symbol(price_symbol):
        # akshare stock_us_daily takes the PLAIN ticker (e.g. 'NVDA'), no prefix.
        # For non-US symbols (e.g. SIVE.ST), this will likely fail gracefully.
        return price_symbol

    def _fetch_df(self, price_symbol):
        # akshare's internal parser throws IndexError/SyntaxError/etc when the symbol
        # isn't recognized. Treat that as "no data for this symbol" -> caller marks
        # not_covered. Genuine network errors propagate for retry.
        try:
            return self._ak.stock_us_daily(symbol=self._symbol(price_symbol))
        except (IndexError, SyntaxError, KeyError, ValueError, AttributeError, TypeError):
            return None

    def fetch_daily(self, price_symbol, start, end):
        # adjust='' (default) => UNADJUSTED close, to match the raw prices quoted.
        # The date may be a 'date' COLUMN (newer akshare) or the DatetimeIndex (older
        # akshare). reset_index() normalizes both.
        df = self._fetch_df(price_symbol)
        if df is None or len(df) == 0:
            return []
        df = df.reset_index()
        cols = {str(c).lower(): c for c in df.columns}
        ccol = cols.get("close")
        dcol = cols.get("date") or cols.get("index")
        if not ccol or not dcol:
            raise ProviderError(f"akshare: cannot find date/close in {list(df.columns)}")
        out = []
        for _, r in df.iterrows():
            d = str(r[dcol])[:10]                  # 'YYYY-MM-DD'
            if start <= d <= end:
                try:
                    out.append({"date": d, "close": float(r[ccol])})
                except (TypeError, ValueError):
                    continue
        return out


# --------------------------------------------------------------------------- cache
def cache_path(price_symbol):
    safe = price_symbol.replace("/", "_").replace("\\", "_")
    return CACHE_DIR / f"{safe}.json"


def load_cache(price_symbol):
    return load_json(cache_path(price_symbol), default=None)


def save_cache(price_symbol, currency, price_unit, series):
    save_json(cache_path(price_symbol), {
        "price_symbol": price_symbol,
        "currency": currency,
        "price_unit": price_unit,
        "series": series,
        "last_updated": datetime.now(timezone.utc).isoformat(),
    })


# --------------------------------------------------------------------------- fetch one ticker
def fetch_one(stock_doc, tmap, provider, force, today_iso):
    """Returns (series, status, price_unit). Mutates nothing on disk here."""
    sym = stock_doc["ticker"]
    price_symbol = stock_doc.get("price_symbol") or sym
    currency = stock_doc.get("currency") or "USD"
    first_mention = stock_doc.get("first_mention") or today_iso
    price_unit = "GBp" if currency == "GBp" else currency

    tmap_entry = tmap.get(sym) if isinstance(tmap.get(sym), dict) else None

    if tmap_entry and tmap_entry.get("no_price"):
        # known to have no fetchable source — skip the doomed API call
        return [], "not_covered", price_unit

    if provider is None:
        return [], "not_covered", price_unit

    cache = None if force else load_cache(price_symbol)
    cached_series = (cache or {}).get("series", []) if cache else []

    if cached_series:
        last_cached = cached_series[-1]["date"]
        start = (date.fromisoformat(last_cached) + timedelta(days=1)).isoformat()
    else:
        start = first_mention
    end = today_iso

    new = []
    if start <= end:
        last_err = None
        for attempt in range(1, RETRY + 1):
            try:
                new = provider.fetch_daily(price_symbol, start, end)
                last_err = None
                break
            except ProviderError as e:
                last_err = str(e)
                break  # provider-level errors are not retryable
            except Exception as e:
                last_err = f"{type(e).__name__}: {e}"
                time.sleep(2 * attempt)
        if last_err:
            log(f"    {sym} ({price_symbol}) fetch failed: {last_err}")
            if cached_series:
                return cached_series, "partial", price_unit
            return [], "not_covered", price_unit

    series = merge_series(cached_series, new)
    if not series:
        return [], "not_covered", price_unit

    # freshness: ok if the latest close is within ~5 calendar days of today
    latest = date.fromisoformat(series[-1]["date"])
    status = "ok" if (date.fromisoformat(today_iso) - latest).days <= 5 else "partial"

    save_cache(price_symbol, currency, price_unit, series)
    return series, status, price_unit


# --------------------------------------------------------------------------- provider test
def provider_test(args):
    """Quick connectivity check: fetch a few known US tickers through akshare."""
    try:
        provider = AkshareProvider()
    except ProviderError as e:
        log(f"akshare provider unavailable: {e}")
        return

    today = args.asof or date.today().isoformat()
    start = (date.fromisoformat(today) - timedelta(days=21)).isoformat()
    test_symbols = ["NVDA", "AAPL", "TSLA"]

    log(f"as-of {today}; window from {start}\n")
    log("--- akshare connectivity test ---")
    for sym in test_symbols:
        try:
            rows = provider.fetch_daily(sym, start, today)
            log(f"  {sym:6} {len(rows):>3} rows; last={rows[-1] if rows else 'NONE'}")
        except Exception as e:
            log(f"  {sym:6} FAILED — {type(e).__name__}: {e}")
    log("\nNote: akshare covers US-listed stocks. Non-US symbols will be marked 'not_covered'.")


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ticker", default="", help="only this ticker (test)")
    ap.add_argument("--min-mentions", type=int, default=DEFAULT_MIN_MENTIONS)
    ap.add_argument("--asof", default="", help="pin 'today' for the 90d window + freshness (YYYY-MM-DD)")
    ap.add_argument("--force", action="store_true", help="ignore cache; full re-fetch from first_mention")
    ap.add_argument("--provider-test", action="store_true", help="just check akshare connectivity")
    args = ap.parse_args()

    if args.provider_test:
        provider_test(args)
        return

    today_iso = args.asof or date.today().isoformat()
    asof_date = date.fromisoformat(today_iso)

    index = load_json(INDEX_PATH, default=None)
    if not index:
        log(f"No {INDEX_PATH}. Run build_db.py first.")
        sys.exit(1)
    tmap = load_json(TMAP_PATH, default={}) or {}
    rows = index.get("stocks", [])

    # ---- scope
    if args.ticker:
        scope = [r for r in rows if r["ticker"].upper() == args.ticker.upper()]
        if not scope:
            log(f"{args.ticker} not in index."); sys.exit(1)
    else:
        scope = [r for r in rows if in_scope(r, args.min_mentions, asof_date)]
    log(f"In scope: {len(scope)} tickers "
        f"(min_mentions={args.min_mentions}, recent<={RECENT_WINDOW_DAYS}d, asof={today_iso}).")

    # ---- build provider
    provider = None
    try:
        provider = AkshareProvider()
    except ProviderError as e:
        log(f"WARNING: akshare unavailable — all tickers will be 'not_covered'. ({e})")

    counts = {"ok": 0, "partial": 0, "not_covered": 0}
    for i, row in enumerate(scope, 1):
        sym = row["ticker"]
        stock_path = STOCKS_DIR / f"{sym}.json"
        doc = load_json(stock_path, default=None)
        if not doc:
            log(f"    {sym}: no stock file, skipping."); continue

        series, status, price_unit = fetch_one(doc, tmap, provider, args.force, today_iso)
        # write back ONLY the price fields (no baked returns; render derives those)
        doc["price_series"] = series
        doc["price_status"] = status
        doc["price_unit"] = price_unit
        doc["price_updated_at"] = datetime.now(timezone.utc).isoformat()
        save_json(stock_path, doc)

        counts[status] = counts.get(status, 0) + 1
        if i % 10 == 0 or i == len(scope):
            log(f"  {i}/{len(scope)} done")

        time.sleep(PACING_SEC)

    # mirror status into index rows so the board knows without opening each file
    by_ticker = {r["ticker"] for r in scope}
    for r in rows:
        if r["ticker"] in by_ticker:
            sp = STOCKS_DIR / f"{r['ticker']}.json"
            d = load_json(sp, default={})
            r["price_status"] = d.get("price_status", "pending")
    index["meta"]["prices_updated_at"] = datetime.now(timezone.utc).isoformat()
    save_json(INDEX_PATH, index)

    log("")
    log("===== prices summary =====")
    for k in ("ok", "partial", "not_covered"):
        log(f"  {k:<14}: {counts.get(k, 0)}")
    log("==========================")


if __name__ == "__main__":
    main()
