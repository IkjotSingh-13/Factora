# VerifyAI — every task the team actually runs, in one place.
# `make` alone prints this list. Windows: the same targets are in README/DEMO.

PY ?= $(shell test -x .venv/bin/python && echo .venv/bin/python || echo python3)
PORT ?= 8000

.PHONY: help install run test checks qa seam ui build-ui doctor connections deck clean zip

help:  ## what is here
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

install:  ## venv + deps + spacy model + .env, then the suite
	./scripts/bootstrap.sh

run:  ## serve the API and the UI at / (make run PORT=8010)
	$(PY) -m uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port $(PORT)

test:  ## the whole suite
	$(PY) -m pytest -q

checks:  ## every gate the demo depends on, in one go
	$(PY) -m pytest -q
	$(PY) scripts/check_s3_contract.py --analyze s3_analysis:analyze --replay data/demo_snapshot.json
	$(PY) scripts/run_test_set.py
	node scripts/ui_render_check.mjs
	$(PY) scripts/doctor.py

qa:  ## labelled QA set, measured, writes data/test_set_results.json
	$(PY) scripts/run_test_set.py

seam:  ## S3 <-> S2 contract gate
	$(PY) scripts/check_s3_contract.py --analyze s3_analysis:analyze --replay data/demo_snapshot.json

ui:  ## check both UIs headlessly against a captured response (needs node)
	node scripts/ui_render_check.mjs

build-ui:  ## install npm deps and build frontend-app -> dist (served at /)
	@command -v npm >/dev/null 2>&1 || { echo "npm not found. The UI at /lite needs no node - run: make run"; exit 1; }
	cd frontend-app && npm install --no-audit --no-fund && npm run build
	@test -f frontend-app/dist/index.html || { echo "build reported success but dist/index.html is missing"; exit 1; }
	@echo "built frontend-app/dist - it is what / serves once you restart the app"

connections:  ## what this machine can reach: ollama, keys, sources, the API
	$(PY) scripts/check_connections.py

ollama:  ## point the app at a local model and pull it if needed
	./scripts/bootstrap.sh --ollama --ollama-model $(MODEL)

doctor:  ## environment + wiring report
	$(PY) scripts/doctor.py

clean:  ## caches only, never data or code
	rm -rf .pytest_cache **/__pycache__ __pycache__ .mypy_cache
	@echo "caches removed"

ZIP_EXCLUDES = "Factora/.venv/*" "Factora/node_modules/*" "Factora/frontend-app/node_modules/*" \
	  "Factora/**/__pycache__/*" "Factora/*.pyc" "Factora/*.tsbuildinfo" "Factora/.pytest_cache/*" \
	  "Factora/frontend-app/.gitignore.bak" "Factora/**/.DS_Store"

zip:  ## the two archives for submission / teammates (built UI included)
	@test -f frontend-app/dist/index.html \
	  && echo "including the built React UI (frontend-app/dist) - no npm needed to run /" \
	  || echo "WARNING: frontend-app/dist is missing - run 'make build-ui' first, or the zip ships /lite only"
	cd .. && rm -f Factora-verifyai.zip Factora-source-only.zip && \
	  zip -qr Factora-verifyai.zip Factora $(ZIP_EXCLUDES) && \
	  zip -qr Factora-source-only.zip Factora $(ZIP_EXCLUDES) -x "Factora/.git/*"
	@ls -lh ../Factora-*.zip
