"""
retriever_node

Consulta Qdrant (embeddings generados localmente vía Ollama). Aplica el
umbral de score de settings.retrieval_score_threshold.

REGLA CRITICA #0 (RBAC - el rol decide qué contenido existe para la consulta):
Solo se recuperan fragmentos de los dominios que el rol del usuario puede ver
(`security.access`). Se aplica como filtro de Qdrant y se verifica otra vez sobre lo
recuperado. El rol lo fija la API desde la sesión (`state["role"]`); si falta o no es
válido, no se recupera nada (falla cerrada). Lo que el rol no puede ver es indistinguible
de lo que no existe: el flujo aborta con la misma respuesta de "sin información".

REGLA CRITICA #1 (cero alucinaciones - parte 1/2):
Si no hay documentos por encima del umbral, marca retrieval_ok=False.
El grafo debe leer este flag en su edge condicional y saltar directo al
nodo de aborto, SIN pasar por Claude.

REGLA CRITICA #2 (privacidad - alias de fuentes):
El contexto se arma con alias estables (Fuente_1, Fuente_2...) en lugar del
nombre real del archivo. Así el nombre (p.ej. "Contrato_Consignacion_034.pdf")
no entra al texto que ve el anonimizador ni, después, Claude. El mapa
alias -> nombre real queda en `source_aliases` (solo local) y se usa para
re-etiquetar la respuesta. `citations` conserva los nombres reales para la UI.
"""

import hashlib
import logging
from functools import lru_cache

from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from pipeline_ai.config import settings
from pipeline_ai.embeddings import TaskPrefixedOllamaEmbeddings
from pipeline_ai.privacy.outbound_gateway import assign_source_aliases
from pipeline_ai.security.access import allowed_domains, can_view_document
from pipeline_ai.state import Citation, MHIAState

log = logging.getLogger("mhia.retriever")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_NOTHING = {"retrieved_docs": [], "citations": [], "retrieval_ok": False}


@lru_cache
def _get_vectorstore() -> QdrantVectorStore:
    embeddings = TaskPrefixedOllamaEmbeddings(
        model=settings.ollama_embed_model, base_url=settings.ollama_base_url
    )
    client = QdrantClient(url=settings.qdrant_url)
    return QdrantVectorStore(
        client=client,
        collection_name=settings.qdrant_collection,
        embedding=embeddings,
    )


def _domain_filter(domains: tuple[str, ...]) -> qmodels.Filter:
    """Condición de Qdrant: solo fragmentos cuyo dominio esté permitido. Un fragmento sin
    `metadata.domain` no cumple MatchAny, así que queda fuera (falla cerrada)."""
    return qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="metadata.domain",
                match=qmodels.MatchAny(any=list(domains)),
            )
        ]
    )


def _query_fingerprint(query: str) -> str:
    """Huella para correlacionar en logs sin escribir la consulta (puede traer datos reales)."""
    return f"len={len(query)} sha256={hashlib.sha256(query.encode('utf-8')).hexdigest()[:12]}"


async def retriever_node(state: MHIAState) -> dict:
    query = state["query"]
    role = state.get("role")

    domains = allowed_domains(role)
    if not domains:
        # Sin rol válido no se consulta ni la base: falla cerrada.
        log.warning("Recuperación omitida: el rol no puede ver ningún dominio.")
        return dict(_NOTHING)

    results = await _get_vectorstore().asimilarity_search_with_score(
        query, k=5, filter=_domain_filter(domains)
    )

    # Segunda capa: aunque el filtro de la base falle o se altere, lo que no es visible para
    # este rol se descarta aquí, antes de puntuar o de armar contexto.
    visible = [(doc, score) for doc, score in results if can_view_document(role, doc.metadata)]
    if len(visible) != len(results):
        log.error(
            "El filtro de Qdrant dejó pasar %d fragmento(s) no permitidos para el rol; "
            "se descartaron.",
            len(results) - len(visible),
        )
    results = visible

    # Diagnóstico: siempre loguea el top-3 de scores encontrados, pase o no
    # el umbral. Útil para calibrar RETRIEVAL_SCORE_THRESHOLD con datos reales en vez de a
    # ciegas. La consulta NO se escribe (puede contener datos reales): solo su huella.
    top = sorted(results, key=lambda r: r[1], reverse=True)[:3]
    log.info(
        "Top scores (%s, umbral=%.2f): %s",
        _query_fingerprint(query),
        settings.retrieval_score_threshold,
        [(round(score, 4), doc.metadata.get("source", "?")) for doc, score in top],
    )

    accepted = [
        (doc, score) for doc, score in results if score >= settings.retrieval_score_threshold
    ]
    # Minimización: a Claude solo viajan los fragmentos cercanos al mejor (el piso absoluto no
    # basta, ver config). Un margen <= 0 lo desactiva.
    if accepted and settings.retrieval_score_margin > 0:
        best = max(score for _, score in accepted)
        accepted = [
            (doc, score)
            for doc, score in accepted
            if score >= best - settings.retrieval_score_margin
        ]

    if not accepted:
        return dict(_NOTHING)

    docs = [doc for doc, _ in accepted]
    citations: list[Citation] = [
        {
            "source": doc.metadata.get("source", "desconocido"),
            "chunk_id": doc.metadata.get("chunk_id", ""),
            "score": float(score),
        }
        for doc, score in accepted
    ]

    # Alias estables por fuente: el nombre real de archivo no entra al contexto.
    real_to_alias = assign_source_aliases(c["source"] for c in citations)
    source_aliases = {alias: real for real, alias in real_to_alias.items()}

    raw_context = "\n\n---\n\n".join(
        f"[Fuente: {real_to_alias[c['source']]}]\n{doc.page_content}"
        for doc, c in zip(docs, citations, strict=True)
    )

    # Segunda capa de confidencialidad (defensa en profundidad): el router
    # (pipeline_ai/nodes/router.py) clasifica confidencialidad SOLO a partir de la
    # pregunta del usuario, que puede ser ambigua o genérica ("resume este
    # documento"). Aquí revisamos de qué carpeta/dominio viene cada chunk
    # REALMENTE recuperado (ver pipeline_ai/ingestion/domain_mapping.py, inferido
    # de la carpeta de departamento en Drive). Si cualquiera de las dos
    # señales dice "confidencial", se anonimiza - nunca se relaja lo que
    # el router ya decidió, solo se puede volver más estricto.
    content_domains = {doc.metadata.get("domain") for doc in docs}
    is_confidential_by_content = bool(content_domains & set(settings.confidential_domains))
    is_confidential = state.get("is_confidential", False) or is_confidential_by_content

    if is_confidential_by_content and not state.get("is_confidential", False):
        log.info(
            "Confidencialidad elevada por contenido recuperado (dominios de carpeta: %s), "
            "aunque el router había clasificado la consulta como no confidencial.",
            content_domains,
        )

    return {
        "retrieved_docs": docs,
        "citations": citations,
        "retrieval_ok": True,
        "raw_context": raw_context,
        "source_aliases": source_aliases,
        "is_confidential": is_confidential,
    }
