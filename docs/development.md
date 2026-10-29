# Repository onboarding and dependency maintenance

Run `make` for supported actions. `make setup` installs the frozen APM dependency
and generates `AGENTS.md`, then installs advisory Git hooks. Review the APM install
preview. Edit `.apm/instructions/`, not compiled instructions. No ontologies are
used. `make apm-update` deliberately refreshes pins/lockfile for review; ordinary
setup must use the accepted lock. Restart the agent session after updating skills.

The hook reminder is advisory, offline and never installs dependencies. Existing
hooks and `core.hooksPath` are preserved. Where a hook already exists, integrate
`bash "$root/scripts/apm-reminder.sh" post-merge` or the corresponding
`post-rewrite "$@"` invocation into it; do not overwrite its existing behavior.

## Renovate

The public shared preset is `github>accelerate-toolkit/renovate-config`. This repository
keeps its local scheduling and dependency grouping rules. All dependency version
updates, including major updates, share one PR, overriding the preset's per-manager
groups. Standalone lock-file maintenance is disabled because Renovate cannot combine
it with version updates; lockfiles still update alongside dependency changes.
The normal dependency PR concurrency limit is one. Automerge includes major updates,
but waits for module checks. Checks have read-only GitHub permissions; the dedicated source-release job
has contents-write permission and no AWS access;
merging never applies infrastructure or sends test mail. The example directory is
explicitly included in dependency extraction, overriding the shared preset's
example exclusion. Private configuration and build outputs remain excluded.

The schedule-only weekly runner uses pinned Node, uv, APM and Renovate. It installs APM
so dependency updates can regenerate `apm.lock.yaml`. The runner starts Mondays at
02:17 UTC (02:17 London in winter, 03:17 in summer), subject to GitHub Actions
scheduling delays. New update creation is allowed before 06:00 Monday in
`Europe/London`. The workflow has no push, pull-request or manual dispatch trigger.
Commit checks validate Renovate configuration without running the dependency bot.
Completed checks may wait
until the next runner invocation for automerge. Renovate covers module/example Terraform providers, Terraform
CLI, GitHub Actions, Node, uv/APM/Renovate pins and versioned APM packages. Run
`make renovate-check` and `make renovate-extract` locally. Extraction mounts only
public dependency inputs; it receives no token, private test files or AWS credentials.

## GitHub activation checklist

Local preparation does not create a running GitHub bot. Before marking onboarding
complete:

1. Publish the reviewed public repository and configure its merge/check policy.
   Review `make privacy-check` and the actual diff first; never publish local config.
2. Provision a GitHub-only `RENOVATE_TOKEN` repository Actions secret, or document
   an approved shared runner/App replacing this repository runner. A fine-grained
   token for this runner needs access to this repository, Contents, Pull requests,
   Issues and Workflows read/write, plus Commit statuses read for check evaluation.
   Never print or commit its value. Record its expiry privately and arrange rotation.
3. Keep the dependency/check jobs’ Actions `GITHUB_TOKEN` read-only; use the separate bot identity for
   dependency branches/PRs. No AWS credentials, OIDC deployment role or state access.
4. Confirm a successful Module checks run and a successful dependency runner.
   Inspect extraction and a real dependency PR, APM lock update where relevant,
   and successful automerge. A secret's presence alone proves none of these.

Repository-level secrets also work when the GitHub plan prevents organization
secrets from being used by private repositories. Do not assume org-secret access
or require a paid-plan change merely to onboard a repository.

Current status: repository publication, Module checks and an authenticated
Renovate run are verified. The Dependency Dashboard exists; actual dependency
PR/merge behaviour remains unverified. The runner cannot currently read
vulnerability alerts. Dedicated source-release automation is prepared;
see [releases](releases.md).


