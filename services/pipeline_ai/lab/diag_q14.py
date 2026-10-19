"""Diagnostico puntual: en que nodo se cuelga una consulta (router, retriever o anonimizador)."""

from __future__ import annotations

import asyncio
import sys
import time

import _env  # noqa: F401  (efecto: fija QDRANT/OLLAMA antes de importar pipeline_ai)

# isort: split
from pipeline_ai.nodes.ollama_node import ollama_anonymizer_node
from pipeline_ai.nodes.retriever_node import retriever_node
from pipeline_ai.nodes.router import router_node

QUERY = sys.argv[1] if len(sys.argv) > 1 else "resume el último documento"
STEP_TIMEOUT_S = 60


async def step(name: str, coro):
    started = time.perf_counter()
    try:
        result = await asyncio.wait_for(coro, timeout=STEP_TIMEOUT_S)
        print(f"{name:<12} ok     {time.perf_counter() - started:5.1f}s")
        return result
    except TimeoutError:
        print(f"{name:<12} TIMEOUT {STEP_TIMEOUT_S}s  <-- aqui se cuelga")
        return None


async def main() -> None:
    print(f"consulta: {QUERY!r}")
    state: dict = {"messages": [], "query": QUERY, "session_id": "diag"}

    out = await step("router", router_node(state))
    if out is None:
        return
    state.update(out)
    print(f"   dominio={state['data_domain']} confidencial={state['is_confidential']}")

    out = await step("retriever", retriever_node(state))
    if out is None:
        return
    state.update(out)
    print(f"   retrieval_ok={state.get('retrieval_ok')} citas={len(state.get('citations', []))}")
    if not state.get("retrieval_ok"):
        return

    out = await step("anonymizer", ollama_anonymizer_node(state))
    if out is None:
        return
    state.update(out)
    print(
        f"   anonymized={state.get('anonymized')} largo={len(state.get('anonymized_context', ''))}"
    )


if __name__ == "__main__":
    asyncio.run(main())
