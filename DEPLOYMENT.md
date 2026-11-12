# Despliegue de MH IA — por entorno

El código, las imágenes, las dependencias y los modelos son **idénticos** en
ambas máquinas, y Ollama corre nativo en las dos. Cambia solo el sistema
operativo, el script de operación (`scripts/dev.ps1` en Windows,
`scripts/mac/mhia.sh` en la Mac) y **cómo se llega a la GUI** (localhost en
desarrollo, Tailscale en producción). Reglas de red y puertos: `SECURITY.md`.

Archivos de compose:

| Archivo | Para qué |
|---|---|
| `docker-compose.yml` | Base: `qdrant`, `mhia-api`, `web`, redes `backend` y `edge` |
| `docker-compose.host-ollama.yml` | **Modo por defecto** (Windows y Mac): Ollama nativo en el host |
| `docker-compose.gpu.yml` | Opcional en la laptop: `ollama` en contenedor con GPU NVIDIA |
| `docker-compose.dev.yml` | Solo desarrollo: monta código y recarga |

Qué queda fijo para que la laptop y la Mac sean iguales:

| Pieza | Dónde se fija | Quién lo verifica |
|---|---|---|
| Imágenes (Python, Node, Qdrant, Ollama, gitleaks) | versión + `@sha256` en compose, Dockerfiles y CI | `tests/test_portability.py` |
| Paquetes de Python | `services/pipeline_ai/requirements.lock` y `requirements-dev.lock` (con hash) | `pip --require-hashes`, pruebas |
| Versión de Ollama y modelos (tag + id) | `scripts/ollama-models.txt` | `doctor` en cada equipo |
| Saltos de línea y codificación | `.gitattributes` (LF), `.ps1` con BOM UTF-8 | pruebas |

---

## 1. Laptop de desarrollo (Windows + PowerShell)

Acer Predator Helios Neo 16S AI · Core Ultra 9 · RTX 5070 Ti (12 GB) · 32 GB RAM.

```powershell
# 0. Ollama nativo para Windows, misma versión que la Mac (scripts/ollama-models.txt)
winget upgrade Ollama.Ollama          # o winget install Ollama.Ollama la primera vez

# 1. Preparar variables (el .env NUNCA se versiona)
Copy-Item .env.example .env
# editar .env: POSTGRES_PASSWORD como mínimo. Con REASONING_MODE=local (por defecto)
# no hace falta ANTHROPIC_API_KEY.

# 2. Modelos (una sola vez) y validar el entorno sin levantar nada
.\scripts\dev.ps1 pull-models
.\scripts\dev.ps1 doctor              # compara versión de Ollama y id de cada modelo

# 3. Levantar y verificar
.\scripts\dev.ps1 up
.\scripts\dev.ps1 ps
.\scripts\dev.ps1 doctor              # con el stack arriba, prueba también mhia-api -> Ollama
Invoke-WebRequest http://127.0.0.1:8000/health -UseBasicParsing   # {"status":"ok"}
Start-Process http://127.0.0.1:3000                                 # GUI
```

Ollama para Windows usa la RTX por CUDA de forma nativa; no hace falta pasar
la GPU a Docker. Para probar Ollama aislado en un contenedor (modo anterior),
agrega `-OllamaMode docker` a cualquier tarea y confirma una vez que Docker ve
la GPU:

```powershell
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
.\scripts\dev.ps1 up -OllamaMode docker
.\scripts\dev.ps1 pull-models -OllamaMode docker
```

Qdrant **no está accesible desde `localhost`** (por diseño). Para
inspeccionarlo: `docker compose exec mhia-api python -c "..."`.

Para probar otro modelo (por ejemplo `qwen2.5:14b-instruct-q4_K_M`): `ollama
pull`, cambia `OLLAMA_LLM_MODEL` en `.env`, mide con el laboratorio y, si se
adopta, actualiza `scripts/ollama-models.txt` en un PR para que la Mac reciba
exactamente el mismo.

### Razonamiento: modelo local o Claude (`REASONING_MODE`)

| Modo | Quién redacta la respuesta | Costo | Si falla |
|---|---|---|---|
| `local` (por defecto) | `OLLAMA_REASONER_MODEL` (vacío = `OLLAMA_LLM_MODEL`) | Ninguno | Error genérico (502); nada sale de la red |
| `claude` | La API de Claude (`ANTHROPIC_MODEL`) | Por uso | Responde el modelo local y queda en la auditoría (`fallback_from`) |

