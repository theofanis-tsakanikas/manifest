.DEFAULT_GOAL := help
SHELL := /bin/bash

# The venv when there is one, the ambient interpreter when there is not.
#
# A hard-coded `.venv/bin/…` is true on the laptop that wrote it and false on a CI runner,
# where the package is installed into the runner's own Python. A target that only runs where
# it was written is a target nobody has tested — and it fails in the expensive place, four
# minutes into a deploy, on `No such file or directory`.
VENV := .venv
PY   := $(if $(wildcard $(VENV)/bin/python),$(VENV)/bin/python,python3)
PIP  := $(if $(wildcard $(VENV)/bin/pip),$(VENV)/bin/pip,python3 -m pip)
RUFF := $(if $(wildcard $(VENV)/bin/ruff),$(VENV)/bin/ruff,ruff)
# Its own environment: checkov pins boto3 exactly, and the application's floor is higher.
# Created on demand by `iac-scan`.
CHECKOV_VENV := .venv-checkov
CHECKOV := $(if $(wildcard $(CHECKOV_VENV)/bin/checkov),$(CHECKOV_VENV)/bin/checkov,checkov)

LINT_PATHS := src tests scripts

# ─────────────────────────────────────────────────────────────────────────────
# Everything above the "cloud" section runs with NO AWS account and NO
# credentials. That is the point: no claim in this repository needs a cloud in
# order to be checked.
# ─────────────────────────────────────────────────────────────────────────────

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'

.venv:
	python3.12 -m venv .venv
	$(PIP) install --upgrade pip

.PHONY: install
install: .venv ## Create the venv and install the package with dev extras
	$(PIP) install -e ".[dev]"

.PHONY: test
test: ## Full test suite — offline, no credentials, no engine binary required
	$(PY) -m pytest

.PHONY: lint
lint: ## ruff check + format check (the exact command CI runs)
	$(RUFF) check $(LINT_PATHS)
	$(RUFF) format --check $(LINT_PATHS)

.PHONY: fmt
fmt: ## Apply ruff formatting
	$(RUFF) format $(LINT_PATHS)
	$(RUFF) check --fix $(LINT_PATHS)

# ── The seven claims ─────────────────────────────────────────────────────────
#
# One target per claim, added by the phase that earns it. A target here whose claim is not
# yet provable would be a green tick for work that has not happened.
#
#   claim 1  no field published below a derived threshold       — phase 2
#   claim 2  every field traces to a box, verified independently — phase 2
#   claim 3  re-extraction is reproducible and versioned         — phase 3
#   claim 4  cross-document disagreement is surfaced             — phase 3
#   claim 5  the human loop is real, and measured                — phase 4
#   claim 6  entity resolution is reversible                     — phase 3
#   claim 7  bulk reprocessing is idempotent, cost modelled      — phase 4

.PHONY: claims
claims: core-pure ## Every claim gate that exists today

.PHONY: core-pure
core-pure: ## The core imports no cloud SDK, no engine, and names no engine
	$(PY) scripts/check_core_is_pure.py

.PHONY: gate-proof
gate-proof: ## Break every gate on purpose; each must be refused, for the right reason
	$(PY) scripts/gate_proof.py

# ── Infrastructure (offline validation only — no cloud calls) ────────────────

.PHONY: tf-fmt
tf-fmt: ## terraform fmt across every layer
	terraform fmt -recursive infra

.PHONY: tf-validate
tf-validate: ## terraform validate per layer, offline (no backend, no provider creds)
	$(PY) scripts/tf_validate.py

.PHONY: checkov-venv
checkov-venv:
	@test -x $(CHECKOV_VENV)/bin/checkov || { \
		echo "  creating $(CHECKOV_VENV) — checkov pins boto3 and cannot share ours"; \
		python3 -m venv $(CHECKOV_VENV) && $(CHECKOV_VENV)/bin/pip install -q --upgrade pip checkov; \
	}

.PHONY: iac-scan
iac-scan: checkov-venv ## checkov over the Terraform layers
	$(CHECKOV) -d infra --quiet --compact

# ── Cloud (never run implicitly; always a deliberate act) ────────────────────
#
# There is no `apply` target and there will not be one. `docs/DECISIONS.md` 14: nothing in
# this repository is ever applied to AWS, including `infra/bootstrap/`. A Makefile target
# that applies is a target somebody runs.

.PHONY: preflight
preflight: ## Everything that must be true before the estate is stood up
	$(PY) scripts/preflight.py

.PHONY: preflight-fast
preflight-fast: ## The same, without gate-proof, terraform and checkov
	$(PY) scripts/preflight.py --fast

.PHONY: ci
ci: preflight ## Everything CI runs, in one command
