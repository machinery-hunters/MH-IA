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
| Docker Desktop (última) | Qdrant, API y GUI en contenedores | Backend **WSL2** |
| Ollama para Windows (versión de `scripts/ollama-models.txt`) | Modelo local nativo, igual que en la Mac | `winget install Ollama.Ollama`; escucha solo en `127.0.0.1` |
| Driver NVIDIA (≥ 550.x) | GPU para Ollama | Se instala en Windows, no dentro de WSL |
| Python 3.12 | Lint y pruebas del pipeline fuera de Docker | CI, imagen y Mac usan 3.12 |
| uv (opcional) | Solo para regenerar los `.lock` | `winget install --id astral-sh.uv -e` |
| Node.js 22 LTS o superior | Desarrollo, lint y build de la GUI | |
| Tailscale (pendiente) | Acceso remoto | Ver `DEPLOYMENT.md`, sección 3 |

### A.2 Clonar y preparar

```powershell
git clone <URL_DEL_REPO> mh-ia
cd mh-ia
Copy-Item .env.example .env       # REASONING_MODE=local no necesita ANTHROPIC_API_KEY
git config core.autocrlf false    # el repo ya normaliza saltos de línea (.gitattributes)
.\scripts\dev.ps1 hooks           # activa el hook que protege main/develop
.\scripts\dev.ps1 pull-models     # modelos exactos de scripts/ollama-models.txt
.\scripts\dev.ps1 doctor          # compose, puertos, .env ignorado, Ollama y modelos
```

### A.3 Levantar el stack

```powershell
.\scripts\dev.ps1 up              # Ollama nativo (por defecto); -OllamaMode docker = en contenedor
```

### A.4 Entorno local para iterar (lint, pruebas, GUI)

```powershell
.\scripts\dev.ps1 venv            # services\pipeline_ai\.venv con Python 3.12 y versiones exactas (.lock)
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
| Homebrew | Instala el resto (desde la cuenta de administrador) |
| Colima + docker + docker-compose | Qdrant, API y GUI (sin Docker Desktop) |
| Ollama (nativo, no Docker) | Modelo local con Metal |
| jq, git | Doctor y despliegue |
| Tailscale | Acceso remoto privado (ver `DEPLOYMENT.md`) |

Todo lo instala y configura `sudo bash scripts/mac/setup.sh`. Pasos completos en
`scripts/mac/README.md`; checklist de migración en `DEPLOYMENT.md`, sección 4.

---

## C. Credenciales necesarias

| Credencial | Dónde se obtiene | Dónde se coloca |
|---|---|---|
| `ANTHROPIC_API_KEY` (solo con `REASONING_MODE=claude`) | console.anthropic.com | `.env` |
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
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml ps   # todo "healthy"
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing         # {"status":"ok"}
docker exec -it mhia-api python -c "import urllib.request as u; print(u.urlopen('http://qdrant:6333/collections').read())"
```
