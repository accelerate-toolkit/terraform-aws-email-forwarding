.DEFAULT_GOAL := help
APM_VERSION := 0.33.0
RENOVATE_VERSION := 44.151.0
APM = UV_TOOL_DIR=$(CURDIR)/build/uv-tools uv tool run --from apm-cli==$(APM_VERSION) apm

.PHONY: help check test fmt validate
help: ## List local workflows.
	@awk 'BEGIN { FS = ":.*##" } /^[[:alnum:]_.-]+:.*##/ { printf "  %-22s %s\n", $$1, $$2 }' $(MAKEFILE_LIST)
test: ## Test mail processing offline.
	uv run --script tests/test_forwarder.py -v
fmt: ## Format Terraform.
	docker run --rm --network none -v "$(CURDIR):/work" -w /work hashicorp/terraform:$(shell cat .terraform-version) fmt -recursive
validate: ## Validate module/example without AWS credentials.
	bash scripts/validate.sh
check: release-test apm-check test harness-test privacy-check validate hooks-test renovate-check ## Run credential-free module checks.

.PHONY: harness-test privacy-check
harness-test: ## Test private configuration and manual verification.
	uv run --script tests/test_harness.py -v
privacy-check: ## Inspect public candidates and staged blobs for private data.
	uv run --script scripts/privacy_check.py

.PHONY: setup apm-install apm-update apm-compile apm-version hooks-install hooks-test renovate-check renovate-extract renovate-run
setup: apm-install hooks-install ## Install locked local skills and advisory Git hooks.
apm-install: ## Install locked APM dependencies and compile instructions.
	$(APM) install --target codex --frozen --dry-run
	$(APM) install --target codex --frozen
	$(MAKE) apm-compile
apm-update: ## Deliberately resolve newer APM dependencies for review.
	$(APM) install --target codex --update --dry-run
	$(APM) install --target codex --update
	$(MAKE) apm-compile
apm-compile: ## Compile repository and dependency instructions.
	$(APM) compile --target codex
apm-version: ## Print the pinned APM CLI version.
	@echo $(APM_VERSION)
hooks-install: ## Install advisory reminders without replacing existing hooks.
	bash scripts/install-hooks.sh
hooks-test: ## Verify advisory Git reminders offline.
	bash scripts/test-hooks.sh
renovate-check: ## Validate Renovate configuration without credentials.
	docker run --rm -v "$(CURDIR)/renovate.json:/config/renovate.json:ro" --entrypoint renovate-config-validator ghcr.io/renovatebot/renovate:$(RENOVATE_VERSION) /config/renovate.json
renovate-extract: ## Check dependency detection using public inputs only.
	bash scripts/renovate-extract.sh $(RENOVATE_VERSION)
renovate-run: ## Run the dependency bot with GitHub-only credentials.
	npx --yes --package=renovate@$(RENOVATE_VERSION) renovate

.PHONY: apm-check
apm-check: ## Preview locked skill installation without changing the workspace.
	$(APM) install --target codex --frozen --dry-run

.PHONY: release-plan release-test
release-plan: ## Preview the next source version without publishing.
	uv run --script scripts/release/release.py plan
release-test: ## Test versioning and publication recovery offline.
	uv run --script scripts/release/test_release.py -v
