"""verify_data.py — Pre-push data health check + write manifest.json
Usage: python verify_data.py [--db <path>]
Default db path: ../data/db (relative to this script)
"""
import json, glob, os, sys, datetime
from pathlib import Path

# Ensure emoji/unicode prints safely on Windows (GBK terminals)
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

def _argval(flag,default=None):
    for i,x in enumerate(sys.argv):
        if x==flag and i+1<len(sys.argv): return sys.argv[i+1]
        if x.startswith(flag+'='): return x.split('=',1)[1]
    return default

SCRIPT_DIR = Path(__file__).resolve().parent
_db_override = _argval('--db') or os.environ.get('SERENITY_DB')
DB = Path(_db_override).resolve() if _db_override else SCRIPT_DIR.parent / 'data' / 'db'
STOCKS_DIR = DB / 'stocks'

print(f"DB path: {DB}")
print(f"Stocks dir: {STOCKS_DIR}")
print()

files = sorted(glob.glob(str(STOCKS_DIR / '*.json')))
if not files:
    print("ERROR: no stock JSON files found!"); sys.exit(1)

total_mentions = 0
total_priced = 0
earliest = None
latest = None
errors = []
tickers = []

for f in files:
    fname = os.path.basename(f)
    try:
        d = json.load(open(f, encoding='utf-8'))
    except Exception as e:
        errors.append(f"{fname}: invalid JSON — {e}"); continue
    tk = d.get('ticker')
    if not tk:
        errors.append(f"{fname}: missing 'ticker'"); continue
    tickers.append(tk)
    mentions = d.get('mentions') or []
    total_mentions += len(mentions)
    ps = d.get('price_series') or []
    if ps:
        total_priced += 1
    for m in mentions:
        dt = m.get('date')
        if dt:
            if earliest is None or dt < earliest: earliest = dt
            if latest is None or dt > latest: latest = dt

size_bytes = sum(os.path.getsize(f) for f in files)
size_mb = size_bytes / (1024*1024)

print(f"Tickers: {len(tickers)}")
print(f"Total mentions: {total_mentions}")
print(f"Priced tickers: {total_priced}")
print(f"Date range: {earliest} — {latest}")
print(f"Total size: {size_mb:.1f} MB ({len(files)} files)")

# index.json check
idx = DB / 'index.json'
if idx.exists():
    print(f"index.json: present ({idx.stat().st_size / 1024:.0f} KB)")
else:
    print("index.json: NOT found (optional, render doesn't need it)")

# state files check (needed for daily automation)
data_dir = DB.parent  # data/
for name in ['raw_tweets.json', 'extracted.json']:
    p = data_dir / name
    if p.exists():
        sz = p.stat().st_size / (1024*1024)
        print(f"{name}: present ({sz:.1f} MB)")
    else:
        print(f"{name}: NOT found — daily automation needs this for incrementality!")

if errors:
    print(f"\n⚠️  {len(errors)} ERRORS:")
    for e in errors: print(f"  {e}")
    sys.exit(1)
else:
    print("\n✅ All files valid.")

# write manifest
manifest = {
    "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    "tickers": len(tickers),
    "total_mentions": total_mentions,
    "priced_tickers": total_priced,
    "date_range": [earliest, latest],
    "size_mb": round(size_mb, 1),
}
mf = DB / 'manifest.json'
json.dump(manifest, open(mf, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
print(f"\nManifest written: {mf}")
print(json.dumps(manifest, indent=2))
