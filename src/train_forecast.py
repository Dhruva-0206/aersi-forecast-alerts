"""
src/train_forecast.py
AERSI Forecast Model — XGBoost next-day AQI prediction
Hackathon: Environmental Hacks, WeMakeDevs × AWS, Oct 2026
"""

import pandas as pd
import numpy as np
import json
import pickle
from pathlib import Path
import warnings
warnings.filterwarnings('ignore')

ROOT = Path(__file__).resolve().parent.parent
PANEL   = ROOT / "data/processed/station_daily_panel.csv"
WEATHER = ROOT / "data/processed/weather_features.csv"
OUT_DIR = ROOT / "data/processed"
OUT_DIR.mkdir(exist_ok=True)

print("Loading panel...")
panel = pd.read_csv(PANEL, parse_dates=["snapshot_date"])
panel = panel.rename(columns={"snapshot_date": "date"})

print("Loading weather features...")
weather = pd.read_csv(WEATHER, parse_dates=["date"])

df = panel.merge(weather, on=["date", "station"], how="inner")
print(f"Merged: {len(df):,} rows | {df['station'].nunique()} stations | {df['date'].min().date()} → {df['date'].max().date()}")

print("Engineering features...")
df = df.sort_values(["station", "date"]).reset_index(drop=True)

def add_features(g):
    g = g.sort_values("date").copy()
    g["target_AQI"] = g["AQI"].shift(-1)
    g["aqi_lag1"]   = g["AQI"].shift(1)
    g["aqi_lag2"]   = g["AQI"].shift(2)
    g["aqi_lag3"]   = g["AQI"].shift(3)
    g["aqi_lag7"]   = g["AQI"].shift(7)
    g["aqi_roll3"]  = g["AQI"].shift(1).rolling(3,  min_periods=2).mean()
    g["aqi_roll7"]  = g["AQI"].shift(1).rolling(7,  min_periods=4).mean()
    g["aqi_roll14"] = g["AQI"].shift(1).rolling(14, min_periods=7).mean()
    g["aqi_delta1"] = g["AQI"].shift(1) - g["AQI"].shift(2)
    g["aqi_delta3"] = g["AQI"].shift(1) - g["AQI"].shift(4)
    for col in ["blh_mean","blh_min","wind_mean","wind_max","precip_sum","humidity_mean","temp_mean","pressure_mean"]:
        if col in g.columns:
            g[f"{col}_lag1"] = g[col].shift(1)
    return g

df = df.groupby("station", group_keys=False).apply(add_features)
df["month"]      = df["date"].dt.month
df["dayofweek"]  = df["date"].dt.dayofweek
df["dayofyear"]  = df["date"].dt.dayofyear
df["is_winter"]  = df["month"].isin([11,12,1,2]).astype(int)
df["is_monsoon"] = df["month"].isin([6,7,8,9]).astype(int)

df = df.dropna(subset=["target_AQI", "aqi_lag1", "blh_mean"])
print(f"Training-ready rows: {len(df):,}")

FEATURES = [
    "AQI","aqi_lag1","aqi_lag2","aqi_lag3","aqi_lag7",
    "aqi_roll3","aqi_roll7","aqi_roll14","aqi_delta1","aqi_delta3",
    "blh_mean","blh_min","wind_mean","wind_max",
    "precip_sum","humidity_mean","temp_mean","pressure_mean",
    "blh_mean_lag1","blh_min_lag1","wind_mean_lag1","precip_sum_lag1",
    "month","dayofweek","dayofyear","is_winter","is_monsoon",
]
FEATURES = [f for f in FEATURES if f in df.columns]
TARGET = "target_AQI"

split_date = pd.Timestamp("2026-09-01")
train = df[df["date"] < split_date]
val   = df[df["date"] >= split_date]
print(f"Train: {len(train):,} rows | Val: {len(val):,} rows")

X_train, y_train = train[FEATURES], train[TARGET]
X_val,   y_val   = val[FEATURES],   val[TARGET]

persistence_mae  = np.mean(np.abs(val["AQI"].values - y_val.values))
persistence_rmse = np.sqrt(np.mean((val["AQI"].values - y_val.values)**2))
print(f"\nBaseline (persistence) — MAE: {persistence_mae:.1f}  RMSE: {persistence_rmse:.1f}")

from xgboost import XGBRegressor
model = XGBRegressor(
    n_estimators=500, max_depth=6, learning_rate=0.05,
    subsample=0.8, colsample_bytree=0.8, min_child_weight=5,
    reg_alpha=0.1, reg_lambda=1.0, random_state=42, n_jobs=-1,
    early_stopping_rounds=30, eval_metric="mae",
)
model.fit(X_train, y_train, eval_set=[(X_val, y_val)], verbose=50)

preds = np.clip(model.predict(X_val), 0, 500)
mae  = np.mean(np.abs(preds - y_val.values))
rmse = np.sqrt(np.mean((preds - y_val.values)**2))
r2   = 1 - np.sum((preds - y_val.values)**2) / np.sum((y_val.values - y_val.mean())**2)

print(f"\n{'='*50}")
print(f"XGBoost — MAE: {mae:.1f}  RMSE: {rmse:.1f}  R²: {r2:.3f}")
print(f"vs Persistence — MAE: {persistence_mae:.1f}  RMSE: {persistence_rmse:.1f}")
print(f"Improvement:  MAE -{persistence_mae-mae:.1f}  RMSE -{persistence_rmse-rmse:.1f}")
print(f"{'='*50}")

fi = pd.Series(model.feature_importances_, index=FEATURES).sort_values(ascending=False)
print("\nTop 15 features:")
print(fi.head(15).to_string())

model_path = OUT_DIR / "aersi_forecast_model.pkl"
with open(model_path, "wb") as f:
    pickle.dump({"model": model, "features": FEATURES}, f)
print(f"\nModel saved → {model_path}")

val_out = val[["date","station","city","state","AQI"]].copy()
val_out["predicted_AQI"] = preds
val_out["error"] = preds - y_val.values

def aqi_to_severity(aqi):
    if aqi <= 50:   return "Good"
    if aqi <= 100:  return "Satisfactory"
    if aqi <= 200:  return "Moderate"
    if aqi <= 300:  return "Poor"
    if aqi <= 400:  return "Very Poor"
    return "Severe"

val_out["predicted_severity"] = val_out["predicted_AQI"].apply(aqi_to_severity)
val_out.to_csv(OUT_DIR / "forecast_validation.csv", index=False)

meta = {
    "features": FEATURES,
    "val_mae": round(float(mae), 2),
    "val_rmse": round(float(rmse), 2),
    "val_r2": round(float(r2), 3),
    "baseline_mae": round(float(persistence_mae), 2),
    "train_rows": len(train),
    "val_rows": len(val),
    "train_date_range": f"{train['date'].min().date()} to {train['date'].max().date()}",
    "val_date_range": f"{val['date'].min().date()} to {val['date'].max().date()}",
}
with open(OUT_DIR / "model_metadata.json", "w") as f:
    json.dump(meta, f, indent=2)
print("Metadata saved. Done.")
