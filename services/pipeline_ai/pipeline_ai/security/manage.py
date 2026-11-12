"""
Administración de usuarios desde el servidor (no hay registro público).

    python -m pipeline_ai.security.manage gen-key
    python -m pipeline_ai.security.manage create-user --username ana --role lider \
        --areas "general:INTERNAL, comercial:CONFIDENTIAL"
    python -m pipeline_ai.security.manage set-areas --username ana --areas "comercial:CONFIDENTIAL"
    python -m pipeline_ai.security.manage import-users --file /app/data/usuarios.xlsx [--apply]
    python -m pipeline_ai.security.manage enroll-totp --username ana
    python -m pipeline_ai.security.manage allow-totp-enroll --username ana   (o --pendientes)
    python -m pipeline_ai.security.manage reset-totp --username ana         (teléfono perdido)
    python -m pipeline_ai.security.manage list-users
    python -m pipeline_ai.security.manage audit-log [--limit 50] [--categoria admin|acceso|consulta]
    python -m pipeline_ai.security.manage audit-verify [--respaldo /app/backups]

Áreas: direccion, comercial, sourcing, logistica, marketing, proyectos, finanzas, rh, taller,
inventario, general. Niveles: PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED (plan maestro §2.5).

Las contraseñas SIEMPRE se piden por getpass (nunca por argumentos: quedarían en el historial
del shell). El secreto TOTP se muestra una sola vez al enrolar y se guarda cifrado.
Cualquier cambio de rol/áreas/contraseña/estado revoca las sesiones abiertas del usuario.

Segundo factor: lo exige control_total y quien tenga un área con nivel MFA_REQUIRED_LEVEL o
más. Si la persona aún no lo tiene, create-user, reset-password, set-areas, set-role,
enable-user e import-users le abren una ventana (TOTP_ENROLL_WINDOW_S, 72 h por defecto) para
activarlo ella misma desde la GUI con su contraseña; allow-totp-enroll la abre a mano.

Cada cambio queda en el registro de auditoría (`audit-log`, categoría admin): quién (la
variable MHIA_ADMIN, o el usuario del sistema), qué, a quién y el antes/después. Nunca
contraseñas ni secretos. El mismo registro guarda los accesos y las consultas; `audit-verify`
recalcula su hash encadenado y falla si alguna fila fue alterada o borrada; con `--respaldo`
además comprueba que la última fila que guardó ese respaldo sigue igual en la base (detecta
que alguien rehízo la cadena o recortó el final). Con Docker:
`docker compose run --rm -e MHIA_ADMIN=fernando mhia-api python -m ...`.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from pipeline_ai.config import settings
from pipeline_ai.security import auth, clock, passwords, secretbox, totp, user_import
from pipeline_ai.security.areas import (
    AREA_MIN_SENSITIVITY,
    InvalidMembership,
    format_memberships,
    parse_memberships,
)
from pipeline_ai.security.auth_store import (
    AUDIT_CATEGORIES,
    DB_ERRORS,
    User,
    UserExistsError,
    get_store,
)
from pipeline_ai.security.roles import Role

Out = Callable[[str], None]


class CliError(Exception):
    """Error de uso: se muestra el mensaje y se sale con código 1."""


def _actor() -> str:
    name = os.environ.get("MHIA_ADMIN", "").strip()
    if not name:
        try:
            name = getpass.getuser()
        except (KeyError, OSError):  # contenedor sin entrada en /etc/passwd
            name = "desconocido"
    return f"cli:{name[:64]}"


def _audit(action: str, target: str | None, **detail: object) -> None:
    get_store().record_admin_event(_actor(), action, target, detail, now=clock.now())


def _find_user(username: str) -> User:
    normalized = auth.normalize_username(username)
    user = get_store().get_user_by_username(normalized) if normalized else None
    if user is None:
        raise CliError(f"No existe el usuario '{username}'.")
    return user


def _ask_new_password(username: str, getpass_fn: Callable[[str], str]) -> str:
    first = getpass_fn("Contraseña nueva: ")
    if first != getpass_fn("Repite la contraseña: "):
        raise CliError("Las contraseñas no coinciden.")
    try:
        passwords.validate_policy(first, username=username)
    except passwords.PasswordPolicyError as exc:
        raise CliError(str(exc)) from None
    return first


def _open_enrollment_if_needed(user: User, out: Out) -> bool:
    """Si la persona necesita segundo factor y no lo tiene, le abre la ventana para activarlo
    desde la GUI. Devuelve True si la abrió."""
    current = get_store().get_user(user.id) or user
    if current.totp_enabled or not current.active or not auth.requires_mfa(current):
        return False
    _open_enrollment(current, out)
    return True


def _open_enrollment(user: User, out: Out) -> None:
    window = settings.totp_enroll_window_s
    until = clock.now() + window
    get_store().set_totp_enroll_window(user.id, until)
    _audit("abrir_activacion_totp", user.username, horas=round(window / 3600, 1))
    out(
        f"'{user.username}' debe activar su segundo factor: al entrar en la GUI con su "
        f"contraseña verá un código QR. Tiene {_hours(window)} para hacerlo."
    )


def _hours(seconds: int) -> str:
    hours = seconds / 3600
    return f"{hours:g} horas" if hours != 1 else "1 hora"


def _enroll_totp(
    user: User, input_fn: Callable[[str], str], out: Out, *, label: str | None = None
) -> bool:
    """Genera un secreto, lo muestra y lo guarda (cifrado) SOLO si el usuario confirma un
    código válido: un enrolamiento a medias no deja al usuario sin su factor anterior."""
    secret = totp.generate_secret()
    # Se cifra ANTES de mostrar nada: sin API_TOTP_KEY válida falla aquí, no tras pedir el código.
    encrypted = secretbox.encrypt(secret)
    uri = totp.provisioning_uri(secret, label or user.username)
    out("")
    out("Agrega esta cuenta a tu app autenticadora (Google Authenticator, Authy, 1Password...):")
    out(f"  Clave manual : {secret}")
    out(f"  URI otpauth  : {uri}")
    out("Se muestra UNA sola vez. Limpia la pantalla de la terminal al terminar.")
    code = input_fn("Escribe el código de 6 dígitos que muestra la app: ").strip()
    step = totp.verify(secret, code, timestamp=clock.now())
    if step is None:
        out("Código incorrecto: el segundo factor NO quedó activado.")
        return False
    store = get_store()
    store.set_totp_secret(user.id, encrypted)
    store.enable_totp(user.id, step)
    store.delete_user_sessions(user.id)
    _audit("enrolar_totp", user.username)
    out("Segundo factor activado.")
    return True


def _cmd_gen_key(_: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    out(secretbox.generate_key())
    return 0


def _cmd_create_user(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    getpass_fn: Callable[[str], str] = io["getpass_fn"]  # type: ignore[assignment]
    input_fn: Callable[[str], str] = io["input_fn"]  # type: ignore[assignment]

    username = auth.normalize_username(args.username)
    if username is None:
        raise CliError("Usuario inválido: de 3 a 64 caracteres entre a-z, 0-9, punto, guion, @.")
    memberships = _parse_areas(args.areas)
    password = _ask_new_password(username, getpass_fn)
    store = get_store()
    try:
        user = store.create_user(
            username, passwords.hash_password(password), args.role, now=clock.now()
        )
    except UserExistsError:
        raise CliError(f"Ya existe el usuario '{username}'.") from None
    store.set_memberships(user.id, memberships)
    _audit("crear_usuario", username, rol=args.role, areas=format_memberships(memberships))
    out(f"Usuario '{username}' creado con rol {args.role}.")
    _report_areas(memberships, out)

    if args.role != Role.CONTROL_TOTAL.value:
        _open_enrollment_if_needed(user, out)
    else:
        out("El rol control_total exige segundo factor: se enrola ahora.")
        if not _enroll_totp(user, input_fn, out):
            out(
                f"El usuario NO podrá entrar hasta enrolar: "
                f"python -m pipeline_ai.security.manage enroll-totp --username {username}"
            )
            return 1
    return 0


def _cmd_enroll_totp(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    input_fn: Callable[[str], str] = io["input_fn"]  # type: ignore[assignment]
    return 0 if _enroll_totp(_find_user(args.username), input_fn, out) else 1


def _cmd_reset_password(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    getpass_fn: Callable[[str], str] = io["getpass_fn"]  # type: ignore[assignment]
    user = _find_user(args.username)
    password = _ask_new_password(user.username, getpass_fn)
    store = get_store()
    store.set_password_hash(user.id, passwords.hash_password(password), now=clock.now())
    store.delete_user_sessions(user.id)
    store.clear_failures(auth.throttle_key(user.username))
    _audit("cambiar_contrasena", user.username)
    out(f"Contraseña de '{user.username}' actualizada; sus sesiones fueron cerradas.")
    _open_enrollment_if_needed(user, out)
    return 0


def _cmd_set_role(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    store = get_store()
    store.set_role(user.id, args.role)
    store.delete_user_sessions(user.id)
    _audit("cambiar_rol", user.username, antes=user.role, despues=args.role)
    out(f"Rol de '{user.username}': {args.role}. Sus sesiones fueron cerradas.")
    _open_enrollment_if_needed(user, out)
    return 0


def _parse_areas(text: str) -> dict:
    try:
        return parse_memberships(text or "")
    except InvalidMembership as exc:
        raise CliError(str(exc)) from None


def _report_areas(memberships: dict, out: Out) -> None:
    out(f"Áreas: {format_memberships(memberships) or '(ninguna: no verá ningún documento)'}")
    for area, level in memberships.items():
        if level < AREA_MIN_SENSITIVITY[area]:
            out(
                f"Aviso: {area.value}:{level.name} no alcanza el mínimo del área "
                f"({AREA_MIN_SENSITIVITY[area].name}); no verá documentos de {area.value}."
            )


def _cmd_set_areas(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    memberships = _parse_areas(args.areas)
    store = get_store()
    before = format_memberships(store.get_memberships(user.id))
    store.set_memberships(user.id, memberships)
    store.delete_user_sessions(user.id)
    _audit("cambiar_areas", user.username, antes=before, despues=format_memberships(memberships))
    out(f"Áreas de '{user.username}' reemplazadas; sus sesiones fueron cerradas.")
    _report_areas(memberships, out)
    _open_enrollment_if_needed(user, out)
    return 0


def _cmd_import_users(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    store = get_store()
    try:
        plan = user_import.build_plan(Path(args.file), store)
    except user_import.ImportError_ as exc:
        raise CliError(str(exc)) from None
    out("Plan de importación (el nombre de las personas no se lee ni se guarda):")
    for line in user_import.describe(plan):
        out(line)
    if not args.apply:
        out("Nada cambió. Revisa el plan y repite con --apply para aplicarlo.")
        return 0
    counts = user_import.apply_plan(plan, store)
    for item in plan.users:
        if item.action != "sin cambios":
            _audit(
                f"importar_{'crear' if item.action == 'crear' else 'actualizar'}",
                item.username,
                rol=item.role,
                areas=format_memberships(item.memberships),
                preset=item.preset,
            )
        if item.action not in ("crear", "sin cambios"):
            # Las cuentas nuevas quedan desactivadas: su ventana se abre al habilitarlas.
            updated = store.get_user_by_username(item.username)
            if updated is not None:
                _open_enrollment_if_needed(updated, out)
    out(
        f"Aplicado: {counts['creados']} creados (desactivados), "
        f"{counts['actualizados']} actualizados, {counts['sin_cambios']} sin cambios."
    )
    if counts["creados"]:
        out("Para habilitar a cada persona nueva: reset-password --username X y enable-user.")
        out("Quien necesite segundo factor lo activará desde la GUI al entrar.")
    return 0


def _cmd_set_active(args: argparse.Namespace, *, active: bool, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    store = get_store()
    store.set_active(user.id, active)
    if not active:
        store.delete_user_sessions(user.id)
    _audit("activar" if active else "desactivar", user.username)
    out(f"Usuario '{user.username}' {'activado' if active else 'desactivado'}.")
    if active:
        _open_enrollment_if_needed(user, out)
    else:
        store.set_totp_enroll_window(user.id, None)
    return 0


def _cmd_unlock(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    get_store().clear_failures(auth.throttle_key(user.username))
    _audit("desbloquear", user.username)
    out(f"Bloqueo de '{user.username}' levantado.")
    return 0


def _cmd_list_users(_: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    users = get_store().list_users()
    if not users:
        out("(sin usuarios)")
        return 0
    store = get_store()
    now = clock.now()
    out(f"{'usuario':<36}{'rol':<16}{'activo':<8}{'totp':<12}áreas")
    for user in users:
        memberships = store.get_memberships(user.id)
        areas = format_memberships(memberships) or "-"
        if user.totp_enabled:
            factor = "sí"
        elif auth.can_self_enroll(user, now=now):
            factor = "pendiente"
        elif auth.requires_mfa(user, memberships):
            factor = "FALTA"
        else:
            factor = "no"
        out(
            f"{user.username:<36}{user.role:<16}"
            f"{'sí' if user.active else 'no':<8}{factor:<12}{areas}"
        )
    return 0


def _cmd_allow_totp_enroll(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    if args.pendientes:
        opened = sum(
            _open_enrollment_if_needed(user, out)
            for user in get_store().list_users()
            if not auth.can_self_enroll(user, now=clock.now())
        )
        out(f"Ventanas abiertas: {opened}.")
        return 0
    if not args.username:
        raise CliError("Indica --username o --pendientes.")
    user = _find_user(args.username)
    if user.totp_enabled:
        raise CliError(
            f"'{user.username}' ya tiene segundo factor. Si perdió el teléfono usa reset-totp."
        )
    if not user.active:
        raise CliError(f"'{user.username}' está desactivado: actívalo primero con enable-user.")
    _open_enrollment(user, out)
    return 0


def _cmd_reset_totp(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    store = get_store()
    store.clear_totp(user.id)
    closed = store.delete_user_sessions(user.id)
    _audit("quitar_totp", user.username, sesiones_cerradas=closed)
    out(f"Segundo factor de '{user.username}' eliminado; sus sesiones fueron cerradas.")
    reset = store.get_user(user.id) or user
    if not _open_enrollment_if_needed(reset, out):
        out("No lo necesita con su rol y áreas actuales.")
    return 0


def _cmd_revoke_sessions(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    store = get_store()
    if args.all:
        closed = store.delete_all_sessions()
        _audit("cerrar_sesiones", "*", cerradas=closed)
        out(f"Sesiones cerradas: {closed}.")
        return 0
    if not args.username:
        raise CliError("Indica --username o --all.")
    user = _find_user(args.username)
    closed = store.delete_user_sessions(user.id)
    _audit("cerrar_sesiones", user.username, cerradas=closed)
    out(f"Sesiones cerradas de '{user.username}': {closed}.")
    return 0


def _cmd_audit_log(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    events = get_store().list_events(args.limit, args.categoria)
    if not events:
        out("(sin eventos registrados)")
        return 0
    for event in events:
        when = datetime.fromtimestamp(event.at, tz=UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
        detail = ", ".join(f"{k}={v}" for k, v in sorted(event.detail.items()))
        out(
            f"{event.id:>6} {when}  {event.category:<8} {event.actor:<20} {event.action:<22} "
            f"{event.target or '-':<36} {detail}"
        )
    return 0


def _cmd_audit_verify(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    check = get_store().verify_audit_chain()
    if not check.ok:
        raise CliError(
            f"el registro de auditoría NO es íntegro: la fila {check.broken_at} no cuadra "
            f"({check.rows} filas correctas antes de esa)."
        )
    out(f"Registro íntegro: {check.rows} filas. Última: {check.head_id} {check.head_hash}")
    if args.respaldo:
        _check_against_backup(Path(args.respaldo), out)
    return 0


def _check_against_backup(path: Path, out: Out) -> None:
    from pipeline_ai.ops import backup

    try:
        target = path if (path / backup.MANIFEST_NAME).exists() else backup.latest_backup(path)
        manifest = json.loads((target / backup.MANIFEST_NAME).read_text(encoding="utf-8"))
    except (backup.BackupError, OSError, ValueError) as exc:
        raise CliError(f"no se pudo leer el respaldo: {exc}") from None
    head = (manifest.get("auth") or {}).get("audit_head")
    if not isinstance(head, dict) or not isinstance(head.get("id"), int):
        raise CliError(f"{target.name} no guarda la cabeza del registro (respaldo anterior).")
    if head["id"] == 0:
        out(f"{target.name}: el registro estaba vacío al respaldar; nada que comparar.")
        return
    current = get_store().audit_hash_at(head["id"])
    if current != head.get("hash"):
        raise CliError(
            f"la fila {head['id']} ya no es la que guardó {target.name}: el registro fue "
            "rehecho o recortado después del respaldo."
        )
    out(f"Coincide con {target.name}: la fila {head['id']} sigue igual.")


def _parser() -> argparse.ArgumentParser:
    roles = [r.value for r in Role]
    parser = argparse.ArgumentParser(prog="python -m pipeline_ai.security.manage")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("gen-key", help="genera una clave Fernet para API_TOTP_KEY")

    p = sub.add_parser("create-user", help="crea un usuario (pide la contraseña)")
    p.add_argument("--username", required=True)
    p.add_argument("--role", required=True, choices=roles)
    p.add_argument("--areas", default="", help='p.ej. "general:INTERNAL, comercial:CONFIDENTIAL"')

    p = sub.add_parser("set-areas", help="reemplaza las áreas de un usuario y cierra sus sesiones")
    p.add_argument("--username", required=True)
    p.add_argument("--areas", required=True, help='"" quita todas las áreas')

    p = sub.add_parser("import-users", help="importa usuarios desde .xlsx/.csv en el dir. de datos")
    p.add_argument("--file", required=True)
    p.add_argument("--apply", action="store_true", help="sin esto solo muestra el plan")

    for name, help_ in (
        ("enroll-totp", "(re)enrola el segundo factor de un usuario"),
        ("reset-password", "cambia la contraseña y cierra sus sesiones"),
        ("disable-user", "desactiva al usuario y cierra sus sesiones"),
        ("enable-user", "reactiva al usuario"),
        ("unlock", "levanta el bloqueo por intentos fallidos"),
    ):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--username", required=True)

    p = sub.add_parser("set-role", help="cambia el rol y cierra sus sesiones")
    p.add_argument("--username", required=True)
    p.add_argument("--role", required=True, choices=roles)

    sub.add_parser("list-users", help="lista usuarios (sin secretos)")

    p = sub.add_parser(
        "allow-totp-enroll", help="abre la ventana para activar el segundo factor desde la GUI"
    )
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--username")
    group.add_argument(
        "--pendientes", action="store_true", help="a todas las personas activas que lo necesiten"
    )

    p = sub.add_parser("reset-totp", help="quita el segundo factor (teléfono perdido)")
    p.add_argument("--username", required=True)

    p = sub.add_parser("revoke-sessions", help="cierra sesiones abiertas")
    p.add_argument("--username")
    p.add_argument("--all", action="store_true")

    p = sub.add_parser("audit-log", help="muestra los eventos recientes del registro")
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--categoria", choices=AUDIT_CATEGORIES, default=None)

    p = sub.add_parser("audit-verify", help="verifica el hash encadenado del registro")
    p.add_argument(
        "--respaldo",
        default=None,
        help="carpeta de un respaldo (o la de todos: usa el último) para comparar su cabeza",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    getpass_fn: Callable[[str], str] = getpass.getpass,
    input_fn: Callable[[str], str] = input,
    out: Out = print,
) -> int:
    args = _parser().parse_args(argv)
    io = {"getpass_fn": getpass_fn, "input_fn": input_fn, "out": out}
    handlers: dict[str, Callable[..., int]] = {
        "gen-key": _cmd_gen_key,
        "create-user": _cmd_create_user,
        "enroll-totp": _cmd_enroll_totp,
        "reset-password": _cmd_reset_password,
        "set-role": _cmd_set_role,
        "set-areas": _cmd_set_areas,
        "import-users": _cmd_import_users,
        "disable-user": lambda a, **kw: _cmd_set_active(a, active=False, **kw),
        "enable-user": lambda a, **kw: _cmd_set_active(a, active=True, **kw),
        "unlock": _cmd_unlock,
        "list-users": _cmd_list_users,
        "allow-totp-enroll": _cmd_allow_totp_enroll,
        "reset-totp": _cmd_reset_totp,
        "revoke-sessions": _cmd_revoke_sessions,
        "audit-log": _cmd_audit_log,
        "audit-verify": _cmd_audit_verify,
    }
    try:
        return handlers[args.command](args, **io)
    except (CliError, secretbox.SecretBoxError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except DB_ERRORS as exc:
        print(
            f"Error: no se pudo usar la base de usuarios ({type(exc).__name__}).", file=sys.stderr
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
