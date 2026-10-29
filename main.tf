data "aws_caller_identity" "current" {}
data "aws_region" "current" {}
data "aws_partition" "current" {}
locals {
  account   = data.aws_caller_identity.current.account_id
  region    = data.aws_region.current.region
  partition = data.aws_partition.current.partition
  rule_arn  = "arn:${local.partition}:ses:${local.region}:${local.account}:receipt-rule-set/${var.receipt_rule_set_name}:receipt-rule/${var.name}"
  # SES can authorize the verified destination identity when sending in sandbox.
  destinations              = distinct([for rule in values(var.forwarding_rules) : lower(rule.forward_to)])
  destination_identity_arns = [for destination in local.destinations : "arn:${local.partition}:ses:${local.region}:${local.account}:identity/${destination}"]
  identity_arn              = "arn:${local.partition}:ses:${local.region}:${local.account}:identity/${var.domain}"
}
resource "aws_ses_domain_identity" "domain" { domain = var.domain }
resource "aws_ses_domain_dkim" "domain" { domain = var.domain }
resource "aws_s3_bucket" "raw" {
  bucket        = "${var.name}-${local.account}-${local.region}"
  force_destroy = false
  lifecycle {
    prevent_destroy = true
    precondition {
      condition     = var.raw_retention_days == null || var.observability_verified
      error_message = "Verify independent failure alerts and replay before enabling expiry."
    }
    precondition {
      condition     = lower(var.recipient_domain) == lower(var.domain) && endswith(lower(var.forward_from), "@${lower(var.domain)}") && alltrue([for destination in local.destinations : !endswith(destination, "@${lower(var.recipient_domain)}")])
      error_message = "Receiving domain must equal the verified domain, sender must belong to it, and destinations must be outside it."
    }
  }
}
resource "aws_s3_bucket_public_access_block" "raw" {
  bucket                  = aws_s3_bucket.raw.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
resource "aws_s3_bucket_ownership_controls" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule { object_ownership = "BucketOwnerEnforced" }
}
resource "aws_s3_bucket_server_side_encryption_configuration" "raw" {
  bucket = aws_s3_bucket.raw.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}
resource "aws_s3_bucket_lifecycle_configuration" "raw" {
  count  = var.raw_retention_days == null ? 0 : 1
  bucket = aws_s3_bucket.raw.id
  dynamic "rule" {
    for_each = { transient-mail = "incoming/", transient-metadata = "metadata/" }
    content {
      id     = rule.key
      status = "Enabled"
      filter { prefix = rule.value }
      expiration { days = var.raw_retention_days }
    }
  }
}
resource "aws_s3_bucket_policy" "raw" {
  bucket = aws_s3_bucket.raw.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Sid = "RequireTLS", Effect = "Deny", Principal = "*", Action = "s3:*", Resource = [aws_s3_bucket.raw.arn, "${aws_s3_bucket.raw.arn}/*"], Condition = { Bool = { "aws:SecureTransport" = "false" } } },
    { Sid = "SESDelivery", Effect = "Allow", Principal = { Service = "ses.amazonaws.com" }, Action = "s3:PutObject", Resource = "${aws_s3_bucket.raw.arn}/incoming/*", Condition = { StringEquals = { "AWS:SourceAccount" = local.account, "AWS:SourceArn" = local.rule_arn } } }
  ] })
}
resource "aws_dynamodb_table" "dedup" {
  name         = "${var.name}-delivery"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"
  attribute {
    name = "id"
    type = "S"
  }
  lifecycle { prevent_destroy = true }
}
resource "aws_sqs_queue" "failures" {
  name                      = "${var.name}-failures"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}
