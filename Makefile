# Uso: make <target>   (Linux/macOS para desarrollo; en Windows usa scripts/dev.ps1)
# En la Mac mini de producción se opera con `mhia <tarea>` (scripts/mac/mhia.sh).
# Requiere: Docker, Docker Compose v2, Python 3.12 (lint/test locales), uv (solo lock)

.PHONY: help up up-gpu down ps logs ingest venv lock lock-upgrade lint fmt test web-install web-build backup backup-verify restore-drill

PIPELINE_DIR := services/pipeline_ai
COMPOSE := docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml
COMPOSE_GPU := docker compose -f docker-compose.yml -f docker-compose.gpu.yml
UV_COMPILE := uv pip compile --universal --python-version 3.12 --generate-hashes -q

help:
	@echo "Targets disponibles:"
	@echo "  make up          - Levanta el stack con Ollama NATIVO en el host (modo por defecto)"
	@echo "  make up-gpu      - Alternativa: Ollama en contenedor con GPU NVIDIA"
	@echo "  make down / ps / logs"
	@echo "  make ingest      - Sincroniza Google Drive -> Qdrant"
	@echo "  make backup      - Respaldo (crea, conserva 14); backup-verify; restore-drill"
	@echo "  make venv        - Entorno virtual con las versiones exactas (requirements-dev.lock)"
	@echo "  make lock        - Regenera los .lock tras cambiar requirements*.txt"
	@echo "  make lock-upgrade - Sube todas las dependencias a su última versión compatible"
	@echo "  make lint / fmt / test"
	@echo "  make web-install / web-build"

up:
	mkdir -p backups
	$(COMPOSE) up -d --build

up-gpu:
	$(COMPOSE_GPU) up -d --build

down:
	$(COMPOSE) down

ps:
	$(COMPOSE) ps

logs:
	$(COMPOSE) logs -f mhia-api

ingest:
	$(COMPOSE) exec mhia-api python -m pipeline_ai.ingestion.run_ingest

backup:
	$(COMPOSE) exec -T mhia-api python -m pipeline_ai.ops.backup create
	$(COMPOSE) exec -T mhia-api python -m pipeline_ai.ops.backup prune --keep 14

backup-verify:
	$(COMPOSE) exec -T mhia-api python -m pipeline_ai.ops.backup verify

restore-drill:
	$(COMPOSE) exec -T mhia-api python -m pipeline_ai.ops.backup drill

venv:
	python3.12 -m venv $(PIPELINE_DIR)/.venv
	$(PIPELINE_DIR)/.venv/bin/pip install --upgrade pip
	$(PIPELINE_DIR)/.venv/bin/pip install --require-hashes -r $(PIPELINE_DIR)/requirements-dev.lock

lock:
	cd $(PIPELINE_DIR) && $(UV_COMPILE) requirements.txt -o requirements.lock
	cd $(PIPELINE_DIR) && $(UV_COMPILE) requirements.txt requirements-dev.txt -c requirements.lock -o requirements-dev.lock

lock-upgrade:
	cd $(PIPELINE_DIR) && $(UV_COMPILE) --upgrade requirements.txt -o requirements.lock
	cd $(PIPELINE_DIR) && $(UV_COMPILE) --upgrade requirements.txt requirements-dev.txt -c requirements.lock -o requirements-dev.lock

lint:
	cd $(PIPELINE_DIR) && ruff check pipeline_ai tests eval && black --check pipeline_ai tests eval

fmt:
	cd $(PIPELINE_DIR) && ruff check pipeline_ai tests eval --fix && black pipeline_ai tests eval

test:
	cd $(PIPELINE_DIR) && pytest -v

web-install:
	cd web && npm ci

web-build:
	cd web && npm run build
