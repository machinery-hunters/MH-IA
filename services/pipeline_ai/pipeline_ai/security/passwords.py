"""
Contraseñas: hash argon2id y política mínima.

- argon2id con los parámetros por defecto de argon2-cffi (RFC 9106, perfil de baja memoria);
  la sal es aleatoria y viaja dentro del hash. `needs_rehash` permite subir parámetros luego.
- La política es deliberadamente simple (largo y lista de contraseñas obvias): NIST 800-63B
  recomienda largo sobre reglas de composición. Se valida SIEMPRE en el servidor.
- `verify_dummy` gasta el mismo tiempo que una verificación real, para que un usuario que no
  existe no se distinga de uno que sí por la latencia del login.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12
MAX_LENGTH = 256  # tope para que una contraseña gigante no sirva de DoS contra argon2

_HASHER = PasswordHasher()

# Contraseñas que cumplen el largo pero son de las primeras que prueba cualquiera.
_COMMON = frozenset(
    {
        "password1234",
        "contrasena123",
        "contraseña123",
        "123456789012",
        "qwertyuiop12",
        "machinery1234",
        "machineryhunters",
        "machinery hunters",
        "administrador",
        "administrator",
    }
)


class PasswordPolicyError(ValueError):
    """La contraseña no cumple la política (el mensaje es seguro de mostrar)."""


def validate_policy(password: str, *, username: str = "") -> None:
    if len(password) < MIN_LENGTH:
        raise PasswordPolicyError(f"La contraseña debe tener al menos {MIN_LENGTH} caracteres.")
    if len(password) > MAX_LENGTH:
        raise PasswordPolicyError(f"La contraseña no puede pasar de {MAX_LENGTH} caracteres.")
    if len(set(password)) < 5:
        raise PasswordPolicyError("La contraseña es demasiado repetitiva.")
    lowered = password.lower()
    if lowered in _COMMON:
        raise PasswordPolicyError("La contraseña es demasiado común.")
    if username and username.lower() in lowered:
        raise PasswordPolicyError("La contraseña no puede contener el nombre de usuario.")


def hash_password(password: str) -> str:
    return _HASHER.hash(password)


def verify_password(stored_hash: str, password: str) -> bool:
    if len(password) > MAX_LENGTH:
        return False
    try:
        return _HASHER.verify(stored_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    try:
        return _HASHER.check_needs_rehash(stored_hash)
    except InvalidHashError:
        return True


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return _HASHER.hash(secrets.token_urlsafe(24))


def verify_dummy(password: str) -> None:
    """Verificación descartable: iguala el costo de un login con usuario inexistente."""
    verify_password(_dummy_hash(), password)
