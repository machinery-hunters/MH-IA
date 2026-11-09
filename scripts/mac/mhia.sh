#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Operación de MH IA en la Mac mini (equivalente a scripts/dev.ps1 en Windows).
#
#   mhia doctor          valida todo sin levantar nada
#   mhia up | down | ps | logs
#   mhia pull-models     descarga los modelos de scripts/ollama-models.txt
#   mhia backup          respaldo + verificación + conserva 14
#   mhia backup-verify | restore-drill
#   mhia update          git pull (solo fast-forward de main) + up
#
# `mhia` es el atajo que instala setup.sh en /usr/local/bin. Todo corre como el
# usuario de servicio (por defecto "mhia"): desde otra cuenta, se re-ejecuta con sudo.
# Compatible con el bash 3.2 de macOS.
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

MHIA_USER="${MHIA_USER:-mhia}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
MODELS_FILE="$REPO/scripts/ollama-models.txt"
OLLAMA_URL="http://127.0.0.1:11434"
COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.host-ollama.yml)

usage() {
  sed -n '3,14p' "$0" | sed 's/^# \{0,1\}//'
  exit 2
}

[ $# -ge 1 ] || usage
TASK="$1"
shift

# Re-ejecutar como el usuario de servicio (dueño del repo, de Colima y de Ollama).
if [ "$(id -un)" != "$MHIA_USER" ]; then
  exec sudo -u "$MHIA_USER" -H /bin/bash "$0" "$TASK" "$@"
fi

if [ -x /opt/homebrew/bin/brew ]; then
  export PATH="/opt/homebrew/bin:$PATH"
fi
# Docker de Colima del usuario de servicio.
if [ -S "$HOME/.colima/default/docker.sock" ]; then
  export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
fi

cd "$REPO"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }
err() { printf 'ERROR: %s\n' "$*" >&2; }

expected_ollama_version() { awk '$1=="ollama"{print $2}' "$MODELS_FILE"; }
expected_models() { awk '!/^#/ && NF>=2 && $1!="ollama"{print $1" "$2}' "$MODELS_FILE"; }

