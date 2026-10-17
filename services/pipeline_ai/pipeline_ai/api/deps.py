"""
Dependencias de autorización de la API.

`get_current_principal` es un STUB deliberado: devuelve 401 siempre hasta que
se implemente el login (sesión por cookie httpOnly firmada con
API_SESSION_SECRET, hash argon2, TOTP para control_total — ver SECURITY.md).
Denegar por defecto es el estado seguro mientras no exista autenticación real.

`require_permission` es la ÚNICA forma autorizada de proteger un endpoint:
el filtrado por rol se aplica aquí, en el servidor, nunca solo en la GUI.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, status

from pipeline_ai.security.roles import Permission, Principal


async def get_current_principal() -> Principal:
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado.",
        headers={"WWW-Authenticate": "Session"},
    )


def require_permission(permission: Permission) -> Callable[..., Principal]:
    async def _dependency(principal: Principal = Depends(get_current_principal)) -> Principal:
        if permission not in principal.permissions:
            # 403 sin revelar qué permiso faltó ni qué recurso existe.
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Sin permiso.")
        return principal

    return _dependency
