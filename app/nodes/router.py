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


_ROUTER_PROMPT = """Eres un clasificador interno de Machinery Hunters.
Clasifica la siguiente consulta de usuario en JSON estricto con este esquema:
{{"data_domain": "ventas|inventario|proveedores|general", "reason": "..."}}

- "ventas": cotizaciones, órdenes, comisiones, clientes, subastas cerradas.
- "inventario": maquinaria en subasta, consignación, stock, ubicación de equipos.
- "proveedores": consignantes, contratos, pagos a proveedores.
- "general": preguntas conceptuales, políticas públicas, uso del sistema, etc.

Consulta: {query}

Responde SOLO el JSON.
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
