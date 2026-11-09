"""
domain_mapping

Infiere el ÁREA de negocio (security.areas.Area: comercial, sourcing, finanzas, rh, ...) a
partir de la carpeta de departamento en Google Drive donde vive un documento (el primer
nivel de carpeta bajo la raíz compartida con MH IA), usando coincidencia de palabras clave,
normalizado sin acentos y en minúsculas.

El área decide quién puede ver el contenido (membresías, `security.access`) y su
sensibilidad mínima (`security.areas.AREA_MIN_SENSITIVITY`), que a su vez decide si se
anonimiza o si ni siquiera puede salir a Claude. Por eso este módulo FALLA CERRADO:

  - Una carpeta cuyo nombre no coincide con ninguna palabra clave NO cae en "general" (que ve
    cualquier usuario): devuelve UNCLASSIFIED, y la ingesta no indexa ese archivo hasta que
    alguien decida su área aquí. Así un departamento nuevo nunca queda expuesto por omisión.
  - Los archivos sueltos en la raíz compartida tampoco tienen departamento: UNCLASSIFIED.
  - "general" solo se asigna si la carpeta lo dice explícitamente (ver _KEYWORDS).

Mantén esta lista alineada con los nombres reales de carpeta que uses en Drive.
"""

import re
import unicodedata

# Marcador de "sin decisión": no es un dominio válido ni visible para ningún rol.
UNCLASSIFIED = "sin_clasificar"

# El orden importa: se devuelve la primera área que coincide. Las áreas más delicadas van
# primero (una carpeta "Nómina Finanzas" es de RH, no de Finanzas) y "general" va al final y
# solo por nombres explícitos.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "rh": ("rh", "rrhh", "humano", "nomina", "personal"),
    "finanzas": (
        "administracion",
        "finanzas",
        "contable",
        "contabilidad",
        "fiscal",
        "tesoreria",
    ),
    "direccion": ("direccion", "consejo"),
    "comercial": ("venta", "comercial", "cotizacion", "cliente"),
    "inventario": (
        "inventario",
        "subasta",
        "consignacion",
        "portafolio",
        "equipo",
        "maquinaria",
    ),
    "sourcing": ("proveedor", "consignante", "compras", "sourcing", "websourcing"),
    # Decisión del negocio (20 oct 2026): los proyectos son confidenciales.
    "proyectos": ("proyecto",),
    # Antes caía en "general"; con el modelo por área es un área propia (plan §2.5.1).
    "logistica": ("logistica", "embarque", "transporte", "flete"),
    "marketing": ("marketing", "mercadotecnia", "publicidad"),
    "taller": ("taller", "mantenimiento", "refaccion"),
    "general": ("general", "publico"),
}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text


def infer_area_from_folder_path(folder_path: list[str]) -> str:
    """folder_path: nombres de carpeta desde la raíz compartida hasta el archivo (sin incluir
    el archivo mismo). Solo se usa el primer nivel (carpeta de departamento).

    Compara por PALABRA completa (tokenizada), no por substring del nombre de carpeta entero:
    "venta" es substring de "inventario" (in-VENTA-rio), así que un match ingenuo clasificaría
    mal "Inventario" como "comercial". Aquí se exige que alguna palabra del nombre de carpeta
    EMPIECE CON la palabra clave (para tolerar plurales: "venta" matchea "ventas", pero no
    "inventario").

    Sin coincidencia (o sin carpeta) devuelve UNCLASSIFIED: falla cerrada."""
    if not folder_path:
        return UNCLASSIFIED

    department = _normalize(folder_path[0])
    words = re.findall(r"\w+", department)

    for area, keywords in _KEYWORDS.items():
        if any(word.startswith(kw) for word in words for kw in keywords):
            return area

    return UNCLASSIFIED
