"""
Rol y áreas por defecto según el PUESTO (plan maestro §2.5.1).

Lo usa la importación de usuarios (`security.user_import`) para no capturar a mano las
membresías de cada persona. Es el valor por defecto de la fase 1: Dirección debe validarlo
y, si cambia, se ajusta aquí en un PR (queda revisado y con historial). Después de
importar, cualquier excepción se maneja por persona con `manage set-areas`.

Criterios: menor privilegio (cada jefe ve su área, más inventario si lo usa para trabajar);
Sistemas administra pero NO lee datos (control_total sin áreas); Dirección ve todo a nivel
confidencial y lo restringido de Finanzas y RH.

Un puesto que no coincide con ningún preset NO se importa (falla cerrada): se reporta para
decidirlo a mano.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.security.areas import Area
from pipeline_ai.security.roles import Role

_I, _C, _R = Sensitivity.INTERNAL, Sensitivity.CONFIDENTIAL, Sensitivity.RESTRICTED


@dataclass(frozen=True)
class Preset:
    name: str
    role: Role
    memberships: dict[Area, Sensitivity]
    note: str = ""


def _lider(*areas: tuple[Area, Sensitivity]) -> dict[Area, Sensitivity]:
    return {Area.GENERAL: _I, **dict(areas)}


_DIRECCION = {area: _C for area in Area} | {
    Area.GENERAL: _I,
    Area.FINANZAS: _R,
    Area.RH: _R,
}

# (palabras clave del puesto, preset). Se evalúan EN ORDEN: "Asistente del CEO" debe caer en
# asistente antes que en CEO. Una clave de varias palabras exige todas, en ese orden.
PRESETS: tuple[tuple[tuple[str, ...], Preset], ...] = (
    (
        ("asistente",),
        Preset("asistente_direccion", Role.CONSULTA, {Area.GENERAL: _I, Area.DIRECCION: _I}),
    ),
    (
        ("ceo", "director general", "directora general", "direccion general"),
        Preset("direccion", Role.DIRECCION, _DIRECCION),
    ),
    (
        ("sistemas", "ti", "tecnologia"),
        Preset(
            "sistemas",
            Role.CONTROL_TOTAL,
            {},
            note="administra sin leer datos; para uso diario crea una segunda cuenta lider",
        ),
    ),
    (("recursos humanos", "rh", "rrhh"), Preset("rh", Role.LIDER, _lider((Area.RH, _R)))),
    (
        ("finanzas", "contabilidad", "contador", "contadora", "administracion"),
        Preset("finanzas", Role.LIDER, _lider((Area.FINANZAS, _R))),
    ),
    (
        ("comercial", "ventas"),
        Preset("comercial", Role.LIDER, _lider((Area.COMERCIAL, _C), (Area.INVENTARIO, _C))),
    ),
    (
        ("logistica",),
        Preset("logistica", Role.LIDER, _lider((Area.LOGISTICA, _C), (Area.INVENTARIO, _C))),
    ),
    (
        ("sourcing", "compras", "proveedores"),
        Preset("sourcing", Role.LIDER, _lider((Area.SOURCING, _C), (Area.INVENTARIO, _C))),
    ),
    (
        ("marketing", "mercadotecnia"),
        Preset(
            "marketing",
            Role.LIDER,
            _lider((Area.MARKETING, _C), (Area.INVENTARIO, _I)),
            note="inventario INTERNAL no alcanza los documentos de inventario (CONFIDENTIAL)",
        ),
    ),
    (("proyectos",), Preset("proyectos", Role.LIDER, _lider((Area.PROYECTOS, _C)))),
    (
        ("taller", "mantenimiento"),
        Preset("taller", Role.LIDER, _lider((Area.TALLER, _C), (Area.INVENTARIO, _C))),
    ),
)


def _words(text: str) -> list[str]:
    text = "".join(
        c for c in unicodedata.normalize("NFD", text.lower()) if unicodedata.category(c) != "Mn"
    )
    return re.findall(r"[a-z0-9]+", text)


def _contains(words: list[str], phrase: str) -> bool:
    target = phrase.split()
    return any(words[i : i + len(target)] == target for i in range(len(words)))


def preset_for(puesto: str) -> Preset | None:
    """Preset del puesto, o None si ninguno coincide (no se importa)."""
    words = _words(puesto)
    for keys, preset in PRESETS:
        if any(_contains(words, key) for key in keys):
            return preset
    return None
