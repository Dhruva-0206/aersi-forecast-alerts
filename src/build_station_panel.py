"""
Build the station-day AQI panel from every raw snapshot.

Reuses AQI_BREAKPOINTS / sub_index from src/process/compute_aqi.py (that file
is a top-level script that reads and writes CSVs on import, so only its
definitions section is executed here -- single source of truth, no copy).

Output: data/processed/station_daily_panel.csv
Run from the repo root: python src/build_station_panel.py
"""

import re
from pathlib import Path

import numpy as np
import pandas as pd

SNAP_DIR = Path("data/snapshots")
OUT_FILE = Path("data/processed/station_daily_panel.csv")
AQI_SRC  = Path("src/process/compute_aqi.py")

# ── Reuse AQI definitions from compute_aqi.py ────────────────────────────────

_src = AQI_SRC.read_text(encoding="utf-8")
_marker = "# ── Load"
if _marker not in _src:
    raise RuntimeError(f"Marker '{_marker}' not found in {AQI_SRC}; update build_station_panel.py")
_ns = {}
exec(compile(_src.split(_marker)[0], str(AQI_SRC), "exec"), _ns)
sub_index = _ns["sub_index"]

FNAME_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.csv$")
POLLUTANT_COLS = ["pm25", "pm10", "no2", "so2", "ozone"]

# ── Per-snapshot station AQI (same rules as compute_aqi.py) ──────────────────

def snapshot_panel(path: Path, snap_date: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["pollutant_id"] = df["pollutant_id"].astype(str).str.upper().str.strip()
    df["avg_value"] = pd.to_numeric(df["avg_value"], errors="coerce")

    rows = []
    for station, g in df.groupby("station"):
        subs = {}
        for pol, val in zip(g["pollutant_id"], g["avg_value"]):
            s = sub_index(pol, val)
            if not np.isnan(s):
                subs[pol] = max(s, subs.get(pol, -np.inf))  # duplicates: keep max

        # Same relaxed rule as compute_aqi.py: one PM species + one other pollutant
        has_pm = ("PM2.5" in subs) or ("PM10" in subs)
        aqi = max(subs.values()) if (has_pm and len(subs) >= 2) else np.nan

        first = g.iloc[0]
        rows.append({
            "snapshot_date": snap_date,
            "station":   station,
            "city":      first["city"],
            "state":     first["state"],
            "latitude":  first["latitude"],
            "longitude": first["longitude"],
            "AQI":       aqi,
        })
    out = pd.DataFrame(rows)

    # Raw per-pollutant concentrations in wide format (NH3 / CO are not in the AQI breakpoints)
    pivot = df.pivot_table(
        index=["station", "city", "state", "latitude", "longitude"],
        columns="pollutant_id",
        values="avg_value",
        aggfunc="first",
    ).reset_index()
    pivot = pivot.rename(columns={"PM2.5": "pm25", "PM10": "pm10", "NO2": "no2",
                                  "SO2": "so2", "OZONE": "ozone"})
    keep = [c for c in POLLUTANT_COLS if c in pivot.columns]
    pivot = pivot[["station"] + keep].drop_duplicates("station")
    out = out.merge(pivot, on="station", how="left")
    for c in POLLUTANT_COLS:
        if c not in out.columns:
            out[c] = np.nan
    return out

# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    files = sorted(SNAP_DIR.glob("*.csv"))
    parts = []
    for f in files:
        m = FNAME_RE.search(f.name)
        if not m:
            print(f"Skipping (no date in filename): {f.name}")
            continue
        parts.append(snapshot_panel(f, m.group(1)))

    panel = pd.concat(parts, ignore_index=True)
    total = len(panel)
    panel = panel.dropna(subset=["AQI"]).reset_index(drop=True)
    panel["AQI"] = panel["AQI"].round(2)
    panel = panel.sort_values(["snapshot_date", "station"]).reset_index(drop=True)

    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(OUT_FILE, index=False)

    dropped = total - len(panel)
    print(f"Snapshots read:     {len(parts)}")
    print(f"Output saved:       {OUT_FILE}")
    print(f"Total rows:         {len(panel)}")
    print(f"Date range:         {panel['snapshot_date'].min()} -> {panel['snapshot_date'].max()}")
    print(f"Unique stations:    {panel['station'].nunique()}")
    for c in POLLUTANT_COLS:
        print(f"Null % {c:<6}:        {100 * panel[c].isna().mean():.1f}%")
    print(f"Null AQI dropped:   {dropped} of {total} station-days ({100 * dropped / total:.1f}%)")


if __name__ == "__main__":
    main()
