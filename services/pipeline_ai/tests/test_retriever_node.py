"""Retriever: respaldo por umbral (piso + margen), juez de relevancia local y RBAC por dominio."""

import logging

import pytest
from langchain_core.documents import Document

import pipeline_ai.nodes.retriever_node as retriever_mod
from pipeline_ai.retrieval.judge import JudgeUnavailable


class FakeStore:
    """Almacén falso. Por defecto IGNORA el filtro de dominios (simula un filtro roto o
    alterado) para comprobar la segunda capa; `honor_filter=True` lo aplica como Qdrant."""

    def __init__(self, scored, *, honor_filter=False):
        self.scored = scored
        self.honor_filter = honor_filter
        self.calls = []

    async def search(self, query, *, domains, k):
        self.calls.append({"query": query, "domains": tuple(domains), "k": k})
        if not self.honor_filter:
            return self.scored
        return [(d, s) for d, s in self.scored if d.metadata.get("domain") in set(domains)]


def _doc(name: str, domain: str | None = "general") -> Document:
    metadata = {"source": f"Google Drive: {name}", "chunk_id": name}
    if domain is not None:
        metadata["domain"] = domain
    return Document(page_content=f"contenido de {name}", metadata=metadata)


@pytest.fixture
def run(monkeypatch):
    async def _run(
        scores: dict[str, float | None],
        *,
        threshold=0.6,
        margin=0.06,
        role="lider",
        domains: dict[str, str | None] | None = None,
        honor_filter=False,
        query="pregunta",
        store_holder=None,
        judge=None,
        max_context=4,
    ):
        """`judge`: None = juez apagado (respaldo por umbral); una función (query, docs) ->
        list[bool] lo enciende y sustituye; una excepción lo enciende y hace que falle."""
        domains = domains or {}
        store = FakeStore(
            [(_doc(name, domains.get(name, "general")), score) for name, score in scores.items()],
            honor_filter=honor_filter,
        )
        if store_holder is not None:
            store_holder.append(store)
        monkeypatch.setattr(retriever_mod, "_get_store", lambda: store)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_score_threshold", threshold)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_score_margin", margin)
        monkeypatch.setattr(retriever_mod.settings, "retrieval_max_context", max_context)
        if judge is not None:
            monkeypatch.setattr(retriever_mod.settings, "retrieval_judge_enabled", True)

            async def fake_judge(q, docs):
                if isinstance(judge, BaseException):
                    raise judge
                return judge(q, list(docs))

            monkeypatch.setattr(retriever_mod, "judge_relevance", fake_judge)
        state = {"query": query}
        if role is not None:
            state["role"] = role
        return await retriever_mod.retriever_node(state)

    return _run


def _sources(result) -> list[str]:
    return [c["source"].removeprefix("Google Drive: ") for c in result["citations"]]


def _approve(*names: str):
    """Juez falso que aprueba solo los fragmentos cuyo nombre está en `names`."""
    return lambda _q, docs: [d.metadata["chunk_id"] in names for d in docs]


# -- Respaldo por umbral: piso absoluto + margen relativo (juez apagado) ---------------------


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


async def test_the_threshold_fallback_ignores_candidates_without_a_cosine_score(run):
    # Un candidato que solo encontró la parte léxica no tiene coseno: no puede pasar el umbral.
    result = await run({"a": 0.80, "solo_lexico": None})

    assert _sources(result) == ["a"]


# -- Juez de relevancia local ----------------------------------------------------------------


async def test_the_judge_decides_what_reaches_the_context_not_the_score(run):
    result = await run({"a": 0.90, "b": 0.80, "c": 0.70}, judge=_approve("b"))

    assert result["retrieval_ok"] is True
    assert _sources(result) == ["b"]


async def test_when_the_judge_rejects_everything_there_is_no_evidence_even_with_high_scores(run):
    result = await run({"a": 0.95, "b": 0.90}, judge=_approve())

    assert result == {"retrieved_docs": [], "citations": [], "retrieval_ok": False}


async def test_the_judge_rescues_a_relevant_fragment_below_the_old_score_floor(run):
    # Un positivo con coseno bajo (0.40) y un negativo alto (0.95): el umbral los ordena al revés.
    result = await run({"ruido": 0.95, "bueno": 0.40}, judge=_approve("bueno"))

    assert _sources(result) == ["bueno"]


