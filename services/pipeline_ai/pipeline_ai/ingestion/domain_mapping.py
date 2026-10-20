"""
domain_mapping

Infiere el dominio de negocio (ventas | inventario | proveedores | administracion | proyectos |
general) a partir de la carpeta de departamento en Google Drive donde vive un documento (el
primer nivel de carpeta bajo la raíz compartida con MH IA), usando coincidencia de palabras
clave, normalizado sin acentos y en minúsculas.

El dominio decide DOS cosas: quién puede ver el contenido (RBAC, `security.access`) y si se
anonimiza antes de ir a Claude (`privacy.sensitivity`). Por eso este módulo FALLA CERRADO:

  - Una carpeta cuyo nombre no coincide con ninguna palabra clave NO cae en "general" (que ve
    hasta el rol `consulta`): devuelve UNCLASSIFIED, y la ingesta no indexa ese archivo hasta
    que alguien decida su dominio aquí. Así un departamento nuevo (RH, Dirección, Legal...)
    nunca queda expuesto por omisión.
  - Los archivos sueltos en la raíz compartida tampoco tienen departamento: UNCLASSIFIED.
  - "general" solo se asigna si la carpeta lo dice explícitamente (ver _KEYWORDS).

Mantén esta lista alineada con los nombres reales de carpeta que uses en Drive.
"""

import re
import unicodedata

# Marcador de "sin decisión": no es un dominio válido ni visible para ningún rol.
UNCLASSIFIED = "sin_clasificar"

# El orden importa: se devuelve el primer dominio que coincide. "general" va al final y solo
# por nombres explícitos.
_KEYWORDS: dict[str, tuple[str, ...]] = {
    "ventas": ("venta", "comercial", "cotizacion", "cliente"),
    "inventario": (
        "inventario",
        "subasta",
        "consignacion",
        "portafolio",
        "equipo",
        "maquinaria",
    ),
    "proveedores": ("proveedor", "consignante", "compras", "sourcing"),
    "administracion": (
        "administracion",
        "finanzas",
        "contable",
        "contabilidad",
        "fiscal",
        "nomina",
    ),
    # Decisión del negocio (20 oct 2026): los proyectos son confidenciales; los ven líder,
    # configuración y control total, no `consulta`.
    "proyectos": ("proyecto",),
    # Confirmado con el negocio: logística no contiene datos de clientes ni cifras financieras.
    "general": ("general", "publico", "logistica"),
}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text


def infer_domain_from_folder_path(folder_path: list[str]) -> str:
    """folder_path: nombres de carpeta desde la raíz compartida hasta el archivo (sin incluir
    el archivo mismo). Solo se usa el primer nivel (carpeta de departamento).

    Compara por PALABRA completa (tokenizada), no por substring del nombre de carpeta entero:
    "venta" es substring de "inventario" (in-VENTA-rio), así que un match ingenuo clasificaría
    mal "Inventario" como "ventas". Aquí se exige que alguna palabra del nombre de carpeta
    EMPIECE CON la palabra clave (para tolerar plurales: "venta" matchea "ventas", pero no
    "inventario").

    Sin coincidencia (o sin carpeta) devuelve UNCLASSIFIED: falla cerrada."""
    if not folder_path:
        return UNCLASSIFIED

    department = _normalize(folder_path[0])
    words = re.findall(r"\w+", department)

    for domain, keywords in _KEYWORDS.items():
        if any(word.startswith(kw) for word in words for kw in keywords):
            return domain

    return UNCLASSIFIED
