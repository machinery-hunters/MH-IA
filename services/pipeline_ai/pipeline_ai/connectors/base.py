"""
Contrato de un conector de sistema fuente.

Un conector SOLO sabe hablar con su sistema (ERP, CRM, Drive, Firestore) y
devolver contexto CRUDO en `extract`. No anonimiza, no decide a quién se le
muestra y no habla con Claude: eso lo hace el sub-agente (agents/subagent.py)
que lo envuelve, con aislamiento de fallas y timeout.

Cada conector declara su `ConnectorSpec`, incluida su sensibilidad POR
DEFECTO. Esa declaración es un piso: la sensibilidad efectiva de lo que
devuelve nunca es menor (ver privacy.sensitivity.effective_sensitivity).
Agregar un conector nuevo = declarar su spec; NO se toca el prompt del router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area
from pipeline_ai.sources import Citation


@dataclass(frozen=True)
class ConnectorSpec:
    name: str  # id estable: "erp", "crm", "drive", "firestore"
    display_name: str
    default_sensitivity: Sensitivity
    # Áreas cuyos datos expone la fuente: para usarla hace falta membresía en alguna de
    # ellas con nivel suficiente (security.areas.can_access). Sin áreas, nadie la usa.
    areas: tuple[Area, ...] = ()
    # Atributos de `settings` que deben estar presentes para que el conector
    # funcione. Si falta alguno -> el sub-agente responde NOT_CONFIGURED sin
    # intentar conectarse (y sin tumbar la petición).
    required_settings: tuple[str, ...] = ()
    # Atributo de `settings` con el timeout (segundos) de este conector.
    timeout_setting: str = "timeout_global_s"
    description: str = ""


@dataclass(frozen=True)
class RawExtraction:
    """Salida CRUDA de un conector. Nunca sale del sub-agente."""

    text: str
    citations: list[Citation] = field(default_factory=list)
    # El conector puede SUBIR la sensibilidad (p.ej. un documento de la
    # carpeta Administración dentro de Drive), nunca bajarla.
    sensitivity: Sensitivity | None = None


class Connector(Protocol):
    spec: ConnectorSpec

    async def extract(self, query: str) -> RawExtraction: ...
