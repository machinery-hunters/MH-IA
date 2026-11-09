# Mac mini M5 Pro: instalación y operación de MH IA

Guía para dejar la Mac mini como servidor de MH IA (fase F0 del
`docs/PLAN-MAESTRO.md`). Todo se hace en la Mac, conectada por Ethernet, desde
tu cuenta de administrador en Terminal (zsh).

## Qué queda instalado

| Pieza | Cómo corre | Escucha en |
|---|---|---|
| Ollama (Metal) | LaunchDaemon `com.machineryhunters.mhia.ollama`, usuario `mhia` | `127.0.0.1:11434` |
| Colima (VM con Docker) | LaunchDaemon `com.machineryhunters.mhia.colima`, usuario `mhia` | socket local |
| API, GUI y Qdrant | Contenedores (`restart: unless-stopped`) dentro de Colima | API `127.0.0.1:8000`, GUI `127.0.0.1:3000`; Qdrant sin puerto |
| Respaldo | LaunchDaemon diario a las 02:30 | — |
| Salud | LaunchDaemon cada 5 minutos | — |

El usuario `mhia` es oculto, no es administrador y nadie conoce su contraseña:
si alguien comprometiera un servicio, no obtiene `sudo` ni desbloquea FileVault.
Los registros quedan en `/Users/mhia/Library/Logs/mhia/`.

## 1. Antes de empezar (una vez)

1. macOS al día. **FileVault activo** (Ajustes > Privacidad y seguridad >
   FileVault). El script se niega a seguir sin él.
2. Homebrew instalado desde tu cuenta, sin `sudo`: <https://brew.sh>.
3. UPS conectado (la Mac se enciende sola tras un corte; ver paso 2).

Tras un reinicio, FileVault pide la contraseña antes de arrancar macOS: los
servicios suben solos en cuanto alguien la escribe. Para un reinicio planeado
sin estar en frente: `sudo fdesetup authrestart`.

## 2. Preparar la Mac

Copia este repositorio a tu cuenta (o descarga solo `scripts/mac/`) y ejecuta:

```bash
sudo bash scripts/mac/setup.sh --check   # muestra qué haría, sin cambiar nada
sudo bash scripts/mac/setup.sh           # aplica (se puede repetir sin riesgo)
```

Instala colima, docker, docker-compose, ollama, jq y git; crea el usuario
`mhia`; instala los LaunchDaemons; deja la Mac sin suspensión y con encendido
automático; y crea el atajo `mhia`.

## 3. Clonar el repo como `mhia`

`setup.sh` imprime una llave pública. En GitHub: repo `MH-IA` > Settings >
Deploy keys > Add deploy key, pega la llave y deja **sin marcar** "Allow write
access". Luego:

```bash
sudo -u mhia -H git clone -b main git@github.com:machinery-hunters/MH-IA.git /Users/mhia/mh-ia
sudo bash /Users/mhia/mh-ia/scripts/mac/setup.sh   # instala respaldo y salud, que esperaban al repo
```

Durante las pruebas de F0 se puede clonar `develop`; producción sigue `main`.

## 4. Secretos y datos

Copia `.env` y `credentials/` desde la laptop por un canal seguro (USB cifrado o
AirDrop directo; nunca git, correo ni chat):

```bash
sudo cp /ruta/segura/.env /Users/mhia/mh-ia/.env
sudo cp /ruta/segura/credentials/*.json /Users/mhia/mh-ia/credentials/
sudo chown mhia:staff /Users/mhia/mh-ia/.env /Users/mhia/mh-ia/credentials/*
sudo chmod 600 /Users/mhia/mh-ia/.env /Users/mhia/mh-ia/credentials/*
```

En `.env` de producción: `MHIA_ENV=production`, `API_COOKIE_SECURE=true`, el
nombre MagicDNS de la Mac en `API_ALLOWED_HOSTS` y, si quieres avisos,
`MHIA_ALERT_URL` (por ejemplo un tema privado de ntfy, gratis).

## 5. Arrancar y validar

```bash
mhia pull-models    # modelos exactos de scripts/ollama-models.txt
mhia up
mhia doctor         # FileVault, .env 600, puertos, Ollama, modelos, contenedor -> Ollama, disco
mhia ps             # todo "healthy"
```

Si el doctor dice que `mhia-api` no alcanza a Ollama, agrega
`MHIA_HOST_GATEWAY=192.168.5.2` al `.env` (la IP del host dentro de la VM de
Colima) y repite `mhia up` y `mhia doctor`.

Para comprobar que Ollama usa la GPU: `sudo -u mhia -H ollama ps` debe mostrar
`100% GPU` con un modelo cargado.

## 6. Operación diaria

| Tarea | Comando |
|---|---|
| Estado | `mhia ps`, `mhia doctor` |
| Registros de la API | `mhia logs` |
| Actualizar a lo último de `main` | `mhia update` (solo avanza; nunca reescribe) |
| Respaldo manual | `mhia backup` |
| Simulacro de restauración (mensual) | `mhia restore-drill` |
| Reiniciar Ollama | `sudo launchctl kickstart -k system/com.machineryhunters.mhia.ollama` |
| Reiniciar Colima | `sudo launchctl kickstart -k system/com.machineryhunters.mhia.colima` |
| Último chequeo de salud | `sudo tail /Users/mhia/Library/Logs/mhia/health.log` |

Restaurar un respaldo (reemplaza los datos actuales):

```bash
sudo -u mhia -H bash -c 'cd /Users/mhia/mh-ia && export DOCKER_HOST=unix:///Users/mhia/.colima/default/docker.sock &&
  C="docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml" &&
  $C stop mhia-api web &&
  $C run --rm --no-deps mhia-api python -m pipeline_ai.ops.backup restore mhia-AAAAMMDDTHHMMSSZ --force &&
  $C up -d'
```

## 7. Cambiar versiones

Nada se actualiza solo en la Mac. Ollama, modelos e imágenes se suben primero
en la laptop (PR que cambia `scripts/ollama-models.txt`, compose o los `.lock`),
pasan CI y luego `mhia update` + `brew upgrade ollama` + `mhia pull-models` +
`mhia doctor` en la Mac.
