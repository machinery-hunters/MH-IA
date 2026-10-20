"""Mide el juez de relevancia local (retrieval/judge.py) fuera del retriever.

Para cada pregunta del golden set y CADA chunk del laboratorio (hay pocos) pregunta al modelo
local si el fragmento sirve para responderla, con el mismo codigo de produccion. Mide:

  - recall    : de los chunks esperados, cuantos aprueba
  - precision : de los que aprueba, cuantos eran esperados
  - ruido     : en preguntas sin documento (negativas), cuantos chunks aprueba (deberian ser 0)

Util para comparar versiones del prompt o del modelo (OLLAMA_LLM_MODEL) antes de cambiarlos.

  python lab\\experimento_juez.py
"""

from __future__ import annotations

import asyncio
import json
import time

import _env  # DEBE ir antes que pipeline_ai: fija QDRANT_URL/COLLECTION antes de leer settings

# isort: split
from langchain_core.documents import Document
from qdrant_client import QdrantClient

from pipeline_ai.config import settings
from pipeline_ai.retrieval.judge import judge_relevance

GOLDEN = _env.LAB_DIR / "retrieval_golden.json"


async def main() -> None:
    client = QdrantClient(url=settings.qdrant_url)
    points, _ = client.scroll(settings.qdrant_collection, limit=1000, with_payload=True)
    chunks = [
        (
            p.payload["metadata"]["source"].removeprefix("Google Drive: "),
            Document(page_content=p.payload["page_content"], metadata=p.payload["metadata"]),
        )
        for p in points
    ]
    questions = json.loads(GOLDEN.read_text(encoding="utf-8"))

    started = time.perf_counter()
    tp = fp = fn = noise = 0
    for q in questions:
        verdicts = await judge_relevance(q["query"], [doc for _, doc in chunks])
        approved = {src for (src, _), ok in zip(chunks, verdicts, strict=True) if ok}
        expected = set(q["expected_sources"])
        tp += len(approved & expected)
        fp += len(approved - expected)
        fn += len(expected - approved)
        if not expected:
            noise += len(approved)
        flag = "" if approved == expected else "  <-- difiere"
        print(f"{q['id']:<4} esperado={sorted(expected)} aprobado={sorted(approved)}{flag}")

    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    print(f"\nrecall={recall:.2f}  precision={precision:.2f}  (tp={tp} fp={fp} fn={fn})")
    print(f"chunks aprobados en preguntas sin documento: {noise}")
    print(f"{len(questions) * len(chunks)} juicios en {time.perf_counter() - started:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