En los dos modos corren igual el anonimizador y la pasarela de salida: el modelo
local recibe exactamente lo mismo que recibiría Claude, así que el laboratorio
prueba la privacidad sin gastar. La respuesta de la API dice quién respondió
(`"reasoner": "local"` o `"claude"`) y la GUI avisa cuando fue el modelo local.

Para validar con Claude real (antes de cada entrega de fase, plan maestro 8.1):
crédito prepagado de US$5–10 en la consola de Anthropic, recarga automática
apagada y tope de gasto del espacio de trabajo en ese monto. Luego:

```powershell
# en .env: REASONING_MODE=claude y ANTHROPIC_API_KEY=sk-ant-...
.\scripts\dev.ps1 doctor            # falla si falta la clave con REASONING_MODE=claude
.\scripts\lab.ps1 ask               # el resumen dice cuántas respondió cada proveedor
# al terminar, de vuelta a REASONING_MODE=local
```

Con un modelo de razonamiento distinto (por ejemplo el grande de la Mac,
plan maestro 3.3), agrégalo a `scripts/ollama-models.txt` y fija
`OLLAMA_REASONER_MODEL` en `.env`; los doctores fallan si el modelo no está en
esa lista. Los modelos que "piensan" (Qwen3, gpt-oss) funcionan: el bloque
`<think>` no se muestra al usuario.

---

## 2. Producción — Mac mini M5 Pro

Guía paso a paso: **`scripts/mac/README.md`**. En resumen:

```bash
sudo bash scripts/mac/setup.sh       # FileVault, brew, usuario mhia, launchd, energía
# clonar como mhia con la llave de solo lectura que imprime setup.sh
# copiar .env (chmod 600) y credentials/ por un canal seguro
mhia pull-models
mhia up
mhia doctor
```

- Ollama corre **nativo** (Metal) como LaunchDaemon del usuario `mhia`,
  escuchando solo en `127.0.0.1`. Docker en macOS no pasa la GPU de Apple
  Silicon a contenedores.
- Docker lo da **Colima** (sin Docker Desktop: sin licencia y sin sesión
  gráfica), también como LaunchDaemon. Los contenedores arrancan solos al
  iniciar la VM.
- Respaldo diario a las 02:30 y chequeo de salud cada 5 minutos (launchd).

En producción `MHIA_ENV=production`: la API **se niega a arrancar** si falta
`API_SESSION_SECRET` o `API_TOTP_KEY` (o `ANTHROPIC_API_KEY` con
`REASONING_MODE=claude`), si `REASONING_MODE` no es `local` ni `claude`, si el secreto de
sesión mide menos de 32 caracteres, si `API_TOTP_KEY` no es una clave Fernet
válida, si las cookies no son `Secure` o si `MHIA_DEBUG=true`.

### Usuarios (no hay registro público)

La clave TOTP se genera una vez y se guarda en `.env` (no se versiona):

```bash
docker compose run --rm mhia-api python -m pipeline_ai.security.manage gen-key
```

Usuarios, áreas y sesiones viven en **Postgres** (servicio `postgres` del
compose, sin puerto publicado). Antes del primer arranque fija
`POSTGRES_PASSWORD` en `.env` (24+ caracteres, solo letras y números; ver
`.env.example`): sin ella Postgres no arranca. Cambiarla después exige cambiarla
también dentro de la base (`ALTER USER mhia PASSWORD ...`).

Si ya tenías usuarios en `data/auth.db`, al primer arranque con Postgres se
**migran solos** (usuarios, contraseñas, segundo factor, áreas) y el archivo se
renombra a `auth.db.migrado-<fecha>`; todos vuelven a iniciar sesión. Revísalo
con `list-users` y `audit-log`. Guarda el archivo renombrado hasta comprobar que
todo está bien.

Los usuarios se crean con la CLI dentro del contenedor (la contraseña se pide
por teclado, nunca como argumento). Pasa `-e MHIA_ADMIN=<tu-nombre>` para que el
registro de cambios diga quién hizo cada uno. `control_total` exige enrolar TOTP
antes de poder iniciar sesión:

