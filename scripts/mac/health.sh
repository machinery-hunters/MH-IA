#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Chequeo de salud de MH IA (lo corre launchd cada 5 minutos como "mhia").
#
# Revisa: API (/health), Ollama nativo, contenedores "healthy" y espacio en
# disco. Solo avisa cuando el estado CAMBIA (sano -> falla, falla -> sano), para
# no inundar el canal. El aviso va a MHIA_ALERT_URL si está en .env (por
# ejemplo un tema privado de ntfy, gratis). El aviso nunca incluye datos del
# negocio: solo qué componente falló.
# Compatible con el bash 3.2 de macOS.
# ─────────────────────────────────────────────────────────────────────────
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
STATE_FILE="${MHIA_HEALTH_STATE:-$HOME/.mhia-health-state}"
DISK_LIMIT="${MHIA_DISK_LIMIT:-85}"

if [ -S "$HOME/.colima/default/docker.sock" ]; then
  export DOCKER_HOST="unix://$HOME/.colima/default/docker.sock"
fi

failures=""
add_failure() { failures="${failures}${failures:+, }$1"; }

curl -fsS --max-time 5 http://127.0.0.1:8000/health >/dev/null 2>&1 || add_failure "api"
curl -fsS --max-time 5 http://127.0.0.1:11434/api/version >/dev/null 2>&1 || add_failure "ollama"
if command -v docker >/dev/null 2>&1; then
  for name in mhia-api mhia-qdrant mhia-postgres mhia-web; do
    status="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$name" 2>/dev/null || echo missing)"
    [ "$status" = "healthy" ] || add_failure "$name:$status"
  done
else
  add_failure "docker"
fi
used="$(df -P "$REPO" | awk 'NR==2{gsub("%","",$5); print $5}')"
[ "${used:-0}" -lt "$DISK_LIMIT" ] || add_failure "disco:${used}%"

if [ -z "$failures" ]; then
  current="ok"
else
  current="falla: $failures"
fi
previous="$(cat "$STATE_FILE" 2>/dev/null || echo ok)"
printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$current"

if [ "$current" != "$previous" ]; then
  printf '%s' "$current" >"$STATE_FILE"
  # Lee SOLO la línea MHIA_ALERT_URL del .env (sin ejecutarlo).
  alert_url="$(grep -E '^MHIA_ALERT_URL=' "$REPO/.env" 2>/dev/null | head -n 1 | cut -d= -f2-)"
  if [ -n "$alert_url" ]; then
    case "$alert_url" in
      https://*) curl -fsS --max-time 10 -d "MH IA ($(hostname -s)): $current" "$alert_url" >/dev/null 2>&1 || true ;;
      *) echo "MHIA_ALERT_URL debe ser https://" >&2 ;;
    esac
  fi
fi

[ "$current" = "ok" ]
