# Uso: make <target>   (macOS/Linux; en Windows usa scripts/dev.ps1)
# Requiere: Docker, Docker Compose v2, Python 3.12 (lint/test locales)

.PHONY: help up-gpu up-mac down logs ingest lint fmt test venv web-install web-build

PIPELINE_DIR := services/pipeline_ai

help:
	@echo "Targets disponibles:"
	@echo "  make up-gpu    - Levanta el stack en la laptop (Ollama con GPU NVIDIA)"
	@echo "  make up-mac    - Levanta el stack en el Mac mini (Ollama nativo)"
	@echo "  make down      - Detiene el stack"
	@echo "  make logs      - Sigue los logs de mhia-api"
	@echo "  make ingest    - Sincroniza Google Drive -> Qdrant"
	@echo "  make venv      - Crea el entorno virtual de $(PIPELINE_DIR)"
	@echo "  make lint      - ruff + black --check"
	@echo "  make fmt       - black + ruff --fix"
	@echo "  make test      - pytest"
	@echo "  make web-install / web-build - dependencias y build de la GUI"

up-gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

up-mac:
	docker compose -f docker-compose.yml -f docker-compose.mac.yml up -d --build

down:
	docker compose down

logs:
	docker compose logs -f mhia-api

ingest:
	docker exec -it mhia-api python -m pipeline_ai.ingestion.run_ingest

venv:
	python3 -m venv $(PIPELINE_DIR)/.venv
	$(PIPELINE_DIR)/.venv/bin/pip install --upgrade pip
	$(PIPELINE_DIR)/.venv/bin/pip install -r $(PIPELINE_DIR)/requirements.txt -r $(PIPELINE_DIR)/requirements-dev.txt

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
