"""
Control de acceso a documentos por rol (RBAC sobre el contenido).

Cada fragmento indexado lleva en su metadata el `domain` de negocio (ventas, inventario,
proveedores, administracion, general), inferido de la carpeta de Drive en la ingesta. La
sensibilidad de un fragmento se DERIVA de ese dominio (`privacy.sensitivity`), así que
filtrar por rol no exige reindexar nada.

Reglas (falla cerrada):
  - Un rol solo ve los dominios cuya sensibilidad puede ver (`security.roles.can_view`).
  - Rol desconocido o ausente -> ningún dominio.
  - Un fragmento SIN `domain` (o con un dominio que no conocemos) no lo ve nadie: no se
    puede demostrar que el rol tenga derecho a él. Se corrige reindexando.

El filtro se aplica dos veces (defensa en profundidad): como condición de Qdrant (para que
el contenido prohibido ni siquiera salga de la base) y de nuevo sobre lo recuperado, por si
el filtro de la base faltara o se alterara. La API fija el rol desde la sesión; nunca lo
manda el cliente.
"""

from __future__ import annotations

from collections.abc import Mapping

from pipeline_ai.privacy.sensitivity import DOMAIN_SENSITIVITY
from pipeline_ai.security.roles import Role, can_view


def allowed_domains(role: Role | str | None) -> tuple[str, ...]:
    """Dominios que este rol puede consultar (vacío si el rol no es válido)."""
    return tuple(domain for domain, level in DOMAIN_SENSITIVITY.items() if can_view(role, level))


def can_view_document(role: Role | str | None, metadata: Mapping[str, object]) -> bool:
    """¿Puede este rol ver un fragmento con esta metadata?"""
    domain = metadata.get("domain")
    return isinstance(domain, str) and domain in allowed_domains(role)
