"""
Compute rolling 30-day AERSI scores per station from WHO-normalized pollutant
concentrations in the station-day panel.

    AERSI = PL^0.50 * EPF^0.25 * VSF^0.25

    PL  = rolling 30-day mean of the daily load
          sum_p w_p * (conc_p / WHO_p)^0.6, weights renormalized over the pollutants
          present that day
    EPF = 1 + fraction of observed days in the window with AQI > 100
    VSF = 1 + tanh(median |AQI[t] - AQI[t-1]| / 45), consecutive calendar days only

Bands: Very Low < 0.6 <= Low < 1.0 <= Moderate < 1.5 <= High < 2.0 <= Extreme.

Input:  data/processed/station_daily_panel.csv
Output: data/processed/station_aersi.csv
Run from the repo root: python src/compute_aersi.py
"""

from pathlib import Path

import numpy as np
import pandas as pd

PANEL_FILE = Path("data/processed/station_daily_panel.csv")
OUT_FILE   = Path("data/processed/station_aersi.csv")

WINDOW      = "30D"
MIN_PERIODS = 7
AQI_EXCEED  = 100     # EPF exceedance threshold
VSF_SCALE   = 45.0

EXPONENT = 0.6  # sub-linear saturation, same for every pollutant

# column: (WHO guideline in ug/m3, burden-of-disease weight)
POLLUTANTS = {
    "pm25":  (15.0, 0.40),
    "pm10":  (45.0, 0.20),
    "no2":   (25.0, 0.15),
    "so2":   (40.0, 0.10),
    "ozone": (60.0, 0.15),
}

BANDS = [(0.6, "Very Low"), (1.0, "Low"), (1.5, "Moderate"), (2.0, "High")]


def aersi_band(x: float) -> str:
    if pd.isna(x):
        return np.nan
    for upper, name in BANDS:
        if x < upper:
            return name
    return "Extreme"


def daily_load(df: pd.DataFrame) -> pd.Series:
    """Weighted sum of (conc / WHO)^0.6, weights renormalized over pollutants present."""
    terms = pd.DataFrame({col: (df[col] / g) ** EXPONENT for col, (g, _) in POLLUTANTS.items()})
    weights = pd.DataFrame({col: np.where(df[col].notna(), w, 0.0)
                            for col, (_, w) in POLLUTANTS.items()}, index=df.index)
    present = weights.sum(axis=1)
    load = (terms.fillna(0.0) * weights).sum(axis=1) / present.where(present > 0)
    return load  # NaN when no pollutant is present


def station_scores(g: pd.DataFrame) -> pd.DataFrame:
    g = g.sort_values("date").copy()
    idx = g["date"]
    aqi = pd.Series(g["AQI"].to_numpy(), index=idx)
    load = pd.Series(daily_load(g).to_numpy(), index=idx)

    # Day-to-day change only between consecutive calendar days; diffs that
    # span a data gap are not a day-to-day change
    gap_days = idx.diff().dt.days.to_numpy()
    delta = aqi.diff().abs().where(gap_days == 1)

    g["PL"]  = load.rolling(WINDOW, min_periods=MIN_PERIODS).mean().to_numpy()
    g["EPF"] = 1 + (aqi.gt(AQI_EXCEED).astype(float).where(aqi.notna())
                       .rolling(WINDOW, min_periods=MIN_PERIODS).mean()).to_numpy()
    med = delta.rolling(WINDOW, min_periods=1).median()
    g["VSF"] = (1 + np.tanh(med.fillna(0.0) / VSF_SCALE)).to_numpy()  # no pairs -> 1.0
    return g


def main():
    df = pd.read_csv(PANEL_FILE, parse_dates=["snapshot_date"])
    df = df.rename(columns={"snapshot_date": "date"})
    df = df.sort_values(["station", "date"]).reset_index(drop=True)

    df = pd.concat([station_scores(g) for _, g in df.groupby("station")], ignore_index=True)
    df = df.dropna(subset=["PL", "EPF"]).copy()  # rows with fewer than MIN_PERIODS days

    df["AERSI"] = df["PL"] ** 0.50 * df["EPF"] ** 0.25 * df["VSF"] ** 0.25
    df["aersi_band"] = df["AERSI"].apply(aersi_band)

    out = df[["date", "station", "city", "state", "AQI",
              "PL", "EPF", "VSF", "AERSI", "aersi_band"]].round(4)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(OUT_FILE, index=False)

    print(f"Output saved:   {OUT_FILE}")
    print(f"Rows:           {len(out)}")
    print(f"Stations:       {out['station'].nunique()}")
    print(f"AERSI mean/min/max: {out['AERSI'].mean():.3f} / {out['AERSI'].min():.3f} / {out['AERSI'].max():.3f}")
    print("Band distribution:")
    order = ["Very Low", "Low", "Moderate", "High", "Extreme"]
    counts = out["aersi_band"].value_counts().reindex(order).fillna(0).astype(int)
    for name, n in counts.items():
        print(f"  {name:<10} {n:>6}  ({100 * n / len(out):.1f}%)")


if __name__ == "__main__":
    main()
