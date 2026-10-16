# Module source releases

Terraform and Lambda release together as immutable `vX.Y.Z` tags. The tagged Git
source is the artifact; there is no separate Lambda binary upload or Terraform
Registry publication. APM tooling pins are development dependencies, not a second
module version field. Consumers pin a published tag in the Git module source;
never use a moving branch or invent an unpublished tag.

The compatibility contract includes inputs/defaults, outputs, required providers,
Terraform versions, resource addresses and delivery/recovery behaviour. Removing
or incompatibly changing these requires a breaking marker and migration notes.
Conventional Commits determine the highest bump: feat = minor, fix/perf = patch,
breaking marker/footer = major, including during 0.x. Docs/chore alone do not
release. With no tags the baseline is 0.0.0. The first published version is 0.1.0.

## Local preview

```sh
make release-plan
make release-test
make privacy-check
```

Preview reads local Git history/tags and writes ignored build/release/notes.md.
It does not fetch, commit, tag or contact GitHub. Notes retain commit bodies and
breaking-change explanations. Synchronize tags before relying on the preview.

## Automatic publication and separate deployment

The dedicated Release workflow runs on pushes to main and manual main dispatches.
It runs `make check` before publishing the checked source with contents-write
GITHUB_TOKEN. Pull requests never publish. No enablement variable, PAT or AWS role
is required. The workflow never applies Terraform or sends test email.

The release policy uses automatic Conventional Commit
versions, immutable tags, retry-safe publication and published-consumer verification.
The artifact-specific difference is source-only publication instead of CLI binaries.
A release identifies a reproducible source version, not proof of production
readiness. Release docs retain known limitations. Each deployment must separately
verify alerts, recovery, bounce handling and independent monitoring before live
mail cutover or message expiry. These are not source-publication gates.

Publication creates a matching immutable tag through the GitHub API, creates a
draft release and then publishes it. Existing matching tags/drafts are reused;
a conflicting tag aborts, and published releases remain unchanged. API errors
propagate. If a tag exists but publication failed, retry resumes it before newer
work; dispatch again for later commits. Publication is serialized. An out-of-order
run can fail on a tag conflict; rerun the latest main workflow after fetching the
current tags rather than moving a tag.

CI invokes:

```sh
uv run --script scripts/release/release.py publish --repo "$GITHUB_REPOSITORY"
```

The command refuses non-main/non-Actions contexts and dirty checkouts. Tests use
disposable Git repositories and mocked GitHub writes for version precedence,
notes, retry recovery and conflicting tags. Release v0.1.0 was published successfully on 2026-10-16. Correct released defects with a new version.

## Published consumer verification

After publication, CI creates a disposable independent Terraform root referencing
exactly `git::https://github.com/OWNER/REPO.git?ref=vX.Y.Z`. It downloads the module
from GitHub, initializes without a backend, validates required inputs and outputs,
and checks that the bundled Lambda source compiles. No local module replacement,
AWS credentials, Terraform plan/apply, private config or state is supplied.
Validation runs with container networking disabled after initialization.

```sh
uv run --script scripts/release/consumer.py --repo OWNER/REPO --version X.Y.Z
```

If this check fails, the release is already published. Retry for a transient
failure; correct source defects with a new version. A no-new-release retry still
verifies the latest published version. This verifies package consumption, not
email delivery. The published v0.1.0 consumer check passed on 2026-10-16 in
[Release run 38927063716](https://github.com/accelerate-toolkit/terraform-aws-email-forwarding/actions/runs/38927063716).
Live email rollout checks remain separate.
