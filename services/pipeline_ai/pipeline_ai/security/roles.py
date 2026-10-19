"""
Roles y permisos de MH IA (RBAC).

La API es la ÚNICA fuente de verdad. La GUI pide sus permisos a
GET /api/v1/me y nunca los deduce por su cuenta: ocultar un botón en Next.js
es UX, no seguridad. Cada endpoint exige un permiso con
`api.deps.require_permission`.

Roles iniciales (nombres y reparto por confirmar con Dirección):
  consulta       solo consulta fuentes no sensibles
  lider          además puede consultar fuentes confidenciales
  configuracion  además puede ver/configurar conectores
  control_total  todo, incluida la infraestructura (rol más restringido;
                 exige segundo factor, ver SECURITY.md)
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pipeline_ai.privacy.sensitivity import Sensitivity


class Role(str, Enum):  # noqa: UP042 - compatible con 3.10 (validación local) y 3.12
    CONSULTA = "consulta"
    LIDER = "lider"
    CONFIGURACION = "configuracion"
    CONTROL_TOTAL = "control_total"


class Permission(str, Enum):  # noqa: UP042
    QUERY = "query"
    QUERY_CONFIDENTIAL = "query_confidential"
    CONFIGURE_CONNECTORS = "configure_connectors"
    ADMIN_SYSTEM = "admin_system"


ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.CONSULTA: frozenset({Permission.QUERY}),
    Role.LIDER: frozenset({Permission.QUERY, Permission.QUERY_CONFIDENTIAL}),
    Role.CONFIGURACION: frozenset(
        {Permission.QUERY, Permission.QUERY_CONFIDENTIAL, Permission.CONFIGURE_CONNECTORS}
    ),
    Role.CONTROL_TOTAL: frozenset(Permission),
}


@dataclass(frozen=True)
class Principal:
    user_id: str
    role: Role

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


def can_view(role: Role | str | None, level: Sensitivity) -> bool:
    """¿Puede este rol ver datos con esta sensibilidad?

    PUBLIC/INTERNAL -> QUERY; CONFIDENTIAL -> QUERY_CONFIDENTIAL;
    RESTRICTED -> solo ADMIN_SYSTEM.
    """
    if level >= Sensitivity.RESTRICTED:
        return has_permission(role, Permission.ADMIN_SYSTEM)
    if level >= Sensitivity.CONFIDENTIAL:
        return has_permission(role, Permission.QUERY_CONFIDENTIAL)
    return has_permission(role, Permission.QUERY)
