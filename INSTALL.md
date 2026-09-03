# Instalación — qué poner en cada máquina

Este documento es el checklist de instalación. `DEPLOYMENT.md` explica cómo
levantar el stack una vez instalado esto; `INGESTION.md` cubre la ingesta de
Drive.

---

## A. Laptop de desarrollo (Acer Predator Helios Neo 16S AI · Core Ultra 9 · RTX 5070 Ti 12GB · 32GB RAM)

### A.1 Software base (obligatorio)

| Software | Para qué | Notas |
|---|---|---|
| **Git** | Clonar el repo, control de versiones | git-scm.com |
| **Docker Desktop** (última versión) | Correr Qdrant + la app + Ollama containerizado | En Windows, backend **WSL2** (no Hyper-V) |
| **Driver NVIDIA** actualizado (≥ 550.x) | Que la GPU sea visible para Docker | Se instala desde el sitio de NVIDIA, no dentro de WSL |
| **Python 3.12** | Desarrollo/lint/tests fuera de Docker | python.org, o `pyenv` |
| **Node.js no es necesario** | — | El proyecto es 100% Python |

### A.2 Soporte de GPU en Docker (para que Ollama use la RTX 5070 Ti)

- **Windows con WSL2 + Docker Desktop**: basta con tener el driver NVIDIA para Windows actualizado y la opción *"Use the WSL 2 based engine"* + *GPU support* habilitada en Docker Desktop. WSL2 expone la GPU automáticamente, no se instala driver dentro de la distro Linux.
- **Linux nativo**: instalar `nvidia-container-toolkit`:
  ```bash
  sudo apt-get install -y nvidia-container-toolkit
  sudo nvidia-ctk runtime configure --runtime=docker
  sudo systemctl restart docker
  ```
- **Verificar** (ambos casos):
  ```bash
  docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
  ```
  Si esto muestra la RTX 5070 Ti, todo lo demás funcionará.

### A.3 Clonar y preparar el proyecto

```bash
git clone <URL_DEL_REPO> mh-ia
cd mh-ia
cp .env.example .env        # editar ANTHROPIC_API_KEY como mínimo
```

### A.4 Levantar el stack

```bash
make up-gpu
docker exec -it mhia-ollama ollama pull llama3.1:8b
docker exec -it mhia-ollama ollama pull nomic-embed-text
```

### A.5 (Opcional) Entorno local sin Docker, para iterar rápido en el código

Útil para correr `pytest`, `ruff`, o `python -m app.main` directo mientras
Qdrant/Ollama siguen corriendo en Docker.

```bash
make venv
source .venv/bin/activate     # .venv\Scripts\activate en cmd de Windows
# En este modo, OLLAMA_BASE_URL y QDRANT_URL en .env deben apuntar a
# localhost (los puertos ya están expuestos por docker-compose.yml):
#   OLLAMA_BASE_URL=http://localhost:11434
#   QDRANT_URL=http://localhost:6333
python -m app.main "¿cuál es el estatus del lote 42?"
```

---

## B. Mac Mini M4 Pro (producción)

### B.1 Software base

| Software | Para qué |
|---|---|
| **Git** | Clonar el repo |
| **Docker Desktop for Mac** (Apple Silicon) | Correr Qdrant + la app |
| **Homebrew** | Instalar Ollama nativo |
| **Ollama** (nativo, no Docker) | `brew install ollama` — usa Metal directamente |

### B.2 Instalación

```bash
brew install ollama git
ollama pull llama3.1:8b
ollama pull nomic-embed-text
brew services start ollama      # deja Ollama corriendo como servicio en background

git clone <URL_DEL_REPO> mh-ia
cd mh-ia
cp .env.example .env            # editar ANTHROPIC_API_KEY
```

### B.3 Levantar el stack

```bash
make up-mac
```

### B.4 Acceso remoto (Tailscale)

```bash
brew install tailscale
sudo tailscale up
```
Después, el equipo autorizado accede a los servicios expuestos por Tailscale
en vez de exponer puertos a internet (ver RNF-03 en el SRS).

---

## C. Credenciales necesarias (ambas máquinas)

| Credencial | Dónde se obtiene | Dónde se coloca |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | `.env` |
| `service_account.json` (Google Drive) | Google Cloud Console → Service Accounts (ver `INGESTION.md`) | `./credentials/service_account.json` |
| `GOOGLE_DRIVE_FOLDER_ID` | URL de la carpeta compartida en Drive | `.env` |
| `ERPNEXT_API_KEY` / `ERPNEXT_API_SECRET` (Fase 2) | Frappe/ERPNext → Settings → API Access, usuario de solo lectura | `.env` |

**Ninguna de estas credenciales se versiona en git** — `.gitignore` ya excluye
`.env` y `credentials/*.json`.

---

## D. Verificación rápida post-instalación

```bash
docker compose ps                 # qdrant y mhia-app en estado "healthy"/"running"
curl http://localhost:6333/collections   # Qdrant responde
docker exec -it mhia-app python -m pytest -q   # tests pasan dentro del contenedor también
```
