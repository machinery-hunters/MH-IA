"""Filtro de relevancia del retriever: piso absoluto + margen relativo al mejor fragmento."""

import pytest
from langchain_core.documents import Document

import pipeline_ai.nodes.retriever_node as retriever_mod


class FakeStore:
    def __init__(self, scored):
        self.scored = scored

    async def asimilarity_search_with_score(self, query, k):
        return self.scored


def _doc(name: str) -> Document:
    return Document(
        page_content=f"contenido de {name}",
        metadata={"source": f"Google Drive: {name}", "chunk_id": name, "domain": "general"},
    )


@pytest.fixture
def run(monkeypatch):
    async def _run(scores: dict[str, float], *, threshold=0.6, margin=0.06):
        store = FakeStore([(_doc(name), score) for name, score in scores.items()])
        monkeypatch.setattr(retriever_mod, "_get_vectorstore", lambda: store)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_score_threshold", threshold)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_score_margin", margin)
        return await retriever_mod.retriever_node({"query": "pregunta"})

    return _run


def _sources(result) -> list[str]:
    return [c["source"].removeprefix("Google Drive: ") for c in result["citations"]]


async def test_margin_keeps_only_fragments_close_to_the_best(run):
    result = await run({"a": 0.80, "b": 0.77, "c": 0.70, "d": 0.62})

    assert result["retrieval_ok"] is True
    assert _sources(result) == ["a", "b"]


async def test_margin_zero_disables_the_relative_cut(run):
    result = await run({"a": 0.80, "b": 0.77, "c": 0.70, "d": 0.62}, margin=0)

    assert _sources(result) == ["a", "b", "c", "d"]


async def test_absolute_floor_still_discards_everything_below_it(run):
    result = await run({"a": 0.59, "b": 0.55})

    assert result["retrieval_ok"] is False
    assert result["citations"] == []


async def test_margin_is_measured_from_the_best_accepted_fragment(run):
    # El mejor (0.65) fija el margen; 0.58 ya no pasa el piso y 0.60 queda dentro del margen.
    result = await run({"a": 0.65, "b": 0.60, "c": 0.58})

    assert _sources(result) == ["a", "b"]
