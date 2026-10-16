---
description: Public module ownership, private test configuration and deployment boundaries.
applyTo: "**"
---

# Email forwarding module

Run make first and read README.md and examples/end-to-end/README.md.
This is a public reusable Terraform module. Do not embed organization accounts,
mailboxes, domains, profiles, credentials or private test results in tracked files.
Use fictional example values; actual test settings live in ignored config.local.json.
Run make privacy-check before publication and review the diff independently.

The module owns project-specific forwarding, not account-level receipt-rule-set
activation or caller DNS. Alert routing is supplied by the deployment. Preserve
MIME content and document duplicate/recovery semantics without promising exactly-once
mail delivery. Distinguish offline tests from verified live end-to-end delivery.

The human operator executes all real Terraform applies, imports, destroys and
state mutations. The assistant may prepare configuration, run offline tests/plans
and read-only verification. Use examples/end-to-end/tf.sh for deployment, not Make
wrappers. No AWS credentials, roles or deployment paths belong in GitHub Actions.
CI checks receive no AWS credentials. A dedicated main-branch release workflow may
publish immutable source releases with GitHub contents-write permission after
checks pass; source publication is separate from production adoption, as documented
in docs/releases.md. Renovate automerge does not apply Terraform.

Use APM without ontologies. Edit .apm sources and compile AGENTS.md rather than
editing generated instructions. Keep manifests/lockfiles, ignore downloaded tools
and skills. The shared package pin must refer to a published release.
