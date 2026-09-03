# Despliegue MH IA — por entorno

El código y el grafo LangGraph son idénticos en ambas máquinas. Lo único
que cambia es **dónde y cómo corre Ollama**, resuelto vía overrides de
Docker Compose.

## 1. Laptop de desarrollo/pruebas
**Acer Predator Helios Neo 16S AI · Core Ultra 9 · RTX 5070 Ti (12GB VRAM) · 32GB RAM**

Ollama corre **containerizado con acceso a la GPU NVIDIA**.

```bash
# una sola vez: confirmar que Docker ve la GPU
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi

cp .env.example .env   # rellenar ANTHROPIC_API_KEY
docker compose -f docker-compose.yml -f docker-compose.gpu.yml up -d

# descargar modelos dentro del contenedor ollama
docker exec -it mhia-ollama ollama pull llama3.1:8b
docker exec -it mhia-ollama ollama pull nomic-embed-text
```

Con 12GB de VRAM tienes margen para modelos más grandes que en el Mac si
en algún momento quieres subir de `llama3.1:8b` a algo como
`qwen2.5:14b-instruct-q4_K_M` para pruebas de calidad — ajusta
`OLLAMA_LLM_MODEL` en `.env` y vuelve a levantar `mhia-app`.

## 2. Producción — Mac Mini M4 Pro

Ollama corre **nativo en macOS** (usa Metal directamente; Docker Desktop en
Mac no puede pasar la GPU Apple Silicon a un contenedor Linux).

```bash
brew install ollama
ollama pull llama3.1:8b
ollama pull nomic-embed-text
ollama serve &          # o dejarlo como servicio con `brew services start ollama`

cp .env.example .env    # rellenar ANTHROPIC_API_KEY
docker compose -f docker-compose.yml -f docker-compose.mac.yml up -d
```

## 3. Notas de portabilidad de modelos

- Usa **los mismos nombres de modelo** (`OLLAMA_LLM_MODEL`,
  `OLLAMA_EMBED_MODEL`) en ambos `.env` si quieres que el comportamiento del
  router/anonymizer sea comparable entre laptop y Mac. Los pesos se
  descargan por separado en cada máquina (`ollama pull`), no viajan en el
  repo ni en las imágenes Docker.
- La colección de Qdrant (`QDRANT_COLLECTION`) generada con el modelo de
  embeddings de la laptop **debe re-indexarse** si cambias
  `OLLAMA_EMBED_MODEL` al pasar a Mac — las dimensiones/semántica del
  vector deben coincidir entre indexación y consulta.
- Recomendado: fijar `OLLAMA_EMBED_MODEL=nomic-embed-text` desde ya en
  ambos entornos para no tener que re-indexar Google Drive al pasar de
  laptop a Mac Mini.
