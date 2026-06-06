#!/usr/bin/env python3
"""
fetch_tweets.py — Serenity Watch, pipeline step 1

Pulls tweets for a single X user (default: aleabitoreddit) using the official
X (Twitter) API v2.

Auth:
  - Bearer Token (App-Only). Sufficient for reading any public account's tweets.
  - Set env var X_BEARER_TOKEN. Get one from https://developer.x.com.

Design:
  - Endpoint: GET /2/users/:id/tweets  (paginated, up to 100/page)
  - Incremental: uses since_id (saved in data/state.json) to only fetch new
    tweets on each run. --backfill ignores state and pulls full history.
  - Idempotent: de-dupes by tweet_id when merging into data/raw_tweets.json.
  - Self-threads kept: replies to themselves are labeled 'self_thread'.
    Replies to others are labeled 'reply' (kept; LLM in extract.py decides
    whether they contain substantive views).
  - Output schema matches exactly what extract.py and build_db.py expect.

Note: the X API user timeline endpoint returns up to ~3,200 of a user's most
recent tweets. The repository ships with a pre-built dataset (~6,200 tweets)
so fork users only need incremental fetches going forward.

Run:
    export X_BEARER_TOKEN="your_token_here"
    python fetch_tweets.py                      # incremental (default)
    python fetch_tweets.py --backfill           # ignore state, pull full history
    python fetch_tweets.py --user someoneelse   # different handle
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

# ----------------------------------------------------------------------------- config
API_BASE = "https://api.x.com/2"
DEFAULT_USER = "aleabitoreddit"

SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parent / "data"
RAW_PATH = DATA_DIR / "raw_tweets.json"
STATE_PATH = DATA_DIR / "state.json"

PAGE_SLEEP_SEC = 1.0              # stay well under rate limits
MAX_PAGES_SAFETY = 200            # 200 * 100 = 20k tweets hard cap
MAX_RESULTS_PER_PAGE = 100        # X API v2 maximum

# tweet.fields to request
TWEET_FIELDS = ",".join([
    "text", "created_at", "conversation_id", "in_reply_to_user_id",
    "public_metrics", "entities", "lang", "referenced_tweets",
])
EXPANSIONS = "referenced_tweets.id,in_reply_to_user_id"
USER_FIELDS = "username"


# ----------------------------------------------------------------------------- helpers
def log(msg: str) -> None:
    print(msg, flush=True)


def get_bearer_token() -> str:
    token = os.environ.get("X_BEARER_TOKEN", "").strip()
    if not token:
        log("ERROR: environment variable X_BEARER_TOKEN is not set.")
        log('Get a Bearer Token from https://developer.x.com')
        sys.exit(1)
    return token


def load_json(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log(f"WARNING: {path.name} was corrupt; ignoring it.")
    return default


def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def auth_session(token: str) -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def iso_to_twitter_date(iso_str: str) -> str:
    """Convert X API v2 ISO 8601 ('2026-06-04T08:28:16.000Z') to the legacy
    Twitter format ('Thu Jun 04 08:28:16 +0000 2026') that extract.py and
    build_db.py expect."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%a %b %d %H:%M:%S %z %Y")
    except (ValueError, AttributeError):
        return iso_str  # pass through if already in legacy format


# ----------------------------------------------------------------------------- user lookup
def resolve_user_id(s: requests.Session, username: str) -> str:
    """GET /2/users/by/username/:username -> user id."""
    url = f"{API_BASE}/users/by/username/{username}"
    r = s.get(url, timeout=30)
    if r.status_code == 200:
        data = r.json().get("data", {})
        uid = data.get("id")
        if uid:
            return uid
    log(f"  Failed to resolve @{username}: HTTP {r.status_code} — {r.text[:200]}")
    sys.exit(1)


# ----------------------------------------------------------------------------- tweet mapping
def _build_user_map(includes: dict) -> dict:
    """Build {user_id: username} from the includes.users block."""
    m = {}
    for u in (includes.get("users") or []):
        m[u.get("id")] = u.get("username", "")
    return m


def _extract_cashtags(entities: dict) -> list:
    """Extract cashtag symbols from X API v2 entities."""
    out = []
    for c in (entities.get("cashtags") or []):
        tag = c.get("tag", "")
        if tag:
            out.append(tag)
    return out


