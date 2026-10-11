# AERSI Forecast + Alert System
### Air Exposure Severity Index — Predictive AQI Forecasting for India

> **Environmental Hacks Hackathon** · WeMakeDevs × AWS · Air Track · Oct 2026  
> Built by **Dhruva Chakrabarty**

---

## What It Does

AERSI predicts **next-day AQI** for **583 Indian monitoring stations** using an XGBoost model trained on 9 months of air quality data + meteorological features. When the forecast crosses dangerous thresholds (AQI > 200), AWS Lambda automatically dispatches **SMS and email alerts** to subscribers in affected cities — giving people 24 hours of advance warning to protect themselves.

---

## The Problem

India has over 200 million people living in cities with hazardous air quality. Current AQI systems report what's happening *right now* — but by the time a "Severe" reading appears, the damage is already done. Children, elderly people, and those with respiratory conditions need **advance notice**, not real-time readings.

---

## AERSI Formula

The Air Exposure Severity Index (AERSI) is computed as:

```
AERSI = PL^0.50 × EPF^0.25 × VSF^0.25
```

Where:
- **PL** = Pollution Load (WHO-normalized pollutant concentrations over the 30-day window)
- **EPF** = Exposure Persistence Factor (fraction of days with AQI > 100)
- **VSF** = Variability Severity Factor (median day-to-day AQI change)

Severity bands: Good → Satisfactory → Moderate → Poor → Very Poor → Severe

---

## Architecture

```
Open-Meteo API (historical weather)
        ↓
XGBoost Forecast Model (trained locally)
        ↓
AWS Lambda (aersi-alert-dispatcher)
    ├── reads aersi-subscribers (DynamoDB)
    ├── filters Alert-worthy stations (AQI > 200)
    └── publishes to aersi-alerts (SNS Topic)
              ↓
    SMS + Email to subscribers
              ↑
EventBridge (daily cron: 8 PM IST)
```

### AWS Services Used
| Service | Role |
|---|---|
| **AWS Lambda** (Python 3.12) | Daily forecast dispatcher + alert logic |
| **Amazon SNS** | SMS & email delivery to subscribers |
| **Amazon DynamoDB** | Subscriber registry (city, contact, preferences) |
| **Amazon EventBridge** | Daily schedule trigger (cron 8 PM IST) |

---

## ML Model

**Algorithm:** XGBoost (native API, `xgb.train`)  
**Target:** Next-day AQI  
**Training data:** Jan–Aug 2026 (54,748 station-days across 583 stations)  
**Validation:** Sep 2026 (time-based split, no leakage)

### Features (27 total)
- **AQI history:** lags (1/2/3/7 days), rolling means (3/7/14 days), trend deltas
- **Weather:** boundary layer height, wind speed/direction, temperature, humidity, precipitation, surface pressure
- **Calendar:** month, day of week, day of year, winter/monsoon flags

### Results
| Metric | XGBoost | Persistence Baseline |
|--------|---------|---------------------|
| MAE | **25.6** | 26.8 |
| RMSE | **43.8** | 49.7 |
| R² | **0.648** | — |

The model reduces RMSE by **11.9%** vs. a naive "tomorrow = today" baseline — most critically, it handles dangerous spike events better, which is exactly when alerts matter.

---

## Data Pipeline

```
data/snapshots/          ← 148 daily AQI snapshots (Jan–Sep 2026)
        ↓  build_station_panel.py
data/processed/station_daily_panel.csv    ← 66,881 rows, 583 stations
        +
data/processed/weather_features.csv      ← 148,665 rows (Open-Meteo ERA5)
        ↓  train_forecast.py
data/processed/aersi_forecast_model.pkl  ← XGBoost booster (705KB)
data/processed/forecast_validation.csv   ← Sep 2026 predictions
data/processed/model_metadata.json       ← metrics + feature list
```

---

## Repository Structure

```
aersi-forecast-alerts/
├── src/
│   ├── build_station_panel.py   # Builds daily AQI panel from 148 snapshots
│   ├── fetch_weather.py         # Batch fetches weather from Open-Meteo ERA5
│   └── train_forecast.py        # Trains XGBoost next-day AQI model
├── aws/
│   ├── lambda_function.py       # Lambda: alert dispatcher
│   └── subscribe_lambda.py      # Lambda: subscriber registration
├── data/
│   └── processed/               # CSVs, model pkl, metadata JSON
└── README.md
```

---

## AWS Lambda: Alert Dispatcher

The core Lambda function (`aersi-alert-dispatcher`):

1. Receives forecast data (or loads from DynamoDB `aersi-forecasts` table)
2. Filters stations where predicted AQI falls in **Poor / Very Poor / Severe** bands
3. Queries `aersi-subscribers` DynamoDB table for users in affected cities
4. Sends personalized SNS notifications: city name, predicted AQI, severity, health advice
5. Broadcasts a daily summary to the `aersi-alerts` SNS topic

**Triggered daily at 8 PM IST** via EventBridge cron `(30 14 * * ? *)` — giving people the night to prepare before the next day's air quality hits.

---

## Alert Message Format

```
AERSI Air Quality Alert — Delhi

Tomorrow's forecast for your city:
• Anand Vihar:  AQI 312 (Very Poor)
• Punjabi Bagh: AQI 287 (Poor)
• Chandni Chowk: AQI 334 (Very Poor)

Health advice: Avoid outdoor activity. Use N95 masks if going out.
Sensitive groups (children, elderly, respiratory conditions) should stay indoors.

— AERSI Forecast System
```

---

## Setup

### Prerequisites
- Python 3.10+
- AWS account with Lambda, SNS, DynamoDB access
- `pip install xgboost pandas numpy requests`

### Run the pipeline

```bash
# 1. Build AQI panel from snapshots
python src/build_station_panel.py

# 2. Fetch weather features (Open-Meteo ERA5)
python src/fetch_weather.py

# 3. Train forecast model
python src/train_forecast.py

# Model saved to data/processed/aersi_forecast_model.pkl
```

### Deploy Lambda

1. Zip `aws/lambda_function.py` and upload to AWS Lambda (Python 3.12)
2. Set environment variable: `SNS_TOPIC_ARN = <your-sns-topic-arn>`
3. Attach IAM policies: `AmazonSNSFullAccess`, `AmazonDynamoDBFullAccess`
4. Add EventBridge trigger: `cron(30 14 * * ? *)` for 8 PM IST daily

---

## Impact

- **583 stations** across India covered
- **24-hour advance warning** before dangerous air quality events
- Personalized alerts by city — no spam, relevant information only
- Boundary layer height (BLH) as key feature — captures atmospheric conditions that trap pollutants overnight
- Built to scale: SNS handles millions of notifications, DynamoDB scales to any subscriber count

---

## Hackathon

**Environmental Hacks** · WeMakeDevs × AWS · Air Track · October 8–11, 2026

All development work committed October 8–11, 2026.
