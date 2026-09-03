import asyncio
import uuid

from app.graph import mhia_graph


async def run(query: str) -> None:
    initial_state = {
        "messages": [],
        "query": query,
        "session_id": str(uuid.uuid4()),
    }

    result = await mhia_graph.ainvoke(initial_state)

    print("\n--- MH IA ---")
    print(f"Dominio detectado : {result.get('data_domain')}")
    print(f"Confidencial      : {result.get('is_confidential')}")
    print(f"Recuperación OK   : {result.get('retrieval_ok')}")
    print(f"Abortado          : {result.get('aborted', False)}")
    print("\nRespuesta:\n", result.get("final_answer"))


if __name__ == "__main__":
    import sys

    query = (
        sys.argv[1]
        if len(sys.argv) > 1
        else ("¿Cuántas máquinas de la consignación de Grupo Industrial X " "siguen sin vender?")
    )
    asyncio.run(run(query))
