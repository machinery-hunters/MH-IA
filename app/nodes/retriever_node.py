"""
retriever_node

Consulta Qdrant (embeddings generados localmente vía Ollama). Aplica el
umbral de score de settings.retrieval_score_threshold.

REGLA CRÍTICA #1 (cero alucinaciones - parte 1/2):
Si no hay documentos por encima del umbral, marca retrieval_ok=False.
El grafo debe leer este flag en su edge condicional y saltar directo al
nodo de aborto, SIN pasar por Claude.
"""

from functools import lru_cache

from langchain_ollama import OllamaEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from app.config import settings
from app.state import Citation, MHIAState


@lru_cache
def _get_vectorstore() -> QdrantVectorStore:
    embeddings = OllamaEmbeddings(
        model=settings.ollama_embed_model, base_url=settings.ollama_base_url
    )
    client = QdrantClient(url=settings.qdrant_url)
    return QdrantVectorStore(
        client=client,
        collection_name=settings.qdrant_collection,
        embedding=embeddings,
    )


async def retriever_node(state: MHIAState) -> dict:
    query = state["query"]

    results = await _get_vectorstore().asimilarity_search_with_score(query, k=5)

    accepted = [
        (doc, score) for doc, score in results if score >= settings.retrieval_score_threshold
    ]

    if not accepted:
        return {
            "retrieved_docs": [],
            "citations": [],
            "retrieval_ok": False,
        }

    docs = [doc for doc, _ in accepted]
    citations: list[Citation] = [
        {
            "source": doc.metadata.get("source", "desconocido"),
            "chunk_id": doc.metadata.get("chunk_id", ""),
            "score": float(score),
        }
        for doc, score in accepted
    ]

    raw_context = "\n\n---\n\n".join(
        f"[Fuente: {c['source']}]\n{doc.page_content}"
        for doc, c in zip(docs, citations, strict=True)
    )

    return {
        "retrieved_docs": docs,
        "citations": citations,
        "retrieval_ok": True,
        "raw_context": raw_context,
    }
