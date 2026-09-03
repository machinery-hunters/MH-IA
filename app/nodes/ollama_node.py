"""
ollama_anonymizer_node

REGLA CRÍTICA #2 (privacidad de datos):
Cuando is_confidential=True, este nodo procesa raw_context 100% en local
(Ollama) y produce anonymized_context: nombres de clientes, montos exactos,
folios de consignación, RFC/datos fiscales, etc. se sustituyen por
placeholders semánticos (p.ej. "Cliente_A", "Monto_X") que preservan la
estructura del razonamiento sin exponer datos reales a la API de Claude.

Si is_confidential=False, el contexto pasa sin cambios (ya es información
pública/general), simplemente copiando raw_context -> anonymized_context.
"""

from functools import lru_cache

from langchain_ollama import ChatOllama

from app.config import settings
from app.state import MHIAState


@lru_cache
def _get_anonymizer_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,
    )


_ANONYMIZE_PROMPT = """Anonimiza el siguiente texto para uso interno de análisis.
Reemplaza SIEMPRE (usando un placeholder consistente si se repite):
- Nombres de personas o empresas -> Cliente_A, Cliente_B, Proveedor_A...
- Montos de dinero exactos -> Monto_X, Monto_Y...
- Números de contrato/folio/RFC/teléfono/correo -> Folio_X, Contacto_X...

Conserva intacta toda la estructura, fechas relativas y lógica de negocio.
No agregues comentarios ni expliques lo que hiciste. Devuelve solo el texto anonimizado.

TEXTO:
{context}
"""


async def ollama_anonymizer_node(state: MHIAState) -> dict:
    raw_context = state.get("raw_context", "")

    if not state.get("is_confidential", False):
        return {"anonymized_context": raw_context}

    if not raw_context.strip():
        return {"anonymized_context": ""}

    result = await _get_anonymizer_llm().ainvoke(_ANONYMIZE_PROMPT.format(context=raw_context))
    return {"anonymized_context": result.content}
