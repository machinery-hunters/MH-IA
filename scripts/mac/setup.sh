#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────
# Preparación de la Mac mini M5 Pro como servidor de MH IA (idempotente).
#
#   sudo bash scripts/mac/setup.sh            # preparar / re-aplicar
#   sudo bash scripts/mac/setup.sh --check    # solo mostrar qué haría
#
# Qué hace (cada paso se salta si ya está hecho):
#   1. Verifica macOS en Apple Silicon y FileVault activo (se detiene si no).
#   2. Instala con Homebrew (en tu cuenta de administrador): colima, docker,
#      docker-compose, ollama, jq, git.
#   3. Crea el usuario de servicio oculto "mhia" (sin privilegios de admin, sin
#      contraseña conocida, no puede desbloquear FileVault).
#   4. Genera una llave SSH de solo lectura para clonar el repo (deploy key).
#   5. Instala los LaunchDaemons: Ollama nativo (127.0.0.1), Colima, respaldo
#      diario y chequeo de salud cada 5 minutos.
#   6. Ajusta energía: sin suspensión y encendido automático tras un corte.
#   7. Instala el atajo /usr/local/bin/mhia (scripts/mac/mhia.sh).
#
# No toca .env ni credentials/: se copian a mano por un canal seguro.
# Compatible con el bash 3.2 de macOS. Pasos completos: scripts/mac/README.md
# ─────────────────────────────────────────────────────────────────────────
set -euo pipefail

MHIA_USER="${MHIA_USER:-mhia}"
MHIA_HOME="/Users/$MHIA_USER"
MHIA_REPO="${MHIA_REPO:-$MHIA_HOME/mh-ia}"
BREW_PREFIX="/opt/homebrew"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAUNCHD_SRC="$SCRIPT_DIR/launchd"
LAUNCHD_DST="/Library/LaunchDaemons"
SERVICES="ollama colima backup health"
CHECK_ONLY=0
[ "${1:-}" = "--check" ] && CHECK_ONLY=1

