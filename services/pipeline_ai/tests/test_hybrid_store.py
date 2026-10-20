"""Almacén híbrido: consultas densa + léxica con el mismo filtro de rol, fusión RRF y esquema."""

import logging
from types import SimpleNamespace

from langchain_core.documents import Document
from qdrant_client.http import models as qmodels

from pipeline_ai.retrieval.collection import (
    DENSE_VECTOR,
    SPARSE_VECTOR,
    build_point,
    is_hybrid,
)
from pipeline_ai.retrieval.hybrid_store import HybridStore, domain_filter, rrf_order
from pipeline_ai.retrieval.lexical import query_sparse_vector


def _info(*, hybrid=True, named_dense=True):
    vectors = {DENSE_VECTOR: object()} if named_dense else object()
    sparse = {SPARSE_VECTOR: object()} if hybrid else None
    return SimpleNamespace(
        config=SimpleNamespace(params=SimpleNamespace(vectors=vectors, sparse_vectors=sparse))
    )


def _point(pid, score, name=None, *, domain="general", payload=None):
    if payload is None:
        payload = {
            "page_content": f"contenido de {name or pid}",
            "metadata": {"source": f"Google Drive: {name or pid}", "domain": domain},
        }
    return SimpleNamespace(id=pid, score=score, payload=payload)


class FakeClient:
    def __init__(self, *, dense, sparse=(), hybrid=True):
        self.dense, self.sparse, self.hybrid = list(dense), list(sparse), hybrid
        self.calls = []
        self.info_calls = 0

    async def get_collection(self, name):
        self.info_calls += 1
        return _info(hybrid=self.hybrid, named_dense=self.hybrid)

    async def query_points(self, **kwargs):
        self.calls.append(kwargs)
        is_sparse = kwargs["using"] == SPARSE_VECTOR
        return SimpleNamespace(points=self.sparse if is_sparse else self.dense)


class FakeEmbeddings:
    async def aembed_query(self, text):
        return [0.1, 0.2, 0.3]


def _store(client):
    return HybridStore(client, "col", FakeEmbeddings())


def _sources(candidates):
    return [doc.metadata["source"].removeprefix("Google Drive: ") for doc, _ in candidates]


# -- Fusión ------------------------------------------------------------------------------------


def test_rrf_lifts_what_both_lists_agree_on():
    assert rrf_order(["a", "b", "c"], ["c"]) == ["c", "a", "b"]


def test_rrf_ties_go_to_the_first_seen():
    assert rrf_order(["a"], ["b"]) == ["a", "b"]


def test_rrf_of_nothing_is_empty():
    assert rrf_order([], []) == []


# -- Consultas ---------------------------------------------------------------------------------


async def test_both_queries_apply_the_same_domain_filter_and_use_their_own_vector():
    client = FakeClient(dense=[_point("a", 0.8)], sparse=[_point("a", 3.0)])
    await _store(client).search("tarifas del flete", domains=("general", "ventas"), k=3)

    dense_call, sparse_call = client.calls
    assert dense_call["using"] == DENSE_VECTOR and dense_call["query"] == [0.1, 0.2, 0.3]
    assert sparse_call["using"] == SPARSE_VECTOR
    indices, _ = query_sparse_vector("tarifas del flete")
    assert sparse_call["query"].indices == indices
    for call in client.calls:
        condition = call["query_filter"].must[0]
        assert condition.key == "metadata.domain"
        assert set(condition.match.any) == {"general", "ventas"}
        assert call["with_payload"] is True


async def test_keywords_lift_a_candidate_the_dense_search_ranked_low():
    client = FakeClient(
        dense=[_point("a", 0.80), _point("b", 0.75), _point("c", 0.55)],
        sparse=[_point("c", 4.0)],
    )
    result = await _store(client).search("pregunta", domains=("general",), k=3)

    assert _sources(result) == ["c", "a", "b"]


