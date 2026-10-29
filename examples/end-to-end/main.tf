terraform {
  required_version = ">= 1.5.0, < 2.0.0"
  required_providers {
    aws     = { source = "hashicorp/aws", version = ">= 6.68.0, < 7.0.0" }
    archive = { source = "hashicorp/archive", version = ">= 2.7.1, < 3.0.0" }
  }
  backend "s3" {}
}
variable "account_id" { type = string }
variable "region" { type = string }
variable "resource_prefix" { type = string }
variable "hosted_zone_id" { type = string }
variable "domain" { type = string }
variable "receipt_rule_set_name" { type = string }
variable "forward_from" { type = string }
variable "destination_a" { type = string }
variable "destination_b" { type = string }
variable "runtime_role_path" {
  type    = string
  default = "/"
}
variable "runtime_permissions_boundary_arn" {
  type    = string
  default = null
}
variable "simulate_destination_b_failure" {
  description = "Test-only explicit SES deny for inbox B. Human plan/apply required; false for normal runs."
  type        = bool
  default     = false
}
variable "alert_email" { type = string }
variable "enable_mx" { type = bool }

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.account_id]
}
data "aws_partition" "current" {}
data "aws_route53_zone" "test" { zone_id = var.hosted_zone_id }

resource "aws_sns_topic" "alerts" { name = "${var.resource_prefix}-alerts" }
resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    # SNS topic policies accept only topic-scoped actions, not sns:*.
    # Preserve the standard owner topic operations explicitly.
    { Sid = "Owner", Effect = "Allow", Principal = { AWS = "arn:${data.aws_partition.current.partition}:iam::${var.account_id}:root" }, Action = ["sns:GetTopicAttributes", "sns:SetTopicAttributes", "sns:AddPermission", "sns:RemovePermission", "sns:DeleteTopic", "sns:Subscribe", "sns:ListSubscriptionsByTopic", "sns:Publish"], Resource = aws_sns_topic.alerts.arn },
    { Sid = "SES", Effect = "Allow", Principal = { Service = "ses.amazonaws.com" }, Action = "sns:Publish", Resource = aws_sns_topic.alerts.arn, Condition = { StringEquals = { "AWS:SourceAccount" = var.account_id, "AWS:SourceArn" = "arn:${data.aws_partition.current.partition}:ses:${var.region}:${var.account_id}:configuration-set/${var.resource_prefix}" } } },
    { Sid = "Alarms", Effect = "Allow", Principal = { Service = "cloudwatch.amazonaws.com" }, Action = "sns:Publish", Resource = aws_sns_topic.alerts.arn, Condition = { StringEquals = { "AWS:SourceAccount" = var.account_id }, ArnLike = { "AWS:SourceArn" = "arn:${data.aws_partition.current.partition}:cloudwatch:${var.region}:${var.account_id}:alarm:${var.resource_prefix}-*" } } }
  ] })
}
resource "aws_sns_topic_subscription" "alerts" {
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}
# A human apply requests verification; the operator must confirm it in the inbox.
moved {
  from = aws_ses_email_identity.destination
  to   = aws_ses_email_identity.destinations["a"]
}
resource "aws_ses_email_identity" "destinations" {
  for_each = { a = var.destination_a, b = var.destination_b }
  email    = each.value
}
module "forwarding" {
  source           = "../.."
  name             = var.resource_prefix
  domain           = var.domain
  recipient_domain = var.domain
  forwarding_rules = {
    acceptance = { patterns = ["original-*", "replacement-*"], forward_to = var.destination_a }
    overlap    = { patterns = ["original-*"], forward_to = var.destination_b }
    support    = { patterns = ["support"], forward_to = var.destination_a }
  }
  runtime_role_path                = var.runtime_role_path
  runtime_permissions_boundary_arn = var.runtime_permissions_boundary_arn
  forward_from                     = var.forward_from
  receipt_rule_set_name            = var.receipt_rule_set_name
  alarm_topic_arn                  = aws_sns_topic.alerts.arn
  raw_retention_days               = null
  observability_verified           = false
  depends_on                       = [aws_sns_topic_policy.alerts]
}
resource "aws_route53_record" "verification" {
  zone_id = var.hosted_zone_id
  name    = module.forwarding.dns_records[1].name
  type    = "TXT"
  ttl     = 300
  records = module.forwarding.dns_records[1].records
  lifecycle {
    precondition {
      condition     = endswith(var.domain, ".${trimsuffix(data.aws_route53_zone.test.name, ".")}")
      error_message = "Use a dedicated test subdomain within the configured delegated zone, never its apex."
    }
  }
}
resource "aws_route53_record" "dkim" {
  count   = 3
  zone_id = var.hosted_zone_id
  name    = module.forwarding.dns_records[2 + count.index].name
  type    = "CNAME"
  ttl     = 300
  records = module.forwarding.dns_records[2 + count.index].records
}
resource "aws_route53_record" "mx" {
  count      = var.enable_mx ? 1 : 0
  zone_id    = var.hosted_zone_id
  name       = var.domain
  type       = "MX"
  ttl        = 300
  records    = module.forwarding.dns_records[0].records
  depends_on = [aws_route53_record.verification, aws_route53_record.dkim]
}
output "test_resources" {
  value = {
    function_name     = module.forwarding.function_name
    raw_bucket        = module.forwarding.raw_bucket
    failure_queue_url = module.forwarding.failure_queue_url
    delivery_table    = module.forwarding.delivery_table
    reader_locations  = module.forwarding.reader_locations
  }
}

# Deliberate, reversible failure for the isolated recovery test. Never used by CI.
resource "aws_iam_role_policy" "deny_destination_b" {
  count = var.simulate_destination_b_failure ? 1 : 0
  name  = "test-deny-destination-b"
  role  = var.resource_prefix
  policy = jsonencode({ Version = "2012-10-17", Statement = [
    { Effect = "Deny", Action = ["ses:SendEmail", "ses:SendRawEmail"], Resource = "*", Condition = { "ForAnyValue:StringEquals" = { "ses:Recipients" = [lower(var.destination_b)] } } }
  ] })
  depends_on = [module.forwarding]
}
