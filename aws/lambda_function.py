"""
aws/lambda_function.py
AERSI Alert Dispatcher
Hackathon: Environmental Hacks, WeMakeDevs x AWS, Oct 2026
"""

import json
import os
import boto3
import logging
from datetime import datetime, timezone, timedelta

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
TABLE_NAME    = os.environ.get("TABLE_NAME", "aersi-subscribers")
ALERT_BANDS   = {"Poor", "Very Poor", "Severe"}

dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
sns      = boto3.client("sns",        region_name="ap-south-1")
table    = dynamodb.Table(TABLE_NAME)


def get_subscribers():
    response = table.scan()
    return response.get("Items", [])


def send_alert(subscriber, alerts):
    name  = subscriber.get("name", "there")
    city  = subscriber.get("city", "your city")
    email = subscriber.get("email", "")

    lines = [
        f"AERSI Air Quality Alert for {city}",
        f"Hi {name}, tomorrow's air quality forecast:",
        "",
    ]
    for a in alerts:
        aqi = int(float(a["predicted_AQI"]))
        sev = a["predicted_severity"]
        stn = a["station"]
        lines.append(f"  {stn}: {sev} (AQI ~{aqi})")

    worst_aqi = max(int(float(a["predicted_AQI"])) for a in alerts)
    lines.append("")
    if worst_aqi > 300:
        lines.append("Action: Avoid all outdoor activity. N95 mask essential.")
    elif worst_aqi > 200:
        lines.append("Action: Limit outdoor time. Wear a mask outdoors.")
    else:
        lines.append("Action: Sensitive groups should stay indoors.")

    lines.append("")
    lines.append("Powered by AERSI | aersi.live")

    message = "\n".join(lines)

    try:
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=message,
            Subject=f"AERSI Alert: {city} air quality tomorrow",
        )
        logger.info(f"Alert sent for {city} to {email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send alert to {email}: {e}")
        return False


def lambda_handler(event, context):
    logger.info("AERSI Alert Dispatcher started")

    # Support manual test payloads (for demo)
    forecasts = event.get("test_forecasts", [])

    if not forecasts:
        # In production: load from DynamoDB aersi-forecasts table
        try:
            forecasts_table = dynamodb.Table("aersi-forecasts")
            ist = datetime.now(timezone(timedelta(hours=5, minutes=30)))
            today = ist.strftime("%Y-%m-%d")
            resp = forecasts_table.query(
                KeyConditionExpression=boto3.dynamodb.conditions.Key("date").eq(today)
            )
            forecasts = resp.get("Items", [])
        except Exception as e:
            logger.warning(f"Could not load forecasts from DB: {e}")
            forecasts = []

    logger.info(f"Forecasts loaded: {len(forecasts)}")

    alert_stations = [f for f in forecasts if f.get("predicted_severity") in ALERT_BANDS]
    logger.info(f"Alert-worthy stations: {len(alert_stations)}")

    city_alerts = {}
    for f in alert_stations:
        city = f.get("city", "Unknown")
        city_alerts.setdefault(city, []).append(f)

    subscribers = get_subscribers()
    logger.info(f"Subscribers: {len(subscribers)}")

    sent = 0
    for sub in subscribers:
        if sub.get("city") in city_alerts:
            if send_alert(sub, city_alerts[sub["city"]]):
                sent += 1

    # Broadcast summary to SNS topic (visible in console for demo)
    if city_alerts:
        summary = {
            "date": datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%Y-%m-%d"),
            "cities_with_alerts": list(city_alerts.keys()),
            "total_alert_stations": len(alert_stations),
            "alerts_sent": sent,
        }
        sns.publish(
            TopicArn=SNS_TOPIC_ARN,
            Message=json.dumps(summary, indent=2),
            Subject="AERSI Daily Alert Summary",
        )

    return {
        "statusCode": 200,
        "body": json.dumps({
            "forecasts_loaded": len(forecasts),
            "alert_stations": len(alert_stations),
            "cities_affected": len(city_alerts),
            "alerts_sent": sent,
        })
    }
