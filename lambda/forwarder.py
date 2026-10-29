"""SES -> S3 -> Lambda forwarding. Never log message content or addresses."""
import copy
import hashlib
import json
import os
import re
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



def authorization_diagnostic(response, config):
    """Classify SDK denial details; never return arbitrary service text."""
    detail = response.get("Error", {})
    message = detail.get("Message", "") if isinstance(detail, dict) else ""
    message = message if isinstance(message, str) else ""
    actions = sorted(set(re.findall(r"\bses:(SendEmail|SendRawEmail)\b", message)))
    resources = set()
    for resource in re.findall(r"arn:[a-z0-9-]+:ses:[a-z0-9-]+:[0-9]{12}:([^\s\"']+)", message):
        resource = resource.rstrip(".,;")
        if resource == "identity/" + config["FORWARD_FROM"]:
            resources.add("sender_address")
        elif resource == "identity/" + config["FORWARD_FROM"].rsplit("@", 1)[-1]:
            resources.add("sender_domain")
        elif resource == "identity/" + config["FORWARD_TO"]:
            resources.add("destination_address")
        elif resource == "configuration-set/" + config["CONFIGURATION_SET"]:
            resources.add("configuration_set")
        else:
            resources.add("other_ses_resource")
    reasons = []
    for phrase, label in (
        ("no identity-based policy allows", "missing_identity_allow"),
        ("explicit deny", "explicit_deny"),
        ("service control policy", "scp"),
        ("resource control policy", "rcp"),
        ("permissions boundary", "permissions_boundary"),
        ("session policy", "session_policy"),
    ):
        if phrase in message.lower():
            reasons.append(label)
    metadata = response.get("ResponseMetadata", {})
    request_id = metadata.get("RequestId", "") if isinstance(metadata, dict) else ""
    if not isinstance(request_id, str) or not re.fullmatch(r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}", request_id):
        request_id = "unavailable"
    return (f"; action={','.join(actions) or 'unknown'}"
            f"; resource={','.join(sorted(resources)) or 'unknown'}"
            f"; reason={','.join(reasons) or 'unknown'}; aws_request_id={request_id}")


class DeliveryFailure(RuntimeError):
    """Sanitized failure safe to aggregate across destinations."""


def routing_metadata(record, config):
    mail, receipt = record["ses"]["mail"], record["ses"]["receipt"]
    domain = config["RECIPIENT_DOMAIN"].lower()
    recipients = receipt["recipients"]
    routes = {}
    for name, rule in sorted(json.loads(config["FORWARDING_RULES"]).items()):
        matched = []
        for recipient in recipients:
            local, separator, host = recipient.rpartition("@")
            if separator and host.lower() == domain and any(
                re.fullmatch(".*".join(re.escape(part) for part in pattern.lower().split("*")), local.lower())
                for pattern in rule["patterns"]
            ):
                matched.append(recipient)
        if matched:
            destination = rule["forward_to"].lower()
            route = routes.setdefault(destination, {"rules": [], "recipients": []})
            route["rules"].append(name)
            route["recipients"] = sorted(set(route["recipients"] + matched))
    return {"schema_version": 1, "message_id": mail["messageId"],
            "mime_key": "incoming/" + mail["messageId"],
            "envelope_recipients": mail.get("destination", recipients),
            "receipt_recipients": recipients, "routes": routes,
            "outcome": "matched" if routes else "filtered"}


def process(record, config, s3, ses, table):
    message_id = record["ses"]["mail"]["messageId"]
    metadata = routing_metadata(record, config)
    key = "metadata/" + message_id + ".json"
    try:
        s3.put_object(Bucket=config["RAW_BUCKET"], Key=key,
                      Body=json.dumps(metadata, sort_keys=True).encode(),
                      ContentType="application/json", IfNoneMatch="*")
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") != "PreconditionFailed":
            raise RuntimeError("Metadata persistence failed; retry required") from None
        # The first routing snapshot wins, including across configuration changes.
        try:
            body = s3.get_object(Bucket=config["RAW_BUCKET"], Key=key)["Body"]
            try:
                metadata = json.loads(body.read())
            finally:
                body.close()
        except Exception:
            raise RuntimeError("Metadata retrieval failed; retry required") from None
    if not metadata["routes"]:
        return "filtered"
    for verdict in ("spamVerdict", "virusVerdict"):
        if record["ses"]["receipt"].get(verdict, {}).get("status") != "PASS":
            raise ValueError("Message did not pass scanning; retained for review")
    # Legacy message-level entries must never be bypassed during migration.
    try:
        legacy = table.get_item(Key={"id": message_id}, ConsistentRead=True).get("Item", {})
    except Exception:
        raise RuntimeError("Legacy delivery ledger unavailable; retry required") from None
    if legacy.get("status") == "sent":
        return "duplicate"
    if legacy.get("status") == "processing":
        raise RuntimeError("Unfinished legacy delivery requires review before replay")
    results, failures = [], []
    for destination in sorted(metadata["routes"]):
        try:
            results.append(deliver(message_id, destination, config, s3, ses, table))
        except Exception as error:
            failures.append(str(error) if isinstance(error, DeliveryFailure) else "Delivery ledger unavailable; retry required")
    if failures:
        raise RuntimeError("; ".join(failures)) from None
    return "duplicate" if all(result == "duplicate" for result in results) else "sent"


