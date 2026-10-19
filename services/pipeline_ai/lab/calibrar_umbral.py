"""Calibracion del umbral de relevancia (RETRIEVAL_SCORE_THRESHOLD) con el laboratorio.

Para cada pregunta de retrieval_golden.json (`expected_sources`: archivos que SI deben salir;
lista vacia = no hay documento relevante y debe quedar sin evidencia), consulta Qdrant (sin Claude, sin anonimizador: es barato) y mide:

  - positivos: el score del mejor documento esperado y su posicion en el ranking;
  - negativos: el score del mejor documento recuperado (todo lo que salga es ruido).

Con eso calcula el rango de umbrales que separa limpiamente positivos de negativos (si
existe) y muestra cuantos fragmentos irrelevantes dejaria pasar cada umbral candidato.

  python lab\\calibrar_umbral.py            # tabla + recomendacion
  python lab\\calibrar_umbral.py --json     # ademas guarda lab/results/umbral_*.json

Usa los documentos SINTETICOS de lab/docs y el Qdrant desechable del laboratorio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime

import _env  # DEBE ir antes que pipeline_ai: fija QDRANT_URL/COLLECTION antes de leer settings

# isort: split
from pipeline_ai.nodes.retriever_node import _get_vectorstore

QUESTIONS_FILE = _env.LAB_DIR / "retrieval_golden.json"
CANDIDATES = (0.50, 0.55, 0.60, 0.62, 0.64, 0.66, 0.68, 0.70, 0.72)
K = 8  # el laboratorio tiene 8 documentos: se ve el ranking completo


def _short(source: str) -> str:
    return source.removeprefix("Google Drive: ")


async def _measure() -> list[dict]:
    store = _get_vectorstore()
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    rows: list[dict] = []
    for question in questions:
        expected = question["expected_sources"]
        results = await store.asimilarity_search_with_score(question["query"], k=K)
        ranking = sorted(
            ((_short(doc.metadata.get("source", "?")), float(score)) for doc, score in results),
            key=lambda item: item[1],
            reverse=True,
        )
        hits = [
            (rank, name, score)
            for rank, (name, score) in enumerate(ranking, start=1)
            if name in expected
        ]
        rows.append(
            {
                "id": question["id"],
                "query": question["query"],
                "expected": expected,
                "ranking": ranking,
                "best_expected": hits[0] if hits else None,
                "top1": ranking[0] if ranking else None,
            }
        )
    return rows


def _report(rows: list[dict]) -> dict:
    positives = [r for r in rows if r["expected"]]
    negatives = [r for r in rows if not r["expected"]]

    print(f"\n{'id':<5}{'tipo':<6}{'mejor esperado (pos, score)':<30}{'top1 (score)':<46}")
    print("-" * 87)
    for row in rows:
        kind = "pos" if row["expected"] else "neg"
        best = row["best_expected"]
        best_txt = f"#{best[0]} {best[2]:.4f}" if best else "-"
        top = row["top1"]
        top_txt = f"{top[1]:.4f} {top[0]}" if top else "-"
        print(f"{row['id']:<5}{kind:<6}{best_txt:<30}{top_txt:<46}")

    missed = [r["id"] for r in positives if r["best_expected"] is None]
    not_first = [r["id"] for r in positives if r["best_expected"] and r["best_expected"][0] > 1]
    pos_scores = [r["best_expected"][2] for r in positives if r["best_expected"]]
    neg_scores = [r["top1"][1] for r in negatives if r["top1"]]
    lowest_pos = min(pos_scores) if pos_scores else 0.0
    highest_neg = max(neg_scores) if neg_scores else 0.0

    print(f"\npositivos: {len(positives)}   negativos: {len(negatives)}")
    print(f"score minimo de un esperado : {lowest_pos:.4f}")
    print(f"score maximo de un negativo  : {highest_neg:.4f}")
    if missed:
        print(f"preguntas cuyo documento esperado no aparece en el ranking: {missed}")
    if not_first:
        print(f"preguntas donde el esperado no es el #1: {not_first}")

    print(f"\n{'umbral':<8}{'positivos conservados':<24}{'negativos que pasan':<22}")
    for threshold in CANDIDATES:
        kept = sum(1 for s in pos_scores if s >= threshold)
        leaked = sum(1 for s in neg_scores if s >= threshold)
        print(
            f"{threshold:<8.2f}{f'{kept}/{len(pos_scores)}':<24}{f'{leaked}/{len(neg_scores)}':<22}"
        )

    separable = highest_neg < lowest_pos
    margin = lowest_pos - highest_neg
    recommended = round((lowest_pos + highest_neg) / 2, 2) if separable else None
    if separable:
        print(f"\nSe separan limpiamente (margen {margin:.4f}). Punto medio: {recommended}")
    else:
        print(
            f"\nNO se separan: un negativo ({highest_neg:.4f}) supera a un positivo ({lowest_pos:.4f})."
        )
    return {
        "lowest_positive": lowest_pos,
        "highest_negative": highest_neg,
        "separable": separable,
        "recommended": recommended,
        "missed": missed,
        "not_first": not_first,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="guarda el detalle en lab/results/")
    args = parser.parse_args()

    rows = asyncio.run(_measure())
    summary = _report(rows)
    if args.json:
        _env.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
        path = _env.RESULTS_DIR / f"umbral_{datetime.now():%Y%m%d_%H%M%S}.json"
        path.write_text(
            json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"detalle: {path}")


if __name__ == "__main__":
    main()
