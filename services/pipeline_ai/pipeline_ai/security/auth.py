"""
Autenticación: login (contraseña + TOTP), sesiones y cierre de sesión.

Principios:
- Respuesta de fallo UNIFORME: usuario inexistente, desactivado, contraseña o código erróneos
  devuelven lo mismo ("invalid") y tardan parecido (se verifica contra un hash descartable).
- Fuerza bruta: tras `login_max_failures` fallos seguidos el usuario (exista o no) se bloquea,
  con espera creciente. Mientras está bloqueado NO se procesa la contraseña.
- `control_total` exige segundo factor SIEMPRE; si no lo tiene enrolado no puede entrar.
  Cualquier usuario con TOTP activado también lo exige.
- Un código TOTP no se puede reutilizar (anti-repetición, actualización atómica).
- La cookie lleva un token opaco aleatorio (256 bits); en la base solo se guarda su HMAC.
- Los eventos se registran en el logger `mhia.auth` sin contraseñas, códigos ni tokens.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import unicodedata
from dataclasses import dataclass
from typing import Literal

from pipeline_ai.config import settings
from pipeline_ai.security import clock, passwords, secretbox, totp
from pipeline_ai.security.auth_store import SessionInfo, User, get_store
from pipeline_ai.security.roles import Role

log = logging.getLogger("mhia.auth")

_USERNAME_RE = re.compile(r"^[a-z0-9._@-]{3,64}$")
_MAX_TOKEN_CHARS = 128

LoginStatus = Literal["ok", "invalid", "locked", "mfa_required", "mfa_not_enrolled", "unavailable"]


class AuthConfigError(RuntimeError):
    """Falta configuración necesaria para autenticar (p. ej. API_SESSION_SECRET)."""


@dataclass(frozen=True)
class LoginResult:
    status: LoginStatus
    token: str | None = None
    user: SessionInfo | None = None
    retry_after: int = 0


def _audit(event: str, **fields: object) -> None:
    log.info(json.dumps({"event": event, **fields}, sort_keys=True, default=str))


def normalize_username(raw: str) -> str | None:
    """Minúsculas, NFKC y solo [a-z0-9._@-] de 3 a 64 caracteres; None si no cumple."""
    candidate = unicodedata.normalize("NFKC", raw).strip().lower()
    return candidate if _USERNAME_RE.fullmatch(candidate) else None


def _short_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def throttle_key(raw_username: str) -> str:
    # Mismo trato para nombres válidos, inválidos e inexistentes.
    normalized = (
        normalize_username(raw_username)
        or unicodedata.normalize("NFKC", raw_username).strip().lower()[:64]
    )
    return "login:" + _short_hash(normalized)


def hash_token(token: str) -> str:
    if not settings.api_session_secret:
        raise AuthConfigError("Falta API_SESSION_SECRET: no se pueden gestionar sesiones.")
    return hmac.new(
        settings.api_session_secret.encode(), token.encode(), hashlib.sha256
    ).hexdigest()


def requires_mfa(user: User) -> bool:
    return user.role == Role.CONTROL_TOTAL.value or user.totp_enabled


def _fail(key: str, *, reason: str, raw_username: str) -> LoginResult:
    get_store().register_failure(
        key,
        now=clock.now(),
        max_failures=settings.login_max_failures,
        lock_s=settings.login_lock_s,
    )
    # Hash del nombre y no el nombre: a veces se teclea la contraseña en ese campo.
    _audit("login_failed", reason=reason, username_hash=_short_hash(raw_username.lower())[:12])
    return LoginResult("invalid")


def authenticate(username: str, password: str, totp_code: str | None = None) -> LoginResult:
    store = get_store()
    now = clock.now()
    key = throttle_key(username)

    remaining = store.locked_for(key, now=now)
    if remaining > 0:
        _audit("login_blocked", username_hash=_short_hash(username.lower())[:12])
        return LoginResult("locked", retry_after=int(remaining) + 1)

    normalized = normalize_username(username)
    user = store.get_user_by_username(normalized) if normalized else None
    if user is None or not user.active:
        passwords.verify_dummy(password)
        return _fail(key, reason="unknown_or_inactive", raw_username=username)

    if not passwords.verify_password(user.password_hash, password):
        return _fail(key, reason="bad_password", raw_username=username)

    # La contraseña es correcta. Segundo factor:
    mfa_ok = False
    if requires_mfa(user):
        if not user.totp_enabled or not user.totp_secret_enc:
            _audit("login_mfa_not_enrolled", user_id=user.id)
            return LoginResult("mfa_not_enrolled")
        if totp_code is None:
            return LoginResult("mfa_required")
        try:
            secret = secretbox.decrypt(user.totp_secret_enc)
        except secretbox.SecretBoxError:
            log.error("No se pudo descifrar el secreto TOTP de un usuario (revisa API_TOTP_KEY).")
            return LoginResult("unavailable")
        step = totp.verify(secret, totp_code, timestamp=now, last_step=user.totp_last_step)
        if step is None or not store.consume_totp_step(user.id, step):
            return _fail(key, reason="bad_totp", raw_username=username)
        mfa_ok = True

    store.clear_failures(key)
    if passwords.needs_rehash(user.password_hash):
        store.set_password_hash(user.id, passwords.hash_password(password), now=now)

    token = secrets.token_urlsafe(32)
    store.create_session(
        hash_token(token),
        user.id,
        mfa=mfa_ok,
        now=now,
        absolute_s=settings.session_absolute_s,
    )
    _audit("login_ok", user_id=user.id, role=user.role, mfa=mfa_ok)
    info = SessionInfo(user_id=user.id, username=user.username, role=user.role, mfa=mfa_ok)
    return LoginResult("ok", token=token, user=info)


def resolve_session(token: str | None) -> SessionInfo | None:
    """Sesión vigente para el token de la cookie, o None. El rol sale de la base cada vez."""
    if not token or len(token) > _MAX_TOKEN_CHARS:
        return None
    info = get_store().lookup_session(
        hash_token(token), now=clock.now(), idle_s=settings.session_idle_s
    )
    if info is None:
        return None
    # control_total sin segundo factor verificado en ESTA sesión nunca opera.
    if info.role == Role.CONTROL_TOTAL.value and not info.mfa:
        return None
    return info


def logout(token: str | None) -> None:
    if token and len(token) <= _MAX_TOKEN_CHARS:
        get_store().delete_session(hash_token(token))
        _audit("logout")
