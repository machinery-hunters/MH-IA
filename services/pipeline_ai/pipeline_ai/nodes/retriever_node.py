"""
retriever_node

Recupera fragmentos de Qdrant en tres pasos:

  1. CANDIDATOS: búsqueda híbrida (significado + palabras clave, retrieval/hybrid_store.py)
     con el filtro de dominios del rol.
  2. JUICIO: un juez local (Ollama, retrieval/judge.py) decide cuáles contienen información
     que responde la pregunta. Es el criterio principal de relevancia.
  3. RESPALDO: si el juez no está disponible (Ollama caído, timeout, salida inválida) se vuelve
     al criterio anterior: piso absoluto de coseno (settings.retrieval_score_threshold) más
     margen relativo (settings.retrieval_score_margin). Un fallo del juez nunca descarta
     evidencia ni abre acceso a nada: el RBAC ya se aplicó antes.

REGLA CRITICA #0 (acceso por área - las membresías deciden qué contenido existe):
Solo se recuperan fragmentos de las áreas, y hasta el nivel, que las membresías del usuario
permiten (`security.access`). Se aplica como filtro de Qdrant y se verifica otra vez sobre
lo recuperado. Las membresías las fija la API desde la sesión (`state["memberships"]`); si
faltan o no son válidas, no se recupera nada (falla cerrada). Lo que el usuario no puede ver
es indistinguible de lo que no existe: el flujo aborta con la misma respuesta de "sin
información".

REGLA CRITICA #1 (cero alucinaciones - parte 1/2):
Si no queda ningún fragmento relevante, marca retrieval_ok=False.
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

from qdrant_client import AsyncQdrantClient

from pipeline_ai.config import settings
from pipeline_ai.embeddings import TaskPrefixedOllamaEmbeddings
from pipeline_ai.privacy.outbound_gateway import assign_source_aliases
from pipeline_ai.privacy.sensitivity import Sensitivity
from pipeline_ai.retrieval.hybrid_store import Candidate, HybridStore
from pipeline_ai.retrieval.judge import JudgeUnavailable, judge_relevance
from pipeline_ai.security.access import access_filter, can_view_document
from pipeline_ai.security.areas import fragment_area, fragment_sensitivity, from_state
from pipeline_ai.state import Citation, MHIAState

log = logging.getLogger("mhia.retriever")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

_NOTHING = {"retrieved_docs": [], "citations": [], "retrieval_ok": False}


@lru_cache
def _get_store() -> HybridStore:
    embeddings = TaskPrefixedOllamaEmbeddings(
        model=settings.ollama_embed_model, base_url=settings.ollama_base_url
    )
    client = AsyncQdrantClient(url=settings.qdrant_url)
    return HybridStore(client, settings.qdrant_collection, embeddings)


def _query_fingerprint(query: str) -> str:
    """Huella para correlacionar en logs sin escribir la consulta (puede traer datos reales)."""
    return f"len={len(query)} sha256={hashlib.sha256(query.encode('utf-8')).hexdigest()[:12]}"


def _select_by_threshold(candidates: list[Candidate]) -> list[Candidate]:
    """Criterio de respaldo: piso absoluto de coseno + margen relativo al mejor. Un candidato
    que solo encontró la parte léxica no tiene coseno y no puede pasar por este criterio."""
    accepted = [
        (doc, score)
        for doc, score in candidates
        if score is not None and score >= settings.retrieval_score_threshold
    ]
    # Minimización: a Claude solo viajan los fragmentos cercanos al mejor (el piso absoluto no
    # basta, ver config). Un margen <= 0 lo desactiva.
    if accepted and settings.retrieval_score_margin > 0:
        best = max(score for _, score in accepted if score is not None)
        accepted = [
            (doc, score)
            for doc, score in accepted
            if score is not None and score >= best - settings.retrieval_score_margin
        ]
    return accepted


async def _select(query: str, candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        return []
    if not settings.retrieval_judge_enabled:
        return _select_by_threshold(candidates)

    try:
        verdicts = await judge_relevance(query, [doc for doc, _ in candidates])
    except JudgeUnavailable as exc:
        log.warning("Juez de relevancia no disponible (%s): se usa el umbral de score.", exc)
        return _select_by_threshold(candidates)

    approved = [c for c, ok in zip(candidates, verdicts, strict=True) if ok]
    log.info(
        "Juez de relevancia (%s): %d de %d candidatos aprobados.",
        _query_fingerprint(query),
        len(approved),
        len(candidates),
    )
    return approved[: max(1, settings.retrieval_max_context)]


async def retriever_node(state: MHIAState) -> dict:
    query = state["query"]
    memberships = from_state(state.get("memberships"))

    flt = access_filter(memberships)
    if flt is None:
        # Sin membresías válidas no se consulta ni la base: falla cerrada.
        log.warning("Recuperación omitida: el usuario no tiene áreas visibles.")
        return dict(_NOTHING)

    results = await _get_store().search(
        query, access_filter=flt, k=max(1, settings.retrieval_candidates)
    )

    # Segunda capa: aunque el filtro de la base falle o se altere, lo que no es visible para
    # este usuario se descarta aquí, antes de juzgar o de armar contexto.
    visible = [
        (doc, score) for doc, score in results if can_view_document(memberships, doc.metadata)
    ]
    if len(visible) != len(results):
        log.error(
            "El filtro de Qdrant dejó pasar %d fragmento(s) no permitidos para el usuario; "
            "se descartaron.",
            len(results) - len(visible),
        )
    results = visible

    # Diagnóstico: siempre loguea los mejores cosenos encontrados, pase o no el criterio. Útil
    # para calibrar el respaldo con datos reales. La consulta NO se escribe (puede contener
    # datos reales): solo su huella.
    dense = [r for r in results if r[1] is not None]
    top = sorted(dense, key=lambda r: r[1], reverse=True)[:3]
    log.info(
        "Candidatos (%s, %d; umbral de respaldo=%.2f), top cosenos: %s",
        _query_fingerprint(query),
        len(results),
        settings.retrieval_score_threshold,
        [(round(score, 4), doc.metadata.get("source", "?")) for doc, score in top],
    )

    accepted = await _select(query, results)
    if not accepted:
        return dict(_NOTHING)

    docs = [doc for doc, _ in accepted]
    citations: list[Citation] = [
        {
            "source": doc.metadata.get("source", "desconocido"),
            "chunk_id": doc.metadata.get("chunk_id", ""),
            "score": float(score) if score is not None else 0.0,
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
    # documento"). Aquí se toma la sensibilidad REAL de cada fragmento recuperado
    # (mínima de su área y su etiqueta, security.areas.fragment_sensitivity). La
    # máxima viaja en `content_sensitivity` hasta la pasarela de salida: CONFIDENTIAL
    # obliga a anonimizar y RESTRICTED impide el envío a Claude. Nunca se relaja lo
    # que el router ya decidió, solo se puede volver más estricto.
    content_sensitivity = max(
        (fragment_sensitivity(doc.metadata) for doc in docs), default=Sensitivity.CONFIDENTIAL
    )
    is_confidential_by_content = content_sensitivity >= Sensitivity.CONFIDENTIAL
    is_confidential = state.get("is_confidential", False) or is_confidential_by_content

    if is_confidential_by_content and not state.get("is_confidential", False):
        log.info(
            "Confidencialidad elevada por contenido recuperado (áreas: %s, nivel %s), "
            "aunque el router había clasificado la consulta como no confidencial.",
            sorted({(a.value if (a := fragment_area(doc.metadata)) else "?") for doc in docs}),
            content_sensitivity.name,
        )

    return {
        "retrieved_docs": docs,
        "citations": citations,
        "retrieval_ok": True,
        "raw_context": raw_context,
        "source_aliases": source_aliases,
        "is_confidential": is_confidential,
        "content_sensitivity": int(content_sensitivity),
    }
