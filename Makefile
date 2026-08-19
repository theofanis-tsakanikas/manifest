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

LINT_PATHS := src tests scripts corpus evals pipelines

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

# ── The corpus and the engine recording ──────────────────────────────────────

.PHONY: corpus
corpus: ## Generate the corpus from its seed — pages and ground truth
	$(PY) -m corpus.generate

.PHONY: corpus-check
corpus-check: ## The generator reproduces the committed ground truth, exactly
	$(PY) -m corpus.generate --check

# The ceremony, not a command. It is the one act that can move every threshold in this
# repository at once, so it refuses to overwrite without ACCEPT=1 and prints what changed
# before it writes. See ADR-0005 and docs/DECISIONS.md 19.
.PHONY: external-record
external-record: ## Read the licensed external set and record how the reader's confidence behaved
	@echo "Fetches an externally licensed document set. corpus/external/LICENCE.md carries the"
	@echo "licence, the source and the date its terms were read. No image is stored here — the"
	@echo "recording is confidences and correctness flags, with no text in it."
	$(PY) scripts/external_record.py

.PHONY: ocr-record
ocr-record: ## Run the tier-0 reader over the corpus and record it (ACCEPT=1 to overwrite)
	$(PY) scripts/ocr_record.py $(if $(ACCEPT),--accept,)

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

.PHONY: reader-version
reader-version: ## The image's reader and the recording's reader are the same reader
	$(PY) scripts/reader_version_check.py

.PHONY: pipeline-routing
pipeline-routing: ## A document that publishes some of itself still owes the rest to a human
	$(PY) scripts/check_pipeline_routing.py

.PHONY: optional-layers
optional-layers: ## Every optional feature plans with its flag on and off (needs credentials)
	$(PY) scripts/check_optional_layers_plan.py

.PHONY: claims
claims: core-pure map-matches planting-blind contracts-validate corpus-check envelope reader-version \
	pipeline-routing \
	claim-1 claim-2 claim-3 claim-4 claim-5 claim-6 claim-7 \
	claim-1-loop drift grounding baseline out-of-distribution \
	injection line-items classification marts warehouse-fed \
	every-gate-runs ## Every claim gate that exists today

.PHONY: core-pure
core-pure: ## The core imports no cloud SDK, no engine, and names no engine
	$(PY) scripts/check_core_is_pure.py

.PHONY: claim-1-loop
claim-1-loop: ## Review evidence moves N, and never an error budget
	$(PY) -m evals.feedback

.PHONY: drift
drift: ## The declared envelope, applied to arriving traffic, in both directions
	$(PY) -m evals.drift

.PHONY: grounding
grounding: ## A classification must point at the text it came from
	$(PY) -m evals.grounding

.PHONY: baseline
baseline: ## What the derived policy buys against a hand-picked threshold and against none
	$(PY) -m evals.baseline

.PHONY: out-of-distribution
out-of-distribution: ## Does a confidence of 0.9 mean the same on paper nobody here designed
	$(PY) -m evals.external

.PHONY: every-gate-runs
every-gate-runs: ## Every harness on disk is run by CI and by this file
	$(PY) scripts/check_every_gate_runs.py

.PHONY: map-matches
map-matches: ## The documented tree exists, and every core module has a caller that runs
	$(PY) scripts/check_the_map_matches_the_ground.py

.PHONY: planting-blind
planting-blind: ## The corpus plants mismatches blind to the rules that will find them
	$(PY) scripts/check_planting_is_blind.py

.PHONY: contracts-validate
contracts-validate: ## Every contract loads and the set cross-checks
	$(PY) scripts/check_contracts.py

.PHONY: envelope
envelope: ## The generator stays inside its declared operating range (decision 20)
	$(PY) scripts/check_corpus_envelope.py

.PHONY: claim-1
claim-1: ## CLAIM 1 — no field publishes below a threshold derived from its error budget
	$(PY) -m evals.calibration

.PHONY: claim-2
claim-2: ## CLAIM 2 — every published field traces to a box, checked against the page
	$(PY) -m evals.provenance

.PHONY: claim-3
claim-3: ## CLAIM 3 — re-extraction is reproducible and versioned
	$(PY) -m evals.reprocessing

.PHONY: claim-4
claim-4: ## CLAIM 4 — cross-document disagreement is surfaced, never smoothed
	$(PY) -m evals.reconciliation

.PHONY: claim-5
claim-5: ## CLAIM 5 — the human loop is real, and measured
	$(PY) -m evals.review

.PHONY: claim-6
claim-6: ## CLAIM 6 — entity resolution is reversible
	$(PY) -m evals.entities

.PHONY: claim-7
claim-7: ## CLAIM 7 — bulk reprocessing is idempotent, and its cost is modelled
	$(PY) -m evals.scale

.PHONY: injection
injection: ## Untrusted document text is fenced structurally and detected as depth
	$(PY) -m evals.injection

.PHONY: line-items
line-items: ## The line-total check that catches a table truncated at a page break
	$(PY) -m evals.lineitems

.PHONY: classification
classification: ## The abstention band on a contested heading, and a field that never publishes
	$(PY) -m evals.classification

.PHONY: marts
marts: ## Every analytics mart reads only columns the warehouse declares
	$(PY) scripts/check_marts.py

.PHONY: warehouse-fed
warehouse-fed: ## Every warehouse column has a source, is derivable, or is declared unsourced
	$(PY) scripts/check_warehouse_is_fed.py

.PHONY: gate-proof
gate-proof: ## Break every gate on purpose; each must be refused, for the right reason
	$(PY) scripts/gate_proof.py

# ── Infrastructure (offline validation only — no cloud calls) ────────────────

.PHONY: redact
redact: ## Cover the AWS account id in every screenshot (--check in preflight)
	$(PY) scripts/mask_account_id.py

.PHONY: deploy-path
deploy-path: ## Every applied layer is torn down, in reverse; both workflows human-only
	$(PY) scripts/check_deploy_path.py
	$(PY) scripts/check_policy_actions_can_match.py

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
# There is no `apply` target and there will not be one. `docs/DECISIONS.md` 14: every layer
# above `infra/bootstrap/` applies from CI and nowhere else, and bootstrap applies by a
# deliberate, documented act rather than by a word somebody can tab-complete. A Makefile target
# that applies is a target somebody runs — which is the whole objection, and it survives the
# estate having been stood up.

.PHONY: preflight
preflight: ## Everything that must be true before the estate is stood up
	$(PY) scripts/preflight.py

.PHONY: preflight-fast
preflight-fast: ## The same, without gate-proof, terraform and checkov
	$(PY) scripts/preflight.py --fast

.PHONY: ci
ci: preflight ## Everything CI runs, in one command
