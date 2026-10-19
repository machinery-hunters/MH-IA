"""
ollama_anonymizer_node

REGLA CRITICA #2 (privacidad de datos):
Cuando is_confidential=True, este nodo procesa la CONSULTA y el contexto 100% en
local (Ollama) y produce anonymized_query y anonymized_context: nombres de
clientes, montos exactos, folios de consignación, RFC/datos fiscales, etc. se
sustituyen por placeholders semánticos (p.ej. "Cliente_A", "Monto_X") que
preservan la estructura del razonamiento sin exponer datos reales a la API de
Claude.

Consulta y contexto se anonimizan en UNA sola llamada, con cabeceras
fijas, para que la misma entidad reciba el mismo placeholder en ambas.

Falla cerrada: si el modelo no devuelve las dos secciones, si falla Ollama o si
la entrada trae las cabeceras (intento de inyección), el nodo devuelve
`anonymized=False` y textos vacíos. El outbound gateway bloquea el envío en ese
caso. Nunca se devuelve el texto crudo como si estuviera anonimizado.

Si is_confidential=False, el texto pasa sin cambios (ya es información
pública/general) con `anonymized=False`: el gateway igual lo revisa.

El anonimizador es probabilístico (modelo de 8B) y un documento puede intentar
convencerlo de "no cambiar nada". Por eso nada de lo que sale de aquí se confía:
el outbound gateway lo verifica con el scrubber determinista.
"""

import logging
import re
from functools import lru_cache

from langchain_ollama import ChatOllama

from pipeline_ai.config import settings
from pipeline_ai.state import MHIAState

log = logging.getLogger("mhia.anonymizer")

_MARK_QUERY = "===CONSULTA==="
_MARK_CONTEXT = "===CONTEXTO==="


class AnonymizationError(Exception):
    """La anonimización no produjo una salida utilizable (se trata como falla cerrada)."""


@lru_cache
def _get_anonymizer_llm() -> ChatOllama:
    return ChatOllama(
        model=settings.ollama_llm_model,
        base_url=settings.ollama_base_url,
        temperature=0,
    )


_ANONYMIZE_PROMPT = """Anonimiza el siguiente texto para uso interno de análisis.
Reemplaza SIEMPRE (usando un placeholder consistente si se repite, y el MISMO
placeholder para la misma entidad en la consulta y en el contexto):
- Nombres de personas o empresas -> Cliente_A, Cliente_B, Proveedor_A...
- Montos de dinero exactos -> Monto_X, Monto_Y...
- Números de contrato/folio/RFC/teléfono/correo -> Folio_X, Contacto_X...

Conserva intacta toda la estructura, fechas relativas, la lógica de negocio y
las etiquetas de fuente tipo "Fuente_1" (no las cambies).
No agregues comentarios ni expliques lo que hiciste.

Devuelve EXACTAMENTE dos secciones, cada cabecera en su propia línea y en este orden:
===CONSULTA===
<consulta anonimizada>
===CONTEXTO===
<contexto anonimizado>

TEXTO A ANONIMIZAR:
===CONSULTA===
{query}
===CONTEXTO===
{context}
"""

_SECTIONS_RE = re.compile(
    re.escape(_MARK_QUERY) + r"(?P<query>.*?)" + re.escape(_MARK_CONTEXT) + r"(?P<context>.*)\Z",
    re.DOTALL,
)


def _split_sections(output: str) -> tuple[str, str]:
    """Extrae (consulta, contexto) de la salida del modelo. Lo que venga antes de
    la primera cabecera (p.ej. un "Aquí está:") se descarta."""
    if output.count(_MARK_QUERY) != 1 or output.count(_MARK_CONTEXT) != 1:
        raise AnonymizationError("formato de salida inválido")
    match = _SECTIONS_RE.search(output)
    if match is None:
        raise AnonymizationError("secciones fuera de orden")
    query, context = match.group("query").strip(), match.group("context").strip()
    if not query or not context:
        raise AnonymizationError("sección vacía")
    return query, context


def _as_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            part if isinstance(part, str) else str(part.get("text", ""))
            for part in content
            if isinstance(part, str | dict)
        )
    raise AnonymizationError("tipo de respuesta inesperado")


async def anonymize_pair(query: str, context: str) -> tuple[str, str]:
    """Anonimiza (consulta, contexto) con el modelo local. Lanza AnonymizationError
    ante cualquier salida no utilizable."""
    if any(marker in text for text in (query, context) for marker in (_MARK_QUERY, _MARK_CONTEXT)):
        raise AnonymizationError("la entrada contiene las cabeceras reservadas")
    if not query.strip() or not context.strip():
        raise AnonymizationError("entrada vacía")
    result = await _get_anonymizer_llm().ainvoke(
        _ANONYMIZE_PROMPT.format(query=query, context=context)
    )
    return _split_sections(_as_text(result.content))


async def ollama_anonymizer_node(state: MHIAState) -> dict:
    query = state["query"]
    raw_context = state.get("raw_context", "")

    if not state.get("is_confidential", False):
        return {"anonymized_query": query, "anonymized_context": raw_context, "anonymized": False}

    try:
        anonymized_query, anonymized_context = await anonymize_pair(query, raw_context)
    except Exception as exc:  # noqa: BLE001 - falla cerrada: jamás se devuelve el crudo
        # Solo el nombre de la clase: el mensaje podría traer fragmentos del texto.
        log.warning("Anonimización fallida: %s", type(exc).__name__)
        return {"anonymized_query": "", "anonymized_context": "", "anonymized": False}

    return {
        "anonymized_query": anonymized_query,
        "anonymized_context": anonymized_context,
        "anonymized": True,
    }
