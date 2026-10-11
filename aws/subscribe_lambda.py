"""
aws/subscribe_lambda.py
AERSI subscriber registration Lambda
Adds a user to the aersi-subscribers DynamoDB table and subscribes them to SNS.
"""
import json, boto3, os

SNS_TOPIC_ARN = os.environ["SNS_TOPIC_ARN"]
dynamodb = boto3.resource("dynamodb", region_name="ap-south-1")
sns = boto3.client("sns", region_name="ap-south-1")

def lambda_handler(event, context):
    body = json.loads(event.get("body", "{}"))
    name     = body.get("name", "").strip()
    contact  = body.get("contact", "").strip()   # email or phone (+91XXXXXXXXXX)
    city     = body.get("city", "").strip()
    protocol = "email" if "@" in contact else "sms"

    if not (name and contact and city):
        return {"statusCode": 400, "body": json.dumps({"error": "name, contact, city required"})}

    # Subscribe to SNS topic
    sns.subscribe(TopicArn=SNS_TOPIC_ARN, Protocol=protocol, Endpoint=contact)

    # Save to DynamoDB
    table = dynamodb.Table("aersi-subscribers")
    table.put_item(Item={"email": contact, "name": name, "city": city, "protocol": protocol})

    return {
        "statusCode": 200,
        "body": json.dumps({"message": f"Subscribed {name} ({contact}) for {city} alerts."})
    }
