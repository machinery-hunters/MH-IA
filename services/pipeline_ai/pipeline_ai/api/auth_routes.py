"""
Endpoints de sesión: POST /api/v1/auth/login y POST /api/v1/auth/logout.

No existe registro público: los usuarios se crean desde el servidor con
`python -m pipeline_ai.security.manage` (ver SECURITY.md).
"""

from __future__ import annotations

import logging
import sqlite3

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from pipeline_ai.api.deps import session_cookie_name
from pipeline_ai.config import settings
from pipeline_ai.security import auth
from pipeline_ai.security.roles import Principal, Role

log = logging.getLogger("mhia.auth")

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_INVALID = "Credenciales inválidas."


class LoginRequest(BaseModel):
    # Validación server-side: tamaños acotados, el código TOTP son exactamente 6 dígitos.
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


def _set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=session_cookie_name(),
        value=token,
        max_age=settings.session_absolute_s,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )


@router.post("/login")
def login(body: LoginRequest, response: Response) -> object:
    try:
        result = auth.authenticate(body.username, body.password, body.totp_code)
    except (sqlite3.Error, auth.AuthConfigError) as exc:
        log.error("Login no disponible: %s", type(exc).__name__)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de autenticación no disponible."
        ) from None

    if result.status == "ok" and result.token and result.user:
        _set_session_cookie(response, result.token)
        principal = Principal(
            user_id=result.user.user_id,
            role=Role(result.user.role),
            username=result.user.username,
        )
        return {
            "user_id": principal.user_id,
            "username": principal.username,
            "role": principal.role.value,
            "permissions": sorted(p.value for p in principal.permissions),
        }
    if result.status == "locked":
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Demasiados intentos. Intenta más tarde.",
            headers={"Retry-After": str(result.retry_after)},
        )
    if result.status == "mfa_required":
        # Solo se llega aquí con la contraseña correcta: la GUI pide el código y reenvía.
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "Se requiere el código de verificación.", "mfa_required": True},
        )
    if result.status == "mfa_not_enrolled":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Este usuario requiere segundo factor y aún no lo tiene configurado. "
            "Contacta al administrador.",
        )
    if result.status == "unavailable":
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Servicio de autenticación no disponible."
        )
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, _INVALID)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response) -> None:
    token = request.cookies.get(session_cookie_name())
    try:
        auth.logout(token)
    except (sqlite3.Error, auth.AuthConfigError) as exc:
        # Aun si la base falla, se borra la cookie del navegador.
        log.error("Logout con error: %s", type(exc).__name__)
    response.delete_cookie(
        key=session_cookie_name(),
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="strict",
    )
