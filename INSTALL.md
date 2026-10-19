# Instalación — qué poner en cada máquina

Checklist de instalación. `DEPLOYMENT.md` explica cómo levantar el stack;
`INGESTION.md` cubre la ingesta de Drive.

---

## A. Laptop de desarrollo (Windows + PowerShell)

Acer Predator Helios Neo 16S AI · Core Ultra 9 · RTX 5070 Ti 12 GB · 32 GB RAM.

### A.1 Software base

| Software | Para qué | Notas |
|---|---|---|
| Git | Control de versiones | |
| Docker Desktop (última) | Qdrant, API, GUI y Ollama en contenedores | Backend **WSL2** con soporte de GPU |
| Driver NVIDIA (≥ 550.x) | GPU visible para Docker | Se instala en Windows, no dentro de WSL |
| Python 3.12 | Lint y pruebas del pipeline fuera de Docker | CI usa 3.12 |
| Node.js 22 LTS o superior | Desarrollo, lint y build de la GUI | |
| Tailscale (pendiente) | Acceso remoto | Ver `DEPLOYMENT.md`, sección 3 |

### A.2 Clonar y preparar

```powershell
git clone <URL_DEL_REPO> mh-ia
cd mh-ia
Copy-Item .env.example .env       # editar ANTHROPIC_API_KEY como mínimo
.\scripts\dev.ps1 doctor          # valida compose, puertos y .env ignorado
```

### A.3 Levantar el stack

```powershell
.\scripts\dev.ps1 up
.\scripts\dev.ps1 pull-models
```

### A.4 Entorno local para iterar (lint, pruebas, GUI)

```powershell
.\scripts\dev.ps1 venv            # crea services\pipeline_ai\.venv con Python 3.12
.\scripts\dev.ps1 test
.\scripts\dev.ps1 lint

cd web
npm ci
npm run dev                       # GUI en http://localhost:3000
```

---

## B. Mac mini M5 Pro (producción, zsh)

| Software | Para qué |
|---|---|
| Git | Clonar el repo |
| Docker Desktop for Mac (Apple Silicon) | Qdrant, API y GUI |
| Homebrew | Instalar Ollama y Tailscale |
| Ollama (nativo, no Docker) | `brew install ollama` — usa Metal |
| Tailscale | Acceso remoto privado (ver `DEPLOYMENT.md`) |

Pasos completos y checklist de migración en `DEPLOYMENT.md`, secciones 2 y 4.

---

## C. Credenciales necesarias

| Credencial | Dónde se obtiene | Dónde se coloca |
|---|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com | `.env` |
| `API_SESSION_SECRET` (≥ 32 caracteres) | Generarlo (ver `.env.example`) | `.env` |
| `service_account.json` (Drive) | Google Cloud Console → Service Accounts (ver `INGESTION.md`) | `credentials/` |
| `GOOGLE_DRIVE_FOLDER_ID` | URL de la carpeta compartida | `.env` |
| `ERPNEXT_*` (solo lectura) | Frappe/ERPNext → API Access (cuando el proveedor lo entregue) | `.env` |
| `GHL_API_TOKEN`, `GHL_LOCATION_ID` (solo lectura) | GoHighLevel → integración privada | `.env` |
| `FIRESTORE_*`, `SUPABASE_*` (solo lectura) | Firebase / Supabase | `.env` y `credentials/` |

**Ninguna se versiona en git.** `.gitignore` excluye `.env`, `.env.*`
(salvo `.env.example`), `credentials/*.json`, `*.pem` y `*.key`.

---

## D. Verificación rápida

```powershell
docker compose -f docker-compose.yml -f docker-compose.gpu.yml ps       # todo "healthy"
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing         # {"status":"ok"}
docker exec -it mhia-api python -c "import urllib.request as u; print(u.urlopen('http://qdrant:6333/collections').read())"
```