Local verification completed: frozen APM install/compile and advisory hook tests
pass; all 17 forwarding/harness tests pass; module and example Terraform validation,
privacy and Renovate schema checks pass. Token-free extraction detects 24 dependency
entries across seven managers, including APM and the executable Terraform example.
GitHub-dependent version lookup warns that a token is required; authenticated
lookup, runner execution and automerge are not yet verified. The module remains
pinned to published org-meta-bundle v0.4.0; newer shared onboarding guidance being
prepared in agent-context requires its own release before consumer adoption.


## Sandbox destination authorization

During a live sandbox test, SES v2 `SendEmail` with raw MIME returned
`AccessDeniedException` for `ses:SendRawEmail` on the verified **destination email
identity**. The Lambda policy already covered the sender domain and configuration
set, but omitted that destination resource.

The applied fix adds the exact destination identity ARN to the send policy,
retaining the exact `ses:FromAddress` and `ses:Recipients` conditions. Fresh
messages were successfully forwarded afterward; see the results below.

Distinguish the documented requirement from the observed behavior:

- AWS [requires verified recipients in the sandbox](https://docs.aws.amazon.com/ses/latest/dg/verify-addresses-and-domains.html).
  Outside the sandbox, recipients do not need SES identities.
- The destination-resource authorization check was observed in this sandbox test.
  Its attribution specifically to sandbox mode is an inference, not a confirmed
  general AWS rule. Do not describe destination identity ARNs as universally
  required, or assume production access will necessarily remove this check.
- `ses:Recipients` is our explicit restriction on where the forwarder can send.
  It is separate from identity verification and resource authorization, and
  remains useful in both sandbox and production mode.

Fresh-message delivery verified this narrow policy change in the sandbox.
Reassess this permission when testing outside the sandbox;
record evidence before removing it or making broader claims. Keep real account,
domain, mailbox and request details in private test artifacts.


## Live verification results

Manual sandbox testing on 2026-10-16 confirmed delivery through SES receipt,
S3 storage, Lambda forwarding and SES outbound delivery to an external inbox.
The simple-message attempt had a successful Lambda invocation and a `sent`
ledger entry with an SES message ID; the human operator confirmed inbox receipt.
A subsequent multipart test was received and its supplied MIME source reviewed.
Only generic results are recorded here; actual addresses, account identifiers,
headers, message IDs and deployment configuration remain private.

| Check | Evidence and result |
| --- | --- |
| Authentication | Receiving-mailbox headers report SPF, DKIM and DMARC pass. |
| Unicode | Accented characters, currency symbols and a check mark appear in the received text and HTML MIME parts. |
| HTML | Bold, italic, list and link markup are present in received MIME. |
| Inline image | HTML Content-ID reference matches the image part; the human operator confirmed the image displays. The pasted source omitted image bytes, so no byte comparison was performed. |
| Text attachment | Received MIME contains the synthetic fixture attachment and expected decoded text. |
| Reply routing | Reply-To contains the original sender; an actual reply was not sent as part of this test. |
| Failure alerts | The human operator confirmed receipt of Lambda-error and pending-failure alarm emails during the earlier failed attempts. |

This was a manual smoke test, not a completed automated harness verification.
Image/attachment hash comparison, PDF delivery, size limits, recovery/replay and
ongoing end-to-end canary coverage remain unverified in the live environment.
The earlier failed messages were retained, not replayed or deleted. Raw-message
expiry remains disabled. Sandbox success does not establish behavior outside
sandbox or readiness to cut over production mail.


### Pattern-routing smoke test

After the named-rule deployment, the human operator confirmed that one message
matching overlapping `original-*` rules arrived at both configured destination
addresses, and a `replacement-*` message arrived at the primary destination.
The destinations were a base email address and a plus alias in the same mailbox;
this confirms routing to distinct addresses, not delivery to independent providers.

These were routing-only smoke tests. The original message omitted attachments
and an inline image, and the replacement message used a simple subject/body.
The full fixture verifier was not completed. Filtered-recipient behavior, S3
metadata correlation, Bcc, duplicate/recovery handling and byte-preservation
checks remain unverified for this live run. Private addresses and run identifiers
are intentionally omitted.
