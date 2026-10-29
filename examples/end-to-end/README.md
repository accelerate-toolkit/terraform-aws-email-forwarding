# Human-operated routing and recovery tests

The loop is **offline checks → plan → human apply → external send → S3/inbox
verification → repeat**. The example uses the local module (`../..`). All real
account, profile, state, zone, domain and mailbox settings belong in ignored
`config.local.json`. Public examples are fictional. No tool sends email, reads
mailboxes, replays Lambda events, resets the ledger or deletes resources for you.

## Private configuration and prerequisites

Use an isolated NonProd account, an existing S3 state backend, a delegated public
DNS zone and an already active SES receipt-rule set. Account provisioning, backend
ownership, DNS delegation and rule-set activation remain caller-owned. Use a
dedicated test subdomain; the example rejects the zone apex. Never use live mail.

Install the Terraform version in `../../.terraform-version`, uv and AWS CLI. Use a
named SSO profile with deployment permissions. The wrapper checks the account and
specific SSO role prefix, clears ambient AWS/TF overrides, and rejects example
identifiers for AWS use. It does not log in or fall back to static credentials.

Copy `config.example.json` only if no local config exists; otherwise edit the
existing file. Configuration now requires:

- `destination_a` and `destination_b`: two distinct external inboxes that you can
  inspect separately, outside the receiving domain. Verify both in the SES sandbox.
- `test_sender`: the external account used to send fixtures; it can be one of the
  destination accounts. No mailbox credentials belong in the config.
- `forward_from`: the sender in the receiving domain.
- `alert_email`: independently delivered alerts, not solely the forwarding route.
- Optional `runtime_role_path` (default `/`) and
  `runtime_permissions_boundary_arn` (default null), when the test environment
  requires them. A boundary must allow the necessary runtime operations.
- Optional `simulate_destination_b_failure` (default false), used only in the
  deliberate recovery test below. Leave it false for the routing matrix.

To migrate an existing local config, rename `destination` to `destination_a`, add
`destination_b`, and remove `recipient`. Preserve all account/backend/domain settings.
The Terraform `moved` block retains the existing destination verification resource
as inbox A; inspect the plan and keep A's address unchanged during this migration.
The local file is never overwritten by the test tools. Historical single-message
artifacts remain readable with `test.py verify`.

All generated backend settings, variables, mail and evidence live under ignored
`build/end-to-end/`, with private files written as mode 0600. Plans and Terraform
outputs can contain private values; never publish them. State remains in S3.

## Deploy and check readiness

After logging into the configured SSO profile:

```sh
make check
./examples/end-to-end/tf.sh init
./examples/end-to-end/tf.sh plan
# Human operator only:
./examples/end-to-end/tf.sh apply
```

For a new deployment, start with `enable_mx: false`. Confirm both SES inbox
verification emails and the SNS alert subscription, domain verification/DKIM,
sandbox readiness, and independent alert delivery. Then set `enable_mx: true`,
review another plan, and apply it yourself. Confirm public MX resolution before
sending. Applies may trigger verification emails and normal AWS charges.

Review these changes in an upgrade plan: whole-domain receipt scope, the named
rules below, both authorized destinations, S3 metadata read/write access, runtime
role path/boundary, and no-expiry retention. The optional failure policy must be
absent during normal tests. No assistant or Make target applies infrastructure.

The example deploys these rules; they do not change between run IDs:

| Rule | Local-part patterns | Destination |
|---|---|---|
| acceptance | `original-*`, `replacement-*` | A |
| overlap | `original-*` | B |
| support | `support` | A |

## Prepare and send the routing matrix

```sh
uv run --script examples/end-to-end/suite.py prepare
```

The command prints a suite run ID and six fixture paths. Its private
`build/end-to-end/runs/<suite-id>/suite.json` records expected recipients, routes,
inboxes and each case's separate fixture ID. Each fixture directory contains
`outbound.eml`, `body.txt`, `body.html`, `fixture.txt`, `inline.png` and `result.json`.
Each case has its own subject/body marker and attachment hash. Dynamic addresses
share the suite ID, so multiple roles from one run are distinguishable.

| Case | Envelope recipients | Expected copies |
|---|---|---|
| original | `original-<suite-id>@<domain>` | A: 1, B: 1 |
| replacement | `replacement-<suite-id>@<domain>` | A: 1, B: 0 |
| multiple | To: original address; Bcc: replacement address | A: 1, B: 1 |
| literal | `support@<domain>` | A: 1, B: 0 |
| plus | `support+<suite-id>@<domain>` | A: 0, B: 0 |
| unmatched | `unmatched-<suite-id>@<domain>` | A: 0, B: 0 |