resource "aws_cloudwatch_log_group" "forwarder" {
  name              = "/aws/lambda/${var.name}"
  retention_in_days = 30
}
resource "aws_iam_role" "forwarder" {
  name                 = var.name
  path                 = var.runtime_role_path
  permissions_boundary = var.runtime_permissions_boundary_arn
  assume_role_policy   = jsonencode({ Version = "2012-10-17", Statement = [{ Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" }, Action = "sts:AssumeRole" }] })
}
resource "aws_iam_role_policy" "forwarder" {
  role = aws_iam_role.forwarder.id
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Allow", Action = ["logs:CreateLogStream", "logs:PutLogEvents"], Resource = "${aws_cloudwatch_log_group.forwarder.arn}:*" },
    { Effect = "Allow", Action = ["s3:GetObject"], Resource = "${aws_s3_bucket.raw.arn}/incoming/*" },
    { Effect = "Allow", Action = ["s3:GetObject", "s3:PutObject"], Resource = "${aws_s3_bucket.raw.arn}/metadata/*" },
    { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = aws_dynamodb_table.dedup.arn },
    { Effect = "Allow", Action = ["sqs:SendMessage"], Resource = aws_sqs_queue.failures.arn },
    { Effect = "Allow", Action = ["ses:SendEmail", "ses:SendRawEmail"], Resource = concat([local.identity_arn, aws_sesv2_configuration_set.forwarder.arn], local.destination_identity_arns), Condition = { StringEquals = { "ses:FromAddress" = var.forward_from }, "ForAllValues:StringEquals" = { "ses:Recipients" = local.destinations } } }
  ] })
}
data "archive_file" "forwarder" {
  type        = "zip"
  source_file = "${path.module}/lambda/forwarder.py"
  output_path = "${path.root}/build/${var.name}-forwarder.zip"
}
resource "aws_lambda_function" "forwarder" {
  function_name    = var.name
  role             = aws_iam_role.forwarder.arn
  runtime          = "python3.13"
  handler          = "forwarder.handler"
  timeout          = 60
  memory_size      = 512
  filename         = data.archive_file.forwarder.output_path
  source_code_hash = data.archive_file.forwarder.output_base64sha256
  environment {
    variables = {
      NAME              = var.name
      RAW_BUCKET        = aws_s3_bucket.raw.id
      DEDUP_TABLE       = aws_dynamodb_table.dedup.name
      FORWARD_FROM      = var.forward_from
      FORWARDING_RULES  = jsonencode(var.forwarding_rules)
      RECIPIENT_DOMAIN  = lower(var.recipient_domain)
      MAX_MESSAGE_BYTES = tostring(var.max_message_bytes)
      CONFIGURATION_SET = aws_sesv2_configuration_set.forwarder.configuration_set_name
    }
  }
  depends_on = [aws_iam_role_policy.forwarder, aws_cloudwatch_log_group.forwarder]
}
resource "aws_lambda_function_event_invoke_config" "forwarder" {
  function_name                = aws_lambda_function.forwarder.function_name
  maximum_retry_attempts       = 2
  maximum_event_age_in_seconds = 3600
  destination_config {
    on_failure { destination = aws_sqs_queue.failures.arn }
  }
}
resource "aws_lambda_permission" "ses" {
  statement_id   = "SESReceiptRule"
  action         = "lambda:InvokeFunction"
  function_name  = aws_lambda_function.forwarder.function_name
  principal      = "ses.amazonaws.com"
  source_account = local.account
  source_arn     = local.rule_arn
}
resource "aws_ses_receipt_rule" "forwarder" {
  name          = var.name
  rule_set_name = var.receipt_rule_set_name
  recipients    = [lower(var.recipient_domain)]
  enabled       = true
  scan_enabled  = true
  tls_policy    = "Optional"
  s3_action {
    bucket_name       = aws_s3_bucket.raw.id
    object_key_prefix = "incoming/"
    position          = 1
  }
  lambda_action {
    function_arn    = aws_lambda_function.forwarder.arn
    invocation_type = "Event"
    position        = 2
  }
  depends_on = [aws_s3_bucket_policy.raw, aws_lambda_permission.ses, aws_lambda_function_event_invoke_config.forwarder]
}
resource "aws_sesv2_configuration_set" "forwarder" { configuration_set_name = var.name }
resource "aws_sesv2_configuration_set_event_destination" "failures" {
  configuration_set_name = aws_sesv2_configuration_set.forwarder.configuration_set_name
  event_destination_name = "delivery-failures"
  event_destination {
    enabled              = true
    matching_event_types = ["BOUNCE", "COMPLAINT", "REJECT", "DELIVERY_DELAY"]
    sns_destination { topic_arn = var.alarm_topic_arn }
  }
}
locals {
  alarms = {
    lambda_errors        = { namespace = "AWS/Lambda", metric = "Errors", dimensions = { FunctionName = aws_lambda_function.forwarder.function_name }, statistic = "Sum" }
    lambda_throttles     = { namespace = "AWS/Lambda", metric = "Throttles", dimensions = { FunctionName = aws_lambda_function.forwarder.function_name }, statistic = "Sum" }
    destination_failures = { namespace = "AWS/Lambda", metric = "DestinationDeliveryFailures", dimensions = { FunctionName = aws_lambda_function.forwarder.function_name }, statistic = "Sum" }
    pending_failures     = { namespace = "AWS/SQS", metric = "ApproximateNumberOfMessagesVisible", dimensions = { QueueName = aws_sqs_queue.failures.name }, statistic = "Maximum" }
  }
}
resource "aws_cloudwatch_metric_alarm" "failure" {
  for_each            = local.alarms
  alarm_name          = "${var.name}-${each.key}"
  namespace           = each.value.namespace
  metric_name         = each.value.metric
  dimensions          = each.value.dimensions
  statistic           = each.value.statistic
  period              = 60
  evaluation_periods  = 1
  comparison_operator = "GreaterThanThreshold"
  threshold           = 0
  treat_missing_data  = "notBreaching"
  alarm_actions       = [var.alarm_topic_arn]
}