async def test_a_lexical_only_candidate_approved_by_the_judge_is_kept_with_score_zero(run):
    result = await run({"a": 0.9, "solo_lexico": None}, judge=_approve("solo_lexico"))

    assert _sources(result) == ["solo_lexico"]
    assert result["citations"][0]["score"] == 0.0


async def test_approved_fragments_keep_the_fused_order_and_are_capped(run):
    result = await run(
        {"a": 0.9, "b": 0.8, "c": 0.7, "d": 0.6},
        judge=lambda _q, docs: [True] * len(docs),
        max_context=2,
    )

    assert _sources(result) == ["a", "b"]


@pytest.mark.parametrize("failure", [JudgeUnavailable("timeout"), JudgeUnavailable("salida")])
async def test_if_the_judge_fails_it_falls_back_to_the_threshold(run, failure, caplog):
    with caplog.at_level(logging.WARNING):
        result = await run({"a": 0.80, "b": 0.77, "c": 0.50}, judge=failure)

    assert result["retrieval_ok"] is True
    assert _sources(result) == ["a", "b"]
    assert "Juez de relevancia no disponible" in caplog.text


async def test_the_judge_never_sees_fragments_the_role_cannot_view(run):
    seen: list[str] = []

    def judge(_q, docs):
        seen.extend(d.metadata["chunk_id"] for d in docs)
        return [True] * len(docs)

    result = await run(
        {"publico": 0.8, "contrato": 0.95},
        role="consulta",
        domains={"contrato": "ventas"},
        honor_filter=False,  # el almacén "falla" y devuelve el de ventas
        judge=judge,
    )

    assert seen == ["publico"]
    assert _sources(result) == ["publico"]


async def test_the_judge_is_not_called_when_there_are_no_candidates(run):
    calls = []

    def judge(_q, docs):
        calls.append(docs)
        return []

    result = await run({}, judge=judge)

    assert result["retrieval_ok"] is False
    assert calls == []


async def test_the_judge_receives_the_query_and_the_store_gets_the_configured_k(run, monkeypatch):
    monkeypatch.setattr(retriever_mod.settings, "retrieval_candidates", 7)
    holder: list[FakeStore] = []
    queries: list[str] = []

    def judge(q, docs):
        queries.append(q)
        return [True] * len(docs)

    await run({"a": 0.9}, query="cuánto cobran de flete", judge=judge, store_holder=holder)

    assert queries == ["cuánto cobran de flete"]
    assert holder[0].calls[0]["k"] == 7


# -- RBAC: el rol decide qué contenido existe para la consulta --------------------------------


async def test_the_store_is_asked_for_exactly_the_domains_the_role_may_see(run):
    holder: list[FakeStore] = []
    await run({"a": 0.9}, role="consulta", store_holder=holder)

    [call] = holder[0].calls
    assert set(call["domains"]) == {"general"}

    holder.clear()
    await run({"a": 0.9}, role="lider", store_holder=holder)
    assert set(holder[0].calls[0]["domains"]) == {
        "general",
        "ventas",
        "inventario",
        "proveedores",
        "administracion",
        "proyectos",
    }


async def test_consulta_never_receives_confidential_fragments_when_the_store_honors_the_filter(
    run,
):
    result = await run(
        {"publico": 0.8, "contrato": 0.9},
        role="consulta",
        domains={"contrato": "ventas"},
        honor_filter=True,
    )

    assert _sources(result) == ["publico"]


async def test_second_layer_drops_forbidden_fragments_even_if_the_store_filter_fails(run):
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
    result = await run({"a": 0.99}, role=role, store_holder=holder, judge=_approve("a"))

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


@pytest.mark.parametrize("use_judge", [False, True])
async def test_the_raw_query_is_never_written_to_the_logs(run, caplog, use_judge):
    secret_query = "¿Cuánto debe Acme S.A. por el contrato 034?"
    with caplog.at_level(logging.DEBUG):
        await run(
            {"a": 0.9},
            query=secret_query,
            judge=_approve("a") if use_judge else None,
        )

    assert "Acme" not in caplog.text and "contrato 034" not in caplog.text
    assert f"len={len(secret_query)}" in caplog.text and "sha256=" in caplog.text
