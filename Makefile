# TraceBrain dev tasks. `make help` lists them.
PY      ?= .venv/bin/python
PIP     ?= .venv/bin/pip
PORT    ?= 8000
TENANT  ?= falkordb
SNAPSHOT ?= deploy/snapshot/org_$(TENANT).json.gz

.PHONY: help install dev-api dev-web mock build serve test seed-export seed-import docker docker-down

help:
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-12s %s\n", $$1, $$2}'

install: ## venv + backend (editable, embedded FalkorDB, test deps) + web deps
	test -x $(PY) || python3 -m venv .venv
	$(PIP) install -e './backend[lite,dev]'
	cd web && npm ci

dev-api: ## FastAPI with reload on :8000 (embedded DB unless FALKORDB_URL is set)
	$(PY) -m uvicorn tracebrain.api:app --reload --port $(PORT) --app-dir backend

dev-web: ## Vite dev server on :5173 (proxies /api -> :8000)
	cd web && npm run dev

mock: ## Node mock backend on :8000 (no Python, no DB, no LLM)
	cd web && npm run mock

build: ## Build the web UI into web/dist (served by the API at /)
	cd web && npm run build

serve: build ## Production-style: one uvicorn on $(PORT) serving UI + API
	$(PY) -m uvicorn tracebrain.api:app --host 0.0.0.0 --port $(PORT) --app-dir backend

test: ## Backend tests
	cd backend && ../$(PY) -m pytest -q

seed-export: ## Export the tenant graph to $(SNAPSHOT) (memory nodes excluded)
	cd backend && ../$(PY) -m tracebrain.seed export --tenant $(TENANT) --path ../$(SNAPSHOT)

seed-import: ## Import $(SNAPSHOT) into the DB at FALKORDB_URL (or embedded)
	cd backend && ../$(PY) -m tracebrain.seed import --tenant $(TENANT) --path ../$(SNAPSHOT)

docker: ## FalkorDB + app via docker compose (app :8000, FalkorDB Browser :3000)
	docker compose up --build

docker-down:
	docker compose down
