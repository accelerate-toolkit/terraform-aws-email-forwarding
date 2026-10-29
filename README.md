# terraform-aws-email-forwarding

Public, versioned Terraform module for `accelerate-toolkit/terraform-aws-email-forwarding`.
The first source release, [v0.1.0](https://github.com/accelerate-toolkit/terraform-aws-email-forwarding/releases/tag/v0.1.0),
has passed independent consumer validation. A live sandbox deployment has passed
a manual forwarding test. See [verification results](docs/development.md#live-verification-results)
for evidence and remaining gaps. Terraform and the Lambda implementation are
versioned together; project roots pin immutable releases.

One module instance owns forwarding for one domain with named recipient-pattern rules. It owns the
SES domain identity/DKIM, recipient rule, private raw-message bucket, Lambda,
delivery ledger, failure queue and alarms. Do not instantiate it twice for the
same domain or import an existing identity without an ownership review.

The account foundation supplies an existing active SES receipt rule set. This
module never creates/activates that set and does not manage DNS. Forwarding runtime
stays in the workload account. Use explicit project-specific names and recipients.

## Delivery behavior

SES stores the original message in S3 before invoking Lambda asynchronously.
The forwarder preserves the MIME body tree, including HTML-only mail, attachments
and inline images. It replaces transport/authentication headers, uses a verified
sender in the domain and preserves the original Reply-To/From address as Reply-To.
No message body or address is deliberately logged. Forwarded mail has a loop marker.
Messages failing spam/virus checks are retained for review, not forwarded.

SES v2 raw delivery supports larger messages than v1; this module defaults to a
35 MB input/output limit below the SES v2 40 MB limit. Unsupported attachment types
or malformed/oversize messages fail visibly; attachments are never silently removed.
See [SES attachments](https://docs.aws.amazon.com/ses/latest/dg/attachments.html).

The DynamoDB ledger suppresses already-successful duplicates. SES has no send
idempotency token: exactly-once delivery cannot be guaranteed. An unfinished ledger
entry is treated as ambiguous and fails further automatic attempts. This includes
some failures known to occur before sending; the initial implementation deliberately
requires operator review for all unfinished attempts. Lambda retries twice, then
sends the invocation record to the failure queue (14-day retention). Successful
ledger entries have no automatic expiry, so delayed replays remain deduplicated.
Raw messages also have **no expiry by default**.

## Inputs and boundaries

Required: `name`, `receipt_rule_set_name`, `domain`, `recipient_domain`,
`forwarding_rules`, `forward_from`, and `alarm_topic_arn`. Use non-sensitive example addresses when
sharing plans or tests; real destination configuration belongs in private repos.

The alert topic must already exist in the same account/region, have confirmed
independent delivery, and allow SES event publishing for this configuration set
and CloudWatch alarm publishing. Its policy and subscribers are caller-owned.
Do not use the forwarding route itself as the sole alert destination.

Outputs include MX, domain-verification TXT and DKIM CNAME records. Publish
verification/DKIM first. Publish MX only after the complete delivery path is ready;
outputs do not automatically alter DNS. Existing SPF/DMARC remain separately owned.
In the SES sandbox, verify the destination too, or obtain production access before
attempting delivery to unverified recipients. See the
[sandbox authorization observation](docs/development.md#sandbox-destination-authorization)
for why the send policy also includes the exact destination identity ARNs.

`raw_retention_days = 30` requires `observability_verified = true`. That assertion
must follow real alert/recovery tests; Terraform cannot prove an inbox receives
alerts. The module has no end-to-end canary yet. Lambda errors/throttling,
failed failure-destination delivery, failure-queue depth, and SES bounce/complaint/
reject/delay events are covered. Failures before Lambda and silent routing problems
still require an independently scheduled canary. Do not enable expiry or cut over
live mail until that gap is addressed.

## Recovery

Authorization failures include a fixed diagnostic category for the denied SES
action/resource and a validated AWS request ID, when supplied by AWS. These are
clues, not proof of the effective policy decision; unknown formats remain
`unknown`. Raw SDK error messages, resource ARNs and mailbox addresses are never
logged. Use a fresh test message after deploying diagnostic changes; existing
unfinished ledger entries deliberately prevent automatic replay.

1. Inspect the failure queue without deleting it; locate the SES message ID in its
   invocation payload, the raw S3 object `incoming/<messageId>` and ledger records. New ledger keys are `<messageId>#<sha256(lowercase destination)>`; legacy entries use the message ID alone.
   Avoid exposing raw messages or addresses in logs, tickets or shell history.
2. For each destination entry, if status is `sent`, correlate its `delivery_id` with SES delivery events and
   the destination inbox. Do not remove successful deduplication records to retry.
3. If status is `processing`, investigate whether SES accepted the message. A crash
   after send but before ledger update is ambiguous. Confirm receipt or explicitly
   accept duplicate risk before replay; never bulk-reset the ledger.
4. Fix the cause. For a reviewed unsent/accepted-risk case only, the human operator
   can remove only that destination’s exact unfinished ledger entry and replay the original SES event
   (`requestPayload` in the Lambda failure destination envelope). Replays use the
   retained S3 object; invoking a queue envelope directly is not supported.
5. Confirm delivery and the ledger's `sent` status before removing the failure
   queue message. Do not delete the raw object during investigation.

## Local checks and release gates

Release automation follows the dedicated main-branch [release workflow](docs/releases.md).
Source publication and production rollout are separate decisions.
`make release-plan` previews a version without publishing.

Run `make`, then `make check`. Tests use synthetic MIME messages and mocked AWS
clients; validation runs in containers without host credentials or state.
No Make target applies infrastructure.

Before production consumer rollout: verify Terraform plans in the project root, supply the alert
topic/policy, add a canary, run sandbox delivery/replay/bounce tests, and review
IAM with AWS. None of those live checks have been claimed as passed. A local
candidate is not a production-ready release. No customer messages were sent.


## Public test harness, private deployment settings

See [the manual end-to-end loop](examples/end-to-end/README.md). It deploys the local
module into a caller-selected NonProd account and checks a six-case routing matrix
using manually downloaded S3 objects and messages from two external inboxes.
A separate recovery run supports a deliberate destination-B failure and ledger
checkpoints before replay and after repair. The same ignored `config.local.json`
feeds Terraform and the fixture generator; public examples use fictional settings.
No organization account IDs, real mailboxes or DNS zones belong in published files.
`make check` now includes harness tests and a publication-candidate privacy check.
This is a development feedback loop; a production canary remains a separate design.


## APM and Renovate

Run `make setup` for locked APM skills and advisory hooks, and `make check` for
local verification. See [development and GitHub activation](docs/development.md).
The shared Renovate preset enables automerge; this module's credential-free checks
must pass first. Dependency updates never deploy infrastructure. Local onboarding
is separate from publication and successful live runner verification.

## Pattern routing and migration

The new interface replaces `recipient` and top-level `forward_to`; this is a
breaking release. Replace an old `recipient = "support@mail.example.org"` with:

```hcl
recipient_domain = "mail.example.org" # must equal domain
forwarding_rules = {
  support = {
    patterns   = ["support", "help"]
    forward_to = "support@example.net"
  }
  acceptance = {
    patterns   = ["original-*", "replacement-*"]
    forward_to = "tests@example.net"
  }
}
```

Patterns match the entire local part case-insensitively. Only `*` is special,
matching zero or more characters. `+`, `?` and brackets are literal. A literal
`support` excludes `support+123`. The domain matches exactly, excluding subdomains.
All matching rules apply; multiple matches for one destination produce one forward
per SES message ID. Destinations are normalized to lowercase and must be outside
the receiving domain. Different destinations have independent delivery ledger
entries; a failed destination does not block attempts to other destinations.
Unfinished attempts remain ambiguous and need operator review, even on retry.
Exactly-once delivery is not guaranteed.

SES now accepts and stores messages for the whole receiving domain. Lambda filters
forwarding, not SMTP acceptance: unmatched messages retain MIME and metadata and
return `filtered` without alarms or delivery attempts. Review this broader receipt
scope and other account-owned rules before upgrading an exact-recipient deployment.
No rule priority or automatic bounce is provided.

Metadata is written to `metadata/<messageId>.json` before scanning checks, delivery
locks and forwarding. Version 1 contains `message_id`, `mime_key`,
`envelope_recipients` (SES mail destinations), `receipt_recipients` (SES receipt
scope), `routes` keyed by destination with matched `rules` and `recipients`, and
routing `outcome` (`matched` or `filtered`). These are SES envelope fields, not
untrusted To/Cc headers; they include Bcc when reported by SES. The outcome is a
routing decision, not a delivery status. Original MIME is untouched in S3.

The first metadata write wins using a conditional S3 write. Retries reuse that
routing snapshot even if rules change. Keep old destinations authorized until
pending deliveries are resolved; do not edit/delete metadata to reroute a replay.
Metadata-write failures retry before any send lock is acquired. Lambda failures
before a successful write can leave MIME without metadata; monitor the failure
queue and recover the original event. Legacy message-level ledger entries still
suppress successful deliveries or block ambiguous attempts after migration.

`reader_locations` exposes bucket name/ARN, region, `incoming/` MIME prefix and
`metadata/` prefix. Consumers attach their own reader policy: `s3:ListBucket` on
the bucket ARN restricted by `s3:prefix` to these prefixes, and `s3:GetObject` on
both object prefixes. Readers should discover metadata then fetch its `mime_key`,
and retry while asynchronous processing is pending. Restrict metadata write access
to the runtime; it includes private recipient and destination addresses.
Both prefixes have no expiry by default. The existing reviewed 30-day expiry
option now covers both MIME and metadata; expiry does not reset the delivery ledger.

Optional `runtime_role_path` (default `/`) and
`runtime_permissions_boundary_arn` (default null) configure only the Lambda role.
A boundary limits permissions; it does not grant them. Changing an existing role's
path may require replacement; review the plan. Caller AWS provider `default_tags`
propagate ownership tags to resources supported by that provider; SES classic
identity/receipt-rule resources do not support tags. No application reader role
or policy attachment is managed here.

Run `make terraform-test` for credential-free mocked routing and IAM plans
using the pinned Terraform Docker image. Offline tests cover routing, metadata retries and independent
delivery recovery. Live multi-recipient delivery and S3 retrieval still require
human-operated deployment and verification; they are not claimed as verified.
