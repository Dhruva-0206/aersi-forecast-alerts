"""
Fetch historical daily weather per station from the Open-Meteo ERA5 archive,
using the multi-location (batch) form of the endpoint.

Reads unique stations from data/processed/station_daily_panel.csv, fetches
BATCH_SIZE stations per API call, aggregates hourly -> daily, and appends to
data/processed/weather_features.csv after every batch.
Resumable: stations already present in the output are skipped.

Run from the repo root: python src/fetch_weather.py
"""

import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

PANEL_FILE = Path("data/processed/station_daily_panel.csv")
OUT_FILE   = Path("data/processed/weather_features.csv")

URL        = "https://archive-api.open-meteo.com/v1/archive"
START_DATE = "2026-01-14"
END_DATE   = "2026-09-25"
HOURLY     = ("temperature_2m,relative_humidity_2m,wind_speed_10m,wind_direction_10m,"
              "precipitation,boundary_layer_height,surface_pressure")

BATCH_SIZE    = 10
MAX_RETRIES   = 5
RATE_WAIT_S   = 60   # wait after HTTP 429
ERROR_WAIT_S  = 5    # wait after other transient errors
BATCH_SLEEP_S = 2

COLUMNS = ["date", "station", "temp_mean", "humidity_mean", "wind_mean", "wind_max",
           "precip_sum", "blh_mean", "blh_min", "pressure_mean", "wind_dir_mean"]


def circular_mean_deg(deg: pd.Series) -> float:
    """Mean wind direction in degrees [0, 360) via unit-vector averaging."""
    d = deg.dropna()
    if d.empty:
        return np.nan
    rad = np.deg2rad(d.to_numpy())
    return float(np.rad2deg(np.arctan2(np.sin(rad).mean(), np.cos(rad).mean())) % 360)


def aggregate_daily(station: str, hourly: dict) -> pd.DataFrame:
    df = pd.DataFrame(hourly)
    df["date"] = df["time"].str[:10]  # local (IST) calendar day
    g = df.groupby("date")
    daily = pd.DataFrame({
        "temp_mean":     g["temperature_2m"].mean(),
        "humidity_mean": g["relative_humidity_2m"].mean(),
        "wind_mean":     g["wind_speed_10m"].mean(),
        "wind_max":      g["wind_speed_10m"].max(),
        "precip_sum":    g["precipitation"].sum(min_count=1),
        "blh_mean":      g["boundary_layer_height"].mean(),
        "blh_min":       g["boundary_layer_height"].min(),
        "pressure_mean": g["surface_pressure"].mean(),
        "wind_dir_mean": g["wind_direction_10m"].apply(circular_mean_deg),
    }).reset_index()
    daily.insert(1, "station", station)
    return daily[COLUMNS].round(3)


def fetch_batch(batch: pd.DataFrame) -> list:
    """One API call for the whole batch; returns one location object per station, in order."""
    params = {
        "latitude":  ",".join(str(x) for x in batch["latitude"]),
        "longitude": ",".join(str(x) for x in batch["longitude"]),
        "start_date": START_DATE, "end_date": END_DATE,
        "hourly": HOURLY, "timezone": "Asia/Kolkata", "models": "era5_seamless",
    }
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            r = requests.get(URL, params=params, timeout=120)
            if r.status_code == 429:
                last_err = "429 Too Many Requests"
                print(f"  429 (attempt {attempt}/{MAX_RETRIES}) - waiting {RATE_WAIT_S}s")
                time.sleep(RATE_WAIT_S)
                continue
            r.raise_for_status()
            data = r.json()
            # A single-location request returns an object, multi-location returns a list
            if isinstance(data, dict):
                data = [data]
            if len(data) != len(batch):
                raise ValueError(f"expected {len(batch)} locations, got {len(data)}")
            return data
        except Exception as e:
            last_err = e
            print(f"  error (attempt {attempt}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES:
                time.sleep(ERROR_WAIT_S)
    raise RuntimeError(f"batch failed after {MAX_RETRIES} attempts: {last_err}")


def main():
    t0 = time.time()
    panel = pd.read_csv(PANEL_FILE, usecols=["station", "latitude", "longitude"])
    stations = panel.drop_duplicates("station").reset_index(drop=True)

    done = set()
    if OUT_FILE.exists():
        done = set(pd.read_csv(OUT_FILE, usecols=["station"])["station"])
    todo = stations[~stations["station"].isin(done)].reset_index(drop=True)
    print(f"Stations total: {len(stations)} | already done: {len(done)} | to fetch: {len(todo)}")

    n_batches = -(-len(todo) // BATCH_SIZE)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_header = not OUT_FILE.exists()
    ok, failed = 0, []

    for b in range(n_batches):
        lo, hi = b * BATCH_SIZE, min((b + 1) * BATCH_SIZE, len(todo))
        batch = todo.iloc[lo:hi]
        try:
            results = fetch_batch(batch)
            frames = [aggregate_daily(name, res["hourly"])
                      for name, res in zip(batch["station"], results)]
            pd.concat(frames, ignore_index=True).to_csv(
                OUT_FILE, mode="a", header=write_header, index=False)
            write_header = False
            ok += len(batch)
            print(f"Batch {b + 1}/{n_batches}: stations {lo + 1}-{hi} done")
        except Exception as e:
            failed.extend(batch["station"])
            print(f"Batch {b + 1}/{n_batches}: stations {lo + 1}-{hi} FAILED - {e}")
        if b < n_batches - 1:
            time.sleep(BATCH_SLEEP_S)

    print(f"Done: {ok} stations fetched, {len(failed)} failed, in {time.time() - t0:.1f}s")
    if failed:
        print("Re-run the script to retry failed stations (resume skips completed ones).")


if __name__ == "__main__":
    main()