# Valida Ollama nativo contra scripts/ollama-models.txt. Imprime errores y devuelve
# su número como código de salida (0 = todo bien).
check_ollama() {
  local problems=0 version want tag id installed
  if ! version="$(curl -fsS --max-time 5 "$OLLAMA_URL/api/version" | sed -E 's/.*"version":"([^"]+)".*/\1/')"; then
    err "Ollama no responde en $OLLAMA_URL (sudo launchctl kickstart -k system/com.machineryhunters.mhia.ollama)"
    return 1
  fi
  want="$(expected_ollama_version)"
  if [ "$version" != "$want" ]; then
    err "Ollama $version instalado; se espera $want (brew upgrade ollama, o actualiza scripts/ollama-models.txt tras probar en la laptop)"
    problems=$((problems + 1))
  else
    log "Ollama $version (OK)"
  fi
  installed="$(ollama list 2>/dev/null | awk 'NR>1{print $1" "$2}')"
  while read -r tag id; do
    [ -n "$tag" ] || continue
    if ! printf '%s\n' "$installed" | grep -q "^$tag "; then
      err "Falta el modelo $tag (scripts/mac/mhia.sh pull-models)"
      problems=$((problems + 1))
    elif ! printf '%s\n' "$installed" | grep -q "^$tag $id\$"; then
      err "El modelo $tag no tiene el id esperado $id (ver scripts/ollama-models.txt)"
      problems=$((problems + 1))
    else
      log "Modelo $tag $id (OK)"
    fi
  done <<EOF
$(expected_models)
EOF
  # Ollama no tiene autenticación: debe escuchar SOLO en loopback.
  if lsof -nP -iTCP:11434 -sTCP:LISTEN 2>/dev/null | awk 'NR>1{print $9}' | grep -vqE '^(127\.0\.0\.1|\[::1\]):11434$'; then
    err "Ollama escucha fuera de 127.0.0.1 (revisa OLLAMA_HOST en el LaunchDaemon)"
    problems=$((problems + 1))
  fi
  return "$problems"
}

doctor() {
  local problems=0 n
  [ "$(uname -s)" = "Darwin" ] || { err "este script es para macOS"; return 1; }
  [ "$(uname -m)" = "arm64" ] || { err "se espera Apple Silicon (arm64)"; problems=$((problems + 1)); }
  if fdesetup status 2>/dev/null | grep -q "FileVault is On"; then
    log "FileVault activo (OK)"
  else
    err "FileVault NO está activo: el disco guarda datos del negocio y respaldos"
    problems=$((problems + 1))
  fi
  [ -f .env ] || { err ".env no existe (cp .env.example .env y complétalo)"; problems=$((problems + 1)); }
  if [ -f .env ]; then
    perms="$(stat -f '%Lp' .env)"
    [ "$perms" = "600" ] || { err ".env tiene permisos $perms; deben ser 600 (chmod 600 .env)"; problems=$((problems + 1)); }
    grep -q '^MHIA_ENV=production' .env || log "AVISO: MHIA_ENV no es production en .env"
  fi
  git check-ignore -q .env || { err ".env NO está ignorado por git"; problems=$((problems + 1)); }
  if ! "${COMPOSE[@]}" config --quiet; then
    err "docker compose config falló"
    problems=$((problems + 1))
  else
    log "docker compose config (OK)"
    # Puertos publicados: todos en 127.0.0.1.
    bad="$("${COMPOSE[@]}" config --format json | jq -r '.services[] | (.ports // [])[] | select(.host_ip != "127.0.0.1") | .published')"
    if [ -n "$bad" ]; then
      err "puertos publicados fuera de 127.0.0.1: $bad"
      problems=$((problems + 1))
    else
      log "Puertos publicados solo en 127.0.0.1 (OK)"
    fi
  fi
  check_ollama || problems=$((problems + $?))
  if [ -n "$("${COMPOSE[@]}" ps --status running -q mhia-api 2>/dev/null)" ]; then
    if "${COMPOSE[@]}" exec -T mhia-api python -c "import urllib.request;urllib.request.urlopen('http://host.docker.internal:11434/api/version',timeout=5)"; then
      log "mhia-api -> Ollama del host (OK)"
    else
      err "mhia-api no alcanza al Ollama del host. Con Colima, agrega MHIA_HOST_GATEWAY=192.168.5.2 a .env y ejecuta: scripts/mac/mhia.sh up"
      problems=$((problems + 1))
    fi
  fi
  n="$(df -P "$REPO" | awk 'NR==2{gsub("%","",$5); print $5}')"
  if [ "$n" -ge 85 ]; then
    err "disco al ${n}%"
    problems=$((problems + 1))
  fi
  command -v tailscale >/dev/null 2>&1 || log "AVISO: Tailscale no está instalado (acceso remoto, F0)"
  if [ "$problems" -gt 0 ]; then
    err "doctor encontró $problems problema(s)"
    return 1
  fi
  log "doctor: todo en orden"
}

case "$TASK" in
  doctor) doctor ;;
  up)
    if ! check_ollama; then
      err "Ollama nativo no está listo"
      exit 1
    fi
    mkdir -p backups data credentials
    "${COMPOSE[@]}" up -d --build
    ;;
  down) "${COMPOSE[@]}" down ;;
  ps) "${COMPOSE[@]}" ps ;;
  logs) "${COMPOSE[@]}" logs -f mhia-api ;;
  pull-models)
    expected_models | while read -r tag _id; do
      ollama pull "$tag"
    done
    ;;
  backup)
    log "respaldo: inicio"
    "${COMPOSE[@]}" exec -T mhia-api python -m pipeline_ai.ops.backup create
    "${COMPOSE[@]}" exec -T mhia-api python -m pipeline_ai.ops.backup verify
    "${COMPOSE[@]}" exec -T mhia-api python -m pipeline_ai.ops.backup prune --keep 14
    log "respaldo: fin"
    ;;
  backup-verify) "${COMPOSE[@]}" exec -T mhia-api python -m pipeline_ai.ops.backup verify "$@" ;;
  restore-drill) "${COMPOSE[@]}" exec -T mhia-api python -m pipeline_ai.ops.backup drill "$@" ;;
  update)
    # Producción sigue a main y solo avanza (nunca reescribe historia local).
    git fetch origin main
    git merge --ff-only origin/main
    /bin/bash "$0" up
    ;;
  *) usage ;;
esac
