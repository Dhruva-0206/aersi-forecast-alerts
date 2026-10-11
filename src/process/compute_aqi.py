"""
Step 3 — Compute daily AQI per station
Uses official CPCB sub-index breakpoints.
AQI = max of all valid pollutant sub-indices for that station-day.
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────────────────

INPUT_FILE  = Path("data/rolling/last_30_days.csv")
OUTPUT_FILE = Path("data/rolling/last_30_days_with_aqi.csv")

# CPCB AQI breakpoints: (conc_lo, conc_hi, aqi_lo, aqi_hi)
AQI_BREAKPOINTS = {
    "PM2.5": [
        (0, 30, 0, 50), (31, 60, 51, 100), (61, 90, 101, 200),
        (91, 120, 201, 300), (121, 250, 301, 400), (251, 10000, 401, 500),
    ],
    "PM10": [
        (0, 50, 0, 50), (51, 100, 51, 100), (101, 250, 101, 200),
        (251, 350, 201, 300), (351, 430, 301, 400), (431, 10000, 401, 500),
    ],
    "NO2": [
        (0, 40, 0, 50), (41, 80, 51, 100), (81, 180, 101, 200),
        (181, 280, 201, 300), (281, 400, 301, 400), (401, 10000, 401, 500),
    ],
    "SO2": [
        (0, 40, 0, 50), (41, 80, 51, 100), (81, 380, 101, 200),
        (381, 800, 201, 300), (801, 1600, 301, 400), (1601, 10000, 401, 500),
    ],
    "OZONE": [
        (0, 50, 0, 50), (51, 100, 51, 100), (101, 168, 101, 200),
        (169, 208, 201, 300), (209, 748, 301, 400), (749, 10000, 401, 500),
    ],
}

AQI_THRESHOLD = 100  # CPCB: above this = unhealthy

# ── Helpers ──────────────────────────────────────────────────────────────────

def sub_index(pollutant: str, concentration: float) -> float:
    """Compute CPCB AQI sub-index for one pollutant reading.

    The published CPCB breakpoint tables are defined on integer boundaries
    (e.g. PM2.5: 0-30, 31-60), which leaves gaps for non-integer readings
    (30.5 matches no bracket). Instead of adjusting the published numbers,
    each bracket is treated as the half-open interval (previous_hi, hi] —
    the first bracket includes 0 — so every concentration >= 0 maps to
    exactly one bracket.
    """
    if pollutant not in AQI_BREAKPOINTS or pd.isna(concentration) or concentration < 0:
        return np.nan

    brackets = AQI_BREAKPOINTS[pollutant]
    for i, (blo, bhi, ilo, ihi) in enumerate(brackets):
        lower = 0 if i == 0 else brackets[i - 1][1]
        if concentration <= bhi and (i == 0 or concentration > lower):
            return ((ihi - ilo) / (bhi - blo)) * (concentration - blo) + ilo

    return np.nan  # above the top bracket

# ── Load ─────────────────────────────────────────────────────────────────────

df = pd.read_csv(INPUT_FILE)

required = {"station", "pollutant_id", "avg_value", "last_update"}
missing  = required - set(df.columns)
if missing:
    raise ValueError(f"Missing columns in rolling dataset: {missing}")

# data.gov.in returns last_update as DD-MM-YYYY HH:MM:SS. The format MUST be
# pinned explicitly: without it pandas infers the format from the first row,
# which silently swaps day/month (day <= 12) or coerces to NaT (day > 12)
# whenever the newest snapshot happens to start with an ambiguous date.
df["last_update"] = pd.to_datetime(
    df["last_update"], format="%d-%m-%Y %H:%M:%S", errors="coerce"
)

n_nat, n_total = df["last_update"].isna().sum(), len(df)
print(f"Date parsing: {n_nat} NaT out of {n_total} rows")
if n_total > 0 and n_nat / n_total > 0.05:
    raise RuntimeError(
        f"Date parsing failed: {n_nat}/{n_total} rows ({100 * n_nat / n_total:.1f}%) "
        f"have unparseable last_update. Expected format DD-MM-YYYY HH:MM:SS — "
        f"check whether the API changed its date format."
    )

df["date"]        = df["last_update"].dt.date.astype(str)
df["pollutant_id"] = df["pollutant_id"].str.upper().str.strip()

# ── Compute daily AQI per station ────────────────────────────────────────────

print("Computing daily AQI per station")

aqi_rows = []
n_rejected = 0

for (station, date), group in df.groupby(["station", "date"]):
    subs = {}
    for _, row in group.iterrows():
        s = sub_index(row["pollutant_id"], row["avg_value"])
        if not np.isnan(s):
            # if a pollutant appears more than once for a station-day
            # (duplicate snapshot rows), keep the max sub-index
            subs[row["pollutant_id"]] = max(s, subs.get(row["pollutant_id"], -np.inf))

    # Minimum-composition rule: CPCB's official AQI methodology requires at
    # least 3 pollutants including one PM species. We enforce a documented,
    # slightly relaxed minimum — at least one PM species (PM2.5 or PM10) AND
    # at least one other pollutant — since requiring the full 3 would drop a
    # large share of stations that report only PM + one gas. Station-days
    # below this minimum get NaN instead of a single-pollutant "AQI".
    has_pm = ("PM2.5" in subs) or ("PM10" in subs)
    if has_pm and len(subs) >= 2:
        aqi = max(subs.values())
    else:
        aqi = np.nan
        if subs:
            n_rejected += 1

    aqi_rows.append({"station": station, "date": date, "AQI": aqi})

aqi_df = pd.DataFrame(aqi_rows)

# ── Merge back and save ──────────────────────────────────────────────────────

df = df.merge(aqi_df, on=["station", "date"], how="left")

OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
df.to_csv(OUTPUT_FILE, index=False)

print(f"Output saved:             {OUTPUT_FILE}")
print(f"Total rows:               {len(df)}")
print(f"Station-days with AQI:    {df['AQI'].notna().sum()}")
print(f"Station-days rejected by min-pollutant rule: {n_rejected}")
print(f"Station-days above {AQI_THRESHOLD}:   {(df['AQI'] > AQI_THRESHOLD).sum()}")
