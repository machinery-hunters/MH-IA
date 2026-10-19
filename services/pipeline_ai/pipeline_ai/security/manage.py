"""
Administración de usuarios desde el servidor (no hay registro público).

    python -m pipeline_ai.security.manage gen-key
    python -m pipeline_ai.security.manage create-user --username ana --role lider
    python -m pipeline_ai.security.manage enroll-totp --username ana
    python -m pipeline_ai.security.manage list-users

Las contraseñas SIEMPRE se piden por getpass (nunca por argumentos: quedarían en el historial
del shell). El secreto TOTP se muestra una sola vez al enrolar y se guarda cifrado.
Cualquier cambio de rol/contraseña/estado revoca las sesiones abiertas del usuario.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Callable, Sequence

from pipeline_ai.security import auth, clock, passwords, secretbox, totp
from pipeline_ai.security.auth_store import User, UserExistsError, get_store
from pipeline_ai.security.roles import Role

Out = Callable[[str], None]


class CliError(Exception):
    """Error de uso: se muestra el mensaje y se sale con código 1."""


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
    password = _ask_new_password(username, getpass_fn)
    try:
        user = get_store().create_user(
            username, passwords.hash_password(password), args.role, now=clock.now()
        )
    except UserExistsError:
        raise CliError(f"Ya existe el usuario '{username}'.") from None
    out(f"Usuario '{username}' creado con rol {args.role}.")

    if args.role == Role.CONTROL_TOTAL.value:
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
    out(f"Contraseña de '{user.username}' actualizada; sus sesiones fueron cerradas.")
    return 0


def _cmd_set_role(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    store = get_store()
    store.set_role(user.id, args.role)
    store.delete_user_sessions(user.id)
    out(f"Rol de '{user.username}': {args.role}. Sus sesiones fueron cerradas.")
    if args.role == Role.CONTROL_TOTAL.value and not user.totp_enabled:
        out("Aviso: control_total exige segundo factor; enrólalo con enroll-totp antes de entrar.")
    return 0


def _cmd_set_active(args: argparse.Namespace, *, active: bool, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    store = get_store()
    store.set_active(user.id, active)
    if not active:
        store.delete_user_sessions(user.id)
    out(f"Usuario '{user.username}' {'activado' if active else 'desactivado'}.")
    return 0


def _cmd_unlock(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    user = _find_user(args.username)
    get_store().clear_failures(auth.throttle_key(user.username))
    out(f"Bloqueo de '{user.username}' levantado.")
    return 0


def _cmd_list_users(_: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    users = get_store().list_users()
    if not users:
        out("(sin usuarios)")
        return 0
    out(f"{'usuario':<24}{'rol':<16}{'activo':<8}{'totp':<6}")
    for user in users:
        out(
            f"{user.username:<24}{user.role:<16}"
            f"{'sí' if user.active else 'no':<8}{'sí' if user.totp_enabled else 'no':<6}"
        )
    return 0


def _cmd_revoke_sessions(args: argparse.Namespace, **io: object) -> int:
    out: Out = io["out"]  # type: ignore[assignment]
    store = get_store()
    if args.all:
        out(f"Sesiones cerradas: {store.delete_all_sessions()}.")
        return 0
    if not args.username:
        raise CliError("Indica --username o --all.")
    user = _find_user(args.username)
    out(f"Sesiones cerradas de '{user.username}': {store.delete_user_sessions(user.id)}.")
    return 0


def _parser() -> argparse.ArgumentParser:
    roles = [r.value for r in Role]
    parser = argparse.ArgumentParser(prog="python -m pipeline_ai.security.manage")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("gen-key", help="genera una clave Fernet para API_TOTP_KEY")

    p = sub.add_parser("create-user", help="crea un usuario (pide la contraseña)")
    p.add_argument("--username", required=True)
    p.add_argument("--role", required=True, choices=roles)

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

    p = sub.add_parser("revoke-sessions", help="cierra sesiones abiertas")
    p.add_argument("--username")
    p.add_argument("--all", action="store_true")
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
        "disable-user": lambda a, **kw: _cmd_set_active(a, active=False, **kw),
        "enable-user": lambda a, **kw: _cmd_set_active(a, active=True, **kw),
        "unlock": _cmd_unlock,
        "list-users": _cmd_list_users,
        "revoke-sessions": _cmd_revoke_sessions,
    }
    try:
        return handlers[args.command](args, **io)
    except (CliError, secretbox.SecretBoxError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
