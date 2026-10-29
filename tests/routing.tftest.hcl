mock_provider "aws" {
  mock_resource "aws_iam_role" {
    defaults = { arn = "arn:aws:iam::123456789012:role/routing-test" }
  }
  mock_resource "aws_sqs_queue" {
    defaults = { arn = "arn:aws:sqs:eu-west-2:123456789012:routing-test" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_region" {
    defaults = { region = "eu-west-2" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
}
mock_provider "archive" {}

variables {
  name                  = "routing-test"
  receipt_rule_set_name = "existing-set"
  domain                = "mail.example.org"
  recipient_domain      = "mail.example.org"
  forward_from          = "forwarder@mail.example.org"
  alarm_topic_arn       = "arn:aws:sns:eu-west-2:123456789012:alerts"
  forwarding_rules = {
    tests   = { patterns = ["original-*", "replacement-*"], forward_to = "tests@example.net" }
    support = { patterns = ["support"], forward_to = "support@example.net" }
  }
}

run "configured_role_and_routing" {
  command = plan
  variables {
    runtime_role_path                = "/workloads/"
    runtime_permissions_boundary_arn = "arn:aws:iam::123456789012:policy/runtime-boundary"
  }
  assert {
    condition     = aws_iam_role.forwarder.path == "/workloads/" && aws_iam_role.forwarder.permissions_boundary == "arn:aws:iam::123456789012:policy/runtime-boundary"
    error_message = "Runtime role must carry the caller's path and boundary."
  }
  assert {
    condition     = toset(aws_ses_receipt_rule.forwarder.recipients) == toset(["mail.example.org"])
    error_message = "Receipt rule must receive only the configured exact domain."
  }
  assert {
    condition     = toset(jsondecode(aws_lambda_function.forwarder.environment[0].variables.FORWARDING_RULES).support.patterns) == toset(["support"])
    error_message = "Named rules must reach the runtime."
  }
}
run "default_role" {
  command = plan
  assert {
    condition     = aws_iam_role.forwarder.path == "/" && aws_iam_role.forwarder.permissions_boundary == null
    error_message = "Role defaults must remain compatible."
  }
}
run "reject_loop_destination" {
  command = plan
  variables {
    forwarding_rules = { loop = { patterns = ["*"], forward_to = "loop@mail.example.org" } }
  }
  expect_failures = [aws_s3_bucket.raw]
}
run "reject_other_domain" {
  command = plan
  variables { recipient_domain = "outside.example.org" }
  expect_failures = [aws_s3_bucket.raw]
}
