"""
TOTP (RFC 6238) sobre HMAC-SHA1, compatible con Google Authenticator, Authy, 1Password...

Sin dependencias externas. Defensas:
- ventana de ±1 paso (30 s) para tolerar desfase del reloj del teléfono;
- anti-repetición: un código ya usado (o uno de un paso anterior al último aceptado) se rechaza;
- comparación en tiempo constante.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1


def generate_secret() -> str:
    """Secreto de 160 bits en base32 (sin relleno), el formato que esperan las apps."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret_b32: str) -> bytes:
    padded = secret_b32.strip().replace(" ", "").upper()
    padded += "=" * (-len(padded) % 8)
    return base64.b32decode(padded)


def hotp(secret_b32: str, counter: int, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret_b32), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % 10**digits).zfill(digits)


def step_for(timestamp: float, step: int = STEP_SECONDS) -> int:
    return int(timestamp // step)


def totp(secret_b32: str, timestamp: float, digits: int = DIGITS) -> str:
    return hotp(secret_b32, step_for(timestamp), digits)


def verify(
    secret_b32: str,
    code: str,
    *,
    timestamp: float,
    last_step: int | None = None,
    window: int = WINDOW,
) -> int | None:
    """Devuelve el paso (counter) que coincidió o None.

    Rechaza códigos mal formados y cualquier paso <= `last_step` (repetición)."""
    candidate = code.strip()
    if len(candidate) != DIGITS or not candidate.isascii() or not candidate.isdigit():
        return None
    current = step_for(timestamp)
    matched: int | None = None
    # Se recorren TODOS los pasos de la ventana sin cortar al primer acierto: tiempo constante.
    for step in range(current - window, current + window + 1):
        if hmac.compare_digest(hotp(secret_b32, step), candidate) and matched is None:
            matched = step
    if matched is None:
        return None
    if last_step is not None and matched <= last_step:
        return None
    return matched


def provisioning_uri(secret_b32: str, account: str, issuer: str = "MH IA") -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return (
        f"otpauth://totp/{label}?secret={secret_b32}&issuer={quote(issuer, safe='')}"
        f"&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"
    )
