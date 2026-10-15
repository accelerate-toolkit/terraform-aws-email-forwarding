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

The public shared preset is `github>leverage-works/renovate-config`. This repository
keeps only its local scheduling/toolchain rules. Automerge includes major updates,
but waits for module checks. CI has read-only GitHub permissions and no AWS access;
merging never applies infrastructure or sends test mail. The example directory is
explicitly included in dependency extraction, overriding the shared preset's
example exclusion. Private configuration and build outputs remain excluded.

The hourly/manual runner uses pinned Node, uv, APM and Renovate. It installs APM
so dependency updates can regenerate `apm.lock.yaml`. Monday scheduling governs
new update creation. Renovate covers module/example Terraform providers, Terraform
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
3. Keep the Actions `GITHUB_TOKEN` read-only; use the separate bot identity for
   dependency branches/PRs. No AWS credentials, OIDC deployment role or state access.
4. Confirm a successful Module checks run and a successful dependency runner.
   Inspect extraction and a real dependency PR, APM lock update where relevant,
   and successful automerge. A secret's presence alone proves none of these.

Repository-level secrets also work when the GitHub plan prevents organization
secrets from being used by private repositories. Do not assume org-secret access
or require a paid-plan change merely to onboard a repository.

Current status: local APM/config/runner/checks prepared. GitHub repository creation,
publication, bot secret, live runner and actual dependency PR/merge verification
remain pending. No token was inspected or changed during local onboarding.


Local verification completed: frozen APM install/compile and advisory hook tests
pass; all 17 forwarding/harness tests pass; module and example Terraform validation,
privacy and Renovate schema checks pass. Token-free extraction detects 24 dependency
entries across seven managers, including APM and the executable Terraform example.
GitHub-dependent version lookup warns that a token is required; authenticated
lookup, runner execution and automerge are not yet verified. The module remains
pinned to published org-meta-bundle v0.4.0; newer shared onboarding guidance being
prepared in agent-context requires its own release before consumer adoption.
