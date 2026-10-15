output "dns_records" {
  value = concat([
    { name = var.domain, type = "MX", records = ["10 inbound-smtp.${local.region}.amazonaws.com"] },
    { name = "_amazonses.${var.domain}", type = "TXT", records = [aws_ses_domain_identity.domain.verification_token] }
    ], [for token in aws_ses_domain_dkim.domain.dkim_tokens : {
      name = "${token}._domainkey.${var.domain}", type = "CNAME", records = ["${token}.dkim.amazonses.com"]
  }])
  description = "Project DNS owner publishes these separately; MX only after delivery readiness checks."
}
output "raw_bucket" { value = aws_s3_bucket.raw.id }
output "failure_queue_url" { value = aws_sqs_queue.failures.url }
output "delivery_table" { value = aws_dynamodb_table.dedup.name }
output "function_name" { value = aws_lambda_function.forwarder.function_name }