def deliver(message_id, destination, config, s3, ses, table):
    config = dict(config, FORWARD_TO=destination)
    delivery_key = message_id + "#" + hashlib.sha256(destination.encode()).hexdigest()
    # Retain deduplication records: no TTL means late replays cannot resend
    # successfully forwarded mail. Raw payload retention is managed separately.
    try:
        table.put_item(Item={"id": delivery_key, "status": "processing", "created": int(time.time())},
                       ConditionExpression="attribute_not_exists(id)")
    except Exception as error:
        if getattr(error, "response", {}).get("Error", {}).get("Code") != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"id": delivery_key}, ConsistentRead=True).get("Item", {})
        if existing.get("status") == "sent":
            return "duplicate"
        # Do not automatically resend after a crash/timeout: SES has no send
        # idempotency token. Failed/ambiguous work must reach the failure queue.
        raise DeliveryFailure("Unfinished delivery requires review before replay") from None
    stage = "read_message"
    try:
        response = s3.get_object(Bucket=config["RAW_BUCKET"], Key="incoming/" + message_id)
        limit = int(config["MAX_MESSAGE_BYTES"])
        body = response["Body"]
        try:
            raw = body.read(limit + 1)
        finally:
            body.close()
        stage = "compose_message"
        outgoing = compose(raw, config["FORWARD_FROM"], config["FORWARD_TO"], config["NAME"], limit)
        stage = "send_email"
        result = ses.send_email(FromEmailAddress=config["FORWARD_FROM"],
                                Destination={"ToAddresses": [config["FORWARD_TO"]]},
                                Content={"Raw": {"Data": outgoing}},
                                ConfigurationSetName=config["CONFIGURATION_SET"])
        stage = "record_delivery"
        table.update_item(Key={"id": delivery_key},
                          UpdateExpression="SET #s = :sent, delivery_id = :delivery",
                          ExpressionAttributeNames={"#s": "status"},
                          ExpressionAttributeValues={":sent": "sent", ":delivery": result["MessageId"]})
        return "sent"
    except Exception as error:
        # Emit only our fixed stage and recognized codes, never SDK messages,
        # request parameters, arbitrary exception names or response payloads.
        response = getattr(error, "response", {})
        detail = response.get("Error", {}) if isinstance(response, dict) else {}
        code = detail.get("Code") if isinstance(detail, dict) else None
        allowed_codes = {
            "AccessDenied", "AccessDeniedException", "MessageRejected",
            "MailFromDomainNotVerifiedException", "NotFoundException",
            "BadRequestException", "TooManyRequestsException",
            "LimitExceededException", "AccountSuspendedException",
            "SendingPausedException", "ConfigurationSetDoesNotExistException",
            "ConfigurationSetSendingPausedException", "NoSuchKey",
            "InvalidParameterValue", "ValidationException", "InternalFailure",
            "ServiceUnavailable", "RequestTimeout",
        }
        code = code if isinstance(code, str) and code in allowed_codes else "Unclassified"
        diagnostic = authorization_diagnostic(response, config) if code in {"AccessDenied", "AccessDeniedException"} else ""
        raise DeliveryFailure(f"Delivery incomplete; stage={stage}; code={code}{diagnostic}; retained for review") from None


def handler(event, context):
    import boto3
    config = os.environ
    table = boto3.resource("dynamodb").Table(config["DEDUP_TABLE"])
    s3, ses = boto3.client("s3"), boto3.client("sesv2")
    return {"results": [process(record, config, s3, ses, table) for record in event["Records"]]}