```bash
docker compose run --rm mhia-api python -m pipeline_ai.security.manage create-user --username <usuario> --role control_total
docker compose run --rm mhia-api python -m pipeline_ai.security.manage enroll-totp --username <usuario>
docker compose run --rm mhia-api python -m pipeline_ai.security.manage list-users
docker compose run --rm mhia-api python -m pipeline_ai.security.manage audit-log --limit 50
```

Otros comandos: `reset-password`, `set-role`, `disable-user`, `enable-user`,
`unlock`, `revoke-sessions`.

#### Áreas: qué ve cada persona

El rol dice qué puede **hacer** la persona; sus **áreas** dicen qué puede **ver**
(`área:NIVEL`, niveles `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`). Sin áreas no
ve ningún documento. `control_total` no lee datos por su rol: para el uso
diario, Sistemas crea una **segunda cuenta** `lider` con `general:INTERNAL`.

```bash
docker compose run --rm mhia-api python -m pipeline_ai.security.manage create-user --username <usuario> --role lider --areas "general:INTERNAL, comercial:CONFIDENTIAL, inventario:CONFIDENTIAL"
docker compose run --rm mhia-api python -m pipeline_ai.security.manage set-areas --username <usuario> --areas "general:INTERNAL, rh:RESTRICTED"
```

`set-areas` reemplaza todas las áreas y cierra las sesiones de la persona. La
CLI avisa si un nivel no alcanza el mínimo del área (`rh` exige RESTRICTED;
`general`, INTERNAL; el resto, CONFIDENTIAL). Lo RESTRICTED nunca se manda a
Claude: la persona recibe un aviso de contenido restringido.

Al actualizar desde una versión sin áreas, la base se migra sola al arrancar:
cada usuario recibe las áreas equivalentes a lo que su rol veía, salvo
`control_total`, que queda sin áreas. Revísalo con `list-users`. Después,
**reindexa** (`ingest`) para que cada fragmento lleve su área nueva (por
ejemplo, Nómina pasa de Finanzas a RH y Logística deja de quedar sin indexar).

#### Importar a las personas desde la lista de Sistemas

La lista (Excel o CSV con columnas de correo y puesto) tiene datos personales:
cópiala **solo** a `./data/` (git la ignora). El nombre no se lee ni se guarda.
El rol y las áreas salen del puesto (`security/presets.py`, plan §2.5.1); un
puesto sin preset se reporta y no se importa.

```bash
# 1. Simulación: muestra el plan, no cambia nada
docker compose run --rm mhia-api python -m pipeline_ai.security.manage import-users --file /app/data/usuarios.xlsx
# 2. Aplicar
docker compose run --rm mhia-api python -m pipeline_ai.security.manage import-users --file /app/data/usuarios.xlsx --apply
# 3. Por cada persona nueva (se crean desactivadas, con contraseña desconocida)
docker compose run --rm mhia-api python -m pipeline_ai.security.manage reset-password --username <correo>
docker compose run --rm mhia-api python -m pipeline_ai.security.manage enable-user --username <correo>
```

Reimportar actualiza rol y áreas de quien cambió (y cierra sus sesiones), sin
tocar contraseña, segundo factor ni estado; a quien ya no está en la lista solo
lo reporta. Borra la lista de `./data/` al terminar. La base de usuarios vive
en el volumen `postgres_data` y entra en el respaldo diario. Guarda una copia de `.env` aparte y
fuera de la Mac (sin `API_TOTP_KEY` los secretos TOTP enrolados no se pueden
descifrar): el respaldo automático **no** incluye `.env`.

### Respaldos y restauración

Corren dentro de `mhia-api`, igual en Windows y en la Mac, y se guardan en
`./backups/mhia-<fecha>Z/` (fuera de git): `auth.json` (usuarios, áreas y
registro de cambios; sin sesiones), el manifest de Drive, un snapshot por
colección de Qdrant y `manifest.json` con el sha256 de cada archivo. La
verificación carga `auth.json` en una base desechable para probar que se puede
restaurar.

| Tarea | Windows | Mac |
|---|---|---|
| Crear (y conservar 14) | `.\scripts\dev.ps1 backup` | `mhia backup` (también diario, 02:30) |
| Verificar el último | `.\scripts\dev.ps1 backup-verify` | `mhia backup-verify` |
| Simulacro sin riesgo | `.\scripts\dev.ps1 restore-drill` | `mhia restore-drill` |

