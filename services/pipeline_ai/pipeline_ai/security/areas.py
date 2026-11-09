"""
Áreas de negocio y membresías de usuario (modelo rol × área, plan maestro §2.5).

El ROL dice qué puede HACER una persona (consultar, configurar, administrar). Las
MEMBRESÍAS dicen qué puede VER: por cada área, el nivel de sensibilidad máximo que alcanza
(`{comercial: CONFIDENTIAL, inventario: INTERNAL}`). Así RH ve lo RESTRICTED de RH sin ver
lo de Comercial, y Sistemas administra sin leer nómina.

Reglas (todas fallan cerradas):
  - Un área desconocida no existe: no se puede asignar ni se puede ver.
  - Un fragmento sin área reconocible no lo ve nadie.
  - La sensibilidad de un fragmento es el MÁXIMO entre la mínima de su área y la que traiga
    el propio fragmento: una etiqueta puede subirla, nunca bajarla.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from enum import Enum

from pipeline_ai.privacy.sensitivity import Sensitivity


class Area(str, Enum):  # noqa: UP042 - mismo estilo que Role
    DIRECCION = "direccion"
    COMERCIAL = "comercial"
    SOURCING = "sourcing"
    LOGISTICA = "logistica"
    MARKETING = "marketing"
    PROYECTOS = "proyectos"
    FINANZAS = "finanzas"
    RH = "rh"
    TALLER = "taller"
    INVENTARIO = "inventario"
    GENERAL = "general"


# Sensibilidad mínima de cualquier dato de cada área. RH es RESTRICTED entera (nómina,
# expedientes); Finanzas es CONFIDENTIAL y sus documentos más delicados se marcan RESTRICTED.
AREA_MIN_SENSITIVITY: dict[Area, Sensitivity] = {
    Area.GENERAL: Sensitivity.INTERNAL,
    Area.RH: Sensitivity.RESTRICTED,
    **{
        a: Sensitivity.CONFIDENTIAL
        for a in Area
        if a not in {Area.GENERAL, Area.RH}  # el resto, confidencial
    },
}

# Fragmentos indexados antes de este modelo llevan `metadata.domain` (vocabulario anterior).
# Se leen con esta equivalencia para no obligar a reindexar; la ingesta nueva escribe `area`.
LEGACY_DOMAIN_TO_AREA: dict[str, Area] = {
    "ventas": Area.COMERCIAL,
    "proveedores": Area.SOURCING,
    "administracion": Area.FINANZAS,
    "proyectos": Area.PROYECTOS,
    "inventario": Area.INVENTARIO,
    "general": Area.GENERAL,
}

Memberships = Mapping[Area, Sensitivity]


class InvalidMembership(ValueError):
    """Área o nivel desconocidos en una asignación."""


def parse_area(value: str) -> Area:
    try:
        return Area(value.strip().lower())
    except ValueError as exc:
        raise InvalidMembership(f"área desconocida: {value!r}") from exc


def parse_level(value: str | int) -> Sensitivity:
    if isinstance(value, int) and not isinstance(value, bool):
        try:
            return Sensitivity(value)
        except ValueError as exc:
            raise InvalidMembership(f"nivel desconocido: {value!r}") from exc
    try:
        return Sensitivity[str(value).strip().upper()]
    except KeyError as exc:
        raise InvalidMembership(f"nivel desconocido: {value!r}") from exc


def parse_memberships(text: str) -> dict[Area, Sensitivity]:
    """'comercial:CONFIDENTIAL, inventario:internal' -> {Area: Sensitivity}. Vacío -> {}.

    Un área repetida es un error (evita que una segunda mención suba el nivel sin querer)."""
    result: dict[Area, Sensitivity] = {}
    for item in (part.strip() for part in text.split(",")):
        if not item:
            continue
        if ":" not in item:
            raise InvalidMembership(f"falta el nivel en {item!r} (formato area:NIVEL)")
        area_text, level_text = item.split(":", 1)
        area = parse_area(area_text)
        if area in result:
            raise InvalidMembership(f"área repetida: {area.value}")
        result[area] = parse_level(level_text)
    return result


def format_memberships(memberships: Memberships) -> str:
    return ", ".join(
        f"{area.value}:{level.name}" for area, level in sorted(memberships.items(), key=_order)
    )


def _order(item: tuple[Area, Sensitivity]) -> int:
    return list(Area).index(item[0])


def to_state(memberships: Memberships) -> dict[str, int]:
    """Forma serializable para el estado del grafo: {"comercial": 2}."""
    return {area.value: int(level) for area, level in memberships.items()}


def from_state(raw: object) -> dict[Area, Sensitivity]:
    """Inverso de `to_state`. Cualquier entrada inválida se DESCARTA (nunca concede acceso)."""
    if not isinstance(raw, Mapping):
        return {}
    result: dict[Area, Sensitivity] = {}
    for key, value in raw.items():
        try:
            result[parse_area(str(key))] = parse_level(value)
        except InvalidMembership:
            continue
    return result


def fragment_area(metadata: Mapping[str, object]) -> Area | None:
    """Área de un fragmento: `metadata.area` (ingesta actual) o la equivalencia del
    `metadata.domain` anterior. Ninguna de las dos -> None (nadie lo ve)."""
    area = metadata.get("area")
    if isinstance(area, str):
        try:
            return Area(area)
        except ValueError:
            return None
    domain = metadata.get("domain")
    if isinstance(domain, str):
        return LEGACY_DOMAIN_TO_AREA.get(domain)
    return None


def fragment_sensitivity(metadata: Mapping[str, object]) -> Sensitivity:
    """Máximo entre la mínima del área y la etiqueta del fragmento. Sin área -> RESTRICTED."""
    area = fragment_area(metadata)
    if area is None:
        return Sensitivity.RESTRICTED
    level = AREA_MIN_SENSITIVITY[area]
    raw = metadata.get("sensitivity")
    if isinstance(raw, int) and not isinstance(raw, bool):
        try:
            level = max(level, Sensitivity(raw))
        except ValueError:
            return Sensitivity.RESTRICTED  # etiqueta corrupta: lo más estricto
    return level


def can_access(memberships: Memberships, areas: Iterable[Area], level: Sensitivity) -> bool:
    """¿Alguna membresía en `areas` alcanza `level`?"""
    return any(memberships.get(area, -1) >= level for area in areas)


def visible_areas(memberships: Memberships) -> dict[Area, Sensitivity]:
    """Áreas donde la membresía alcanza al menos la sensibilidad mínima del área: en las
    demás no se podría ver ni un fragmento, así que ni se consultan."""
    return {
        area: level
        for area, level in memberships.items()
        if isinstance(area, Area) and level >= AREA_MIN_SENSITIVITY[area]
    }