Send each fixture exactly once through the configured external account, with text,
HTML, attachment and inline image. Use a MIME-capable client that sends imported
drafts, or compose manually in Gmail using the generated subject/body marker,
HTML formatting, `fixture.txt`, and **Insert photo → Inline** for `inline.png`.
Gmail's browser UI does not send an imported EML as the original message. Do not
attach `outbound.eml` as a substitute for sending it.

For `multiple`, put replacement in the actual Bcc envelope; the client should strip
the Bcc header before transmission. Do not send separate messages for the two
recipients. The storage verifier requires both envelope recipients in SES metadata
and the Bcc address absent from the original MIME's To/Cc/Bcc headers.

Filtered cases are still accepted and stored by SES. This tests Lambda forwarding
filters, not SMTP rejection. Out-of-domain matching is covered offline; this
harness does not send mail to domains outside the isolated receiving scope.

## Retrieve and verify S3 evidence for every case

Use the configured account/profile and the `test_resources` Terraform output to
locate `reader_locations` (bucket, region and prefixes), the delivery table and
function. You can save the output privately:

```sh
./examples/end-to-end/tf.sh output -json test_resources > build/end-to-end/resources.json
```

Using the AWS console or a read-only AWS CLI session, inspect new objects under
`incoming/`, identify each case by its subject/body marker, and download the raw
object and `metadata/<same-SES-message-id>.json` into that case's fixture directory.
Do not use the MIME `Message-ID` header as the S3 key. Metadata may arrive later
than raw MIME; allow asynchronous processing to complete before diagnosing absence.
No queue polling or mailbox API synchronization is required.

For example, with private shell variables `PROFILE`, `REGION`, `BUCKET`,
`MESSAGE_ID` and `CASE_DIR` populated from the reviewed deployment and selected case:

```sh
umask 077
aws s3api get-object --profile "$PROFILE" --region "$REGION" \
  --bucket "$BUCKET" --key "incoming/$MESSAGE_ID" "$CASE_DIR/downloaded.eml"
aws s3api get-object --profile "$PROFILE" --region "$REGION" \
  --bucket "$BUCKET" --key "metadata/$MESSAGE_ID.json" "$CASE_DIR/downloaded.json"
uv run --script examples/end-to-end/suite.py verify-storage \
  --run-id <suite-id> --case <case> --message-id <SES-message-id> \
  --metadata <fixture-directory>/downloaded.json \
  --mime <fixture-directory>/downloaded.eml --confirm-s3
```

Only use `--confirm-s3` after confirming that these files came from the expected
bucket and object keys. The verifier checks the schema, MIME key, original sender,
case marker, text/HTML, attachment/image hashes, exact envelope recipients, matched
rules/destinations, and filtered/matched outcome. It retains private copies and
hashes, and writes `ledger-key-a.json`/`ledger-key-b.json` for expected destinations.
This is human-attested retrieval, not authenticated S3 provenance. MIME content
hash checks do not compare the entire outbound file byte-for-byte: the sending
mail service can add transport headers or change encoding before SES receives it.

## Verify each expected inbox and count copies

Download the **received forwarded message** from each expected inbox. In Gmail,
use **Show original → Download original**. Check rendering and attachment contents.
For each expected A/B delivery:

```sh
uv run --script examples/end-to-end/suite.py verify-inbox \
  --run-id <suite-id> --case <case> --inbox a \
  --message <fixture-directory>/downloaded-inbox-a.eml \
  --confirm-inbox --confirm-rendering
```

Use `--inbox b` for the second destination where expected. Confirmations assert
actual inbox receipt and rendering; without them the command cannot pass. Wrong
case markers, wrong destinations and corrupted payloads fail. The verifier accepts
changed Content-IDs only if the original image bytes remain referenced by HTML.
If Gmail changes image bytes, record the failure and use a MIME-capable client;
do not weaken the check silently.

After an actual observation window (for example ten minutes after sending), count
messages with each case marker in **both** inboxes, including spam/trash, then record:

```sh
uv run --script examples/end-to-end/suite.py observe \
  --run-id <suite-id> --case original --count-a 1 --count-b 1 --minutes 10
uv run --script examples/end-to-end/suite.py summary --run-id <suite-id>
```

Repeat `observe` for all six cases using actual counts. The tool does not wait or
poll. Unexpected copies and duplicates fail the observation. A case passes only
with S3 checks, every expected inbox check, and matching counts; missing evidence
stays incomplete. Counts establish only what was observed in that window, not
permanent absence, exactly-once delivery, or ongoing health.

