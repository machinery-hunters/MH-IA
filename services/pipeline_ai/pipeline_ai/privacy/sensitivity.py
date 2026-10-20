"""
Niveles de sensibilidad y su regla de combinación.

Regla central de MH IA: la sensibilidad efectiva de un dato es el MÁXIMO de
todas las señales disponibles (default del conector, dominio del documento,
clasificación del router). Ninguna señal puede BAJARLA, solo subirla. Si no
hay ninguna señal, se asume el nivel más estricto que aún permite trabajar
(CONFIDENTIAL): falla cerrada.
"""

from __future__ import annotations

from enum import IntEnum


class Sensitivity(IntEnum):
    PUBLIC = 0  # información pública de la empresa
    INTERNAL = 1  # uso interno sin datos de clientes ni cifras financieras
    CONFIDENTIAL = 2  # clientes, precios, contratos, finanzas: se anonimiza antes de Claude
    RESTRICTED = 3  # credenciales, nómina, fiscal: no sale de la red local sin aprobación


# Dominio de negocio (router / domain_mapping) -> sensibilidad mínima.
DOMAIN_SENSITIVITY: dict[str, Sensitivity] = {
    "general": Sensitivity.INTERNAL,
    "ventas": Sensitivity.CONFIDENTIAL,
    "inventario": Sensitivity.CONFIDENTIAL,
    "proveedores": Sensitivity.CONFIDENTIAL,
    "administracion": Sensitivity.CONFIDENTIAL,
    "proyectos": Sensitivity.CONFIDENTIAL,
}


def sensitivity_for_domain(domain: str | None) -> Sensitivity:
    """Dominio desconocido o ausente -> CONFIDENTIAL (falla cerrada)."""
    return DOMAIN_SENSITIVITY.get(domain or "", Sensitivity.CONFIDENTIAL)


def effective_sensitivity(*levels: Sensitivity) -> Sensitivity:
    """Máximo de las señales. Sin señales -> CONFIDENTIAL."""
    return max(levels, default=Sensitivity.CONFIDENTIAL)


def requires_anonymization(level: Sensitivity) -> bool:
    return level >= Sensitivity.CONFIDENTIAL
