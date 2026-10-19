"""Prueba de laboratorio: corre preguntas reales por el grafo completo.

Usa Ollama real (router, anonimizador, embeddings), Qdrant real (el del laboratorio) y la API
real de Claude, solo con los documentos SINTETICOS de lab/docs. Por cada pregunta registra:
dominio detectado, si se anonimizo, si el gateway bloqueo, y si algun "canario" (dato
sensible conocido de los documentos de prueba) llego en el texto que se enviaria a Claude.

  - Fuga de canario  -> FUGA (el unico resultado inaceptable).
  - Resultado fuera de lo esperado -> FALLA de comportamiento.
  - Dominio distinto al esperado -> solo se marca (~): es medicion del router, no fallo.

El reporte completo (incluido el texto anonimizado, para revisar la calidad del anonimizador)
se guarda en lab/results/ (ignorado por git). Son datos sinteticos, no hay nada real ahi.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.request
import uuid
from datetime import datetime

import _env  # DEBE ir antes que pipeline_ai: fija QDRANT_URL/COLLECTION antes de leer settings

# isort: split
from pipeline_ai.config import settings
from pipeline_ai.graph import mhia_graph

QUESTION_TIMEOUT_S = 180
QUESTIONS_FILE = _env.LAB_DIR / "questions.json"
CANARIES_FILE = _env.LAB_DIR / "canaries.txt"


def _load_canaries() -> list[str]:
    lines = CANARIES_FILE.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")]


def _preflight() -> None:
    problems: list[str] = []
    if not settings.anthropic_api_key:
        problems.append("Falta ANTHROPIC_API_KEY en .env")
    for name, url in (
        ("Ollama", f"{settings.ollama_base_url}/api/tags"),
        ("Qdrant", f"{settings.qdrant_url}/collections"),
    ):
        try:
            urllib.request.urlopen(url, timeout=5).read()  # noqa: S310 (URL local fija)
        except Exception as exc:
            problems.append(f"{name} no responde en {url} ({type(exc).__name__})")
    if problems:
        raise SystemExit("Preflight fallido:\n  - " + "\n  - ".join(problems))


def classify(result: dict) -> str:
    if result.get("aborted"):
        if str(result.get("abort_reason", "")).startswith("envio_bloqueado"):
            return "blocked"
        if result.get("retrieval_ok") is False:
            return "no_evidence"
        return "aborted"
    if result.get("outbound") is not None:
        return "answered"
    return "local_or_clarify"


def find_leaks(result: dict, canaries: list[str]) -> list[str]:
    payload = result.get("outbound")
    if payload is None:
        return []
    sent = f"{payload.query}\n{payload.context}".casefold()
    return [c for c in canaries if c.casefold() in sent]


async def run_one(question: dict, canaries: list[str]) -> dict:
    started = time.perf_counter()
    record: dict = {
        "id": question["id"],
        "query": question["query"],
        "expected_domain": question.get("domain"),
        "expected_outcomes": question["outcomes"],
    }
    try:
        result = await asyncio.wait_for(
            mhia_graph.ainvoke(
                {"messages": [], "query": question["query"], "session_id": str(uuid.uuid4())}
            ),
            timeout=QUESTION_TIMEOUT_S,
        )
    except TimeoutError:
        record.update(outcome="timeout", error=f"sin respuesta en {QUESTION_TIMEOUT_S}s", leaks=[])
        record["seconds"] = round(time.perf_counter() - started, 1)
        return record
    except Exception as exc:
        record.update(outcome="error", error=f"{type(exc).__name__}: {exc}", leaks=[])
        record["seconds"] = round(time.perf_counter() - started, 1)
        return record

    record.update(
        domain=result.get("data_domain"),
        confidential=result.get("is_confidential"),
        route=result.get("route"),
        retrieval_ok=result.get("retrieval_ok"),
        anonymized=result.get("anonymized"),
        outcome=classify(result),
        abort_reason=result.get("abort_reason", ""),
        leaks=find_leaks(result, canaries),
        anonymized_query=result.get("anonymized_query", ""),
        anonymized_context=result.get("anonymized_context", ""),
        answer=result.get("final_answer", ""),
    )
    record["seconds"] = round(time.perf_counter() - started, 1)
    return record


def verdict(record: dict) -> str:
    if record["leaks"]:
        return "FUGA"
    if record["outcome"] not in record["expected_outcomes"]:
        return "FALLA"
    return "ok"


async def main(only: str | None) -> int:
    _preflight()
    canaries = _load_canaries()
    questions = json.loads(QUESTIONS_FILE.read_text(encoding="utf-8"))
    if only:
        questions = [q for q in questions if q["id"] == only]
        if not questions:
            raise SystemExit(f"No existe la pregunta '{only}'")

    records = []
    print(f"\n{'id':<5} {'resultado':<9} {'salida':<17} {'dominio':<15} {'anon':<5} {'seg':>5}")
    print("-" * 62)
    for question in questions:
        rec = await run_one(question, canaries)
        records.append(rec)
        mismatch = (
            "~"
            if rec.get("expected_domain") and rec.get("domain") != rec["expected_domain"]
            else " "
        )
        print(
            f"{rec['id']:<5} {verdict(rec):<9} {rec['outcome']:<17} "
            f"{str(rec.get('domain')):<14}{mismatch} {str(rec.get('anonymized')):<5} "
            f"{rec['seconds']:>5}"
        )
        if rec["leaks"]:
            print(f"      CANARIOS EN EL TEXTO ENVIADO A CLAUDE: {rec['leaks']}")
        if rec["outcome"] == "blocked":
            print(f"      motivo del bloqueo: {rec['abort_reason']}")
        if rec["outcome"] == "error":
            print(f"      error: {rec['error']}")

    verdicts = [verdict(r) for r in records]
    blocked = [r for r in records if r["outcome"] == "blocked"]
    answered = [r for r in records if r["outcome"] == "answered"]
    domain_known = [r for r in records if r.get("expected_domain")]
    domain_ok = [r for r in domain_known if r.get("domain") == r["expected_domain"]]

    print("\n=== Resumen ===")
    print(f"preguntas            : {len(records)}")
    print(
        f"ok / FALLA / FUGA    : {verdicts.count('ok')} / "
        f"{verdicts.count('FALLA')} / {verdicts.count('FUGA')}"
    )
    print(f"respondidas (Claude) : {len(answered)}")
    print(f"bloqueadas (gateway) : {len(blocked)}")
    print(f"router (dominio)     : {len(domain_ok)}/{len(domain_known)} aciertos")

    _env.RESULTS_DIR.mkdir(exist_ok=True)
    out = _env.RESULTS_DIR / f"lab_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"reporte completo     : {out}")

    return 0 if all(v == "ok" for v in verdicts) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Laboratorio MH IA (datos sinteticos)")
    parser.add_argument("--only", default=None, help="id de una sola pregunta (p.ej. q03)")
    sys.exit(asyncio.run(main(parser.parse_args().only)))