def _ref_type_id(referenced_tweets: list, ref_type: str) -> str | None:
    """Get the tweet id for a given reference type (replied_to / quoted / retweeted)."""
    for ref in (referenced_tweets or []):
        if ref.get("type") == ref_type:
            return ref.get("id")
    return None


def slim_tweet(t: dict, kind: str, user_map: dict, owner_username: str) -> dict:
    """Map X API v2 tweet object to the schema extract.py/build_db.py expect."""
    metrics = t.get("public_metrics") or {}
    entities = t.get("entities") or {}
    refs = t.get("referenced_tweets") or []

    in_reply_to_uid = t.get("in_reply_to_user_id")
    in_reply_to_username = user_map.get(in_reply_to_uid, "") if in_reply_to_uid else ""

    tweet_id = t.get("id")
    return {
        "tweet_id": tweet_id,
        "kind": kind,
        "url": f"https://x.com/{owner_username}/status/{tweet_id}",
        "created_at": iso_to_twitter_date(t.get("created_at", "")),
        "text": t.get("text", ""),
        "lang": t.get("lang"),
        "like_count": metrics.get("like_count"),
        "retweet_count": metrics.get("retweet_count"),
        "reply_count": metrics.get("reply_count"),
        "quote_count": metrics.get("quote_count"),
        "view_count": metrics.get("impression_count"),
        "bookmark_count": metrics.get("bookmark_count"),
        "conversation_id": t.get("conversation_id"),
        "is_reply": bool(_ref_type_id(refs, "replied_to")),
        "in_reply_to_id": _ref_type_id(refs, "replied_to"),
        "in_reply_to_username": in_reply_to_username,
        "author_username": owner_username,
        "is_retweet": bool(_ref_type_id(refs, "retweeted")),
        "is_quote": bool(_ref_type_id(refs, "quoted")),
        "quoted_tweet_id": _ref_type_id(refs, "quoted"),
        "retweeted_tweet_id": _ref_type_id(refs, "retweeted"),
        "entities": entities,
        "cashtags": _extract_cashtags(entities),
    }


def reply_kind(t: dict, owner_username: str) -> str:
    refs = t.get("referenced_tweets") or []
    is_reply = bool(_ref_type_id(refs, "replied_to"))
    if not is_reply:
        return "post"
    in_reply_to_uid = t.get("in_reply_to_user_id")
    # self-thread: the tweet is a reply AND in_reply_to is the owner themselves
    # We check this via the user_map after fetching, but here we also keep the
    # owner's own user_id for comparison. For initial classification, we'll
    # refine in the fetch loop where we have the user_map.
    return "reply"  # will be refined to "self_thread" in fetch loop