async def test_candidates_keep_their_cosine_and_lexical_only_ones_have_none():
    client = FakeClient(dense=[_point("a", 0.80)], sparse=[_point("a", 2.0), _point("solo", 5.0)])
    result = await _store(client).search("pregunta", domains=("general",), k=5)

    scores = {doc.metadata["source"].removeprefix("Google Drive: "): s for doc, s in result}
    assert scores == {"a": 0.80, "solo": None}


async def test_lexical_points_without_any_match_do_not_count():
    client = FakeClient(dense=[_point("a", 0.8), _point("b", 0.7)], sparse=[_point("b", 0.0)])
    result = await _store(client).search("pregunta", domains=("general",), k=5)

    assert _sources(result) == ["a", "b"]  # el score léxico 0 no adelanta a "b"


async def test_the_result_is_capped_at_k():
    client = FakeClient(dense=[_point(str(i), 0.9 - i / 100) for i in range(10)])
    result = await _store(client).search("pregunta", domains=("general",), k=3)

    assert len(result) == 3


async def test_a_query_without_useful_terms_skips_the_lexical_search():
    client = FakeClient(dense=[_point("a", 0.8)])
    result = await _store(client).search("¿qué es lo de la?", domains=("general",), k=3)

    assert [c["using"] for c in client.calls] == [DENSE_VECTOR]
    assert _sources(result) == ["a"]


async def test_points_without_a_valid_payload_are_skipped():
    client = FakeClient(
        dense=[
            _point("a", 0.9),
            _point("roto", 0.8, payload={}),
            _point("sin_metadata", 0.7, payload={"page_content": "x"}),
        ]
    )
    result = await _store(client).search("pregunta", domains=("general",), k=5)

    assert _sources(result) == ["a"]


async def test_the_collection_schema_is_checked_once(caplog):
    client = FakeClient(dense=[_point("a", 0.8)])
    store = _store(client)
    await store.search("uno", domains=("general",), k=2)
    await store.search("dos", domains=("general",), k=2)

    assert client.info_calls == 1


# -- Colección anterior (solo denso) -----------------------------------------------------------


async def test_a_legacy_collection_is_searched_dense_only_and_warns(caplog):
    client = FakeClient(dense=[_point("a", 0.8), _point("b", 0.7)], hybrid=False)
    with caplog.at_level(logging.WARNING):
        result = await _store(client).search("tarifas", domains=("general",), k=5)

    [call] = client.calls
    assert call["using"] is None  # vector sin nombre
    assert call["query_filter"].must[0].key == "metadata.domain"  # el RBAC sigue aplicando
    assert _sources(result) == ["a", "b"]
    assert "no tiene vectores híbridos" in caplog.text


# -- Esquema -----------------------------------------------------------------------------------


def test_is_hybrid_needs_a_named_dense_vector_and_a_sparse_one():
    assert is_hybrid(_info()) is True
    assert is_hybrid(_info(hybrid=False, named_dense=False)) is False  # esquema anterior
    assert is_hybrid(_info(hybrid=False, named_dense=True)) is False  # sin parte léxica


def test_build_point_carries_both_vectors_and_the_original_payload():
    doc = Document(
        page_content="La cotización de la excavadora pide anticipo.",
        metadata={"source": "Google Drive: c.txt", "chunk_id": "f::0", "domain": "ventas"},
    )
    point = build_point("id-1", doc, [0.1, 0.2])

    assert point.id == "id-1"
    assert point.vector[DENSE_VECTOR] == [0.1, 0.2]
    sparse = point.vector[SPARSE_VECTOR]
    assert isinstance(sparse, qmodels.SparseVector)
    assert sparse.indices and len(sparse.indices) == len(sparse.values)
    assert point.payload == {"page_content": doc.page_content, "metadata": doc.metadata}


def test_domain_filter_lists_the_allowed_domains():
    condition = domain_filter(["general", "ventas"]).must[0]

    assert condition.key == "metadata.domain"
    assert condition.match.any == ["general", "ventas"]
