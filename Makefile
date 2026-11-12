PY ?= python

.PHONY: help test fixture workspace export verify audit
help:
	@echo "make test      - run offline smoke tests"
	@echo "make workspace - build a demo workspace from fixtures/synthetic"
	@echo "make export    - export demo workspace to build/sft (requires workspace)"
	@echo "make verify    - verify exported dataset"

workspace:
	$(PY) pipeline/setup_workspace.py --evidence-dir fixtures/synthetic/evidence \
	  --selection fixtures/synthetic/selection.json --out build/workspace --budget-cny 1

export:
	$(PY) export/export_sft.py --out build/sft --workspaces build/workspace

verify:
	$(PY) export/verify_sft.py --export-dir build/sft --workspaces build/workspace
	$(PY) export/audit_numeric.py --export-dir build/sft --workspaces build/workspace

test:
	$(PY) tests/test_smoke.py
