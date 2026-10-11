"""
Step 1 — Fetch daily CPCB snapshot
Pulls all station data from data.gov.in and saves as a dated CSV.
Skips gracefully if today's snapshot already exists.
"""

import os
import time
import requests
import pandas as pd
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dotenv import load_dotenv

# ── Config ───────────────────────────────────────────────────────────────────

load_dotenv()
API_KEY = os.getenv("DATA_GOV_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "DATA_GOV_API_KEY not found.\n"
        "  Local: add it to your .env file\n"
        "  GitHub Actions: add it in repo Settings → Secrets → Actions"
    )

RESOURCE_ID = "3b01bcb8-0b14-4abf-b6f2-c1bfd384ba69"
BASE_URL     = f"https://api.data.gov.in/resource/{RESOURCE_ID}"
PAGE_SIZE    = 1000

SNAPSHOT_DIR = Path("data/snapshots")
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

TODAY       = datetime.now(timezone.utc).strftime("%Y-%m-%d")
OUTPUT_FILE = SNAPSHOT_DIR / f"cpcb_snapshot_{TODAY}.csv"

# ── Skip if already fetched today ────────────────────────────────────────────

if OUTPUT_FILE.exists():
    print(f"Snapshot already exists for {TODAY}, skipping fetch.")
    raise SystemExit(0)

# ── Fallback helpers ─────────────────────────────────────────────────────────

FALLBACK_MAX_DAYS = 14

def find_recent_snapshot(max_days_back: int = FALLBACK_MAX_DAYS):
    """Return the most recent existing snapshot within max_days_back days, or None."""
    today_date = datetime.now(timezone.utc).date()
    for delta in range(1, max_days_back + 1):
        candidate = today_date - timedelta(days=delta)
        f = SNAPSHOT_DIR / f"cpcb_snapshot_{candidate}.csv"
        if f.exists():
            return f
    return None


def fall_back_or_halt():
    """Use a recent snapshot if the API is unusable, otherwise halt the pipeline."""
    fallback = find_recent_snapshot(FALLBACK_MAX_DAYS)
    if fallback:
        print(f"WARNING: API fetch failed - falling back to existing snapshot: {fallback.name}")
        print(f"WARNING: Fallback is within the {FALLBACK_MAX_DAYS}-day window. Downstream steps will use this data.")
        raise SystemExit(0)
    print(f"ERROR: API unreachable and no snapshot found within {FALLBACK_MAX_DAYS} days.")
    raise SystemExit(1)

# ── Paginated fetch ──────────────────────────────────────────────────────────

print(f"Fetching CPCB snapshot for {TODAY}")

all_records = []
offset = 0
page   = 1

# Add browser-like headers to avoid being blocked by data.gov.in
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

while True:
    response = None
    for attempt in range(5):
        try:
            response = requests.get(
                BASE_URL,
                params={
                    "api-key": API_KEY,
                    "format":  "json",
                    "limit":   PAGE_SIZE,
                    "offset":  offset,
                },
                headers=headers,
                timeout=90,
            )
            if response.status_code in (429, 502, 503, 504):
                retry_after = 120
                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 120))
                print(f"  HTTP {response.status_code} on page {page}, attempt {attempt + 1}/5 — retrying in {retry_after}s...")
                if attempt == 4:
                    print(f"WARNING: API returned {response.status_code} five times on page {page}.")
                    fall_back_or_halt()
                time.sleep(retry_after)
                continue
            break
        except requests.exceptions.ConnectionError as e:
            print(f"  Connection error on page {page}, attempt {attempt + 1}/5: {e}")
            print("WARNING: Connection refused/unreachable - retrying will not help. Skipping to fallback.")
            fall_back_or_halt()
        except requests.exceptions.Timeout:
            print(f"  Timeout on page {page}, attempt {attempt + 1}/5 — retrying in 30s...")
            if attempt == 4:
                print("WARNING: API timed out 5 times in a row.")
                fall_back_or_halt()
            time.sleep(30)
        except requests.exceptions.RequestException as e:
            print(f"  Request error on page {page}, attempt {attempt + 1}/5: {e}")
            if attempt == 4:
                print("WARNING: Request failed 5 times in a row.")
                fall_back_or_halt()
            time.sleep(30)

    if response is None or response.status_code != 200:
        status = response.status_code if response is not None else "no response"
        print(f"WARNING: API request failed (HTTP {status}).")
        fall_back_or_halt()

    payload = response.json()
    records = payload.get("records", [])

    if not records:
        break

    all_records.extend(records)
    print(f"  Page {page}: {len(records)} records")
    offset += PAGE_SIZE
    page   += 1
    time.sleep(5)

print(f"Total records fetched: {len(all_records)}")

if not all_records:
    print("WARNING: API returned zero records.")
    fall_back_or_halt()

# ── Save ─────────────────────────────────────────────────────────────────────

df = pd.DataFrame(all_records)
df.to_csv(OUTPUT_FILE, index=False)

print(f"Snapshot saved: {OUTPUT_FILE}")
print(f"Columns: {list(df.columns)}")
