"""
hybrid_store

Búsqueda híbrida sobre Qdrant: una consulta densa (significado) y una dispersa (palabras
clave, BM25), ambas con el MISMO filtro de dominios del rol, fusionadas con Reciprocal Rank
Fusion. RRF solo usa posiciones, así que no depende de que los dos scores estén en la misma
escala.

Cada candidato conserva su similitud coseno densa (o None si solo lo encontró la parte léxica):
el retriever la usa como respaldo cuando el juez de relevancia no está disponible.

El filtro de dominio es la primera capa del RBAC y se aplica DENTRO de Qdrant en ambas
consultas; el retriever vuelve a verificar lo recuperado (segunda capa).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from langchain_core.documents import Document
from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qmodels

from pipeline_ai.retrieval.collection import DENSE_VECTOR, SPARSE_VECTOR, is_hybrid
from pipeline_ai.retrieval.lexical import query_sparse_vector

log = logging.getLogger("mhia.retriever")

RRF_K = 60

Candidate = tuple[Document, float | None]


def domain_filter(domains: Sequence[str]) -> qmodels.Filter:
    """Solo fragmentos cuyo dominio está permitido. Un fragmento sin `metadata.domain` no
    cumple MatchAny, así que queda fuera (falla cerrada)."""
    return qmodels.Filter(
        must=[
            qmodels.FieldCondition(
                key="metadata.domain",
                match=qmodels.MatchAny(any=list(domains)),
            )
        ]
    )


def rrf_order(*rankings: Sequence[str]) -> list[str]:
    """Reciprocal Rank Fusion: ids ordenados por la suma de 1/(RRF_K + posición). Los empates
    se resuelven por aparición (primero el de la primera lista)."""
    scores: dict[str, float] = {}
    first_seen: dict[str, int] = {}
    for ranking in rankings:
        for position, point_id in enumerate(ranking, start=1):
            scores[point_id] = scores.get(point_id, 0.0) + 1.0 / (RRF_K + position)
            first_seen.setdefault(point_id, len(first_seen))
    return sorted(scores, key=lambda pid: (-scores[pid], first_seen[pid]))


def _document(point: qmodels.ScoredPoint) -> Document | None:
    payload = point.payload or {}
    content = payload.get("page_content")
    metadata = payload.get("metadata")
    if not isinstance(content, str) or not isinstance(metadata, dict):
        return None
    return Document(page_content=content, metadata=metadata)


class HybridStore:
    def __init__(self, client: AsyncQdrantClient, collection: str, embeddings) -> None:
        self._client = client
        self._collection = collection
        self._embeddings = embeddings
        self._hybrid: bool | None = None

    async def _is_hybrid(self) -> bool:
        if self._hybrid is None:
            info = await self._client.get_collection(self._collection)
            self._hybrid = is_hybrid(info)
            if not self._hybrid:
                log.warning(
                    "La colección '%s' no tiene vectores híbridos: se busca solo por "
                    "significado. Reindexa (colección nueva) para activar las palabras clave.",
                    self._collection,
                )
        return self._hybrid

    async def search(self, query: str, *, domains: Sequence[str], k: int) -> list[Candidate]:
        """Hasta `k` candidatos en orden de relevancia fusionada, ya filtrados por dominio."""
        flt = domain_filter(domains)
        pool = max(k * 3, 20)
        vector = await self._embeddings.aembed_query(query)
        hybrid = await self._is_hybrid()

        dense_call = self._client.query_points(
            collection_name=self._collection,
            query=vector,
            using=DENSE_VECTOR if hybrid else None,
            query_filter=flt,
            limit=pool,
            with_payload=True,
        )
        indices, values = query_sparse_vector(query) if hybrid else ([], [])
        if indices:
            sparse_call = self._client.query_points(
                collection_name=self._collection,
                query=qmodels.SparseVector(indices=indices, values=values),
                using=SPARSE_VECTOR,
                query_filter=flt,
                limit=pool,
                with_payload=True,
            )
            dense_resp, sparse_resp = await asyncio.gather(dense_call, sparse_call)
            sparse_points = sparse_resp.points
        else:
            dense_resp = await dense_call
            sparse_points = []

        dense_points = dense_resp.points
        by_id: dict[str, qmodels.ScoredPoint] = {}
        for point in [*dense_points, *sparse_points]:
            by_id.setdefault(str(point.id), point)
        dense_score = {str(p.id): float(p.score) for p in dense_points}

        # Un punto sin coincidencias léxicas no aporta a la lista dispersa (score 0).
        sparse_ids = [str(p.id) for p in sparse_points if p.score > 0]
        order = rrf_order([str(p.id) for p in dense_points], sparse_ids)

        candidates: list[Candidate] = []
        for point_id in order:
            doc = _document(by_id[point_id])
            if doc is not None:
                candidates.append((doc, dense_score.get(point_id)))
            if len(candidates) == k:
                break
        return candidates
