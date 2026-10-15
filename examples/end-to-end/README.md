# Human-operated end-to-end module test

This public harness uses the local module source (`../..`). All actual account,
SSO profile/role, state bucket, hosted-zone, domain, alert and mailbox settings
belong in **config.local.json**, which is gitignored. `config.example.json` has
fictional values only. The runner rejects example account identifiers for AWS use.
There is no public organization-specific default, inbox password or Gmail token.

## Prerequisites

Use an isolated NonProd account, an existing state bucket, a delegated public DNS
zone and an already active shared SES receipt rule set. Account provisioning,
state-bucket ownership, delegation and shared-set activation belong to the account
foundation, not this example. Use a dedicated test subdomain below the delegated
zone; the example rejects the zone apex. Never point it at a production domain.

Install the CLI version from `../../.terraform-version`, uv and AWS CLI. Configure
a named SSO profile with permission to deploy this test stack. The wrapper verifies
the account and specific SSO role prefix and clears ambient AWS/TF overrides.
It does not create accounts, log in automatically or use ambient static credentials.

Copy `config.example.json` to `config.local.json` and fill it privately. If a local
file already exists, edit it rather than overwriting it. Account, backend and DNS
identifiers must come from the provisioned test environment. `terraform_binary`
can be an executable on PATH or a local absolute path. `destination` is the external
inbox receiving forwarded messages; `test_sender` is the mailbox used to send them.
They may be the same address. `alert_email` is independently chosen per deployment;
it must not depend solely on the forwarding route under test.

No secrets belong in this file. Account/profile/mailbox values are configuration;
AWS credentials stay in the SSO cache. Generated Terraform variables, backend
configuration, caches and run artifacts live under ignored `build/end-to-end/`.
The backend remains S3; there is no local-state fallback. Local files are written
with mode 0600. Terraform plans and terminal output may contain private values;
never paste them into public issues or commit them.

## Deploy and verify readiness

From the module repository root, after logging into the configured SSO profile:

```sh
make check
./examples/end-to-end/tf.sh init
./examples/end-to-end/tf.sh plan
# Human operator only:
./examples/end-to-end/tf.sh apply
```

Start with `enable_mx: false`. The first apply creates the test forwarding stack,
verification/DKIM records and deployment-owned alert topic. It also requests SES
verification of the destination inbox and SNS confirmation for alert delivery.
Confirm both messages manually. Check domain verification/DKIM status and SES
sandbox recipient readiness. Then set `enable_mx: true`, review and apply again.
Confirm public DNS resolves the test domain's MX before sending.

No email is sent by the local test tool. Terraform apply may trigger the AWS
verification/subscription emails just described. Module/Lambda checks run offline;
this example's plan/apply require AWS and may incur normal resource charges.

## Prepare and send

```sh
uv run --script examples/end-to-end/test.py prepare
```

The command prints a run ID and creates private fixtures:

- `outbound.eml`: complete MIME message for a client supporting draft import or
  resend. Send it through the configured external account, not an AWS send call.
- `body.txt` / `body.html`: unique test identifier and body content.
- `fixture.txt`: synthetic attachment with a recorded SHA-256 checksum.
- `inline.png`: a small blue square whose bytes are also checksummed.
- `result.json`: expected values and pending status.

Gmail's browser UI does **not** directly send an imported .eml as its original
message. Either use a mail client that supports sending the draft through your
external account, or compose in Gmail manually: use the generated subject marker,
paste the text, add bold formatting, attach `fixture.txt` and insert `inline.png`
using **Insert photo → Inline**. Include the marker in the body. Do not attach
`outbound.eml` as a substitute for sending its contents. The verifier accepts a
changed Content-ID if the original image bytes are still referenced by the HTML.
If Gmail rewrites image bytes, record that mismatch and use a MIME-capable client
for the strict byte-preservation test; do not weaken the check silently.

## Receive and verify

Open the forwarded message in the configured external inbox. Check the text,
HTML formatting, inline blue square and attachment. In Gmail, use **Show original →
Download original** on the *received forwarded message*, not your sent copy.
Save it under that run's ignored directory as `received.eml`.

```sh
uv run --script examples/end-to-end/test.py verify \
  --run-id <printed-run-id> \
  --message build/end-to-end/runs/<printed-run-id>/received.eml
```

Checks cover the run identifier, sender, destination, Reply-To, text/HTML markers,
attachment bytes and inline image bytes/reference. Passing file checks alone leave
status **pending_manual_confirmation** and return nonzero. After personally
confirming inbox receipt and rendering, repeat with:

```sh
uv run --script examples/end-to-end/test.py verify \
  --run-id <printed-run-id> \
  --message build/end-to-end/runs/<printed-run-id>/received.eml \
  --confirm-inbox --confirm-rendering
```

Only all file checks plus both human confirmations record **passed**. This is a
human-attested end-to-end test, not authenticated mailbox automation. It does not
prove ongoing health or validate failure/replay cases; those require separate tests.
No mailbox credentials or inbox API access are used.

## Repeat, cleanup and publication

Edit the local module, review/apply the test stack, generate a new run and repeat.
Keep state between iterations. No automatic expiry or teardown is enabled. The
module's data-preservation guards currently block destruction of its bucket and
ledger; teardown requires an explicit reviewed cleanup change, not automatic
`terraform test` cleanup or deletion of state. The human operator performs it.

Run `make privacy-check` before publishing. It checks Git-visible files and staged
blobs for private/generated paths, non-example account/mailbox values, common key
formats and configured local identifiers. It never prints matching values. It is
not a secret scanner for all possible formats; review the publication diff too.
Private local settings and run artifacts stay on the laptop.
