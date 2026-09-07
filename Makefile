.PHONY: api api-check hooks

# Rebuild frontend/openapi.json and the generated TS client from the backend code.
api:
	scripts/regen-api.sh

# What CI and the pre-commit hook run: fail if either is stale, change nothing.
api-check:
	scripts/regen-api.sh --check

# Point git at .githooks so the contract check runs on every commit.
hooks:
	git config core.hooksPath .githooks
	@echo "pre-commit hook installed — bypass once with SKIP_API_CHECK=1"
