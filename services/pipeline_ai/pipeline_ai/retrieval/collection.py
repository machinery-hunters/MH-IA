"""
collection

Esquema de la colección de Qdrant para la búsqueda híbrida. Lo comparten la ingesta de
producción (ingestion/indexer.py), la del laboratorio y el almacén de consulta, para que no
puedan divergir:

  - vector denso con nombre "dense" (coseno), el de nomic-embed-text de siempre
  - vector disperso "bm25" con Modifier.IDF (palabras clave; ver retrieval/lexical.py)

Una colección creada antes de este cambio tiene un único vector sin nombre y NO es híbrida:
el almacén de consulta sigue funcionando con ella (solo denso) y la ingesta se niega a mezclar
puntos de esquemas distintos hasta que se reindexe.
"""

from __future__ import annotations

from typing import Any

from langchain_core.documents import Document
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from pipeline_ai.retrieval.lexical import document_sparse_vector

DENSE_VECTOR = "dense"
SPARSE_VECTOR = "bm25"


def create_collection(client: QdrantClient, name: str, dim: int) -> None:
    client.create_collection(
        collection_name=name,
        vectors_config={
            DENSE_VECTOR: qmodels.VectorParams(size=dim, distance=qmodels.Distance.COSINE)
        },
        sparse_vectors_config={
            SPARSE_VECTOR: qmodels.SparseVectorParams(modifier=qmodels.Modifier.IDF)
        },
    )


def is_hybrid(info: Any) -> bool:
    """True si la colección (resultado de get_collection) tiene el esquema híbrido."""
    params = info.config.params
    vectors = params.vectors
    sparse = params.sparse_vectors or {}
    return isinstance(vectors, dict) and DENSE_VECTOR in vectors and SPARSE_VECTOR in sparse


def build_point(point_id: str, doc: Document, dense: list[float]) -> qmodels.PointStruct:
    indices, values = document_sparse_vector(doc.page_content)
    return qmodels.PointStruct(
        id=point_id,
        vector={
            DENSE_VECTOR: dense,
            SPARSE_VECTOR: qmodels.SparseVector(indices=indices, values=values),
        },
        payload={"page_content": doc.page_content, "metadata": doc.metadata},
    )