El simulacro restaura cada snapshot en una colección temporal, compara el
número de puntos con el respaldo y la borra; las colecciones reales no se
tocan. Hazlo al menos una vez al mes.

Restaurar de verdad (reemplaza los datos actuales; Postgres y Qdrant deben
estar arriba, solo se detienen la API y la GUI). Todos vuelven a iniciar sesión
y el registro de cambios anota la restauración. Un respaldo anterior a Postgres
(con `auth.db`) también se restaura así:

```bash
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml stop mhia-api web
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml run --rm --no-deps mhia-api \
  python -m pipeline_ai.ops.backup restore mhia-AAAAMMDDTHHMMSSZ --force
docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml up -d
```

Postgres está fijado en la versión mayor 18. Subir de versión mayor (19, 20...)
no es un simple cambio de imagen: respalda, levanta la versión nueva con un
volumen vacío y restaura el respaldo.

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
- [ ] Mac mini con macOS al día y FileVault **activo**.
- [ ] `sudo bash scripts/mac/setup.sh` sin errores (usuario `mhia`, Colima, Ollama, launchd).
- [ ] Repo clonado como `mhia` en `main` con la llave de despliegue de solo lectura.
- [ ] `mhia pull-models` y `mhia doctor` en verde (misma versión de Ollama y mismos id de modelo que la laptop).
- [ ] `.env` y `credentials/` copiados por un canal seguro (nunca por git ni chat), `chmod 600 .env`.

**Datos**
- [ ] Opción A (recomendada): reindexar con `docker exec -it mhia-api python -m pipeline_ai.ingestion.run_ingest`.
- [ ] Opción B: en la laptop `.\scripts\dev.ps1 backup`, copiar la carpeta del respaldo a `backups/` de la Mac y restaurarla (sección 2). Válido porque el id del modelo de embeddings es el mismo (lo comprueba el doctor).
- [ ] La colección debe crearse con la ingesta de esta versión (esquema híbrido: vector denso + palabras clave). Un snapshot de una colección anterior solo responde por significado.

**Verificación**
- [ ] `mhia ps` con todo `healthy` y `mhia doctor` en verde (incluye mhia-api -> Ollama).
- [ ] `mhia backup` y `mhia restore-drill` correctos.
- [ ] `curl http://127.0.0.1:8000/health` responde `ok`.
- [ ] Ningún puerto publicado fuera de `127.0.0.1` (`docker ps`).
- [ ] `docker exec -it mhia-api python -m eval.eval_router` iguala el resultado de la laptop.
- [ ] Desde otro dispositivo **fuera** del tailnet, la URL no responde.
- [ ] Desde un dispositivo aprobado, la GUI carga por HTTPS.

**Reversa**
- La laptop conserva su stack y su volumen hasta confirmar la Mac mini; volver es apuntar de nuevo la GUI a la laptop.

---

## 5. Notas de portabilidad de modelos

- Los modelos exactos (tag e id) están en `scripts/ollama-models.txt`; los doctores de ambos equipos fallan si no coinciden. Los pesos se descargan por separado (`ollama pull`), pero el id garantiza que son los mismos.
- Si cambias `OLLAMA_EMBED_MODEL`, hay que **reindexar** la colección de Qdrant.
- Las colecciones creadas antes de la búsqueda híbrida siguen funcionando, pero solo por significado, y la ingesta se niega a escribir en ellas. Para activar las palabras clave: cambia `QDRANT_COLLECTION` a un nombre nuevo, borra `sync_manifest.json`, corre la ingesta, apunta la API a la colección nueva y borra la anterior.
- El juez de relevancia usa el mismo `OLLAMA_LLM_MODEL` que el router y el anonimizador (`RETRIEVAL_JUDGE_ENABLED=false` lo apaga y vuelve al umbral de score).
- El razonamiento local usa `OLLAMA_REASONER_MODEL` (vacío = `OLLAMA_LLM_MODEL`). El modelo grande de la Mac (~18–20 GB) no cabe en la VRAM de la laptop: ahí se prueba con el de trabajo.
- Las imágenes propias (`mhia-api`, `web`) compilan para `linux/amd64` y `linux/arm64` (verificado en CI).
