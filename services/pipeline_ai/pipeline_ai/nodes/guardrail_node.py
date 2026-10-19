"""
abort_node

REGLA CRÍTICA #1 (cero alucinaciones - parte 2/2):
Punto terminal cuando retrieval_ok=False. Genera una respuesta explícita de
desconocimiento SIN invocar a Claude ni a ningún LLM generativo: es una
plantilla fija para garantizar que nunca se "rellene" la ausencia de datos
con una inferencia no fundamentada.
"""

from pipeline_ai.state import MHIAState


async def abort_node(state: MHIAState) -> dict:
    domain = state.get("data_domain", "general")
    return {
        "aborted": True,
        "abort_reason": (
            f"No se encontró información suficiente en las fuentes disponibles "
            f"(dominio: {domain}) para responder con certeza."
        ),
        "final_answer": (
            "No tengo información suficiente y verificada en las fuentes "
            "disponibles (Google Drive / ERPNext) para responder esto con "
            "certeza. Para evitar darte un dato incorrecto, prefiero indicar "
            "que lo desconozco. ¿Puedes reformular la consulta o indicarme "
            "en qué documento/registro debería buscar?"
        ),
    }
