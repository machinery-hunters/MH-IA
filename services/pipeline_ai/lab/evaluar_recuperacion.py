"""Evaluacion de punta a punta de la RECUPERACION (retriever_node real, sin Claude).

Para cada pregunta de lab/retrieval_golden.json corre `retriever_node` con el rol indicado y
compara las fuentes que llegarian al contexto contra `expected_sources`:

  - recall    : fuentes esperadas que si llegan
  - precision : fuentes que llegan que eran esperadas (ruido = lo que sobra)
  - sin_evid  : en preguntas sin documento, cuantas quedan correctamente sin evidencia

  python lab\\evaluar_recuperacion.py --etiqueta antes
  python lab\\evaluar_recuperacion.py --etiqueta despues --rol lider

Con datos REALES ya ingestados en otra coleccion del Qdrant local (preguntas escritas por ti en
lab/real_golden.json, que git ignora; formato en lab/real_golden.example.json):

  $env:LAB_COLLECTION='mhia_real_eval'
  python lab\\evaluar_recuperacion.py --golden lab\\real_golden.json --etiqueta real --rol lider

Guarda lab/results/recuperacion_<etiqueta>.json (solo ids y nombres de archivo; git lo ignora)
para comparar corridas. Por defecto usa los documentos SINTETICOS del laboratorio.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

import _env  # DEBE ir antes que pipeline_ai: fija QDRANT_URL/COLLECTION antes de leer settings

# isort: split
from pipeline_ai.nodes.retriever_node import retriever_node

GOLDEN = _env.LAB_DIR / "retrieval_golden.json"


def _short(source: str) -> str:
    return source.removeprefix("Google Drive: ")


async def _run(role: str, golden: Path) -> list[dict]:
    questions = json.loads(golden.read_text(encoding="utf-8"))
    rows = []
    for q in questions:
        started = time.perf_counter()
        out = await retriever_node(
            {"query": q["query"], "role": role, "memberships": _env.lab_memberships(role)}
        )
        elapsed = time.perf_counter() - started
        got = sorted({_short(c["source"]) for c in out.get("citations", [])})
        rows.append(
            {
                "id": q["id"],
                "expected": sorted(q["expected_sources"]),
                "got": got,
                "ok_flag": bool(out.get("retrieval_ok")),
                "seconds": round(elapsed, 2),
            }
        )
    return rows


def _summary(rows: list[dict]) -> dict:
    tp = fp = fn = 0
    neg_total = neg_clean = 0
    for r in rows:
        expected, got = set(r["expected"]), set(r["got"])
        tp += len(expected & got)
        fp += len(got - expected)
        fn += len(expected - got)
        if not expected:
            neg_total += 1
            neg_clean += 0 if got else 1
    pos = [r for r in rows if r["expected"]]
    perfect = sum(1 for r in pos if set(r["got"]) == set(r["expected"]))
    return {
        "recall": round(tp / (tp + fn), 3) if tp + fn else 0.0,
        "precision": round(tp / (tp + fp), 3) if tp + fp else 0.0,
        "positivos_exactos": f"{perfect}/{len(pos)}",
        "negativos_sin_evidencia": f"{neg_clean}/{neg_total}",
        "fuentes_de_ruido": fp,
        "fuentes_perdidas": fn,
        "segundos_promedio": round(sum(r["seconds"] for r in rows) / len(rows), 2),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--etiqueta", default="corrida")
    parser.add_argument("--rol", default="control_total")
    parser.add_argument("--golden", type=Path, default=GOLDEN, help="archivo de preguntas")
    args = parser.parse_args()

    rows = asyncio.run(_run(args.rol, args.golden))
    for r in rows:
        mark = "" if set(r["got"]) == set(r["expected"]) else "  <-- difiere"
        print(f"{r['id']:<4} esperado={r['expected']} llega={r['got']} ({r['seconds']}s){mark}")
    summary = _summary(rows)
    print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))

    _env.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _env.RESULTS_DIR / f"recuperacion_{args.etiqueta}.json"
    path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"detalle: {path}")


if __name__ == "__main__":
    main()