step() { printf '\n==> %s\n' "$*"; }
ok() { printf '    OK: %s\n' "$*"; }
todo() { printf '    PENDIENTE: %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
run() {
  if [ "$CHECK_ONLY" -eq 1 ]; then
    printf '    (haría) %s\n' "$*"
  else
    "$@"
  fi
}

# ── 1. Plataforma ────────────────────────────────────────────────────────
step "Plataforma"
[ "$(uname -s)" = "Darwin" ] || die "este script es solo para macOS"
[ "$(uname -m)" = "arm64" ] || die "se espera Apple Silicon (arm64)"
[ "$(id -u)" -eq 0 ] || die "ejecútalo con sudo: sudo bash scripts/mac/setup.sh"
ADMIN_USER="${SUDO_USER:-}"
[ -n "$ADMIN_USER" ] && [ "$ADMIN_USER" != "root" ] || die "ejecútalo con sudo desde tu cuenta de administrador"
ok "macOS $(sw_vers -productVersion) arm64, administrador: $ADMIN_USER"
if fdesetup status | grep -q "FileVault is On"; then
  ok "FileVault activo"
else
  die "FileVault NO está activo. Actívalo (Ajustes > Privacidad y seguridad > FileVault) y vuelve a correr esto."
fi

# ── 2. Homebrew y paquetes ───────────────────────────────────────────────
step "Homebrew y paquetes"
[ -x "$BREW_PREFIX/bin/brew" ] || die "instala Homebrew primero (https://brew.sh) desde tu cuenta, sin sudo"
for pkg in colima docker docker-compose ollama jq git; do
  if sudo -u "$ADMIN_USER" "$BREW_PREFIX/bin/brew" list --formula "$pkg" >/dev/null 2>&1; then
    ok "$pkg instalado"
  else
    run sudo -u "$ADMIN_USER" "$BREW_PREFIX/bin/brew" install "$pkg"
  fi
done
# Si alguna vez se inició Ollama con brew services en la cuenta del administrador,
# se detiene: el servicio oficial es el LaunchDaemon (un solo Ollama en 11434).
if sudo -u "$ADMIN_USER" "$BREW_PREFIX/bin/brew" services list 2>/dev/null | grep -qE '^ollama +started'; then
  run sudo -u "$ADMIN_USER" "$BREW_PREFIX/bin/brew" services stop ollama
fi

# ── 3. Usuario de servicio ───────────────────────────────────────────────
step "Usuario de servicio $MHIA_USER"
if id "$MHIA_USER" >/dev/null 2>&1; then
  ok "el usuario existe"
else
  # Contraseña aleatoria que nadie conoce: nadie inicia sesión con esta cuenta.
  random_pw="$(openssl rand -base64 32)"
  run sysadminctl -addUser "$MHIA_USER" -fullName "MH IA servicio" -password "$random_pw" -home "$MHIA_HOME"
  unset random_pw
  run createhomedir -c -u "$MHIA_USER"
fi
if [ "$CHECK_ONLY" -eq 0 ]; then
  dscl . -create "/Users/$MHIA_USER" IsHidden 1
  # Sin admin: si alguien comprometiera el servicio, no obtiene sudo.
  if dseditgroup -o checkmember -m "$MHIA_USER" admin >/dev/null 2>&1; then
    dseditgroup -o edit -d "$MHIA_USER" -t user admin
  fi
  for d in "$MHIA_HOME/Library/Logs/mhia" "$MHIA_HOME/.ollama" "$MHIA_HOME/.docker" "$MHIA_HOME/.ssh"; do
    install -d -o "$MHIA_USER" -g staff -m 700 "$d"
  done
  # docker compose como plugin del CLI de docker (instalado por brew).
  cat >"$MHIA_HOME/.docker/config.json" <<EOF
{ "cliPluginsExtraDirs": ["$BREW_PREFIX/lib/docker/cli-plugins"] }
EOF
  chown "$MHIA_USER":staff "$MHIA_HOME/.docker/config.json"
  chmod 600 "$MHIA_HOME/.docker/config.json"
  chmod 700 "$MHIA_HOME"
  ok "usuario oculto, sin admin, carpetas listas"
fi

# ── 4. Llave de despliegue (solo lectura) ────────────────────────────────
step "Llave para clonar el repo"
KEY="$MHIA_HOME/.ssh/id_ed25519_mhia_deploy"
if [ -f "$KEY" ]; then
  ok "la llave existe"
else
  run sudo -u "$MHIA_USER" -H ssh-keygen -q -t ed25519 -N "" -C "mhia-deploy@$(hostname -s)" -f "$KEY"
fi
if [ "$CHECK_ONLY" -eq 0 ]; then
  cat >"$MHIA_HOME/.ssh/config" <<EOF
Host github.com
  IdentityFile $KEY
  IdentitiesOnly yes
EOF
  chown "$MHIA_USER":staff "$MHIA_HOME/.ssh/config"
  chmod 600 "$MHIA_HOME/.ssh/config"
fi
if [ -d "$MHIA_REPO/.git" ]; then
  ok "repo clonado en $MHIA_REPO"
else
  todo "agrega esta llave como Deploy key de SOLO LECTURA en GitHub (repo > Settings > Deploy keys):"
  [ -f "$KEY.pub" ] && sed 's/^/        /' "$KEY.pub"
  todo "después: sudo -u $MHIA_USER -H git clone -b main git@github.com:machinery-hunters/MH-IA.git $MHIA_REPO"
fi

# ── 5. LaunchDaemons ─────────────────────────────────────────────────────
step "Servicios (launchd)"
for svc in $SERVICES; do
  label="com.machineryhunters.mhia.$svc"
  src="$LAUNCHD_SRC/$label.plist"
  dst="$LAUNCHD_DST/$label.plist"
  [ -f "$src" ] || die "falta $src"
  tmp="$(mktemp)"
  sed -e "s#__MHIA_USER__#$MHIA_USER#g" \
    -e "s#__MHIA_HOME__#$MHIA_HOME#g" \
    -e "s#__MHIA_REPO__#$MHIA_REPO#g" \
    -e "s#__BREW_PREFIX__#$BREW_PREFIX#g" "$src" >"$tmp"
  plutil -lint "$tmp" >/dev/null || die "plist inválido: $label"
  if [ -f "$dst" ] && cmp -s "$tmp" "$dst"; then
    ok "$label sin cambios"
    rm -f "$tmp"
    continue
  fi
  # Los servicios que usan el repo esperan a que exista.
  case "$svc" in
    backup | health)
      if [ ! -d "$MHIA_REPO/.git" ]; then
        todo "$label se instalará cuando el repo esté clonado"
        rm -f "$tmp"
        continue
      fi
      ;;
  esac
  if [ "$CHECK_ONLY" -eq 1 ]; then
    printf '    (haría) instalar %s\n' "$dst"
    rm -f "$tmp"
    continue
  fi
  launchctl bootout "system/$label" >/dev/null 2>&1 || true
  install -o root -g wheel -m 644 "$tmp" "$dst"
  rm -f "$tmp"
  launchctl bootstrap system "$dst"
  ok "$label instalado y cargado"
done

# ── 6. Energía ───────────────────────────────────────────────────────────
step "Energía"
run pmset -a sleep 0 disksleep 0 displaysleep 10 autorestart 1 womp 1 powernap 0
ok "sin suspensión; enciende sola tras un corte (conéctala al UPS)"

# ── 7. Atajo de operación ────────────────────────────────────────────────
step "Atajo /usr/local/bin/mhia"
if [ "$CHECK_ONLY" -eq 0 ]; then
  install -d -m 755 /usr/local/bin
  cat >/usr/local/bin/mhia <<EOF
#!/bin/bash
# Generado por scripts/mac/setup.sh: opera MH IA como el usuario de servicio.
exec sudo -u $MHIA_USER -H /bin/bash $MHIA_REPO/scripts/mac/mhia.sh "\$@"
EOF
  chmod 755 /usr/local/bin/mhia
  ok "usa: mhia doctor | mhia up | mhia backup ..."
fi

step "Listo"
echo "    Siguiente: copia .env y credentials/ (chmod 600 .env), luego: mhia pull-models && mhia up && mhia doctor"
