"""
eval_router

Corre app.nodes.router.router_node contra el dataset de calibración
(router_eval_dataset.json) y reporta aciertos/fallos por dominio.

Requiere Ollama corriendo de verdad (usa el modelo real, no un mock) — por
eso vive en eval/ y no en tests/: no es apto para CI, es una herramienta de
calibración manual que se corre contra el stack levantado.

Uso:
    docker exec -it mhia-app python -m eval.eval_router
"""

import asyncio
import json
from pathlib import Path

from app.nodes.router import router_node

DATASET_PATH = Path(__file__).parent / "router_eval_dataset.json"


async def run() -> None:
    cases = json.loads(DATASET_PATH.read_text(encoding="utf-8"))

    correct, total_checked, failures = 0, 0, []

    print(f"{'Consulta':<70} {'Esperado':<12} {'Obtenido':<12} {'OK'}")
    print("-" * 105)

    for case in cases:
        result = await router_node({"query": case["query"]})
        got_domain = result.get("data_domain")
        expected = case.get("expected_domain")

        if expected is None:
            ok = got_domain in {"ventas", "inventario", "proveedores", "general"}
            marker = "—" if ok else "FALLÓ (dominio inválido)"
        else:
            total_checked += 1
            ok = got_domain == expected
            correct += int(ok)
            marker = "✅" if ok else "❌"
            if not ok:
                failures.append((case["query"], expected, got_domain))

        q_short = case["query"][:68]
        print(f"{q_short:<70} {str(expected):<12} {str(got_domain):<12} {marker}")

    print("-" * 105)
    if total_checked:
        pct = 100 * correct / total_checked
        print(f"\nAciertos: {correct}/{total_checked} ({pct:.0f}%)")

    if failures:
        print("\nFallos a revisar:")
        for query, expected, got in failures:
            print(f"  - {query!r}: esperado={expected!r}, obtenido={got!r}")
    else:
        print("\nSin fallos en los casos con dominio esperado definido. 🎉")


if __name__ == "__main__":
    asyncio.run(run())