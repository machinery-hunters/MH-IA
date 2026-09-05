"""
router_node

Corre 100% en Ollama. Es el primer nodo del grafo y decide:
  1. data_domain: ventas | inventario | proveedores | general
  2. is_confidential: True si domain está en settings.confidential_domains
  3. route: a dónde va el flujo después

REGLA CRÍTICA #2: esta clasificación NUNCA se delega a Claude. La query cruda
del usuario podría contener nombres de clientes, precios de subasta, montos
de consignación, etc. Por eso el propio enrutamiento se resuelve localmente.
"""

import json
from functools import lru_cache

from langchain_ollama import ChatOllama

from app.config import settings
from app.state import MHIAState


@lru_cache
def _get_router_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,
        format="json",
    )


_ROUTER_PROMPT = """Eres un clasificador interno de Machinery Hunters (MH),
empresa de compra-venta, subasta y consignación de maquinaria pesada.

Clasifica la siguiente consulta de usuario en JSON estricto con este esquema:
{{"data_domain": "ventas|inventario|proveedores|general", "reason": "..."}}

- "ventas": cotizaciones, órdenes, comisiones, clientes, subastas cerradas,
  montos vendidos.
- "inventario": maquinaria en subasta o consignación, stock, ubicación de
  equipos, precios de compra/venta de referencia, portafolio de equipos,
  categorías/marcas/modelos de maquinaria.
- "proveedores": consignantes, contratos de consignación, pagos a
  proveedores/consignantes.
- "general": preguntas conceptuales, políticas públicas de la empresa, uso
  del sistema de IA, temas de mercado sin datos internos de MH.

REGLA IMPORTANTE: clasifica por el CONTENIDO de negocio que la consulta
implica, no solo por el verbo de la pregunta. Preguntas que piden "resumir",
"explicar" o "qué dice" un documento/tema deben clasificarse según de qué
trata ese documento/tema, no como "general" por defecto. Si el nombre de un
documento o la consulta menciona equipos, maquinaria, portafolio de activos,
precios de compra/venta, tonelaje, marcas de maquinaria, subastas,
consignación, cotizaciones, comisiones, clientes o proveedores, NO es
"general" — clasifica el dominio de negocio correspondiente.

Ejemplos:
Consulta: "¿Cuántas grúas quedan disponibles en subasta?"
{{"data_domain": "inventario", "reason": "pregunta por stock de maquinaria en subasta"}}

Consulta: "Resume de qué trata el documento de Efectividad en Equipos de Portafolio"
{{"data_domain": "inventario", "reason": "el nombre del documento indica portafolio de equipos, es contenido de inventario aunque el verbo sea 'resumir'"}}

Consulta: "¿Cuánto le debemos al proveedor Grúas del Norte?"
{{"data_domain": "proveedores", "reason": "pago a un consignante/proveedor específico"}}

Consulta: "¿Cómo funciona el sistema de IA de MH?"
{{"data_domain": "general", "reason": "pregunta sobre el propio sistema, sin datos de negocio de MH"}}

Ahora clasifica esta consulta:
Consulta: {query}

Responde SOLO el JSON, sin texto adicional.
"""


async def router_node(state: MHIAState) -> dict:
    query = state["query"]

    raw = await _get_router_llm().ainvoke(_ROUTER_PROMPT.format(query=query))
    try:
        parsed = json.loads(raw.content)
        domain = parsed.get("data_domain", "general")
    except (json.JSONDecodeError, AttributeError):
        # Fail-safe: ante duda, tratar como confidencial (privacidad > conveniencia)
        domain = "inventario"

    is_confidential = domain in settings.confidential_domains

    return {
        "data_domain": domain,
        "is_confidential": is_confidential,
        "route": "retrieve_then_reason",
    }