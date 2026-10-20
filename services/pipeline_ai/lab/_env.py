"""Configuracion comun del laboratorio. Importar SIEMPRE antes que pipeline_ai.

Fija, antes de que pipeline_ai.config lea el entorno:
  - QDRANT_URL / QDRANT_COLLECTION: un Qdrant desechable en 127.0.0.1 y una coleccion
    propia del laboratorio (nunca la de produccion ni la del compose normal).
  - OLLAMA_BASE_URL: el Ollama local.
Y hace importable el paquete pipeline_ai aunque el script se ejecute como archivo suelto.

Los documentos del laboratorio son SINTETICOS (lab/docs). No apuntes esto a datos reales.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

LAB_DIR = Path(__file__).resolve().parent
SERVICE_DIR = LAB_DIR.parent
DOCS_DIR = LAB_DIR / "docs"
RESULTS_DIR = LAB_DIR / "results"

# LAB_COLLECTION permite apuntar las herramientas de MEDICION (evaluar_recuperacion.py) a otra
# coleccion del mismo Qdrant desechable, p. ej. una con datos reales ingestados para evaluar.
# lab_ingest.py recrea esta coleccion en cada corrida: no lo uses con una que quieras conservar.
LAB_COLLECTION = os.environ.get("LAB_COLLECTION", "mhia_lab_docs")
LAB_QDRANT_URL = os.environ.get("LAB_QDRANT_URL", "http://127.0.0.1:6333")
LAB_OLLAMA_URL = os.environ.get("LAB_OLLAMA_URL", "http://127.0.0.1:11434")

if os.environ.get("MHIA_ENV", "development") == "production":
    raise SystemExit("El laboratorio no se ejecuta con MHIA_ENV=production.")

# Asignacion directa (no setdefault): el .env trae QDRANT_URL=http://qdrant:6333 (red de Docker)
# y el laboratorio debe usar siempre su propio Qdrant local.
os.environ["QDRANT_URL"] = LAB_QDRANT_URL
os.environ["QDRANT_COLLECTION"] = LAB_COLLECTION
os.environ["OLLAMA_BASE_URL"] = LAB_OLLAMA_URL

if str(SERVICE_DIR) not in sys.path:
    sys.path.insert(0, str(SERVICE_DIR))