# ----------------------------------------------------------------------------- main fetch
def fetch(username: str, backfill: bool) -> None:
    token = get_bearer_token()
    s = auth_session(token)

    # resolve username -> user_id
    user_id = resolve_user_id(s, username)
    log(f"Resolved @{username} -> user_id {user_id}")

    # load state for incremental mode
    state = load_json(STATE_PATH, {})
    since_id = None if backfill else state.get("newest_tweet_id")

    # fallback: if no state but raw_tweets.json exists, infer since_id from it
    if not backfill and not since_id:
        existing = load_json(RAW_PATH, [])
        if existing:
            max_id = max(
                (t["tweet_id"] for t in existing if t.get("tweet_id")),
                key=lambda x: int(x) if str(x).isdigit() else 0,
                default=None,
            )
            if max_id:
                since_id = max_id
                log(f"  state.json missing; inferred since_id={since_id} from raw_tweets.json")

    mode = "BACKFILL (full history)" if backfill else "INCREMENTAL"
    log(f"Mode: {mode}")
    if since_id:
        log(f"  since_id={since_id} (fetching only newer tweets)")

    # pagination
    pagination_token = None
    page = 0
    seen_total = 0
    kept_new = []
    kind_counts = {}
    newest_id_this_run = None

    while True:
        page += 1
        if page > MAX_PAGES_SAFETY:
            log("Hit page safety limit; stopping.")
            break

        params = {
            "max_results": MAX_RESULTS_PER_PAGE,
            "tweet.fields": TWEET_FIELDS,
            "expansions": EXPANSIONS,
            "user.fields": USER_FIELDS,
        }
        if since_id:
            params["since_id"] = since_id
        if pagination_token:
            params["pagination_token"] = pagination_token

        url = f"{API_BASE}/users/{user_id}/tweets"
        try:
            r = s.get(url, params=params, timeout=30)
        except requests.RequestException as e:
            log(f"  network error on page {page}: {e}; retrying once in 3s")
            time.sleep(3)
            try:
                r = s.get(url, params=params, timeout=30)
            except requests.RequestException as e2:
                log(f"  retry failed: {e2}; stopping (partial data preserved).")
                break

        if r.status_code == 429:
            # rate limited — wait and retry
            retry_after = int(r.headers.get("retry-after", 60))
            log(f"  rate limited; waiting {retry_after}s")
            time.sleep(retry_after)
            continue

        if r.status_code != 200:
            log(f"  HTTP {r.status_code} on page {page}: {r.text[:300]}")
            break

        payload = r.json()
        tweets = payload.get("data") or []
        includes = payload.get("includes") or {}
        meta = payload.get("meta") or {}

        if not tweets:
            log(f"  page {page}: 0 tweets, done.")
            break

        user_map = _build_user_map(includes)
        seen_total += len(tweets)

        for t in tweets:
            tid = t.get("id")
            if newest_id_this_run is None:
                newest_id_this_run = tid

            # classify reply type
            refs = t.get("referenced_tweets") or []
            is_reply = bool(_ref_type_id(refs, "replied_to"))
            if not is_reply:
                kind = "post"
            else:
                in_reply_to_uid = t.get("in_reply_to_user_id")
                replied_username = user_map.get(in_reply_to_uid, "")
                if replied_username.lower() == username.lower():
                    kind = "self_thread"
                else:
                    kind = "reply"

            kind_counts[kind] = kind_counts.get(kind, 0) + 1
            kept_new.append(slim_tweet(t, kind, user_map, username))

        log(f"  page {page}: api={len(tweets)} kept_running={len(kept_new)}")

        # check for next page
        next_token = meta.get("next_token")
        if not next_token:
            log("  no more pages.")
            break
        pagination_token = next_token
        time.sleep(PAGE_SLEEP_SEC)

    # ----- merge into raw store (de-dupe by tweet_id)
    existing = load_json(RAW_PATH, [])
    by_id = {t["tweet_id"]: t for t in existing if t.get("tweet_id")}
    added = 0
    for t in kept_new:
        if t["tweet_id"] and t["tweet_id"] not in by_id:
            by_id[t["tweet_id"]] = t
            added += 1

    merged = sorted(by_id.values(), key=lambda t: t.get("tweet_id") or "", reverse=True)
    save_json(RAW_PATH, merged)

    # update state
    if newest_id_this_run:
        state["newest_tweet_id"] = max(
            [newest_id_this_run] + ([state["newest_tweet_id"]] if state.get("newest_tweet_id") else []),
            key=lambda x: int(x) if str(x).isdigit() else 0,
        )
    state["last_run_utc"] = datetime.now(timezone.utc).isoformat()
    state["username"] = username
    save_json(STATE_PATH, state)

    # ----- summary
    log("")
    log("===== summary =====")
    log(f"  pages fetched      : {page}")
    log(f"  tweets seen (API)  : {seen_total}")
    log(f"  kept new this run  : {added}")
    log(f"  breakdown (run)    : posts={kind_counts.get('post', 0)} "
        f"self_thread={kind_counts.get('self_thread', 0)} reply={kind_counts.get('reply', 0)}")
    log(f"  total in store     : {len(merged)}  -> {RAW_PATH}")
    log(f"  newest id          : {state.get('newest_tweet_id')}")
    log("===================")


def main():
    ap = argparse.ArgumentParser(description="Fetch @user tweets via X API v2")
    ap.add_argument("--user", default=DEFAULT_USER, help="screen name (no @)")
    ap.add_argument("--backfill", action="store_true",
                    help="ignore saved state and pull full history")
    args = ap.parse_args()
    fetch(args.user, args.backfill)


if __name__ == "__main__":
    main()
