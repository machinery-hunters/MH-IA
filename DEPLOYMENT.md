# Despliegue de MH IA — por entorno

El código es idéntico en ambas máquinas. Cambia **dónde corre Ollama** (override
de compose) y **cómo se llega a la GUI** (localhost en desarrollo, Tailscale en
producción). Reglas de red y puertos: `SECURITY.md`.

Archivos de compose:

| Archivo | Para qué |
|---|---|
| `docker-compose.yml` | Base: `qdrant`, `mhia-api`, `web`, redes `backend` y `edge` |
| `docker-compose.gpu.yml` | Laptop: suma `ollama` en contenedor con GPU NVIDIA |
| `docker-compose.mac.yml` | Mac mini: Ollama nativo en el host |
| `docker-compose.dev.yml` | Solo desarrollo: monta código y recarga |

---

## 1. Laptop de desarrollo (Windows + PowerShell)

Acer Predator Helios Neo 16S AI · Core Ultra 9 · RTX 5070 Ti (12 GB) · 32 GB RAM.

```powershell
# 1. Preparar variables (el .env NUNCA se versiona)
Copy-Item .env.example .env
# editar .env: ANTHROPIC_API_KEY como mínimo

# 2. Validar el entorno sin levantar nada
.\scripts\dev.ps1 doctor

# 3. Levantar y descargar modelos (una sola vez)
.\scripts\dev.ps1 up
.\scripts\dev.ps1 pull-models

# 4. Verificar
docker compose -f docker-compose.yml -f docker-compose.gpu.yml ps
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing   # {"status":"ok"}
Start-Process http://127.0.0.1:3000                                 # GUI
```

Confirmar que Docker ve la GPU (una vez):

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

Qdrant y Ollama **no están accesibles desde `localhost`** (por diseño). Para
inspeccionarlos: `docker exec -it mhia-api python -c "..."` o
`docker compose exec`.

Con 12 GB de VRAM puedes probar `qwen2.5:14b-instruct-q4_K_M`; cambia
`OLLAMA_LLM_MODEL` en `.env` y vuelve a levantar `mhia-api`.

---

## 2. Producción — Mac mini M5 Pro (zsh)

Ollama corre **nativo** (Metal). Docker Desktop for Mac no pasa la GPU de
Apple Silicon a contenedores.

```bash
brew install ollama git
brew services start ollama          # escucha solo en 127.0.0.1; NO cambiar OLLAMA_HOST
ollama pull llama3.1:8b
ollama pull nomic-embed-text

git clone <URL_DEL_REPO> mh-ia && cd mh-ia
cp .env.example .env                # completar; MHIA_ENV=production, API_SESSION_SECRET (>= 32)
docker compose -f docker-compose.yml -f docker-compose.mac.yml up -d --build
```

En producción `MHIA_ENV=production`: la API **se niega a arrancar** si falta
`ANTHROPIC_API_KEY` o `API_SESSION_SECRET`, si este mide menos de 32
caracteres o si `MHIA_DEBUG=true`.

---

## 3. Acceso remoto (Tailscale) — prerrequisito pendiente

**Estado:** aún no hay cuenta de Tailscale. Es lo único que falta para el
acceso remoto; el sistema funciona en local sin ella.

### Preparación (una vez, en la consola de administración de Tailscale)

1. Crear la cuenta con un proveedor de identidad que **tenga MFA activo**.
2. Activar **aprobación de dispositivos** y **expiración de claves**.
3. Activar **MagicDNS** y **HTTPS** del tailnet.
4. Definir una ACL por etiqueta: solo las personas autorizadas llegan a `tag:mhia-brain` (la Mac mini). Nadie más ve ese equipo.
5. Instalar Tailscale en la laptop y en el celular de quien administra.

### En la Mac mini

```bash
brew install --cask tailscale        # o la app oficial
sudo tailscale up --advertise-tags=tag:mhia-brain
# Publicar la GUI SOLO dentro del tailnet (HTTPS). Verifica la sintaxis con
# `tailscale serve --help` en tu versión:
tailscale serve --bg 3000
```

- **No usar `tailscale funnel`**: expone el servicio a internet.
- Agrega el nombre MagicDNS de la Mac mini a `API_ALLOWED_HOSTS` en `.env`.
- La GUI queda en `https://<nombre-mac>.<tu-tailnet>.ts.net` solo para dispositivos aprobados.
- Instalar el PWA: abrir esa URL en el celular y "Agregar a pantalla de inicio".

---

## 4. Checklist de migración laptop → Mac mini

**Antes**
- [ ] Mac mini con macOS al día, cifrado de disco (FileVault) **activo**, usuario dedicado.
- [ ] Docker Desktop, Homebrew, Ollama nativo y Tailscale instalados.
- [ ] `ollama pull llama3.1:8b` y `ollama pull nomic-embed-text` (mismo `OLLAMA_EMBED_MODEL` que en la laptop).
- [ ] `.env` y `credentials/` copiados por un canal seguro (nunca por git ni chat).
- [ ] Versiones de `qdrant` y `ollama` fijadas en compose (ver `SECURITY.md`, "aceptado").

**Datos**
- [ ] Opción A (recomendada): reindexar con `docker exec -it mhia-api python -m pipeline_ai.ingestion.run_ingest`.
- [ ] Opción B: copiar un snapshot de Qdrant, solo si el modelo de embeddings es idéntico.

**Verificación**
- [ ] `docker compose -f docker-compose.yml -f docker-compose.mac.yml ps` con todo `healthy`.
- [ ] `curl http://127.0.0.1:8000/health` responde `ok`.
- [ ] Ningún puerto publicado fuera de `127.0.0.1` (`docker ps`).
- [ ] `docker exec -it mhia-api python -m eval.eval_router` iguala el resultado de la laptop.
- [ ] Desde otro dispositivo **fuera** del tailnet, la URL no responde.
- [ ] Desde un dispositivo aprobado, la GUI carga por HTTPS.

**Reversa**
- La laptop conserva su stack y su volumen hasta confirmar la Mac mini; volver es apuntar de nuevo la GUI a la laptop.

---

## 5. Notas de portabilidad de modelos

- Usa los mismos `OLLAMA_LLM_MODEL` y `OLLAMA_EMBED_MODEL` en ambas máquinas para que router y anonimizador sean comparables. Los pesos se descargan por separado (`ollama pull`).
- Si cambias `OLLAMA_EMBED_MODEL`, hay que **reindexar** la colección de Qdrant.
- Las imágenes propias (`mhia-api`, `web`) compilan para `linux/amd64` y `linux/arm64` (verificado en CI).
