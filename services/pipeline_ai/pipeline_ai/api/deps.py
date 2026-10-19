"""
Dependencias de autorización de la API.

`get_current_principal` lee la cookie de sesión, la valida en el servidor (existencia,
caducidad absoluta e inactividad, usuario activo, segundo factor del rol control_total) y
construye el `Principal` con el rol que figura HOY en la base: si un administrador cambia el
rol o desactiva al usuario, el cambio aplica en la siguiente petición. Sin sesión válida: 401.

`require_permission` es la UNICA forma autorizada de proteger un endpoint:
el filtrado por rol se aplica aquí, en el servidor, nunca solo en la GUI.
"""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Callable

from fastapi import Depends, HTTPException, Request, status

from pipeline_ai.config import settings
from pipeline_ai.security import auth
from pipeline_ai.security.roles import Permission, Principal, Role

log = logging.getLogger("mhia.auth")


def session_cookie_name() -> str:
    # El prefijo __Host- (el navegador exige Secure, Path=/ y sin Domain) solo es posible con
    # HTTPS; en desarrollo local sobre http se usa un nombre normal.
    return "__Host-mhia_session" if settings.cookie_secure else "mhia_session"


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado.",
        headers={"WWW-Authenticate": "Session"},
    )


def get_current_principal(request: Request) -> Principal:
    # Dependencia síncrona: FastAPI la ejecuta en un hilo, así SQLite no bloquea el event loop.
    token = request.cookies.get(session_cookie_name())
    try:
        info = auth.resolve_session(token)
    except (sqlite3.Error, auth.AuthConfigError) as exc:
        # Falla cerrada: sin poder validar la sesión nadie entra.
        log.error("Autenticación no disponible: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Servicio de autenticación no disponible.",
        ) from None
    if info is None:
        raise _unauthorized()
    try:
        role = Role(info.role)
    except ValueError:
        raise _unauthorized() from None
    return Principal(user_id=info.user_id, role=role, username=info.username)


def require_permission(permission: Permission) -> Callable[..., Principal]:
    async def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if permission not in principal.permissions:
            # 403 sin revelar qué permiso faltó ni qué recurso existe.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sin permiso.")
        return principal

    return _dependency
