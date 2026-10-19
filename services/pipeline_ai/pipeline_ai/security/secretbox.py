"""
Cifrado en reposo de los secretos TOTP (Fernet = AES-128-CBC + HMAC-SHA256, autenticado).

La clave (API_TOTP_KEY) vive en el .env, fuera de la base de datos: quien robe solo el archivo
auth.db no obtiene los secretos de segundo factor. Si falta o es inválida se falla explícito;
nunca se guarda un secreto en claro "por comodidad".
"""

from __future__ import annotations

from cryptography.fernet import Fernet, InvalidToken

from pipeline_ai.config import settings


class SecretBoxError(RuntimeError):
    """Clave ausente/inválida o dato ilegible (el mensaje no incluye datos sensibles)."""


def _fernet() -> Fernet:
    if not settings.api_totp_key:
        raise SecretBoxError(
            "Falta API_TOTP_KEY en .env (genera una con: python -m pipeline_ai.security.manage gen-key)."
        )
    try:
        return Fernet(settings.api_totp_key.encode())
    except (ValueError, TypeError) as exc:
        raise SecretBoxError("API_TOTP_KEY no es una clave Fernet válida.") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken:
        raise SecretBoxError(
            "No se pudo descifrar el secreto TOTP (¿cambió API_TOTP_KEY?)."
        ) from None
