"""
domain_mapping

Infiere el dominio de negocio (ventas | inventario | proveedores | general)
a partir de la carpeta de departamento en Google Drive donde vive un
documento (el primer nivel de carpeta bajo la raíz compartida con MH IA),
usando coincidencia de palabras clave, normalizado sin acentos y en
minúsculas.

Esto es una SEGUNDA capa de clasificación de confidencialidad, independiente
de la que hace el router sobre la consulta del usuario (pipeline_ai/nodes/router.py).
El router solo ve la pregunta; este módulo ve de qué carpeta viene el
documento ya recuperado. retriever_node.py combina ambas señales con un OR:
si cualquiera de las dos dice "confidencial", se anonimiza. Esto reduce el
riesgo de que una pregunta ambigua deje pasar sin anonimizar un documento
que en realidad sí vive en una carpeta de Ventas/Inventario/Proveedores.

Mantén esta lista de palabras clave alineada con los nombres reales de
carpeta que uses en Drive. Si agregas un departamento nuevo y no aparece
aquí, cae en "general" por defecto — no es un error de privacidad (sigue
sin ir a Claude sin al menos pasar el router), pero conviene revisar
INGESTION.md para agregar sus palabras clave si el contenido es sensible.
"""

import re
import unicodedata

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
}


def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = "".join(c for c in unicodedata.normalize("NFD", text) if unicodedata.category(c) != "Mn")
    return text


def infer_domain_from_folder_path(folder_path: list[str]) -> str:
    """folder_path: nombres de carpeta desde la raíz compartida hasta el
    archivo (sin incluir el archivo mismo). Solo se usa el primer nivel
    (carpeta de departamento) para la clasificación.

    Compara por PALABRA completa (tokenizada), no por substring del nombre
    de carpeta entero: "venta" es substring de "inventario" (in-VENTA-rio),
    así que un match ingenuo de substring clasificaría mal "Inventario"
    como "ventas". Aquí se exige que alguna palabra del nombre de carpeta
    EMPIECE CON la palabra clave (para tolerar plurales: "venta" matchea
    la palabra "ventas", pero no matchea la palabra "inventario")."""
    if not folder_path:
        return "general"

    department = _normalize(folder_path[0])
    words = re.findall(r"\w+", department)

    for domain, keywords in _KEYWORDS.items():
        if any(word.startswith(kw) for word in words for kw in keywords):
            return domain

    return "general"
