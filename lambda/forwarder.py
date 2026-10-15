"""SES -> S3 -> Lambda forwarding. Never log message content or addresses."""
import copy
import os
import time
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses


def compose(raw, sender, destination, marker, max_bytes):
    if len(raw) > max_bytes:
        raise ValueError("Message exceeds configured forwarding limit")
    original = BytesParser(policy=policy.SMTP).parsebytes(raw)
    if original.get("X-Email-Forwarded"):
        raise ValueError("Forwarding loop marker present")
    if any(part.defects for part in original.walk()):
        raise ValueError("Malformed MIME message; retained for review")
    # Retain the MIME tree (HTML, inline images and attachments), but not the
    # original transport/authentication headers or untrusted routing headers.
    message = copy.deepcopy(original)
    for name in set(message.keys()):
        if name.lower() not in {"mime-version", "content-type", "content-transfer-encoding", "content-disposition", "content-id", "content-description"}:
            del message[name]
    message["From"] = sender
    message["To"] = destination
    message["Subject"] = str(original.get("Subject", "(no subject)"))
    addresses = getaddresses(original.get_all("Reply-To", []) or original.get_all("From", []))
    if addresses:
        message["Reply-To"] = ", ".join(address for _, address in addresses if address)
    message["X-Email-Forwarded"] = marker
    message["Auto-Submitted"] = "auto-generated"
    output = message.as_bytes(policy=policy.SMTP)
    if len(output) > max_bytes:
        raise ValueError("Forwarded MIME exceeds configured limit")
    return output


def process(record, config, s3, ses, table):
    receipt = record["ses"]["receipt"]
    mail = record["ses"]["mail"]
    message_id = mail["messageId"]
    if config["RECIPIENT"].lower() not in [r.lower() for r in receipt["recipients"]]:
        raise ValueError("Unexpected envelope recipient")
    for verdict in ("spamVerdict", "virusVerdict"):
        if receipt.get(verdict, {}).get("status") != "PASS":
            raise ValueError("Message did not pass scanning; retained for review")
    # Retain deduplication records: no TTL means late replays cannot resend
    # successfully forwarded mail. Raw payload retention is managed separately.
    try:
        table.put_item(Item={"id": message_id, "status": "processing", "created": int(time.time())},
                       ConditionExpression="attribute_not_exists(id)")
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"id": message_id}, ConsistentRead=True).get("Item", {})
        if existing.get("status") == "sent":
            return "duplicate"
        # Do not automatically resend after a crash/timeout: SES has no send
        # idempotency token. Failed/ambiguous work must reach the failure queue.
        raise RuntimeError("Unfinished delivery requires review before replay") from None
    try:
        response = s3.get_object(Bucket=config["RAW_BUCKET"], Key="incoming/" + message_id)
        limit = int(config["MAX_MESSAGE_BYTES"])
        body = response["Body"]
        try:
            raw = body.read(limit + 1)
        finally:
            body.close()
        outgoing = compose(raw, config["FORWARD_FROM"], config["FORWARD_TO"], config["NAME"], limit)
        result = ses.send_email(FromEmailAddress=config["FORWARD_FROM"],
                                Destination={"ToAddresses": [config["FORWARD_TO"]]},
                                Content={"Raw": {"Data": outgoing}},
                                ConfigurationSetName=config["CONFIGURATION_SET"])
        table.update_item(Key={"id": message_id},
                          UpdateExpression="SET #s = :sent, delivery_id = :delivery",
                          ExpressionAttributeNames={"#s": "status"},
                          ExpressionAttributeValues={":sent": "sent", ":delivery": result["MessageId"]})
        return "sent"
    except Exception:
        # Never include SDK exception strings: these can expose recipient data.
        raise RuntimeError("Delivery incomplete; inspect retained message and delivery evidence") from None


def handler(event, context):
    import boto3
    config = os.environ
    table = boto3.resource("dynamodb").Table(config["DEDUP_TABLE"])
    s3, ses = boto3.client("s3"), boto3.client("sesv2")
    return {"results": [process(record, config, s3, ses, table) for record in event["Records"]]}
