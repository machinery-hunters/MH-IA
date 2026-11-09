"""
Control de acceso a documentos por área (rol × área, plan maestro §2.5).

Cada fragmento indexado lleva su `area` (inferida de la carpeta de departamento de Drive en
la ingesta) y, si la tiene, una `sensitivity` propia. Los fragmentos anteriores llevan
`domain` y se leen con la equivalencia de `security.areas.LEGACY_DOMAIN_TO_AREA`.

Un usuario ve un fragmento si tiene membresía en su área con un nivel que cubre la
sensibilidad del fragmento (`security.areas.fragment_sensitivity`). Falla cerrada:
  - Sin membresías válidas -> no se consulta ni la base.
  - Fragmento sin área reconocible o con etiqueta corrupta -> nadie lo ve.

El filtro se aplica dos veces (defensa en profundidad): como condición de Qdrant (para que
el contenido prohibido ni siquiera salga de la base) y de nuevo sobre lo recuperado, por si
el filtro de la base faltara o se alterara. La API fija las membresías desde la sesión;
nunca las manda el cliente.
"""

from __future__ import annotations

from collections.abc import Mapping

from qdrant_client.http import models as qmodels

from pipeline_ai.security.areas import (
    LEGACY_DOMAIN_TO_AREA,
    Memberships,
    fragment_sensitivity,
    visible_areas,
)
from pipeline_ai.security.areas import fragment_area as _fragment_area


def can_view_document(memberships: Memberships, metadata: Mapping[str, object]) -> bool:
    """¿Pueden estas membresías ver un fragmento con esta metadata?"""
    area = _fragment_area(metadata)
    if area is None:
        return False
    level = memberships.get(area)
    return level is not None and level >= fragment_sensitivity(metadata)


def access_filter(memberships: Memberships) -> qmodels.Filter | None:
    """Condición de Qdrant equivalente a `can_view_document`. None si no hay nada visible
    (el llamador no debe consultar).

    Por cada área visible: (area == A  o  domain ∈ dominios anteriores de A)  y
    (sensitivity <= nivel  o  sin etiqueta). Las áreas se combinan con OR."""
    clauses: list[qmodels.Filter] = []
    for area, level in visible_areas(memberships).items():
        legacy = [d for d, a in LEGACY_DOMAIN_TO_AREA.items() if a is area]
        where_area: list[qmodels.Condition] = [
            qmodels.FieldCondition(key="metadata.area", match=qmodels.MatchValue(value=area.value))
        ]
        if legacy:
            # Solo fragmentos anteriores (sin `area`): uno nuevo con `domain` residual no
            # puede colarse en un área distinta de la suya.
            where_area.append(
                qmodels.Filter(
                    must=[
                        qmodels.FieldCondition(
                            key="metadata.domain", match=qmodels.MatchAny(any=legacy)
                        ),
                        qmodels.IsEmptyCondition(
                            is_empty=qmodels.PayloadField(key="metadata.area")
                        ),
                    ]
                )
            )
        clauses.append(
            qmodels.Filter(
                must=[
                    qmodels.Filter(should=where_area),
                    qmodels.Filter(
                        should=[
                            qmodels.FieldCondition(
                                key="metadata.sensitivity", range=qmodels.Range(lte=int(level))
                            ),
                            qmodels.IsEmptyCondition(
                                is_empty=qmodels.PayloadField(key="metadata.sensitivity")
                            ),
                        ]
                    ),
                ]
            )
        )
    if not clauses:
        return None
    return qmodels.Filter(should=clauses)
