# Uso: make <target>
# Requiere: Docker, Docker Compose v2, Python 3.12 (para lint/test locales)

.PHONY: help up-gpu up-mac down logs ingest lint fmt test venv

help:
	@echo "Targets disponibles:"
	@echo "  make up-gpu    - Levanta el stack en la laptop (Ollama con GPU NVIDIA)"
	@echo "  make up-mac    - Levanta el stack en el Mac Mini (Ollama nativo)"
	@echo "  make down      - Detiene el stack"
	@echo "  make logs      - Sigue los logs de mhia-app"
	@echo "  make ingest    - Corre la sincronización de Google Drive -> Qdrant"
	@echo "  make venv      - Crea un entorno virtual local para desarrollo (sin Docker)"
	@echo "  make lint      - Corre ruff + black --check"
	@echo "  make fmt       - Aplica black + ruff --fix"
	@echo "  make test      - Corre la suite de pytest"

up-gpu:
	docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d --build

up-mac:
	docker compose -f docker-compose.yml -f docker-compose.mac.yml up -d --build

down:
	docker compose down

logs:
	docker compose logs -f mhia-app

ingest:
	docker exec -it mhia-app python -m app.ingestion.run_ingest

venv:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
	@echo "Activa con: source .venv/bin/activate"

lint:
	ruff check app tests
	black --check app tests

fmt:
	ruff check app tests --fix
	black app tests

test:
	pytest -v