## Controlled destination failure and recovery

This is a separate, human-operated test in the isolated deployment. It intentionally
raises errors/alerts. The optional example-only IAM policy denies SES sending to B
on the forwarding role while leaving A allowed; it does not alter the module's
rules. No fault or replay is enabled by a fixture command.

1. Set `simulate_destination_b_failure: true` privately. Review the plan: it should
   add only the destination-B deny policy for this exercise. Apply it yourself and
   allow IAM propagation. Keep both inboxes verified, so the intended fault is the
   explicit deny rather than sandbox readiness.
2. Run `suite.py prepare --recovery`. Send its single `original` fixture once.
   Verify S3 storage and inbox A. Expect no copy in B. Inspect sanitized Lambda
   diagnostics for the explicit authorization denial and confirm independent alerts.
3. Retrieve both per-destination ledger entries with consistent reads. Use the key
   files produced by successful `verify-storage`, not a table scan or bulk operation:

   ```sh
   aws dynamodb get-item --profile "$PROFILE" --region "$REGION" \
     --table-name "$TABLE" --consistent-read \
     --key "file://$CASE_DIR/ledger-key-a.json" > "$CASE_DIR/ledger-a.json"
   aws dynamodb get-item --profile "$PROFILE" --region "$REGION" \
     --table-name "$TABLE" --consistent-read \
     --key "file://$CASE_DIR/ledger-key-b.json" > "$CASE_DIR/ledger-b.json"
   uv run --script examples/end-to-end/suite.py verify-ledger \
     --run-id <recovery-suite-id> --phase failed \
     --ledger-a <fixture-directory>/ledger-a.json \
     --ledger-b <fixture-directory>/ledger-b.json --confirm-aws
   ```

   The checkpoint requires A=`sent` with a delivery ID and B=`processing`, using
   the exact message-plus-destination keys. `--confirm-aws` attests fresh reads
   from the correct table at this phase. Keep default no-expiry retention.
4. After automatic retries reach the failure queue, inspect its invocation record
   without deleting it. Save the original `requestPayload` as a private SES event
   JSON file. The human operator replays that event to the test Lambda, **without
   resetting either ledger entry**. Do not invoke the whole queue envelope.
   Save results privately. Confirm no extra inbox copy; fetch both entries again
   and run `verify-ledger --phase replayed` with fresh files. A's delivery ID must
   remain unchanged and B must still require review.
5. Set the failure flag back to false, review the policy-removal plan, apply it
   yourself, and allow IAM propagation. Confirm the failed attempt was denied
   before sending. For this exact reviewed B entry only, the human may remove its
   unfinished record and replay the same original event. Never reset A, bulk-delete
   ledger entries, or delete/edit the routing metadata to change destinations.
   A timeout/crash after a send is ambiguous; investigate or explicitly accept
   duplicate risk before resetting any such attempt.
6. Re-download S3 evidence and run `verify-storage` again. Fetch fresh ledger
   entries and run `verify-ledger --phase recovered`. This requires both entries
   `sent`, A's original delivery ID unchanged, and S3 MIME/metadata hashes unchanged
   from the failed checkpoint. Verify B's inbox MIME and observe final cumulative
   counts A=1, B=1. Run `summary`: recovery runs additionally require all three
   ledger checkpoints. Re-recording an earlier checkpoint clears later conclusions.
7. Only after successful recovery may the human remove the handled failure-queue
   message. Confirm the fault flag is false and the deny policy absent. Keep
   private evidence; do not claim success from file checks alone.

This scenario verifies one known pre-send denial and reviewed replay. It does not
cover every ambiguous send failure, bounce, or alarm path. No automated Terraform
apply, Lambda invoke, queue deletion, ledger mutation or teardown is implemented.

## Repeat, offline tests and publication

Prepare a second routing suite and send it without Terraform changes to verify
new dynamic addresses work. For code changes, rerun checks, review/apply the plan,
then prepare fresh fixtures. `test.py prepare` remains an optional single-message
`support` smoke test; the routing suite is the acceptance loop.

`make harness-test` runs synthetic fixture/evidence and configuration tests with no
AWS or mailbox access; it does not establish live delivery. `make check` includes
it. Suite commands have `--help` and intentionally return nonzero for missing
confirmations, failed checks and incomplete summaries.

No automatic expiry or teardown is enabled. Bucket/ledger destruction guards
remain active; cleanup requires an explicit reviewed change and human operation.
Run `make privacy-check` and independently review the publication diff. Keep all
private settings and run evidence out of commits and public issue discussions.
