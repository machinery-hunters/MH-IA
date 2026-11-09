"""
Roles y permisos de MH IA (RBAC).

La API es la ÚNICA fuente de verdad. La GUI pide sus permisos a
GET /api/v1/me y nunca los deduce por su cuenta: ocultar un botón en Next.js
es UX, no seguridad. Cada endpoint exige un permiso con
`api.deps.require_permission`.

El rol dice qué puede HACER una persona; qué puede VER lo deciden sus membresías por área
(`security.areas`, plan maestro §2.5). Ningún rol da acceso a datos por sí mismo:
`control_total` administra la infraestructura y, sin membresías, no lee ningún documento.

Roles:
  consulta       consulta en sus áreas
  lider          consulta en sus áreas (y, más adelante, ve el estado de sus fuentes)
  direccion      consulta en sus áreas (por defecto todas, ver security.presets)
  configuracion  además administra conectores (sin ver valores de secretos)
  control_total  todo lo de configuración más la infraestructura (rol más restringido;
                 exige segundo factor, ver SECURITY.md)
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area


class Role(str, Enum):  # noqa: UP042 - compatible con 3.10 (validación local) y 3.12
    CONSULTA = "consulta"
    LIDER = "lider"
    DIRECCION = "direccion"
    CONFIGURACION = "configuracion"
    CONTROL_TOTAL = "control_total"


class Permission(str, Enum):  # noqa: UP042
    QUERY = "query"
    CONFIGURE_CONNECTORS = "configure_connectors"
    ADMIN_SYSTEM = "admin_system"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.CONSULTA: frozenset({Permission.QUERY}),
    Role.LIDER: frozenset({Permission.QUERY}),
    Role.DIRECCION: frozenset({Permission.QUERY}),
    Role.CONFIGURACION: frozenset({Permission.QUERY, Permission.CONFIGURE_CONNECTORS}),
    Role.CONTROL_TOTAL: frozenset(Permission),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: Role
    username: str = ""
    # Qué puede ver, por área (vacío = nada). Sale SIEMPRE de la base, nunca del cliente.
    memberships: Mapping[Area, Sensitivity] = field(default_factory=dict)

    @property
    def permissions(self) -> frozenset[Permission]:
        return permissions_for(self.role)


def permissions_for(role: Role | str | None) -> frozenset[Permission]:
    """Rol desconocido o ausente -> sin permisos (deniega por defecto)."""
    try:
        return ROLE_PERMISSIONS[Role(role)]  # type: ignore[arg-type]
    except (ValueError, KeyError):
        return frozenset()


def has_permission(role: Role | str | None, permission: Permission) -> bool:
    return permission in permissions_for(role)


def principal_view(principal: Principal) -> dict[str, object]:
    """Lo que la GUI recibe de /login y /me: permisos y áreas visibles (sin niveles de otras
    personas ni nada que el usuario no tenga ya)."""
    return {
        "user_id": principal.user_id,
        "username": principal.username,
        "role": principal.role.value,
        "permissions": sorted(p.value for p in principal.permissions),
        "areas": {
            area.value: level.name
            for area, level in sorted(principal.memberships.items(), key=lambda kv: kv[0].value)
        },
    }
