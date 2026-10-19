"""Retriever: filtro de relevancia (piso + margen) y RBAC por dominio (filtro + segunda capa)."""

import logging

import pytest
from langchain_core.documents import Document

import pipeline_ai.nodes.retriever_node as retriever_mod


class FakeStore:
    """Qdrant falso. Por defecto IGNORA el filtro (simula un filtro roto o alterado) para
    comprobar la segunda capa; `honor_filter=True` lo aplica como lo haría Qdrant."""

    def __init__(self, scored, *, honor_filter=False):
        self.scored = scored
        self.honor_filter = honor_filter
        self.calls = []

    async def asimilarity_search_with_score(self, query, k, filter=None):
        self.calls.append({"query": query, "k": k, "filter": filter})
        if not self.honor_filter or filter is None:
            return self.scored
        allowed = set(filter.must[0].match.any)
        return [(d, s) for d, s in self.scored if d.metadata.get("domain") in allowed]


def _doc(name: str, domain: str | None = "general") -> Document:
    metadata = {"source": f"Google Drive: {name}", "chunk_id": name}
    if domain is not None:
        metadata["domain"] = domain
    return Document(page_content=f"contenido de {name}", metadata=metadata)


@pytest.fixture
def run(monkeypatch):
    async def _run(
        scores: dict[str, float],
        *,
        threshold=0.6,
        margin=0.06,
        role="lider",
        domains: dict[str, str | None] | None = None,
        honor_filter=False,
        query="pregunta",
        store_holder=None,
    ):
        domains = domains or {}
        store = FakeStore(
            [(_doc(name, domains.get(name, "general")), score) for name, score in scores.items()],
            honor_filter=honor_filter,
        )
        if store_holder is not None:
            store_holder.append(store)
        monkeypatch.setattr(retriever_mod, "_get_vectorstore", lambda: store)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_score_threshold", threshold)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_score_margin", margin)
        state = {"query": query}
        if role is not None:
            state["role"] = role
        return await retriever_mod.retriever_node(state)

    return _run


def _sources(result) -> list[str]:
    return [c["source"].removeprefix("Google Drive: ") for c in result["citations"]]


# -- Relevancia: piso absoluto + margen relativo ----------------------------------------------


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


# -- RBAC: el rol decide qué contenido existe para la consulta ----------------------------------


async def test_the_qdrant_filter_lists_exactly_the_domains_the_role_may_see(run):
    holder: list[FakeStore] = []
    await run({"a": 0.9}, role="consulta", store_holder=holder)

    [call] = holder[0].calls
    condition = call["filter"].must[0]
    assert condition.key == "metadata.domain"
    assert set(condition.match.any) == {"general"}

    holder.clear()
    await run({"a": 0.9}, role="lider", store_holder=holder)
    assert set(holder[0].calls[0]["filter"].must[0].match.any) == {
        "general",
        "ventas",
        "inventario",
        "proveedores",
        "administracion",
    }


async def test_consulta_never_receives_confidential_fragments_when_qdrant_honors_the_filter(run):
    result = await run(
        {"publico": 0.8, "contrato": 0.9},
        role="consulta",
        domains={"contrato": "ventas"},
        honor_filter=True,
    )

    assert _sources(result) == ["publico"]


async def test_second_layer_drops_forbidden_fragments_even_if_the_qdrant_filter_fails(run):
    # El "Qdrant" ignora el filtro y devuelve un fragmento de ventas con el mejor score.
    result = await run(
        {"publico": 0.8, "contrato": 0.95},
        role="consulta",
        domains={"contrato": "ventas"},
        honor_filter=False,
    )

    assert result["retrieval_ok"] is True
    assert _sources(result) == ["publico"]
    assert "contrato" not in str(result["raw_context"])


async def test_when_everything_retrieved_is_forbidden_the_answer_is_no_evidence(run):
    # Lo que el rol no puede ver es indistinguible de lo que no existe.
    result = await run({"contrato": 0.95}, role="consulta", domains={"contrato": "proveedores"})

    assert result == {"retrieved_docs": [], "citations": [], "retrieval_ok": False}


@pytest.mark.parametrize("role", [None, "", "root", "CONSULTA "])
async def test_without_a_valid_role_nothing_is_retrieved_and_the_store_is_not_queried(run, role):
    holder: list[FakeStore] = []
    result = await run({"a": 0.99}, role=role, store_holder=holder)

    assert result["retrieval_ok"] is False
    assert result["citations"] == []
    assert holder[0].calls == []


@pytest.mark.parametrize("domain", [None, "", "rrhh", "GENERAL"])
async def test_fragments_without_a_recognised_domain_are_not_visible_to_anyone(run, domain):
    result = await run({"a": 0.9}, role="control_total", domains={"a": domain})

    assert result["retrieval_ok"] is False


@pytest.mark.parametrize("role", ["lider", "configuracion", "control_total"])
async def test_confidential_roles_do_see_confidential_domains(run, role):
    result = await run({"a": 0.9}, role=role, domains={"a": "ventas"})

    assert result["retrieval_ok"] is True
    assert result["is_confidential"] is True


# -- Privacidad en logs ------------------------------------------------------------------------


async def test_the_raw_query_is_never_written_to_the_logs(run, caplog):
    secret_query = "¿Cuánto debe Acme S.A. por el contrato 034?"
    with caplog.at_level(logging.DEBUG):
        await run({"a": 0.9}, query=secret_query)

    assert "Acme" not in caplog.text and "contrato 034" not in caplog.text
    assert f"len={len(secret_query)}" in caplog.text and "sha256=" in caplog.text
