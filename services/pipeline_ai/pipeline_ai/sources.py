"""
Tipos compartidos entre conectores, sub-agentes y el estado del grafo.

Vive en un módulo sin dependencias internas para evitar ciclos de import
(state.py, connectors/ y agents/ lo importan, él no importa a nadie).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TypedDict


class Citation(TypedDict):
    source: str  # p.ej. "Google Drive: Contrato_Consignacion_034.pdf"
    chunk_id: str
    score: float


class SourceStatus(str, Enum):  # noqa: UP042 - compatible con 3.10 (validación local) y 3.12
    """Resultado de consultar UNA fuente. Nunca se oculta una falla:
    el supervisor y Claude siempre saben qué fuentes sí respondieron."""

    OK = "ok"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"  # error al consultar o al anonimizar (falla cerrada)
    NOT_CONFIGURED = "not_configured"  # faltan credenciales (p.ej. ERP hoy)
    FORBIDDEN = "forbidden"  # el rol del usuario no puede ver esta fuente


@dataclass(frozen=True)
class SourceResult:
    """Lo ÚNICO que un sub-agente devuelve al supervisor.

    `context` ya viene anonimizado y normalizado: el supervisor nunca ve el
    dato crudo de la fuente. `detail` describe la falla SIN datos (solo el
    nombre de la clase de excepción), porque un mensaje de error de un
    sistema fuente puede traer fragmentos de registros.
    """

    source: str
    status: SourceStatus
    context: str = ""
    citations: list[Citation] = field(default_factory=list)
    sensitivity: int = 0  # valor de privacy.sensitivity.Sensitivity
    detail: str = ""
    elapsed_ms: int = 0

    @property
    def ok(self) -> bool:
        return self.status is SourceStatus.OK
